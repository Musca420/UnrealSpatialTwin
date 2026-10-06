"""Read/analyze MCP. This process has no Unreal live action client."""
import argparse
import json
from contextlib import nullcontext
from functools import wraps
from pathlib import Path
from typing import Literal
from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent
from .store import Store,bounds,vector,select_fields
from .query import Queries
from .shadow import preview,validate
from . import geometry as g
from . import require_current_runtime
from .contracts import PatchOperation,RayRequest,RayHitField,WorldSelection,Vector2,Vector3,Quaternion,EntityPageOutput,GroundPlanOutput,ReadQuery,ReadBatchOutput

class CompactMCP(FastMCP):
    compact_tools=False
    tool_profile='full'

    async def list_tools(self):
        tools=await super().list_tools()
        if self.tool_profile!='focused':tools=[tool for tool in tools if tool.name!='shadow_plan']
        # patch_prepare covers these three compatible entrypoints with one
        # operation schema. Existing callers may still invoke their exact names.
        if self.compact_tools:
            tools=[tool for tool in tools if tool.name not in {'patch_create','patch_update','patch_validate'}]
        if self.tool_profile=='workflow':
            entrypoints={'world_status','world_read','world_query','tool_describe','asset_stage',
                         'patch_prepare','patch_place','patch_ground','patch_preview','patch_status'}
            tools=[tool for tool in tools if tool.name in entrypoints]
        if self.tool_profile=='focused':
            tools=[tool for tool in tools if tool.name in {'world_read','shadow_plan','tool_describe'}]
        return tools

    async def call_tool(self,name,arguments):
        result=await super().call_tool(name,arguments)
        from .store import encode
        if isinstance(result,tuple) and len(result)==2 and isinstance(result[1],dict):
            # Keep the advertised structured result and legacy JSON fallback
            # identical, without formatting every coordinate on its own line.
            return [TextContent(type='text',text=encode(result[1]))],result[1]
        if isinstance(result,list) and len(result)==1 and isinstance(result[0],TextContent):
            try:value=json.loads(result[0].text)
            except ValueError:return result
            if isinstance(value,(dict,list)):
                return [TextContent(type='text',text=encode(value))]
        return result

