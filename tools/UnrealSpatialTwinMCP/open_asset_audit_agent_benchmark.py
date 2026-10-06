"""Read-only whole-agent asset-impact task; no candidate answers supplied."""
import json,os,subprocess,time
from pathlib import Path
from agent_benchmark import usage
from construction_benchmark import SCENE,ACTOR,OBJECT,ASSET,PROGRAMMATIC,program
ROOT=Path(__file__).resolve().parents[2]


def run(spec_path,route,name,cli,plugin,source_schemas=None):
    spec_path=Path(spec_path).resolve();spec=json.loads(spec_path.read_text());base=spec_path.parent
    assert Path(spec['project']).parent==base and Path(spec['project']).stem=='SpatialTwinBench'
    folder=base/'runs'/name;folder.mkdir(exist_ok=False)
    prompt='''Perform this authorized read-only asset-impact audit in the disposable benchmark. Identify the StaticMesh used by the Actor whose exact label is Bench_Move_00. Discover every Actor whose label starts Bench_ that directly uses that mesh through a component or instance. Count each Actor once. Return its total, counts grouped by the first two underscore-separated label segments (e.g. Bench_Move), and the union of those Actors' source world AABBs in cm, preserving double precision. Also return the mesh's actual direct Asset Registry package dependencies, sorted and unique. Do not infer transitive dependencies or substitute material references. Require known dependency coverage and complete pagination. Derive all identities, counts, coordinates and dependencies from current source; no answer tables are supplied.
Return only valid JSON with keys mesh_path, dependencies (package paths), owners_total, group_counts (object), bounds ([[minX,minY,minZ],[maxX,maxY,maxZ]]). Unknown/incomplete source must be reported as an error instead of a successful answer. Retain and check source revision consistency on the Twin route. No edits, Shadow patch, render, GUI, shell, browser, direct database, other projects, old trial files, config changes, restart or cleanup. This audit introduces no visual change. The orchestrator independently checks the answer against the official native source before verified delivery.
Both routes: batch independent reads, keep all source records and calculations local in Code Mode, and emit only the checked aggregate. Do not spend model turns copying IDs/coordinates or print source tables. Resolve a unique tool binding and execute it in that same call. Native Programmatic executes registered tools, not general editor Python; follow its mandatory SDK instructions. Never weaken completeness checks. Jev is unused.
'''
    if route=='with':
        plugin=Path(plugin).resolve();python=ROOT/'Build/MCP/venv/Scripts/python.exe'
        config='mcp_servers.spatial_twin={command='+json.dumps(python.as_posix())+',args='+json.dumps([(plugin/'server.py').as_posix(),'--root',(base/'Saved/SpatialTwin').as_posix(),'--tool-profile','focused'])+',required=true,startup_timeout_sec=40,env={SPATIAL_TWIN_PROJECT='+json.dumps(spec['project'].replace('\\','/'))+',SPATIAL_TWIN_HOME='+json.dumps((plugin/'runtime').as_posix())+'}}'
        prompt+='\nInstalled skill:\n'+(plugin/'skills/unreal-spatial/SKILL.md').read_text()
        prompt+='\nRoute named Twin reads through world_read(queries=[{tool:<name>,arguments:<exact arguments>}]); results entries contain data and state must be READY. In a single Code Mode execution, first world_search(text="Bench_Move_00",kind="Actor",limit=1,fields=["label"],component_fields=["asset_id"]); require the unique exact label and complete child pagination. The known search output is results[0].data:{revision,entities:[{id,kind,label,components:{revision,entities:[{id,kind,asset_id}],cursor,returned}}],cursor,returned}; components is a page object, not an array. Use its actual component asset_id. Do not emit or end the execution after this source discovery: all dependent input/output contracts are given here and no new selection decision is required. Decode and check that result locally, then in that same execution the dependent world_read batches asset_get(asset_id,fields=["path"],include_dependencies=true) and asset_usage(asset_id,kind="Actor",detail="summary",label_prefix="Bench_",group_by="label_prefix",label_group_depth=2). Preserve the same observed status.canonical_revision. asset_get returns entity.path, dependencies [{id,path}], dependency_count, dependencies_complete and dependency_coverage:{state,package_id}; coverage is an object, require dependency_coverage.state === "CURRENT" and dependencies_complete=true. groups is an object mapping source group labels to integer counts; bounds is [[minX,minY,minZ],[maxX,maxY,maxZ]]. Summary returns total, groups, bounds, bounds_complete and groups_complete; require both complete flags. These generic indexed aggregations read actual current owners, deduplicate components/instances and group the source labels exactly as requested; they are not supplied answers. Use those source results directly; no owner-page enumeration, extra relationship lookup or manual ID/path inference is needed when all complete flags pass. Emit only the requested checked aggregate, stop on unknown/truncated/changed source.\n'
    elif route=='without':
        config='mcp_servers.unreal={url='+json.dumps(spec['url'])+',required=true,startup_timeout_sec=40}'
        prompt+=f'''\nUse the unique official call_tool binding with {{toolset_name,tool_name,arguments}}. First call {PROGRAMMATIC}.get_execution_environment(arguments={{}}) and read its current mandatory instructions. Then one execute_tool_script can discover all Bench_ Actors through {SCENE}.find_actors(name='Bench_',tag='',collision_channels=[]), check their actual labels with {ACTOR}.get_label, roots with get_root_component, and {OBJECT}.get_properties(instance=root,properties=['StaticMesh']). get_properties returns a JSON string; decode it locally. StaticMesh is a native reference with refPath. Use the unique exact Bench_Move_00 to derive the target mesh path. Read actual world bounds only for matching owners using {ACTOR}.get_actor_bounds(actor), whose native Box contains min/max vectors with x/y/z. Keep full source and calculation local in Code Mode. Finally {ASSET}.get_dependencies(asset_path=<observed mesh path>) returns direct package paths. Use Python True/False and json.dumps(arguments) for execute_tool; native refs and records have known keys and use indexing, not .get(). Do not invent Class refs or inspect unrelated schemas. No native writes.\n'''
    else:raise ValueError('Unknown route')
    if route=='without':
        prompt+='\nThe environment call uses toolset_name='+json.dumps(PROGRAMMATIC)+', tool_name="get_execution_environment", arguments={}. A fully qualified function name is not the tool_name field.\n'
        if source_schemas:prompt+='Exact relevant schemas freshly obtained from this installed native Toolset Registry (other toolset catalogues are unnecessary):\n'+json.dumps(source_schemas,separators=(',',':'))+'\nRead the mandatory environment instructions, then reuse these verified relevant schemas; if runtime differs, stop and report it.\n'
    (folder/'prompt.txt').write_bytes(prompt.encode())
    args=[str(cli),'exec','--ignore-user-config','--json','--approve-for-me','--model','gpt-6.1-sol','-c','model_reasoning_effort="high"','-c','features.code_mode=true','-c','features.code_mode_only=true','-C',str(folder),'-c','sandbox_workspace_write.writable_roots=[]','-c','sandbox_workspace_write.exclude_tmpdir_env_var=true','-c','sandbox_workspace_write.exclude_slash_tmp=true','-c',config,'-o',str(folder/'final.txt'),'-']
    started=time.perf_counter();events=[]
    with (folder/'stderr.log').open('w',encoding='utf-8') as err,(folder/'events.jsonl').open('w',encoding='utf-8') as log:
        child=subprocess.Popen(args,cwd=folder,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=err,text=True,encoding='utf-8',errors='replace',env={**os.environ,'DISABLE_TELEMETRY':'true','PYTHONDONTWRITEBYTECODE':'1'})
        (folder/'process.json').write_text(json.dumps({'pid':child.pid,'route':route,'name':name}))
        child.stdin.write(prompt);child.stdin.close()
        for line in child.stdout:
            try:event=json.loads(line)
            except json.JSONDecodeError:continue
            if event.get('item',{}).get('type')=='reasoning':continue
            log.write(json.dumps(event,ensure_ascii=False)+'\n');log.flush()
            if event.get('type') in ('thread.started','turn.completed','turn.failed','error'):events.append(event)
            if event.get('type')=='item.completed':print(json.dumps({'name':name,'elapsed':round(time.perf_counter()-started,2),'item':event['item']['type']}),flush=True)
        code=child.wait()
    report={'state':'FAILED','route':route,'name':name,'seconds':time.perf_counter()-started,'exit_code':code,'events':events}
    try:
        report['usage']=usage(events);answer=json.loads((folder/'final.txt').read_text())
        assert set(answer)=={'mesh_path','dependencies','owners_total','group_counts','bounds'}
        (folder/'result.json').write_text(json.dumps({'state':'AWAITING_NATIVE_VERIFICATION','answer':answer},indent=2)+'\n')
        if code==0:report['state']='AWAITING_INDEPENDENT_VERIFICATION'
    except (ValueError,AssertionError,OSError) as error:report['error']=str(error)
    (folder/'usage.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='events'}),flush=True);return report


async def verify(spec_path,name):
    from datetime import timedelta
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
    from apply_patch import call_tool
    spec_path=Path(spec_path);spec=json.loads(spec_path.read_text());folder=spec_path.parent/'runs'/name
    assert not (folder/'independent.json').exists()
    body="""    rows=[]
    for actor in call(DATA['scene'],'find_actors',{'name':'Bench_','tag':'','collision_channels':[]}):
        label=call(DATA['actor'],'get_label',{'actor':actor})
        if not label.startswith('Bench_'):continue
        root=call(DATA['actor'],'get_root_component',{'actor':actor})
        props=json.loads(call(DATA['object'],'get_properties',{'instance':root,'properties':['StaticMesh']}))
        rows.append({'label':label,'mesh':props['StaticMesh']['refPath'],'bounds':call(DATA['actor'],'get_actor_bounds',{'actor':actor})})
    target=[r['mesh'] for r in rows if r['label']=='Bench_Move_00']
    assert len(target)==1
    return {'rows':rows,'mesh':target[0],'dependencies':call(DATA['asset'],'get_dependencies',{'asset_path':target[0]})}
"""
    started=time.perf_counter()
    async with streamablehttp_client(spec['url']) as (r,w,_):
        async with ClientSession(r,w,read_timeout_seconds=timedelta(seconds=180)) as session:
            await session.initialize()
            raw=await call_tool(session,PROGRAMMATIC,'execute_tool_script',{'script':program(body,{'scene':SCENE,'actor':ACTOR,'object':OBJECT,'asset':ASSET})})
    raw=json.loads(raw) if isinstance(raw,str) else raw;data=raw['value'];rows=[r for r in data['rows'] if r['mesh']==data['mesh']]
    groups={}
    for row in rows:
        group='_'.join(row['label'].split('_')[:2]);groups[group]=groups.get(group,0)+1
    expected={'mesh_path':data['mesh'],'dependencies':sorted(set(data['dependencies'])),'owners_total':len(rows),'group_counts':groups,'bounds':[[min(r['bounds']['min'][k] for r in rows) for k in ('x','y','z')],[max(r['bounds']['max'][k] for r in rows) for k in ('x','y','z')]]}
    answer=json.loads((folder/'result.json').read_text())['answer']
    for field in ('mesh_path','dependencies','owners_total','group_counts'):assert answer[field]==expected[field],field
    assert len(answer['bounds'])==2 and all(len(b)==3 for b in answer['bounds'])
    assert max(abs(x-y) for a,b in zip(answer['bounds'],expected['bounds']) for x,y in zip(a,b))<.01
    (folder/'independent.json').write_text(json.dumps({'state':'VERIFIED','expected':expected,'native_rows':len(data['rows']),'verification_seconds':time.perf_counter()-started},indent=2)+'\n')
