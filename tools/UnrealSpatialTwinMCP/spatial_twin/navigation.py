"""Use the installed engine's Recast/Detour offline; no Editor or live RPC."""
import ctypes
import json
import os
import math
import hashlib
import tempfile
import uuid
from pathlib import Path
from functools import lru_cache
from .store import Store, encode, vector
from . import file_signature,native_library_path


def input_changed(before,after):
    """Aggregate bounds track children; they are not an extra rasterized body."""
    if not any(e and e.get('affects_navigation') for e in (before,after)) or before==after:return False
    if all(e and e.get('collision',{}).get('aggregate_only') for e in (before,after)):
        bounds={'bounds','local_bounds','collision_bounds','collision_local_bounds'}
        if {k:v for k,v in before.items() if k not in bounds}=={k:v for k,v in after.items() if k not in bounds}:return False
    return True


def backend(engine_directory, native_library):
    native_library=str(native_library_path(engine_directory,native_library))
    library=_load_backend(engine_directory,native_library)
    if file_signature(native_library)!=library._source_signature:
        raise ValueError('Native offline library changed since loading; restart MCP before continuing')
    return library


@lru_cache(maxsize=4)
def _load_backend(engine_directory, native_library):
    if os.name != 'nt':
        raise ValueError('This native build supports Win64')
    signature=file_signature(native_library)
    folders = [Path(engine_directory)/'Binaries/Win64', Path(native_library).parent]
    handles = [os.add_dll_directory(str(p)) for p in folders]
    directory=Path.cwd()
    try:library = ctypes.CDLL(str(native_library))
    finally:os.chdir(directory) # Unreal DLL initialization can change process cwd.
    library._directory_handles = handles
    library._source_signature=signature
    library.STNavigationQuery.argtypes = [ctypes.c_char_p,ctypes.c_char_p,ctypes.c_void_p,ctypes.c_int]
    library.STNavigationQuery.restype = ctypes.c_int
    library.STBuildNavigation.argtypes = [ctypes.c_char_p,ctypes.c_char_p,ctypes.c_char_p,ctypes.c_void_p,ctypes.c_int]
    library.STBuildNavigation.restype = ctypes.c_int
    return library


def invoke(function, *arguments):
    directory=Path.cwd()
    try:return _invoke(function,*arguments)
    finally:os.chdir(directory)


def _invoke(function, *arguments):
    output = ctypes.create_string_buffer(1 << 20)
    count = function(*(str(a).encode('utf-8') for a in arguments),output,len(output))
    if count < 0:
        if -count > 16 << 20:
            raise ValueError('Navigation response exceeds budget')
        output = ctypes.create_string_buffer(-count)
        count = function(*(str(a).encode('utf-8') for a in arguments),output,len(output))
    if count <= 0:
        raise ValueError('Native navigation returned no valid result')
    return json.loads(output.raw[:count])