def create_server(store,follow_active=False,compact_tools=False,tool_profile=None):
    mcp=CompactMCP('Unreal Spatial Twin',instructions=(
        'Use Spatial Twin for Unreal facts; official Unreal MCP for authorized live actions. '
        'world_read includes status and accepts exact bounded queries. In Code Mode, keep resolved '
        'source reads, exact task-predicate evaluation, strict Shadow validation, previously authorized '
        'official action, Canonical confirmation and returned image in one execution. Resolve a known '
        'binding and read actual source in the first execution; never print tool catalogues or stop '
        'solely for arithmetic/receipt decoding. Return for unresolved semantic decisions. Use exact '
        'dependent awaits inside that first execution, reusing the checked source revision; no '
        'repeat discovery or conclusive preview turn. For known long action compositions use '
        'exec/wait yield_time_ms=60000, return on completion, never one-second polling. Use exact '
        'output schemas and returned next_query for pagination. Stop on conflict, UNKNOWN '
        'or failed writes; never replay. Canonical is read-only. No screenshots for positions/bounds/'
        'collision, no per-task full scan. Jev is optional.'))
    mcp.tool_profile=tool_profile or ('compact' if compact_tools else 'full')
    if mcp.tool_profile not in ('full','compact','workflow','focused'):raise ValueError('Unknown tool profile')
    mcp.compact_tools=mcp.tool_profile!='full'
    from .worlds import ProjectStore,worlds
    base=store.root
    if follow_active:store=ProjectStore(base)
    def tool():
        def register(function):
            @wraps(function)
            def pinned(*args,**kwargs):
                require_current_runtime()
                with (store.bind() if isinstance(store,ProjectStore) else nullcontext()):
                    return function(*args,**kwargs)
            return mcp.tool()(pinned)
        return register
    def context(db,world,patch_id):
        if world=='canonical': return Queries(store,db)
        if world!='shadow' or not patch_id: raise ValueError('Shadow requires patch_id')
        patch=store.patch_get(patch_id)
        overlay,errors=preview(store,db,patch)
        if errors: raise ValueError('Invalid shadow operations: '+json.dumps(errors))
        result=Queries(store,db,overlay)
        result.patch_revision=patch_token(patch)
        return result

    @tool()
    def tool_describe(names:list[str])->dict:
        """Exact registered schemas for 1..8 named Twin tools, including reads exposed through world_read. Request only unknown arguments; no world/editor access. All implementation and validation remain available in every profile."""
        if not 1<=len(names)<=8 or len(set(names))!=len(names):raise ValueError('Supply 1..8 distinct exact tool names')
        registered=[mcp._tool_manager.get_tool(name) for name in names]
        if any(item is None for item in registered):raise ValueError('Unknown Twin tool')
        return {'tools':[{'name':item.name,'description':item.description,'inputSchema':item.parameters,
                         **({'outputSchema':item.output_schema} if item.output_schema else {})} for item in registered]}

    @tool()
    def world_status(include_counts:bool=False)->dict:
        """Freshness, READY snapshot, coverage and counts; no editor request."""
        return store.status(include_counts)

    @tool()
    def world_maps(limit:int=50,offset:int=0)->dict:
        """Cached map identities and store paths. Each map keeps its own history/patches."""
        if not 1<=limit<=100 or offset<0:raise ValueError('limit must be1..100; offset nonnegative')
        values=worlds(base)
        return {'maps':values[offset:offset+limit],'next_offset':offset+limit if offset+limit<len(values) else None}

    @tool()
    def world_query(request:str,queries:list[dict],decision_timeout_seconds:float=5)->dict:
        """Jev selects and executes one supplied bounded Twin query. Fixed arguments; no Unreal control. One choice bypasses AI."""
        from .assist import query
        return query(store,mcp,request,queries,decision_timeout_seconds)

    @tool()
    def world_read(queries:list[ReadQuery],schemas:list[str]|None=None)->ReadBatchOutput:
        """Known reads with fresh status. Bundle 1..16 {tool, arguments}, pages max20. Returns status and results:[{tool,data,next_query?}]; follow that row's next_query at the same revision. Entity pages in data have entities/cursor; bounds are [[minX,minY,minZ],[maxX,maxY,maxZ]]. Optional schemas returns tool_schemas:[{name,inputSchema,outputSchema?}], an array to find by name, never schemas[tool]. Omit schemas when known, never send an empty list; supply1..8 distinct names when missing. Source and only missing contracts together; no inventory turn. Rejects mixed Canonical/Shadow versions. No model calls; large results/schemas stay on disk, so narrow fields/pages."""
        from .assist import prepare
        from .store import encode
        import hashlib
        calls=prepare(mcp,queries)
        described=tool_describe(schemas)['tools'] if schemas is not None else None
        if any(c['tool']=='patch_validate' for c in calls):raise ValueError('Use patch_prepare/patch_validate explicitly')
        with store.read_snapshot():
            before=store.status(include_counts=False);revision=before.get('canonical_revision')
            patch_ids={c['arguments']['patch_id'] for c in calls if c['arguments'].get('patch_id')}
            patch_versions={ident:patch_token(store.patch_get(ident)) for ident in patch_ids}
            # prepare validated every advertised schema and the outer dispatcher
            # pinned the map/runtime. Recheck runtime and revision after the batch.
            results=[]
            for call in calls:
                handler=mcp._tool_manager.get_tool(call['tool']).fn
                data=getattr(handler,'__wrapped__',handler)(**call['arguments'])
                row={'tool':call['tool'],'data':data}
                if data.get('cursor') and 'cursor' in mcp._tool_manager.get_tool(call['tool']).parameters.get('properties',{}):
                    row['next_query']={'tool':call['tool'],'arguments':{**call['arguments'],'cursor':data['cursor']}}
                results.append(row)
        require_current_runtime()
        after=store.status(include_counts=False)
        if (revision,before.get('snapshot_id'))!=(after.get('canonical_revision'),after.get('snapshot_id')):
            return {'state':'CONFLICTED','status':after,'reason':'World changed during reads; mixed results discarded'}
        if any(patch_token(store.patch_get(ident))!=version for ident,version in patch_versions.items()):
            return {'state':'CONFLICTED','status':after,'reason':'Shadow patch changed during reads; mixed results discarded'}
        if any(r['data'].get('revision',revision)!=revision for r in results):
            return {'state':'CONFLICTED','status':after,'reason':'Query revision differs; mixed results discarded'}
        text=encode(results);result={'state':'READY','status':after,'query_count':len(results),'response_bytes':len(text.encode()),'model_calls':0}
        if described is not None:
            description=encode(described)
            if len(text.encode())+len(description.encode())<=12000:result['tool_schemas']=described
            else:
                path=store.root/'logs'/'read_batches'/(hashlib.sha256(description.encode()).hexdigest()+'.schemas.json')
                path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(description.encode())
                result['tool_schemas_reference']=str(path)
        if len(text.encode())<=12000:result['results']=results
        else:
            path=store.root/'logs'/'read_batches'/(hashlib.sha256(text.encode()).hexdigest()+'.json')
            path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text,encoding='utf-8')
            result.update(results_reference=str(path),reason='Narrow fields/pages or read the needed artifact section')
        return result

    @tool()
    def shadow_plan(tool:Literal['patch_prepare','patch_place','patch_ground','patch_preview','patch_status','patch_continue','asset_stage'],arguments:dict,map_id:str)->dict:
        """Exact offline Shadow operation through its existing validated handler; no live Unreal or Canonical writes. map_id must match world_read.status.map. Request an unknown handler schema alongside source with world_read(schemas=[tool]), then pass those exact arguments here. Keeps full/compact/workflow interfaces compatible; focused avoids advertising every patch schema upfront. Never guesses selection, rebases or executes a patch."""
        import inspect
        if tool not in ('patch_prepare','patch_place','patch_ground','patch_preview','patch_status','patch_continue','asset_stage'):
            raise ValueError('Only named offline Shadow operations are permitted')
        if store.status(False).get('map')!=map_id:raise ValueError('CONFLICTED: selected map changed')
        handler=mcp._tool_manager.get_tool(tool)
        inspect.signature(handler.fn).bind(**arguments)
        values=handler.fn_metadata.arg_model.model_validate(arguments,strict=True).model_dump(exclude_unset=True,by_alias=True)
        return getattr(handler.fn,'__wrapped__',handler.fn)(**values)

    @tool()
    def world_search(text:str='',kind:str|None=None,limit:int=50,cursor:str|None=None,fields:list[str]|None=None,
                     component_fields:list[str]|None=None,component_limit:int=4)->EntityPageOutput:
        """Search source label/path/class. Returns {revision,entities,cursor}; each entity retains id/kind and requested fields. component_fields embeds Actor.components:{revision,entities,cursor}, with requested fields (e.g. asset_id) on each component entity. Check exact selection and both cursors locally before dependent reads in the same execution. Requires kind=Actor, limit<=20 and component_limit1..8; follow child cursor with entity_children. No recursive expansion or inferred suitability. Source units cm. StaticMesh is exact mesh kind; Asset is registry metadata."""
        query={'text':text,'kind':kind,'fields':fields}
        if component_fields is not None:
            if (kind!='Actor' or type(limit) is not int or not 1<=limit<=20
                or type(component_limit) is not int or not 1<=component_limit<=8
                or not isinstance(component_fields,list) or not 1<=len(component_fields)<=20
                or any(not isinstance(f,str) or not f for f in component_fields)):
                raise ValueError('Actor child expansion requires1..20Actors,1..8children and1..20field names')
            query.update(component_fields=component_fields,component_limit=component_limit)
        with store.read() as db:
            sql,args=store.search_sql(db,text,kind)
            result=store.page(db,sql,args,query,limit,cursor,fields,entity_ids=True)
            if component_fields is not None:
                for actor in result['entities']:
                    actor['components']=store.page(db,'SELECT source FROM entities WHERE parent_id=? ORDER BY id',
                        [actor['id']],{'parent':actor['id'],'fields':component_fields},component_limit,None,component_fields)
            return result

    @tool()
    def world_region(center:list[float],radius:float,detail:Literal['summary','entities']='summary',kind:str='Actor',fields:list[str]|None=None,limit:int=50,cursor:str|None=None,world:WorldSelection='canonical',patch_id:str|None=None)->dict:
        """Sphere region, summary by class or bounded entities. radius is cm."""
        import math
        center=vector(center)
        if not math.isfinite(radius) or radius<0: raise ValueError('Invalid radius')
        with store.read() as db:
            q=context(db,world,patch_id); box=[g.sub(center,[radius]*3),g.add(center,[radius]*3)]
            if world=='canonical':
                if detail=='summary':
                    sql,args=Queries.region_sql(box,kind,"coalesce(e.class,e.kind),count(*)",center,radius)
                    counts=dict(db.execute(sql+' GROUP BY coalesce(e.class,e.kind)',args))
                    return {'revision':Store.revision(db),'world':world,'counts':counts,'total':sum(counts.values())}
                if detail!='entities':raise ValueError('detail must be summary or entities')
                sql,args=Queries.region_sql(box,kind,center=center,radius=radius)
                return store.page(db,sql+' ORDER BY e.id',args,{'center':center,'radius':radius,'kind':kind,'world':world,'patch':patch_id,'fields':fields},limit,cursor,fields)
            if detail=='summary':
                counts=q.region_counts(box,kind,center,radius)
                return {'revision':Store.revision(db),'world':world,'counts':counts,'total':sum(counts.values())}
            if detail!='entities': raise ValueError('detail must be summary or entities')
            # Region filtering is indexed. A bounded page is assembled without a massive JSON dump.
            return q.region_page(box,kind,{'center':center,'radius':radius,'kind':kind,'world':world,'patch':patch_id,'patch_revision':q.patch_revision,'fields':fields},limit,cursor,fields,center,radius)

    @tool()
    def entity_get(entity_id:str,world:WorldSelection='canonical',patch_id:str|None=None,fields:list[str]|None=None)->dict:
        """Compact source identity/pose/bounds. fields supports nested paths such as collision.enabled or properties.bHidden; selecting collision returns all its geometry, so request only needed leaves. Unknown leaves remain absent."""
        with store.read() as db:
            e=context(db,world,patch_id).entity(entity_id)
            return {'revision':Store.revision(db),'entity':select_fields(e,fields)}

    @tool()
    def entity_children(entity_id:str,limit:int=50,cursor:str|None=None,fields:list[str]|None=None)->dict:
        """Direct source containment; paginated."""
        with store.read() as db:
            return store.page(db,'SELECT source FROM entities WHERE parent_id=? ORDER BY id',[entity_id],{'parent':entity_id,'fields':fields},limit,cursor,fields)

    @tool()
    def entity_relationships(entity_id:str,direction:Literal['both','incoming','outgoing']='both',kind:str|None=None,limit:int=50,cursor:str|None=None)->dict:
        """Actual source relations. No inferred NEAR/LEFT_OF relations."""
        if direction not in ('both','incoming','outgoing'): raise ValueError('Invalid direction')
        condition={'both':'(source=? OR target=?)','incoming':'target=?','outgoing':'source=?'}[direction]
        args=[entity_id]* (2 if direction=='both' else 1)
        if kind: condition+=' AND kind=?'; args.append(kind)
        with store.read() as db:
            entity=Store.entity(db,entity_id)
            result=store.page(db,"SELECT json_object('id',source||':'||kind||':'||target,'kind',kind,'source',source,'target',target) AS source FROM relationships WHERE "+condition+' ORDER BY source,kind,target',args,{'id':entity_id,'direction':direction,'kind':kind},limit,cursor,['source','target'])
            if direction=='outgoing' and kind=='DEPENDS_ON':
                result['dependency_state']=entity.get('dependencies_state','UNKNOWN')
                result['dependency_source']='asset_registry_disk'
                result['live_dependency_state']=entity.get('live_dependencies_state','UNKNOWN')
                result['package_dirty']=entity.get('package_dirty')
                result['complete']=result['dependency_state']=='CURRENT'
            return result

    @tool()
    def spatial_nearest(position:list[float],k:int=1,kind:str='Actor',max_distance:float|None=None,world:WorldSelection='canonical',patch_id:str|None=None,fields:list[str]|None=None)->dict:
        """Indexed nearest/k-nearest by distance to exact bounds, in cm."""
        with store.read() as db:
            result=context(db,world,patch_id).nearest(position,k,kind,max_distance)
            for item in result['results']:item['entity']=select_fields(item['entity'],fields)
            return result

    @tool()
    def spatial_overlap(aabb:list[list[float]],kind:str='Component',world:WorldSelection='canonical',patch_id:str|None=None,limit:int=50,cursor:str|None=None,fields:list[str]|None=None)->dict:
        """Indexed broad-phase region. Component gives group poses/assets; Actor gives native spawn class. Bundle both in world_read if needed. fields supports collision.enabled, collision.simple_coverage, etc.; avoid full collision geometry for placement planning, use spatial_support and patch_prepare for exact checks. Source: class, transform, bounds, actor_id, asset_id. phase=AABB is not a penetration certificate."""
        with store.read() as db:
            q=context(db,world,patch_id)
            if world=='canonical':
                sql,args=Queries.region_sql(aabb,kind)
                return {'phase':'AABB','complete':q.collision_index_ready,**store.page(db,sql+' ORDER BY e.id',args,{'aabb':aabb,'kind':kind,'world':world,'patch':patch_id,'fields':fields},limit,cursor,fields)}
            return {'phase':'AABB','complete':q.collision_index_ready,**q.region_page(aabb,kind,{'aabb':aabb,'kind':kind,'world':world,'patch':patch_id,'patch_revision':q.patch_revision,'fields':fields},limit,cursor,fields)}

    @tool()
    def spatial_raycast(origin:list[float],direction:list[float],max_distance:float,channel:str='Visibility',complex:bool=False,world:WorldSelection='canonical',patch_id:str|None=None)->dict:
        """Collision triangle raycast with normals and explicit coverage. No screenshots."""
        with store.read() as db: return context(db,world,patch_id).raycast(origin,direction,max_distance,channel,complex)

    @tool()
    def spatial_raycast_batch(rays:list[RayRequest],channel:str='Visibility',complex:bool=False,world:WorldSelection='canonical',patch_id:str|None=None,fields:list[RayHitField]|None=None,detail:Literal['hits','distances']='hits')->dict:
        """1..128 rays in one consistent indexed read; selected hit fields, explicit completeness. No editor requests."""
        if not 1<=len(rays)<=128:raise ValueError('Supply 1..128 rays')
        if detail not in ('hits','distances'):raise ValueError('Choose hits or distances')
        if detail=='distances' and fields not in (None,['distance']):raise ValueError('Distances detail only returns distance values')
        permitted={'distance','actor_id','component_id','instance_id','position','normal','triangle','primitive'}
        selected=fields if fields is not None else ['distance','actor_id','component_id']
        if len(selected)>len(permitted) or set(selected)-permitted:raise ValueError('Unknown ray hit field; choose: '+', '.join(sorted(permitted)))
        # Validate the entire batch before doing work; no silent partial argument acceptance.
        for ray in rays:
            if set(ray)!={'origin','direction','max_distance'}:raise ValueError('Each ray needs origin, direction, max_distance')
            vector(ray['origin']);vector(ray['direction'])
            import math
            if type(ray['max_distance']) not in (int,float) or not math.isfinite(ray['max_distance']) or ray['max_distance']<0:raise ValueError('Invalid ray distance')
            if sum(v*v for v in ray['direction'])<1e-20:raise ValueError('Ray direction is zero')
        with store.read() as db:
            q=context(db,world,patch_id);results=[]
            for ray in rays:
                value=q.raycast(ray['origin'],ray['direction'],ray['max_distance'],channel,complex)
                hit=value.get('hit')
                result={'complete':value['complete'],'hit':None if hit is None else {k:hit[k] for k in selected if k in hit}}
                for key in ('unknown','unknown_candidates','coverage','unknown_geometry','unknown_geometry_count'):
                    if key in value:result[key]=value[key]
                results.append(result)
            result={'revision':Store.revision(db),'world':world,'complete':all(r['complete'] for r in results)}
            if detail=='distances':
                return {**result,'distances':[r['hit']['distance'] if r['hit'] else None for r in results],
                        'incomplete':[{'index':i,**r} for i,r in enumerate(results) if not r['complete']]}
            return {**result,'results':results}

    @tool()
    def spatial_support(candidates:list[Vector3],offsets:list[Vector2],max_distance:float,max_height_difference:float=1,clearance:float=.2,world:WorldSelection='canonical',patch_id:str|None=None,detail:Literal['compact','full']='compact')->dict:
        """Complex collision footprint samples in cm. Compact preserves decisions, positions, height ranges and continuous-support identity/normal/primitive. Full adds source geometry hash/tolerance and certificate scope. Sampling alone never proves continuous support or navigation."""
        import math
        if detail not in ('compact','full'):raise ValueError('Unknown support detail')
        if not 1<=len(candidates)<=128 or not 1<=len(offsets)<=512 or len(candidates)*len(offsets)>8192:raise ValueError('Supply1..128candidates,1..512offsets,at most8192samples')
        candidates=[vector(p) for p in candidates];offsets=[vector(p,2) for p in offsets]
        for value in (max_distance,max_height_difference,clearance):
            if type(value) not in (int,float) or not math.isfinite(value) or value<0:raise ValueError('Finite nonnegative support parameters required')
        if max_distance<=0:raise ValueError('Positive ray distance required')
        accepted=[];rejected=[];unknown=set();complete=True
        with store.read() as db:
            q=context(db,world,patch_id)
            low=[min(p[i] for p in offsets) for i in (0,1)]
            high=[max(p[i] for p in offsets) for i in (0,1)]
            for index,p in enumerate(candidates):
                heights=[];hits=[];missing=False;uncertain=False;inside=False
                try:
                    nearby=tuple(q.ray_candidates([[p[0]+low[0],p[1]+low[1],p[2]-max_distance],
                                                   [p[0]+high[0],p[1]+high[1],p[2]]],limit=1024))
                except OverflowError:nearby=None  # Sparse huge footprints retain indexed per-ray queries.
                for x,y in offsets:
                    result=q.raycast([p[0]+x,p[1]+y,p[2]],[0,0,-1],max_distance,complex=True,candidates=nearby)
                    uncertain|=not result['complete'];unknown.update(result.get('unknown_geometry',[]))
                    if result['hit']:
                        heights.append(p[2]-result['hit']['distance']);hits.append(result['hit'])
                        inside|=bool(result['hit'].get('initial_overlap'))
                    else:missing=True
                complete&=not uncertain
                reason='unknown_geometry' if uncertain else 'origin_inside_collision' if inside else 'missing_support' if missing else 'uneven_support' if max(heights)-min(heights)>max_height_difference else None
                if reason:rejected.append({'index':index,'reason':reason})
                else:
                    # A single box face is convex: if the four footprint
                    # corners hit that same face, its whole rectangle is
                    # supported. A single actual triangle is convex too;
                    # disconnected faces and arbitrary samples remain unproven.
                    corners={(low[0],low[1]),(low[0],high[1]),(high[0],low[1]),(high[0],high[1])}
                    first=hits[0];normal=first.get('normal')
                    identity=(first.get('component_id'),first.get('instance_id'),first.get('primitive_index'))
                    proven=(corners.issubset({tuple(v) for v in offsets}) and first.get('primitive')=='box'
                        and identity[0] is not None and identity[2] is not None and normal is not None and normal[2]>0
                        and all(h.get('primitive')=='box' and (h.get('component_id'),h.get('instance_id'),h.get('primitive_index'))==identity
                            and h.get('normal')==normal for h in hits))
                    evidence={'state':'PROVEN' if proven else 'UNPROVEN','scope':'footprint_rectangle_surface'}
                    if proven:evidence.update(component_id=identity[0],instance_id=identity[1],primitive_index=identity[2],primitive='box',normal=normal)
                    elif corners.issubset({tuple(v) for v in offsets}) and first.get('geometry_hash'):
                        digest=first['geometry_hash']
                        same=all(h.get('geometry_hash')==digest and h.get('component_id')==identity[0]
                                 and h.get('instance_id')==identity[1] and 'triangle' in h for h in hits)
                        if same and normal is not None and normal[2]>0 and all(h['triangle']==first['triangle'] for h in hits):
                            evidence.update(state='PROVEN',component_id=identity[0],instance_id=identity[1],
                                geometry_hash=digest,primitive='triangle_face',triangle=first['triangle'],normal=normal)
                        elif same:
                            mesh=g.load_mesh(q.geometry(digest)['path']);certificate=g.closed_convex_planes(mesh)
                            if certificate:
                                planes,epsilon=certificate;n,d=planes[first['triangle']]
                                face_matches=all(g.length(g.sub(planes[h['triangle']][0],n))<=1e-12
                                                 and abs(planes[h['triangle']][1]-d)<=epsilon for h in hits)
                                component=q.entity(identity[1] or identity[0]);transform=component.get('collision_transform',component['transform'])
                                world_normal=g.unit(g.rotate(transform['rotation'],[n[i]/transform['scale'][i] for i in range(3)]))
                                if face_matches and world_normal[2]>0:
                                    evidence.update(state='PROVEN',component_id=identity[0],instance_id=identity[1],
                                        geometry_hash=digest,primitive='closed_convex_mesh_face',normal=world_normal,
                                        local_geometry_tolerance_cm=epsilon)
                    if detail=='compact':
                        evidence={k:v for k,v in evidence.items() if k in ('state','normal','component_id','instance_id','primitive','primitive_index','triangle')}
                    accepted.append({'index':index,'position':[p[0],p[1],max(heights)+clearance],
                        'height_range':[min(heights),max(heights)],'continuous_support':evidence})
            return {'revision':Store.revision(db),'world':world,'complete':complete,'accepted':accepted,'rejected':rejected,'sample_count':len(candidates)*len(offsets),'unknown_geometry':sorted(unknown)[:50]}

    @tool()
    def asset_get(asset_id:str,fields:list[str]|None=None,include_dependencies:bool=False)->dict:
        """Cached asset facts; fields selects exact/nested source fields. Returns {revision,entity,dependency_coverage:{state,package_id,source,live_state,package_dirty}}; coverage is an object. state describes the saved Asset Registry projection; live_state is CURRENT only when indexed package state is known clean, UNKNOWN_UNSAVED when dirty, UNKNOWN for legacy/missing data. include_dependencies adds dependencies:[{id,path}], dependency_count and dependencies_complete. Require CURRENT plus dependencies_complete for the complete Asset Registry projection; unknown or >20 dependencies has dependency_query for pagination. No Unreal request or guarantee about unsaved package references."""
        if asset_id.startswith('staged:'):
            from .staging import get
            e=get(store,asset_id)
            result={'entity':select_fields(e,fields) if fields is not None else e,'world':'authoring'}
            if include_dependencies:result.update(dependencies=[],dependencies_complete=False,dependency_coverage={'state':'UNKNOWN','reason':'Staged asset has no native package dependencies'})
            return result
        with store.read() as db:
            e=Store.entity(db,asset_id)
            package=db.execute("SELECT e.id,json_extract(e.source,'$.dependencies_state') AS state,json_extract(e.source,'$.live_dependencies_state') AS live_state,json_extract(e.source,'$.package_dirty') AS dirty FROM relationships r JOIN entities e ON e.id=r.target WHERE r.source=? AND r.kind='IN_PACKAGE'",(asset_id,)).fetchone()
            coverage={'state':package['state'] or 'UNKNOWN','package_id':package['id'],'source':'asset_registry_disk','live_state':package['live_state'] or 'UNKNOWN','package_dirty':bool(package['dirty']) if package['dirty'] is not None else None} if package else {'state':'UNKNOWN','live_state':'UNKNOWN','reason':'No indexed package relationship'}
            result={'revision':Store.revision(db),'entity':select_fields(e,fields) if fields is not None else e,'dependency_coverage':coverage}
            if include_dependencies:
                count=db.execute("SELECT count(*) FROM relationships WHERE source=? AND kind='DEPENDS_ON'",(package['id'],)).fetchone()[0] if package else None
                entries=[dict(row) for row in db.execute("SELECT r.target AS id,json_extract(e.source,'$.path') AS path FROM relationships r LEFT JOIN entities e ON e.id=r.target WHERE r.source=? AND r.kind='DEPENDS_ON' ORDER BY r.target LIMIT 20",(package['id'],))] if package else []
                result.update(dependencies=entries,dependency_count=count,dependencies_complete=coverage['state']=='CURRENT' and count==len(entries) and all(row['path'] for row in entries))
                if package and not result['dependencies_complete']:result['dependency_query']={'tool':'entity_relationships','arguments':{'entity_id':package['id'],'direction':'outgoing','kind':'DEPENDS_ON','limit':20}}
            return result

    @tool()
    def asset_stage(manifest_file:str,template_asset_id:str)->dict:
        """Cache authored FBX and UE-coordinate geometry for Shadow. No Unreal edits or canonical writes."""
        from .staging import stage
        asset=stage(store,manifest_file,template_asset_id)
        return {'asset_id':asset['id'],'target_path':asset['path'],'bounds':asset['local_bounds'],
                'provenance':asset['provenance'],'import_verification':asset['import_verification'],
                'navigation_prediction':asset.get('navigation_input_coverage','UNKNOWN')}

    @tool()
    def asset_usage(asset_id:str,limit:int=50,cursor:str|None=None,kind:str|None=None,detail:Literal['entities','summary']='entities',fields:list[str]|None=None,label_prefix:str|None=None,group_by:Literal['class','level','label_prefix']='class',label_group_depth:int=2)->dict:
        """Indexed asset users; Actor summary returns unique owner counts, source AABB union and bounded groups, without serializing Actors. Label groups use the first label_group_depth underscore-separated segments. Incomplete bounds/groups are explicit; narrow label_prefix to refine. No semantic inference or Unreal requests."""
        if type(limit) is not int or not 1<=limit<=500:raise ValueError('limit must be 1..500')
        if label_prefix is not None and (not isinstance(label_prefix,str) or len(label_prefix)>256):raise ValueError('Invalid label prefix')
        if group_by not in ('class','level','label_prefix') or type(label_group_depth) is not int or not 1<=label_group_depth<=8:raise ValueError('Invalid grouping')
        if kind!='Actor' and (label_prefix is not None or group_by!='class'):raise ValueError('Owner filtering/grouping requires kind=Actor')
        with store.read() as db:
            asset=db.execute('SELECT kind FROM entities WHERE id=?',(asset_id,)).fetchone()
            if not asset:raise ValueError('Unknown asset ID: use the persistent ID returned by asset/entity queries, not an Unreal object path')
            if asset[0] not in ('Asset','StaticMesh','Material'):raise ValueError('Expected an asset ID, not '+asset[0])
            args=[asset_id]
            if kind=='Actor':
                owners="FROM entities e JOIN relationships r ON e.id=r.source JOIN entities a ON a.id=COALESCE(e.actor_id,e.id) WHERE r.target=? AND r.kind='USES_ASSET' AND a.kind='Actor'"
                if label_prefix is not None:owners+=' AND substr(a.label,1,length(?))=?';args.extend([label_prefix,label_prefix])
                sql='SELECT DISTINCT a.source,a.id '+owners
            else:
                sql="SELECT e.source,e.id FROM entities e JOIN relationships r ON e.id=r.source WHERE r.target=? AND r.kind='USES_ASSET'"
                if kind:sql+=' AND e.kind=?';args.append(kind)
            if detail=='summary':
                if kind=='Actor':
                    row=db.execute("SELECT count(DISTINCT a.id),count(DISTINCT CASE WHEN a.x0 IS NOT NULL AND a.y0 IS NOT NULL AND a.z0 IS NOT NULL AND a.x1 IS NOT NULL AND a.y1 IS NOT NULL AND a.z1 IS NOT NULL THEN a.id END),min(a.x0),min(a.y0),min(a.z0),max(a.x1),max(a.y1),max(a.z1) "+owners,args).fetchone()
                    total=row[0];column={'class':'a.class','level':'a.parent_id','label_prefix':'a.label'}[group_by];groups={}
                    for key,count in db.execute('SELECT coalesce('+column+",'UNKNOWN'),count(DISTINCT a.id) "+owners+' GROUP BY '+column,args):
                        if group_by=='label_prefix':key='_'.join(key.split('_')[:label_group_depth])
                        groups[key]=groups.get(key,0)+count
                    grouped=dict(sorted(groups.items())[:limit])
                    return {'revision':Store.revision(db),'asset_id':asset_id,'counts':{'Actor':total} if total else {},'total':total,'bounds':[list(row[2:5]),list(row[5:8])] if total and row[1] else None,'bounds_complete':row[1]==total,'group_by':group_by,'groups':grouped,'group_count':len(groups),'groups_complete':len(grouped)==len(groups)}
                else:
                    aggregate="SELECT e.kind,count(*) FROM entities e JOIN relationships r ON e.id=r.source WHERE r.target=? AND r.kind='USES_ASSET'"
                    if kind:aggregate+=' AND e.kind=?'
                    aggregate+=' GROUP BY e.kind'
                counts=dict(db.execute(aggregate,args))
                return {'revision':Store.revision(db),'asset_id':asset_id,'counts':counts,'total':sum(counts.values())}
            if detail!='entities':raise ValueError('detail must be summary or entities')
            return store.page(db,'SELECT source FROM ('+sql+') ORDER BY id',args,{'asset':asset_id,'kind':kind,'fields':fields,'label_prefix':label_prefix},limit,cursor,fields)

    @tool()
    def geometry_get(geometry_hash:str)->dict:
        """Geometry metadata and file reference; never serializes all triangles."""
        with store.read() as db: return Queries(store,db).geometry(geometry_hash)

    @tool()
    def world_changes(since_revision:int,until_revision:int|None=None,limit:int=50,cursor:str|None=None,fields:list[str]|None=None)->dict:
        """Canonical committed changes, with before/after source state."""
        return store.changes(since_revision,until_revision,limit,cursor,fields or ['revision','entity_id'])

    @tool()
    def world_diff(revision_a:int,revision_b:int,limit:int=50,cursor:str|None=None,fields:list[str]|None=None)->dict:
        """Net entity difference over a revision range (not change count)."""
        fields = fields if fields is not None else []
        if not isinstance(fields,list) or any(not isinstance(f,str) for f in fields):raise ValueError('fields must be field names')
        columns="'id',i.entity_id,'kind','DIFF'"
        for name,column in (('before','a.before_json'),('after','b.after_json')):
            if name in fields or any(f.startswith(name+'.') for f in fields):columns+=f",'{name}',json({column})"
        with store.read() as db:
            if not 0<=revision_a<=revision_b<=Store.revision(db): raise ValueError('Invalid revisions')
            sql=f"""WITH ids AS (SELECT entity_id,min(revision) first,max(revision) last FROM changes WHERE revision>? AND revision<=? GROUP BY entity_id)
                SELECT json_object({columns}) source
                FROM ids i JOIN changes a ON a.entity_id=i.entity_id AND a.revision=i.first JOIN changes b ON b.entity_id=i.entity_id AND b.revision=i.last
                WHERE a.before_json IS NOT b.after_json ORDER BY i.entity_id"""
            return store.page(db,sql,[revision_a,revision_b],{'diff':[revision_a,revision_b],'fields':fields},limit,cursor,fields)

    @tool()
    def navigation_status(agent:str|None=None)->dict:
        """Exported navmesh coverage and offline backend availability."""
        from .navigation import Navigation
        return Navigation(store,agent).status()

    @tool()
    def navigation_path(start:list[float],end:list[float],agent:str|None=None,world:WorldSelection='canonical',patch_id:str|None=None)->dict:
        """Detour path within exported tiles; baseline_tile_pool_full warns of capacity. UNKNOWN is never unreachable proof."""
        from .navigation import Navigation
        return Navigation(store,agent).query('path',start,end,world,patch_id)

    @tool()
    def is_navigable(position:list[float],agent:str|None=None)->dict:
        from .navigation import Navigation
        return Navigation(store,agent).query('navigable',position)

    @tool()
    def nearest_navigable_position(position:list[float],agent:str|None=None)->dict:
        from .navigation import Navigation
        return Navigation(store,agent).query('nearest',position)

    @tool()
    def reachable(start:list[float],end:list[float],agent:str|None=None)->dict:
        return navigation_path(start,end,agent)

    @tool()
    def patch_place(candidates:list[Vector3],asset_id:str,actor_class:str,base_revision:int,
                    max_distance:float,label_prefix:str,rotation:Quaternion=[0,0,0,1],
                    scale:Vector3=[1,1,1],max_height_difference:float=1,clearance:float=.2,
                    size_cm:Vector3|None=None,include_operations:bool=False,manifest_file:str|None=None)->dict:
        """Sample support and prepare a validated Shadow placement patch in one call. candidates are world ray origins above the desired locations, cm. Uses observed asset bounds for a 3x3 footprint/bottom offset; preserves rotation/navigation defaults. For a new Blender FBX, optional manifest_file stages authored geometry using asset_id as the observed canonical spawn-profile template, then plans with the staged asset in this same revision; no separate asset_stage call/import. Optional positive size_cm sets local-axis dimensions from asset bounds, retaining scale signs. continuous_support is PROVEN only for a single actual primitive box face, actual collision triangle, or a verified closed convex collision-mesh face covering all footprint corners; otherwise UNPROVEN, finite samples alone are not proof. include_operations returns exact validated operations. Rejects unsupported candidates; UNKNOWN blocks the batch. Strict collision/navigation validation included; no Unreal edits."""
        import uuid
        from .store import check_operations
        if not 1<=len(candidates)<=128:raise ValueError('Supply1..128candidate ray origins')
        if not isinstance(label_prefix,str) or not label_prefix or len(label_prefix)>100:
            raise ValueError('Supply a label prefix of1..100characters')
        candidates=[vector(p) for p in candidates]
        pose={'position':[0,0,0],'rotation':rotation,'scale':scale}
        check_operations([{'type':'CREATE_ACTOR','target':'placement','class':actor_class,'asset':asset_id,'transform':pose}])
        accepted=[];rejected=[];operations=[];namespace=uuid.uuid4().hex
        with store.read_snapshot() as db:
            if Store.revision(db)!=base_revision:raise ValueError('CONFLICTED: canonical revision changed')
            if manifest_file is not None:
                if asset_id.startswith('staged:'):raise ValueError('manifest_file requires an observed canonical StaticMesh template ID')
                from .staging import stage
                asset=stage(store,manifest_file,asset_id)
                asset_id=asset['id']
            if asset_id.startswith('staged:'):
                from .staging import get
                asset=get(store,asset_id)
            else:asset=Store.entity(db,asset_id)
            if asset['kind']!='StaticMesh' or 'local_bounds' not in asset:
                raise ValueError('Observed StaticMesh with local bounds required')
            local_box=bounds(asset['local_bounds'])
            if size_cm is not None:
                size=vector(size_cm);extents=g.sub(local_box[1],local_box[0])
                if any(x<=0 for x in size+extents):raise ValueError('Positive size and nondegenerate asset bounds required')
                pose['scale']=[size[i]/extents[i]*(-1 if scale[i]<0 else 1) for i in range(3)]
                vector(pose['scale'])
            box=g.transform_bounds(local_box,pose)
            offsets=[[x,y] for x in (box[0][0],(box[0][0]+box[1][0])/2,box[1][0])
                          for y in (box[0][1],(box[0][1]+box[1][1])/2,box[1][1])]
            support=spatial_support(candidates,offsets,max_distance,max_height_difference,clearance)
            if not support['complete']:
                return {'status':'BLOCKED','base_revision':base_revision,'reason':'UNKNOWN support coverage; no patch created','support':support}
            rejected=support['rejected']
            for value in support['accepted']:
                index=value['index'];position=list(value['position']);position[2]-=box[0][2]
                accepted.append({'index':index,'position':position,'height_range':value['height_range'],
                    'continuous_support':value['continuous_support']})
                operations.append({'type':'CREATE_ACTOR','target':namespace+':'+str(index),
                    'class':actor_class,'asset':asset_id,'label':label_prefix+str(index),
                    'transform':{**pose,'position':position}})
        if not operations:
            return {'status':'BLOCKED','base_revision':base_revision,'reason':'No supported candidates','accepted':[],'rejected':rejected}
        result=patch_prepare(operations,base_revision)
        response={**result,'accepted':accepted,'rejected':rejected,'support_samples_per_candidate':len(offsets),
                  'support_limit':'Continuous surface proof only when PROVEN; does not certify full bottom contact or navigability'}
        if include_operations:response['operations']=store.patch_get(result['id'])['operations']
        if manifest_file is not None:
            response['authoring']={'asset_id':asset_id,'target_path':asset['path'],
                'import_verification':asset['import_verification'],
                'navigation_prediction':asset.get('navigation_input_coverage','UNKNOWN')}
        return response

    @tool()
    def patch_ground(entity_ids:list[str],base_revision:int,max_distance:float,clearance:float=.2,
                     max_height_difference:float=1,max_tilt_degrees:float=.1,include_operations:bool=False,detail:Literal['compact','full']='compact')->GroundPlanOutput:
        """Offline vertical grounding of explicitly selected Actors. Preserves XY, rotation, scale, assets and navigation defaults. Uses each actual world bounds envelope (including collision bounds), nine footprint samples and PROVEN continuous support; bounds are conservative, not an exact bottom-contact/physics solver. Refuses moving supports, selected ancestor/descendant pairs, unsupported geometry and tilted/unknown surfaces; no partial patch. Strict Shadow collision/navigation validation. No Unreal actions or semantic target selection."""
        import math
        from .store import encode
        if detail not in ('compact','full'):raise ValueError('Unknown grounding detail')
        if not isinstance(entity_ids,list) or not 1<=len(entity_ids)<=128 or any(not isinstance(i,str) or not i for i in entity_ids) or len(set(entity_ids))!=len(entity_ids):
            raise ValueError('Supply1..128unique persistent Actor IDs')
        if type(max_tilt_degrees) not in (int,float) or not math.isfinite(max_tilt_degrees) or not 0<=max_tilt_degrees<90:
            raise ValueError('Finite tilt in [0,90) required')
        accepted=[];rejected=[];operations=[];selected=set(entity_ids);sample_count=0
        with store.read_snapshot() as db:
            if Store.revision(db)!=base_revision:raise ValueError('CONFLICTED: canonical revision changed')
            q=context(db,'canonical',None)
            for index,ident in enumerate(entity_ids):
                entity=q.entity(ident)
                if entity['kind']!='Actor' or not entity.get('transform') or not entity.get('bounds'):
                    raise ValueError('Observed Actor transform/bounds required')
                # Native attachment moves descendants too. A second selected
                # descendant or a floor attached to a selected Actor is unsafe.
                if db.execute("WITH RECURSIVE parents(id) AS (SELECT target FROM relationships WHERE source=? AND kind='ATTACHED_TO' UNION SELECT r.target FROM relationships r JOIN parents p ON r.source=p.id WHERE r.kind='ATTACHED_TO') SELECT 1 FROM parents WHERE id IN (SELECT value FROM json_each(?)) LIMIT 1",(ident,encode(entity_ids))).fetchone():
                    raise ValueError('Selected attachment ancestor/descendant pair')
                box=bounds(entity['bounds'])
                if entity.get('collision_bounds'):
                    c=bounds(entity['collision_bounds']);box=[[min(box[0][i],c[0][i]) for i in range(3)],[max(box[1][i],c[1][i]) for i in range(3)]]
                if any(box[1][i]<=box[0][i] for i in (0,1)):raise ValueError('Nondegenerate footprint required')
                position=vector(entity['transform']['position'])
                offsets=[[x-position[0],y-position[1]] for x in (box[0][0],(box[0][0]+box[1][0])/2,box[1][0]) for y in (box[0][1],(box[0][1]+box[1][1])/2,box[1][1])]
                # Start below every occupied source bound; never hit oneself.
                origin=[position[0],position[1],box[0][2]-.01]
                support=spatial_support([origin],offsets,max_distance,max_height_difference,clearance)
                sample_count+=support['sample_count']
                reason=None;proof=None
                if not support['complete']:reason='unknown_geometry'
                elif support['rejected']:reason=support['rejected'][0]['reason']
                else:
                    value=support['accepted'][0];proof=value['continuous_support']
                    normal=proof.get('normal')
                    if proof['state']!='PROVEN':reason='unproven_continuous_support'
                    elif not normal or normal[2]+1e-12<math.cos(math.radians(max_tilt_degrees)):reason='tilted_support'
                    else:
                        component=q.entity(proof['component_id']);owner=component.get('actor_id')
                        if not owner:reason='unknown_support_owner'
                        elif owner in selected or db.execute("WITH RECURSIVE parents(id) AS (SELECT target FROM relationships WHERE source=? AND kind='ATTACHED_TO' UNION SELECT r.target FROM relationships r JOIN parents p ON r.source=p.id WHERE r.kind='ATTACHED_TO') SELECT 1 FROM parents WHERE id IN (SELECT value FROM json_each(?)) LIMIT 1",(owner,encode(entity_ids))).fetchone():reason='support_moves_with_plan'
                if reason:rejected.append({'index':index,'entity_id':ident,'reason':reason});continue
                pose=[position[0],position[1],value['position'][2]+position[2]-box[0][2]]
                accepted.append({'index':index,'entity_id':ident,'position':pose,'height_range':value['height_range'],'continuous_support':proof})
                operations.append({'type':'MOVE_ACTOR','target':ident,'value':pose})
        if rejected:return {'status':'BLOCKED','base_revision':base_revision,'accepted':accepted,'rejected':rejected,'sample_count':sample_count,'reason':'No partial grounding patch created'}
        result=patch_prepare(operations,base_revision)
        response={**result,'accepted_count':len(accepted),'rejected':[],'sample_count':sample_count,
                  'continuous_support':{'state':'PROVEN','minimum_normal_z':min(a['continuous_support']['normal'][2] for a in accepted),
                                        'maximum_height_difference':max(a['height_range'][1]-a['height_range'][0] for a in accepted)},
                  'support_limit':'Conservative bounds envelope over a proven surface; not exact bottom contact or navigability'}
        if detail=='full':response['accepted']=accepted
        if include_operations:response['operations']=store.patch_get(result['id'])['operations']
        return response

    @tool()
    def patch_prepare(operations:list[PatchOperation],base_revision:int,patch_id:str|None=None)->dict:
        """Create/update and validate Shadow in one call. Explicit current base; compact diagnostics, no Unreal edits."""
        if patch_id:
            patch=store.patch_get(patch_id)
            if patch['base_revision']!=base_revision:raise ValueError('Patch base mismatch; create a new plan')
            with store.read() as db:
                if Store.revision(db)!=base_revision:raise ValueError('CONFLICTED: canonical revision changed')
            patch=store.patch_update(patch_id,operations)
        else:patch=store.patch_create(operations,base_revision)
        return patch_summary(validate(store,patch['id']))

    @tool()
    def patch_create(operations:list[PatchOperation],base_revision:int|None=None)->dict:
        """Create DRAFT; cannot write the canonical database or execute Unreal."""
        return patch_summary(store.patch_create(operations,base_revision))

    @tool()
    def patch_update(patch_id:str,operations:list[PatchOperation])->dict:
        return patch_summary(store.patch_update(patch_id,operations))

    @tool()
    def patch_validate(patch_id:str)->dict:
        """Validate shadow effects, rejecting unknown essential facts."""
        return patch_summary(validate(store,patch_id))

    @tool()
    def patch_preview(patch_id:str,limit:int=50,cursor:str|None=None,fields:list[str]|None=None,detail:Literal['entities','operations','collisions']='entities')->dict:
        with store.read() as db:
            patch=store.patch_get(patch_id)
            if detail=='operations':
                values=[{'id':op['operation_id'],'kind':op['type'],**op} for op in patch['operations']]
                fields=fields if fields is not None else ['target','value','property','asset','parent','class','transform','label']
            elif detail=='collisions':
                values=[{'id':str(i),'kind':'Collision',**c} for i,c in enumerate((patch.get('validation') or {}).get('new_collisions',[]))]
                fields=fields if fields is not None else ['pair','depth','normal','before','after']
            elif detail=='entities':
                overlay,errors=preview(store,db,patch)
                if errors:raise ValueError('Shadow invalid; inspect patch_status validation counts')
                values=[e or {'id':ident,'kind':'Deleted'} for ident,e in sorted(overlay.items())]
            else:raise ValueError('detail must be entities, operations or collisions')
            return {**patch_summary(patch),**page_values(db,values,{'patch':patch_id,'updated':patch['updated'],'detail':detail,'fields':fields},limit,cursor,fields)}

    @tool()
    def patch_continue(patch_id:str)->dict:
        """Prepare/validate only an unexecuted suffix after proving interrupted effects. No Unreal calls or replay."""
        from .continuation import prepare_continuation
        return patch_summary(prepare_continuation(store,patch_id))

    @tool()
    def patch_status(patch_id:str,recover:bool=False)->dict:
        """Inspect, or reconcile interrupted effects as of Canonical. Recovery never replays or saves Unreal."""
        if recover:
            from .verification import recover as recover_patch
            return patch_summary(recover_patch(store,patch_id))
        return patch_summary(store.patch_get(patch_id))
    return mcp


