"""Test-only bounded MCP: identical tasks, official live actions, optional Twin.

This is not part of the product MCP. Runs mutate only the disposable benchmark
map and their own asset, retain a journal, and never save the map.
"""
import argparse,asyncio,json,hashlib,os,sys,time
from contextlib import AsyncExitStack,asynccontextmanager
from datetime import timedelta
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.server.fastmcp import FastMCP,Context
from construction_benchmark import program,data,SCENE,ACTOR,OBJECT,MESH,ASSET,PROGRAMMATIC,EDITOR,streamablehttp_client
import apply_patch,materialize
from spatial_twin.store import Store
from agent_benchmark_guard import source_hashes
ROOT=Path(__file__).resolve().parents[2]
READ={'readOnlyHint':True,'destructiveHint':False,'openWorldHint':False}
WRITE={'readOnlyHint':False,'destructiveHint':False,'openWorldHint':False}

def server(spec_path,route,scenario,name,*,defer_native=False):
    if route not in ('with','without') or not name.isalnum() or len(name)>24:raise ValueError('Invalid arm/run name')
    spec_path=Path(spec_path).resolve();spec=json.loads(spec_path.read_text());project=Path(spec['project']).resolve()
    if spec.get('fixture_format')!=1 or project.parent!=spec_path.parent or project.stem!='SpatialTwinBench' or spec['map']!='/Game/Benchmark/Base':raise ValueError('Not the disposable benchmark project')
    task=spec['scenarios'][scenario];folder=project.parent/'runs'/name;journal=folder/'journal.json'
    target=f'/Game/BenchRuns/SM_ST_Rail_{name}.SM_ST_Rail_{name}' if task['kind']=='author' else spec['template_asset']
    camera=spec['cameras'][scenario];label='BenchRun_'+name;root=project.parent/'Saved/SpatialTwin'
    def record(s):
        journal.parent.mkdir(parents=True,exist_ok=True)
        journal.write_text(json.dumps({k:v for k,v in s.items() if k not in ('native','twin','blender','store')},indent=2)+'\n')
    @asynccontextmanager
    async def lifespan(app):
        if journal.exists():raise ValueError('Run journal exists; verify/clean it instead of replaying')
        async with AsyncExitStack() as stack:
            async def stdio(command,args):
                r,w=await stack.enter_async_context(stdio_client(StdioServerParameters(command=str(command),args=list(map(str,args)),cwd=str(ROOT),env={**os.environ,'SPATIAL_TWIN_PROJECT':str(project),'SPATIAL_TWIN_HOME':str(ROOT),'DISABLE_TELEMETRY':'true','PYTHONDONTWRITEBYTECODE':'1'})))
                client=await stack.enter_async_context(ClientSession(r,w,read_timeout_seconds=timedelta(seconds=180)));await client.initialize();return client
            native=None
            if not defer_native:
                r,w,_=await stack.enter_async_context(streamablehttp_client(spec['url']))
                native=await stack.enter_async_context(ClientSession(r,w,read_timeout_seconds=timedelta(seconds=180)));await native.initialize()
            twin=await stdio(sys.executable,[ROOT/'SpatialTwinCodexPlugin/server.py']) if route=='with' else None
            blender=await stdio(ROOT/'Build/MCP/venv/Scripts/mcp-for-blender.exe',[]) if task['kind']=='author' else None
            s=dict(native=native,twin=twin,blender=blender,store=Store(root) if twin else None,stage='CONNECTED',created=[],rpc=[],route=route,scenario=scenario,name=name)
            record(s);yield s
    app=FastMCP('production-benchmark',lifespan=lifespan)
    def state(ctx):return ctx.request_context.lifespan_context
    def require(s,stage):
        if s['stage']!=stage:raise ValueError('Required '+stage+', actual '+s['stage']+'; inspect journal before retry')
    async def native(s,t,n,a):
        start=time.perf_counter();result=await apply_patch.call_tool(s['native'],t,n,a)
        s['rpc'].append(dict(kind='Unreal',tool=n,seconds=time.perf_counter()-start));return result
    async def batch(s,body,values):
        v=await native(s,PROGRAMMATIC,'execute_tool_script',{'script':program(body,values)})
        return (json.loads(v) if isinstance(v,str) else v)['value']
    async def twin(s,n,a):
        start=time.perf_counter();v=data(await s['twin'].call_tool(n,a))
        s['rpc'].append(dict(kind='Twin',tool=n,seconds=time.perf_counter()-start));return v
    def check_source(s):
        if task['kind']=='author' and source_hashes(folder,name,target)!=s['source_hashes']:raise ValueError('Authored source changed')
    def operations(s,poses):
        if task['kind']=='move':return [dict(type='MOVE_ACTOR',target=e['id'],value=p) for e,p in zip(s['targets'],poses)]
        return [dict(type='CREATE_ACTOR',target=f'new:{label}_{i:02d}',label=f'{label}_{i:02d}',
                     **{'class':'/Script/Engine.StaticMeshActor'},asset=s.get('staged_asset','asset:'+target),
                     transform=dict(position=p,rotation=[0,0,0,1],scale=task['scale']),component_properties={'bCanEverAffectNavigation':False}) for i,p in enumerate(poses)]
    @app.tool(annotations=READ)
    async def inspect_task(ctx:Context)->dict:
        """Verify the editor/map/plugin arm and describe the concrete construction task."""
        s=state(ctx);require(s,'CONNECTED')
        catalog=await s['native'].call_tool('list_toolsets',{})
        present='UnrealSpatialTwin.SpatialTwinToolset' in '\n'.join(c.text for c in catalog.content if c.type=='text')
        if catalog.isError or present!=(route=='with'):raise ValueError('Wrong native plugin state')
        level=await native(s,SCENE,'get_current_level',{})
        if level!=spec['map']:raise ValueError('Wrong map: '+str(level))
        if await native(s,EDITOR,'IsPIERunning',{}):raise ValueError('Stop the fixture play session first')
        if task['kind']=='author' and await native(s,ASSET,'exists',{'path':target}):raise ValueError('Asset already exists')
        if s['twin']:
            status=await twin(s,'world_status',{'include_counts':False})
            if status['project_id']!='SpatialTwinBench' or status['map']!=spec['map'] or not status['editor_connected']:raise ValueError('Wrong Twin or disconnected editor')
        s['stage']='INSPECTED';record(s)
        return dict(task=task['kind'],candidate_count=len(task['candidates']),goal='Use every fully supported flat candidate; leave unsupported/uneven candidates empty. Move all selected existing blocks for batch; otherwise place the asset.',
                    height_offset_cm=task['height_offset'],requires_blender=task['kind']=='author',next='author_asset then observe' if task['kind']=='author' else 'observe',scope='Only this independent benchmark map; no map save')
    @app.tool(annotations=WRITE)
    async def author_asset(ctx:Context)->dict:
        """Author the same editable Blender rail and six UCX bodies for either arm."""
        s=state(ctx);require(s,'INSPECTED')
        if task['kind']!='author':raise ValueError('This task reuses an existing asset')
        code="import importlib.util\ns=importlib.util.spec_from_file_location('rail_builder',"+repr(str(ROOT/'art/SpatialTwinBenchmark/build_recovered_rail.py'))+")\nm=importlib.util.module_from_spec(s);s.loader.exec_module(m)\nprint(m.build("+repr('SM_ST_Rail_'+name)+','+repr(str(folder))+','+repr(target)+'))'
        authored=data(await s['blender'].call_tool('execute_blender_code',{'code':code,'user_prompt':'Author the fixed technical rail for the user-authorized same-task benchmark in the isolated Blender benchmark scene.'}))
        if 'Error' in str(authored):raise ValueError('Blender export failed')
        s['source_hashes']=source_hashes(folder,name,target);s['stage']='AUTHORED';record(s)
        return dict(state='AUTHORED',convex_bodies=6,source=str(folder/('SM_ST_Rail_'+name+'.blend')))
    @app.tool(annotations=READ)
    async def observe(ctx:Context)->dict:
        """Read current objects and actual support heights; no cached answer table."""
        s=state(ctx);require(s,'AUTHORED' if task['kind']=='author' else 'INSPECTED')
        if task['kind']=='move':
            if s['twin']:
                found=await twin(s,'world_search',dict(text=task['label_prefix'],kind='Actor',limit=64,fields=['label','path','transform']))
                if found['cursor']:raise ValueError('Incomplete target page')
                targets=found['entities']
            else:
                body="    return [{'path':a['refPath'],'label':call("+repr(ACTOR)+",'get_label',{'actor':a}),'native_transform':call("+repr(ACTOR)+",'get_actor_transform',{'actor':a})} for a in call("+repr(SCENE)+",'find_actors',{'name':DATA,'tag':'','collision_channels':[]})]\n"
                targets=await batch(s,body,task['label_prefix'])
            s['targets']=sorted(targets,key=lambda e:e['label'])
            if len(s['targets'])!=len(task['candidates']):raise ValueError('Missing/duplicate batch targets')
            for i,e in enumerate(s['targets']):
                if e['label']!=task['label_prefix']+f'{i:02d}' or not e['path'].startswith(spec['map']+'.'):raise ValueError('Target outside fixture')
        if s['twin']:
            support=await twin(s,'spatial_support',dict(candidates=task['candidates'],offsets=task['offsets'],max_distance=700))
        else:
            body="    accepted=[];rejected=[]\n    for i,c in enumerate(DATA['candidates']):\n        d=[call("+repr(SCENE)+",'trace_world',{'start':{'x':c[0]+x,'y':c[1]+y,'z':c[2]},'end':{'x':c[0]+x,'y':c[1]+y,'z':c[2]-700}}) for x,y in DATA['offsets']]\n        h=[c[2]-v for v in d if v is not None]\n        reason='missing_support' if any(v is None for v in d) else 'uneven_support' if max(h)-min(h)>1 else None\n        if reason:rejected.append({'index':i,'reason':reason})\n        else:accepted.append({'index':i,'position':[c[0],c[1],max(h)+.2],'height_range':[min(h),max(h)]})\n    return {'complete':True,'accepted':accepted,'rejected':rejected,'sample_count':len(DATA['candidates'])*len(DATA['offsets'])}\n"
            support=await batch(s,body,task)
        s['support']=support;record(s)
        if not support['complete'] or not support['accepted']:raise ValueError('Incomplete/empty support coverage; see journal')
        for e in support['accepted']:
            e['position'][2]+=task['height_offset']
            for key in ('position','height_range'):e[key]=[round(v,6) for v in e[key]]
        if task['kind']=='move' and len(support['accepted'])!=len(s['targets']):raise ValueError('Batch requires a supported target for each object')
        s['support']=support;s['stage']='OBSERVED';record(s)
        return {k:support[k] for k in ('accepted','rejected','sample_count','complete')}
    @app.tool(annotations=WRITE)
    async def complete_plan(candidate_indexes:list[int],positions_cm:list[list[float]],ctx:Context,prepared_patch_id:str|None=None)->dict:
        """Validate the agent's plan, execute through official MCP, verify native state and capture a real render. No map save."""
        s=state(ctx);require(s,'OBSERVED');check_source(s)
        wanted=s['support']['accepted'];poses=[e['position'] for e in wanted]
        if candidate_indexes!=[e['index'] for e in wanted] or len(positions_cm)!=len(poses) or any(len(p)!=3 or any(type(a) not in (int,float) or not abs(a-b)<=.001 for a,b in zip(p,q)) for p,q in zip(positions_cm,poses)):raise ValueError('Plan must match the fresh supported positions')
        bounds=[dict(min=dict(zip(('x','y','z'),[p[i]+task['bounds'][0][i]+.1 for i in range(3)])),max=dict(zip(('x','y','z'),[p[i]+task['bounds'][1][i]-.1 for i in range(3)])),isValid=True) for p in poses]
        if s['twin']:
            if task['kind']=='author':
                asset=await twin(s,'asset_stage',dict(manifest_file=str(folder/'manifest.json'),template_asset_id='asset:'+spec['template_asset']));s['staged_asset']=asset['asset_id']
            status=await twin(s,'world_status',{'include_counts':False})
            if prepared_patch_id:
                if task['kind']!='move':raise ValueError('Prepared patch only for source-selected movements')
                existing=s['store'].patch_get(prepared_patch_id)
                expected=operations(s,poses)
                observed=[{k:v for k,v in op.items() if k!='operation_id'} for op in existing['operations']]
                if existing['base_revision']!=status['canonical_revision'] or len(observed)!=len(expected) or any(set(a)!=set(b) or a['type']!=b['type'] or a['target']!=b['target'] or any(abs(x-y)>.001 for x,y in zip(a['value'],b['value'])) for a,b in zip(observed,expected)):
                    raise ValueError('Prepared patch differs from independent current source/plan')
                patch=await twin(s,'patch_status',dict(patch_id=prepared_patch_id))
            else:patch=await twin(s,'patch_prepare',dict(operations=operations(s,poses),base_revision=status['canonical_revision']))
            if patch['status']!='VALIDATED':raise ValueError(str(patch))
            s['patch_id']=patch['id'];record(s)
        else:
            if prepared_patch_id:raise ValueError('Prepared Twin patch on native-only route')
            overlaps=await batch(s,"    return [call("+repr(SCENE)+",'find_actors',{'name':'','tag':'','bounds':b,'collision_channels':['ObjectTypeQuery1','ObjectTypeQuery2']}) for b in DATA]\n",bounds)
            if any(overlaps):raise ValueError('Native candidate volume occupied')
        s.update(stage='APPLYING',positions=poses,candidate_indexes=candidate_indexes);record(s)
        if task['kind']=='author':
            materials={'RecoveredMetal':'/Engine/BasicShapes/BasicShapeMaterial.BasicShapeMaterial','SafetyAmber':'/Engine/BasicShapes/BasicShapeMaterial.BasicShapeMaterial'}
            if s['twin']:
                imported=await materialize.materialize(s['store'],s['patch_id'],spec['url'],folder/'import.json',materials,s['native']);s['patch_id']=imported['canonical_patch_id']
            else:
                result=await native(s,PROGRAMMATIC,'execute_tool_script',{'script':materialize.import_script(dict(path=target,source_file=str(folder/('SM_ST_Rail_'+name+'.fbx'))),materials)})
                result=json.loads(result) if isinstance(result,str) else result
                if not result.get('complete'):raise ValueError('Partial import; inspect before retry')
        if s['twin']:
            patch=await apply_patch.execute(s['store'],s['patch_id'],spec['url'],180,False,s['native'])
            if patch['status']!='APPLIED':raise ValueError('Not canonically confirmed')
            ids=next(r['actor_ids'] for r in reversed(patch['receipts']) if r['state']=='CANONICAL_VERIFIED')
            with s['store'].read() as db:
                refs=[dict(refPath=Store.entity(db,op['target'] if task['kind']=='move' else ids[op['target']])['path']) for op in patch['operations']]
        elif task['kind']=='move':
            refs=[dict(refPath=e['path']) for e in s['targets']]
            values=[dict(actor=a,worldspace=True,xform=apply_patch.xform(dict(position=p,rotation=[0,0,0,1],scale=task['scale']))) for a,p in zip(refs,poses)]
            result=await batch(s,"    return [call("+repr(ACTOR)+",'set_actor_transform',v) for v in DATA]\n",values)
            if not all(result):raise ValueError('Partial batch; inspect journal')
        else:
            entries=[dict(target=op['target'],operation_id=str(i),tool='add_to_scene_from_asset',arguments=dict(asset_path=target,name=op['label'],xform=apply_patch.xform(op['transform']),parent=None,snap_to_ground=False),properties=op['component_properties']) for i,op in enumerate(operations(s,poses))]
            v=await native(s,PROGRAMMATIC,'execute_tool_script',{'script':apply_patch.create_script(entries)});v=json.loads(v) if isinstance(v,str) else v
            refs=[x['actor'] for x in v['created']]
            if not v.get('complete') or not all(x['properties_confirmed'] for x in v['created']):raise ValueError('Partial creation')
        s['created']=refs;record(s)
        rows=await batch(s,"    return [{'transform':call("+repr(ACTOR)+",'get_actor_transform',{'actor':a}),'properties':call("+repr(OBJECT)+",'get_properties',{'instance':call("+repr(ACTOR)+",'get_root_component',{'actor':a}),'properties':['StaticMesh']})} for a in DATA]\n",refs)
        if len(rows)!=len(poses):raise ValueError('Incomplete native verification')
        for p,row in zip(poses,rows):
            if max(abs(row['transform']['location'][k]-v) for k,v in zip(('x','y','z'),p))>.01 or json.loads(row['properties'])['StaticMesh']['refPath']!=target:raise ValueError('Native result differs')
            if any(abs(row['transform']['scale'][k]-v)>.001 for k,v in zip(('x','y','z'),task['scale'])) or any(abs(row['transform']['rotation'][k])>.001 for k in ('pitch','yaw','roll')):raise ValueError('Native rotation/scale differs')
        # Identical native acceptance for both arms, including real physics overlaps.
        overlaps=await batch(s,"    return [call("+repr(SCENE)+",'find_actors',{'name':'','tag':'','bounds':b,'collision_channels':['ObjectTypeQuery1','ObjectTypeQuery2']}) for b in DATA]\n",bounds)
        for own,hits in zip(refs,overlaps):
            if any(hit['refPath']!=own['refPath'] for hit in hits):raise ValueError('Final native overlap with another actor')
        mesh=await batch(s,"    mesh={'refPath':DATA}\n    return {'triangles':call("+repr(MESH)+",'get_triangle_count',{'mesh':mesh,'lod_index':0}),'vertices':call("+repr(MESH)+",'get_vertex_count',{'mesh':mesh,'lod_index':0}),'bounds':call("+repr(MESH)+",'get_bounds',{'mesh':mesh})}\n",target)
        from unreal_mcp import save_capture,capture_viewport
        capture=save_capture(await capture_viewport(lambda t,n,a:native(s,t,n,a),camera),folder/'render.png')
        result=dict(state='VERIFIED',route=route,scenario=scenario,positions_cm=poses,candidate_indexes=candidate_indexes,rejected=s['support']['rejected'],actor_refs=refs,asset_path=target,patch_id=s.get('patch_id'),native_rows=rows,native_overlaps=overlaps,native_mesh=mesh,render_path=capture['image_path'],capture=capture,rpc=s['rpc'],source_hashes=s.get('source_hashes'),saved_map=False)
        (folder/'result.json').write_text(json.dumps(result,indent=2)+'\n');s['stage']='VERIFIED';record(s)
        return dict(state='VERIFIED',actor_count=len(rows),render_path=capture['image_path'],receipt=str(folder/'result.json'),rejected=result['rejected'],canonical_confirmed=bool(s['twin']),map_saved=False)
    return app

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--spec',required=True,type=Path);p.add_argument('--route',choices=['with','without'],required=True);p.add_argument('--scenario',choices=['batch','reuse','author'],required=True);p.add_argument('--name',required=True)
    a=p.parse_args();server(a.spec,a.route,a.scenario,a.name).run(transport='stdio')