class Navigation:
    def __init__(self,store,agent):
        self.store,self.agent=store,agent

    def source(self,db,include_tiles=True):
        state=db.execute("SELECT value FROM metadata WHERE key='navigation_state'").fetchone()
        if state and (state[0].startswith('STALE') or state[0]=='BUILDING'):
            raise ValueError('Native navigation is not synchronized: '+state[0])
        if self.agent is None:
            agents=[r[0] for r in db.execute("SELECT DISTINCT json_extract(source,'$.agent') FROM entities WHERE kind='NavRegion' ORDER BY 1")]
            if len(agents)!=1:
                raise ValueError('Select an exported navigation agent: '+json.dumps(agents))
            self.agent=agents[0]
        # Tile inventories are needed for rebuilding, never for a path/status
        # request. Drop them inside SQLite before copying/decoding large arrays.
        column='source' if include_tiles else "json_remove(source,'$.tiles','$.input_gaps')"
        row=db.execute("SELECT "+column+" FROM entities WHERE kind='NavRegion' AND json_extract(source,'$.agent')=?",(self.agent,)).fetchone()
        if not row: raise ValueError('No exported navigation for agent '+self.agent)
        source=json.loads(row[0])
        path=(self.store.root/source['path']).resolve()
        if not path.is_relative_to(self.store.root): raise ValueError('Navigation path escapes Twin')
        return source,path

    def native(self,db):
        meta=dict(db.execute('SELECT key,value FROM metadata'))
        try: return backend(meta['engine_directory'],meta['native_library'])
        except (KeyError,OSError) as error: raise ValueError('Native offline backend unavailable: '+str(error)) from error

    def status(self):
        with self.store.read() as db:
            try:
                source,path=self.source(db,include_tiles=False)
                if not path.is_file():raise ValueError('Navigation cache missing')
                if not source.get('active_tiles'):raise ValueError('No exported navigation tiles; runtime invokers may be required')
                checked=invoke(self.native(db).STNavigationQuery,path,encode({'mode':'status'}))
                if checked.get('state')!='READY':raise ValueError(checked.get('error','Navigation cache contains no usable tiles'))
                capacity=source.get('detour_parameters',{}).get('max_tiles')
                return {'state':'READY','revision':Store.revision(db),'agent':self.agent,
                        'coverage':source.get('coverage'),'active_tiles':source.get('active_tiles'),
                        'tile_capacity':capacity,
                        'tile_pool_full':checked['active_tiles']>=capacity if capacity else None,
                        'capacity_settings':source.get('capacity_settings'),
                        'native_actor':{k:source.get(v) for k,v in (('id','source_actor_id'),('path','source_object_path'),('package','source_package'))},
                        'cache_exists':path.is_file(),'settings':source.get('settings'),
                        'input_coverage':source.get('input_coverage','unverified_legacy'),
                        'input_gap_count':source.get('input_gap_count')}
            except ValueError as error:
                return {'state':'UNKNOWN','agent':self.agent,'reason':str(error)}

    def query(self,mode,start,end=None,world='canonical',patch_id=None):
        request={'mode':mode,'start':vector(start)}
        if end is not None: request['end']=vector(end)
        with self.store.read() as db:
            try:
                source,path=self.source(db,include_tiles=False)
                if world=='canonical' and not source.get('active_tiles'):
                    raise ValueError('No exported navigation tiles; runtime invokers may be required')
                native=self.native(db)
                request['filter']=source.get('filter',{})
                if 'link_area_order' in source:request['link_area_order']=source['link_area_order']
                if source.get('query_extent'):request['extent']=source['query_extent']
                if world=='shadow':
                    if not patch_id: raise ValueError('Shadow requires patch_id')
                    patch=self.store.patch_get(patch_id)
                    if patch['base_revision']!=Store.revision(db): raise ValueError('CONFLICTED: canonical revision changed')
                    receipt=(patch.get('validation') or {}).get('navigation',{}).get(self.agent,{})
                    if receipt.get('state')!='READY': raise ValueError('Validate patch to build shadow navigation first')
                    path=(self.store.root/receipt['path']).resolve()
                    if not path.is_relative_to(self.store.root): raise ValueError('Shadow navigation path escapes Twin')
                elif world!='canonical': raise ValueError('Invalid world')
                result=invoke(native.STNavigationQuery,path,encode(request))
                capacity=source.get('detour_parameters',{}).get('max_tiles')
                return {**result,'revision':Store.revision(db),'agent':self.agent,'world':world,
                        'coverage':source.get('coverage'),
                        'baseline_tile_pool_full':source['active_tiles']>=capacity if capacity and 'active_tiles' in source else None}
            except ValueError as error:
                return {'state':'UNKNOWN','agent':self.agent,'reason':str(error),'path':[]}

    def rebuild(self,db,patch_id,tiles,*,_source=None):
        source,path=_source if _source is not None else self.source(db)
        native=self.native(db)
        masked=any(m.get('mask_fill_underneath') for t in tiles for m in t.get('modifiers',[]))
        if masked or any(t.get('rasterization_groups') for t in tiles):
            version=getattr(native,'STNavigationRasterizationVersion',None)
            if version is None or version()<(2 if masked else 1):raise ValueError('Native offline backend needs grouped rasterization support'+(' with masks' if masked else ''))
        directory=(self.store.root/'patches'/str(uuid.UUID(patch_id))).resolve()
        if not directory.is_relative_to(self.store.root):raise ValueError('Shadow navigation path escapes Twin')
        directory.mkdir(parents=True,exist_ok=True)
        descriptor,name=tempfile.mkstemp(prefix='navigation-',suffix='.tmp',dir=directory)
        os.close(descriptor);temporary=Path(name)
        try:
            request={'settings':source['settings'],'tiles':tiles}
            if 'link_area_order' in source:request['link_area_order']=source['link_area_order']
            result=invoke(native.STBuildNavigation,path,encode(request),temporary)
            if result.get('state')=='READY':
                with temporary.open('rb') as stream:digest=hashlib.file_digest(stream,'sha1').hexdigest()
                destination=directory/(digest+'.stn')
                if destination.exists():
                    with destination.open('rb') as stream:
                        if hashlib.file_digest(stream,'sha1').hexdigest()!=digest:raise ValueError('Corrupted Shadow navigation cache')
                else:os.replace(temporary,destination)
                result['path']=destination.relative_to(self.store.root).as_posix()
            return result
        finally:temporary.unlink(missing_ok=True)

    def simulate(self,db,patch_id,overlay):
        """Rebuild affected tiles from exact exported navigation inputs and modifiers."""
        source,path=self.source(db);parameters=source['detour_parameters'];settings=source['settings']
        if not source.get('active_tiles'):raise ValueError('Cannot validate navigation changes without baseline navigation coverage')
        origin=parameters['origin'];width=parameters['tile_width'];depth=parameters['tile_height'];padding=(math.ceil(settings['radius']/settings['cell_size'])+3)*settings['cell_size']
        affected={}
        for ident,after in overlay.items():
            try:before=Store.entity(db,ident)
            except ValueError:before=None
            if not input_changed(before,after):continue
            for e in (before,after):
                if not e or not e.get('affects_navigation') or not e.get('bounds'):continue
                lo,hi=e['bounds'];x0=math.floor((-hi[0]-padding-origin[0])/width);x1=math.floor((-lo[0]+padding-origin[0])/width)
                y0=math.floor((-hi[1]-padding-origin[2])/depth);y1=math.floor((-lo[1]+padding-origin[2])/depth)
                if (x1-x0+1)*(y1-y0+1)>65536:raise ValueError('Affected navigation tile budget exceeded')
                for x in range(x0,x1+1):
                    for y in range(y0,y1+1):
                        heights=affected.setdefault((x,y),[lo[2]-settings['height']-padding,hi[2]+settings['height']+padding])
                        heights[0]=min(heights[0],lo[2]-settings['height']-padding);heights[1]=max(heights[1],hi[2]+settings['height']+padding)
                        if len(affected)>65536:raise ValueError('Affected navigation tile budget exceeded')
        return self.rebuild_tiles(db,patch_id,affected,overlay,_source=(source,path))

    def rebuild_tiles(self,db,patch_id,affected,overlay=None,*,_source=None):
        """Rebuild explicit tile coordinates from indexed native source inputs."""
        from .query import Queries
        from . import geometry as g
        if len(affected)>65536:raise ValueError('Affected navigation tile budget exceeded')
        source,path=_source if _source is not None else self.source(db)
        if affected:
            if source.get('input_coverage')!='octree_audited':raise ValueError('Navigation input owners need a native coverage refresh before Shadow rebuilding')
            if source.get('input_gap_count',0)>len(source.get('input_gaps',[])):raise ValueError('Navigation input coverage is incomplete; gap diagnostics truncated')
        q=Queries(self.store,db,overlay);parameters=source['detour_parameters'];settings=source['settings']
        origin=parameters['origin'];width=parameters['tile_width'];depth=parameters['tile_height'];padding=(math.ceil(settings['radius']/settings['cell_size'])+3)*settings['cell_size']
        # One pass over cached layers, not a whole navigation scan per changed tile.
        prior_heights={}
        for tile in source.get('tiles',[]):
            key=(tile['x'],tile['y'])
            if key not in affected:continue
            height=prior_heights.setdefault(key,[tile['minimum_height'],tile['maximum_height']+settings['height']])
            height[0]=min(height[0],tile['minimum_height']);height[1]=max(height[1],tile['maximum_height']+settings['height'])
        capacity=parameters.get('max_tiles',0)
        if capacity>0 and source.get('active_tiles',0)>=capacity and len(prior_heights)<len(affected):
            raise ValueError('Navigation tile pool full; affected region has no baseline tiles. '
                             'Refresh native navigation coverage/capacity before certifying this Shadow region')
        requests=[];projected_owners={};raster_owners={}
        for (x,y),height in sorted(affected.items()):
            prior=prior_heights.get((x,y),height)
            height=[min(height[0],prior[0]),max(height[1],prior[1])]
            box=[[-(origin[0]+(x+1)*width)-padding,-(origin[2]+(y+1)*depth)-padding,height[0]],
                 [-(origin[0]+x*width)+padding,-(origin[2]+y*depth)+padding,height[1]]]
            # Replacing a coordinate replaces all its layers. Gather the native
            # vertical column, including surfaces hidden by the old geometry.
            for low,high in source.get('navigation_bounds',[]):
                if all(low[i]<=box[1][i] and high[i]>=box[0][i] for i in (0,1)):
                    height[0]=min(height[0],low[2]);height[1]=max(height[1],high[2])
            box[0][2],box[1][2]=height
            for gap in source.get('input_gaps',[]):
                if g.overlaps(box,gap['bounds']):raise ValueError('Native navigation input missing: '+gap.get('object_path','unavailable'))
            vertices=[];triangles=[];modifiers=[];links=[];groups=[]
            for e in q.region(box,('Component','Instance','NavigationInput')):
                if not e.get('affects_navigation'):continue
                if e.get('collision',{}).get('aggregate_only'):
                    if e.get('navigation_has_links') or e.get('navigation_modifiers'):
                        raise ValueError('Instanced navigation modifiers/links require per-instance native source: '+e['id'])
                    continue
                if e.get('navigation_preview_error'):raise ValueError(e['navigation_preview_error'])
                raster=e
                if e['kind']=='Instance' and e.get('parent_id'):
                    parent=e['parent_id']
                    if parent not in raster_owners:raster_owners[parent]=q.entity(parent)
                    if raster_owners[parent].get('collision',{}).get('aggregate_only'):raster=raster_owners[parent]
                flags=int(bool(raster.get('navigation_fill_underneath')))|2*int(bool(raster.get('navigation_filled_convex')))
                if flags & 1 and (source.get('fill_underneath_mask_count') is None or
                                  (source['fill_underneath_mask_count']>0 and source.get('rasterization_mask_version')!=1) or
                                  'navigation_filled_convex' not in raster or not source.get('navigation_bounds')):
                    raise ValueError('Fill-underneath requires a current native mask audit and geometry flags')
                if e.get('navigation_has_links'):
                    if e.get('navigation_link_coverage')!='point_links':raise ValueError('Native link source incomplete: '+e['id'])
                    if 'agent_index' not in settings or 'off_mesh_link_flag' not in source:raise ValueError('Navigation agent/link flags require a native export refresh')
                    for value in e.get('navigation_links',[]):
                        if settings['agent_index'] not in value['supported_agents']:continue
                        if value.get('requires_projection') and e['id'] in q.overlay:
                            # Recast consumes cached, already processed endpoints.
                            # A tile rebuild alone does not gather this owner again.
                            if e['id'] not in projected_owners:projected_owners[e['id']]=Store.entity(db,e['id'])
                            original=projected_owners[e['id']]
                            if not original or any(original.get(k)!=e.get(k) for k in ('transform','navigation_links')):
                                raise ValueError('Projected link endpoints require native geometry reprojection: '+e['id'])
                        area=source.get('areas',{}).get(value['area_class'])
                        if area is None:raise ValueError('Unresolved navigation link area '+value['area_class'])
                        link={k:value[k] for k in ('start','end','radius','user_id','bidirectional','reversed','snap_to_cheapest_area','generated')}
                        link['height']=value['height'] if value['use_snap_height'] else math.ceil(settings['climb']/settings['cell_height'])*settings['cell_height']
                        link['area_id']=area['id'];link['flags']=area['flags']|source['off_mesh_link_flag'];links.append(link)
                digest=e.get('navigation_geometry_hash')
                if not digest:
                    if e.get('collision',{}).get('enabled') and e.get('navigation_input_coverage')!='native_empty_geometry':raise ValueError('Native navigation input missing: '+e['id'])
                else:
                    if e.get('navigation_slope_behavior',0)!=0 and source.get('walkable_slope_policy')!='ue5.8.2-56702186-global-agent-slope':
                        raise ValueError('Per-surface walkable slope override requires native parity support: '+e['id'])
                    if any(type(raster.get(field)) is not bool for field in ('navigation_fill_underneath','navigation_filled_convex')):
                        raise ValueError('Native rasterization flags require a scoped cache refresh: '+e['id'])
                    mesh=g.load_mesh(q.geometry(digest)['path']);start=len(vertices)
                    points,faces=g.tile_mesh(mesh,e['transform'],box,preserve_volume=bool(flags))
                    if faces:groups.append({'first_triangle':len(triangles),'triangle_count':len(faces),'flags':flags})
                    vertices.extend(points)
                    triangles.extend([k+start for k in t] for t in faces)
                for value in e.get('navigation_modifiers',[]):
                    m=dict(value);area=source.get('areas',{}).get(m.get('area_class'))
                    if area is None:raise ValueError('Unresolved navigation area '+str(m.get('area_class')))
                    m['area_id']=area['id'];m['area_flags']=area['flags']
                    if m.get('mode',0)==1:
                        replacement=source.get('areas',{}).get(m.get('replace_area_class'))
                        if replacement is None:raise ValueError('Unresolved replacement navigation area')
                        m['replace_area_id']=replacement['id']
                    modifiers.append(m)
            requests.append({'x':x,'y':y,'layer':0,'minimum_height':height[0],'maximum_height':height[1],
                             'vertices':vertices,'triangles':triangles,'modifiers':modifiers,'areas':source.get('areas',{}),
                             'links':links,
                             **({'rasterization_groups':groups} if any(g['flags'] for g in groups) else {}),
                             'navigation_bounds':source.get('navigation_bounds',[])})
        return self.rebuild(db,patch_id,requests,_source=(source,path))