def patch_summary(patch):
    result={key:patch[key] for key in ('id','base_revision','status','updated')}
    if patch.get('continuation'):result['continuation']=patch['continuation']
    if patch.get('continued_by'):result['continued_by']=patch['continued_by']
    result['operation_count']=len(patch['operations'])
    validation=patch.get('validation')
    if validation:
        result['validation']={key:validation[key] for key in ('valid','base_revision','changed_entities','application_error') if key in validation}
        for key in ('errors','new_collisions','preexisting_collisions','navigation_changed','expected'):
            result['validation'][key+'_count']=len(validation.get(key,[]))
        # Error detail may contain thousands of pairs; expose bounded diagnostics.
        result['validation']['errors']=[{'error':e.get('error','Unknown validation error'),'operation_id':e.get('operation_id')} if isinstance(e,dict) else {'error':str(e)} for e in validation.get('errors',[])[:10]]
        result['validation']['navigation']={agent:{key:value[key] for key in ('state','error','reason') if key in value} for agent,value in validation.get('navigation',{}).items()}
        if validation.get('new_collisions'):
            result['validation']['conflicts']=[{'pair':c['pair'],'penetration':c.get('after',{}).get('penetration')} for c in validation['new_collisions'][:10]]
    receipts=patch.get('receipts') or []
    result['receipt_count']=len(receipts)
    if receipts:result['last_receipt']={key:receipts[-1][key] for key in ('state','canonical_revision','saved','operation_id','recovered','editor_connected','native_result_recorded','native_saved_revision','save_unconfirmed_reason') if key in receipts[-1]}
    if patch['status']=='APPLIED' and receipts and receipts[-1].get('state')=='CANONICAL_VERIFIED':
        result.update(canonical_revision=receipts[-1]['canonical_revision'],saved=receipts[-1]['saved'],canonical_verified=True)
    return result


