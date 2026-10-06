"""Indexed canonical/overlay spatial queries with explicit geometry coverage."""
import json
import math
import sqlite3
from pathlib import Path
from .store import Store,vector,bounds
from . import geometry as g


class Queries:
    def __init__(self,store,db,overlay=None):
        self.is_shadow=overlay is not None
        self.store,self.db,self.overlay=store,db,overlay if overlay is not None else {}
        self.shadow_index=None
        self.entities={};self.geometries={}
        version=db.execute("SELECT value FROM metadata WHERE key='collision_bounds_version'").fetchone()
        self.collision_index_ready=bool(version and version[0]=='2')

    def __del__(self):
        if self.shadow_index is not None:self.shadow_index.close()

    def shadow_candidates(self,box,kind):
        if self.shadow_index is None:
            self.shadow_index=sqlite3.connect(':memory:')
            self.shadow_index.execute('CREATE VIRTUAL TABLE boxes USING rtree(rowid,x0,x1,y0,y1,z0,z1)')
            self.shadow_entities={}
            rows=[]
            for index,e in enumerate(self.overlay.values()):
                if e and g.spatial_bounds(e):
                    lo,hi=g.spatial_bounds(e);rows.append((index,lo[0],hi[0],lo[1],hi[1],lo[2],hi[2]));self.shadow_entities[index]=e
            self.shadow_index.executemany('INSERT INTO boxes VALUES(?,?,?,?,?,?,?)',rows)
        lo,hi=box
        for row in self.shadow_index.execute('SELECT rowid FROM boxes WHERE x0<=? AND x1>=? AND y0<=? AND y1>=? AND z0<=? AND z1>=?',(hi[0],lo[0],hi[1],lo[1],hi[2],lo[2])):
            e=self.shadow_entities[row[0]]
            if (not kind or e['kind']==kind or isinstance(kind,tuple) and e['kind'] in kind) and g.overlaps(g.spatial_bounds(e),box):yield e

    def entity(self,ident):
        if ident in self.overlay:
            if self.overlay[ident] is None: raise ValueError('Deleted entity: '+ident)
            return self.overlay[ident]
        if self.is_shadow and ident.startswith('staged:'):
            from .staging import get
            return get(self.store,ident)
        return Store.entity(self.db,ident)

    @staticmethod
    def region_sql(box,kind=None,columns='e.source',center=None,radius=None):
        box=bounds(box); lo,hi=box
        # CROSS JOIN keeps RTree candidates first even with selective kind filters.
        sql=f'SELECT {columns} FROM spatial_bounds s CROSS JOIN entities e ON e.rowid=s.rowid WHERE '
        clause='x0<=? AND x1>=? AND y0<=? AND y1>=? AND z0<=? AND z1>=?'
        sql+=' AND '.join(' AND '.join(prefix+'.'+part for part in clause.split(' AND ')) for prefix in ('s','e'))
        args=[hi[0],lo[0],hi[1],lo[1],hi[2],lo[2]]*2
        if kind:
            if isinstance(kind,tuple):sql+=' AND e.kind IN ('+','.join('?' for _ in kind)+')';args.extend(kind)
            else:sql+=' AND e.kind=?'; args.append(kind)
        if center is not None:
            center=vector(center);terms=[]
            for i,axis in enumerate('xyz'):
                delta=f'max(e.{axis}0-?,?-e.{axis}1,0)';terms.append(f'({delta}*{delta})');args.extend([center[i]]*4)
            sql+=' AND ('+'+'.join(terms)+')<=?';args.append(radius*radius)
        return sql,args

    def region(self,box,kind=None):
        sql,args=self.region_sql(box,kind,columns='e.id,e.source')
        for row in self.db.execute(sql,args):
            if row[0] not in self.entities:self.entities[row[0]]=json.loads(row[1])
            e=self.entities[row[0]]
            if e['id'] not in self.overlay and g.overlaps(g.spatial_bounds(e),box): yield e
        if self.overlay:yield from self.shadow_candidates(box,kind)

    def region_page(self,box,kind,query,limit,cursor,fields,center=None,radius=None):
        """Page indexed IDs before fetching canonical source blobs; overlay stays sparse."""
        sql,args=self.region_sql(box,kind,'e.id,NULL AS source',center,radius)
        if self.overlay:
            sql+=' AND e.id NOT IN (SELECT value FROM json_each(?))'
            args.append(json.dumps(list(self.overlay)))
        changed=[e for e in self.shadow_candidates(box,kind) if
                 center is None or g.box_distance(center,g.spatial_bounds(e))<=radius] if self.overlay else []
        sql+=" UNION ALL SELECT json_extract(value,'$.id') AS id,value AS source FROM json_each(?)"
        args.append(json.dumps(changed))
        return Store.page(self.db,sql+' ORDER BY id',args,query,limit,cursor,fields,source_overrides=True)

    def region_counts(self,box,kind=None,center=None,radius=None):
        """Aggregate indexed canonical rows and only decode the changed overlay."""
        sql,args=self.region_sql(box,kind,'coalesce(e.class,e.kind),count(*)',center,radius)
        if self.overlay:
            sql+=' AND e.id NOT IN (SELECT value FROM json_each(?))'
            args.append(json.dumps(list(self.overlay)))
        counts=dict(self.db.execute(sql+' GROUP BY coalesce(e.class,e.kind)',args))
        for e in self.shadow_candidates(box,kind) if self.overlay else ():
            if center is not None and g.box_distance(center,g.spatial_bounds(e))>radius:continue
            key=e.get('class') if e.get('class') is not None else e['kind']
            counts[key]=counts.get(key,0)+1
        return counts

    def nearest(self,position,k=1,kind='Actor',max_distance=None):
        position=vector(position)
        if type(k) is not int or not 1<=k<=500: raise ValueError('k must be 1..500')
        # Separate scalar aggregates use the six covering indexes (O(log n)).
        extent=self.db.execute('SELECT (SELECT min(x0) FROM entities),(SELECT max(x1) FROM entities),'
                               '(SELECT min(y0) FROM entities),(SELECT max(y1) FROM entities),'
                               '(SELECT min(z0) FROM entities),(SELECT max(z1) FROM entities)').fetchone()
        boxes=[g.spatial_bounds(e) for e in self.overlay.values() if e and g.spatial_bounds(e)]
        cap=max([abs(position[i]-extent[2*i+j]) for i in range(3) for j in range(2) if extent[2*i+j] is not None]+[1]+[abs(position[i]-b[j][i]) for b in boxes for i in range(3) for j in range(2)])
        cap*=math.sqrt(3)
        if max_distance is not None:
            if not isinstance(max_distance,(int,float)) or not math.isfinite(max_distance) or max_distance<0: raise ValueError('Invalid max_distance')
            cap=min(cap,max_distance)
        radius=min(100.,cap)
        while True:
            candidates=sorted(((g.box_distance(position,g.spatial_bounds(e)),e['id'],e) for e in self.region([g.sub(position,[radius]*3),g.add(position,[radius]*3)],kind)),key=lambda x:(x[0],x[1]))
            candidates=[c for c in candidates if c[0]<=cap]
            if (len(candidates)>=k and candidates[k-1][0]<=radius) or radius>=cap:
                return {'revision':Store.revision(self.db),'metric':'distance_to_bounds','results':[{'distance':d,'entity':e} for d,_,e in candidates[:k]]}
            radius=min(cap,max(radius*2,1))

    def geometry(self,digest):
        if digest in self.geometries:return self.geometries[digest]
        row=self.db.execute('SELECT path,metadata FROM geometry WHERE hash=?',(digest,)).fetchone()
        if not row:
            from .staging import geometry
            staged=geometry(self.store,digest)
            if not staged:raise ValueError('Geometry not cached: '+digest)
            row=(staged['path'],json.dumps(staged['metadata']))
        path=(self.store.root/row[0]).resolve()
        if not path.is_relative_to(self.store.root): raise ValueError('Geometry path escapes cache')
        value={'hash':digest,'path':str(path),'metadata':json.loads(row[1])};self.geometries[digest]=value;return value

    def ray_candidates(self,box,channel='Visibility',limit=None):
        """Query-local candidates; never reuse across snapshots or Shadow overlays."""
        for index,e in enumerate(self.region(box,('Component','Instance'))):
            if limit is not None and index>=limit:raise OverflowError('Footprint prefetch budget exceeded; use indexed individual rays')
            collision=e.get('collision',{})
            if collision.get('enabled') in (False,'NoCollision'):continue
            if collision.get('query_mode','3') not in ('1','3','5'):continue
            if collision.get('responses',{}).get(channel,'Block')!='Block':continue
            if not collision.get('aggregate_only'):yield e

    def raycast(self,origin,direction,max_distance,channel='Visibility',complex=False,*,candidates=None):
        origin=vector(origin); direction=g.unit(vector(direction))
        if not isinstance(max_distance,(int,float)) or not math.isfinite(max_distance) or max_distance<=0: raise ValueError('max_distance must be finite and positive')
        end=g.add(origin,g.mul(direction,max_distance)); box=[[min(a,b) for a,b in zip(origin,end)],[max(a,b) for a,b in zip(origin,end)]]
        hit=None; unknown=[]; seen=set()
        for e in self.ray_candidates(box,channel) if candidates is None else candidates:
            if candidates is not None and not g.overlaps(g.spatial_bounds(e),box):continue
            collision=e.get('collision',{})
            use_complex=(complex and collision.get('trace_mode')!='UseSimpleAsComplex') or collision.get('trace_mode')=='UseComplexAsSimple'
            coverage=collision.get('complex_coverage' if use_complex else 'simple_coverage')
            if coverage=='UNKNOWN':unknown.append(e['id'])
            shapes=collision.get('shapes',[]) if not complex or collision.get('trace_mode')=='UseSimpleAsComplex' else []
            if collision.get('trace_mode')=='UseComplexAsSimple': shapes=[]
            meshes=collision.get('complex' if complex else 'simple',[])
            if collision.get('trace_mode')=='UseComplexAsSimple': meshes=collision.get('complex',[])
            if collision.get('trace_mode')=='UseSimpleAsComplex': meshes=collision.get('simple',[])
            if not meshes and not shapes:
                if coverage not in ('EMPTY','UNKNOWN') and collision.get('enabled') and g.ray_box(origin,direction,g.spatial_bounds(e),max_distance) is not None: unknown.append(e['id'])
                continue
            for digest in meshes:
                key=(e['id'],digest)
                if key in seen: continue
                seen.add(key)
                try:
                    geo=self.geometry(digest); mesh=g.load_mesh(geo['path'])
                except ValueError:
                    unknown.append(e['id'])
                    continue
                transform=e.get('collision_transform',e['transform'])
                is_convex=(not complex or collision.get('trace_mode')=='UseSimpleAsComplex') and collision.get('trace_mode')!='UseComplexAsSimple' and e.get('spatial_type')!='Landscape'
                value=({'distance':0.,'position':origin,'normal':None,'initial_overlap':True} if is_convex and g.convex_contains(mesh,g.local(transform,origin)) else
                       g.mesh_hit(mesh,transform,origin,direction,hit['distance'] if hit else max_distance))
                if value:
                    hit={**value,'actor_id':e.get('actor_id'),'component_id':e.get('component_id',e['id']),
                         'instance_id':e['id'] if e['kind']=='Instance' else None,'geometry_hash':digest}
            for index,shape in enumerate(shapes):
                value=g.shape_hit(shape,e['transform'],origin,direction,hit['distance'] if hit else max_distance)
                if value:
                    hit={**value,'actor_id':e.get('actor_id'),'component_id':e.get('component_id',e['id']),
                         'instance_id':e['id'] if e['kind']=='Instance' else None,'primitive_index':index}
        return {'revision':Store.revision(self.db),'hit':hit,'complete':not unknown and self.collision_index_ready,'unknown_geometry':unknown[:50],'unknown_geometry_count':len(unknown),
                **({} if self.collision_index_ready else {'coverage_error':'Legacy collision index requires native cache upgrade'})}

    def overlap(self,box,kind='Component'):
        return {'revision':Store.revision(self.db),'phase':'AABB','complete':self.collision_index_ready,'entities':list(self.region(box,kind))}