def patch_token(patch):
    import hashlib
    from .store import encode
    return hashlib.sha256(encode(patch).encode('utf-8')).hexdigest()


def page_values(db,values,query,limit,cursor,fields):
    # Reuse exactly the same cursor contract without writing canonical state.
    import sqlite3
    memory=sqlite3.connect(':memory:'); memory.row_factory=sqlite3.Row
    try:
        memory.execute('CREATE TABLE metadata(key,value)'); memory.execute('INSERT INTO metadata VALUES(?,?)',('world_revision',str(Store.revision(db))))
        memory.execute('CREATE TABLE page(source)'); memory.executemany('INSERT INTO page VALUES(?)',((json.dumps(e),) for e in values))
        return Store.page(memory,'SELECT source FROM page ORDER BY rowid',[],query,limit,cursor,fields)
    finally: memory.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',type=Path,required=True,help='.uproject or project directory')
    parser.add_argument('--root',type=Path,help='Override Twin root for offline fixtures')
    parser.add_argument('--map',help='Pin an already cached map package; otherwise follow the active world')
    parser.add_argument('--tool-profile',choices=['full','compact','workflow','focused'],default='full',help='Workflow exposes ten entrypoints; focused exposes world_read/shadow_plan/tool_describe. Exact schemas are available alongside source. All handlers remain callable and validated.')
    parser.add_argument('--status',action='store_true')
    parser.add_argument('--validate-patch')
    parser.add_argument('--call',help='Invoke an existing offline MCP tool without a persistent client')
    parser.add_argument('--arguments',type=Path,help='JSON argument object for --call')
    args=parser.parse_args(); project=args.project.resolve()
    if args.arguments and not args.call:parser.error('--arguments requires --call')
    from .worlds import select_world
    base=args.root or ((project.parent if project.suffix=='.uproject' else project)/'Saved/SpatialTwin')
    store=Store(select_world(base,args.map))
    if args.status: print(json.dumps(store.status(),indent=2))
    elif args.validate_patch: print(json.dumps(validate(store,args.validate_patch),indent=2))
    elif args.call:
        import inspect
        from .store import encode
        tool=create_server(store if args.map else Store(base),follow_active=args.map is None)._tool_manager.get_tool(args.call)
        if tool is None:parser.error('Unknown offline Twin tool')
        arguments=json.loads(args.arguments.read_text(encoding='utf-8')) if args.arguments else {}
        inspect.signature(tool.fn).bind(**arguments)
        tool.fn_metadata.arg_model.model_validate(arguments,strict=True)
        print(encode(tool.fn(**arguments)))
    else: create_server(store if args.map else Store(base),follow_active=args.map is None,tool_profile=args.tool_profile).run(transport='stdio')

if __name__=='__main__': main()
