"""Whole-agent benchmark in the disposable project. Native counters, no estimates."""
import argparse,json,os,subprocess,time
from pathlib import Path
from agent_benchmark import usage

ROOT=Path(__file__).resolve().parents[2]

def run(spec_path,route,scenario,name,cli,record_session=False,batched=False):
    if not name.isalnum() or len(name)>24:raise ValueError('Unique alphanumeric name required')
    spec_path=spec_path.resolve();spec=json.loads(spec_path.read_text())
    project=Path(spec['project']).resolve()
    if spec.get('fixture_format')!=1 or project.parent!=spec_path.parent or project.stem!='SpatialTwinBench':raise ValueError('Disposable project required')
    folder=project.parent/'runs'/name;folder.mkdir(parents=True,exist_ok=False)
    python=ROOT/'Build/MCP/venv/Scripts/python.exe'
    prompt=f'''Complete the authorized technical construction task using only the construction MCP supplied in this run. This is an independent disposable Unreal benchmark, not a game/lore task. Working/output folder: {folder.as_posix()}.
Use inspect_task, then author_asset only if requested, then observe. From the fresh accepted candidates choose every supported position and pass their indexes and positions to complete_plan. Read rejection reasons and do not place rejected candidates. Do not hardcode positions or counts. complete_plan validates, applies through official Unreal MCP, checks native output and retrieves a real render; on the Twin route it also simulates Shadow and waits for Canonical confirmation.
Use view_image to inspect the returned render, assess visible defects, and finish with a short honest result. Spatial facts come from the tools. No Computer Use, browser, direct HTTP/Blender, shell authoring, other MCPs, saves, restarts, cleanup, or writes outside this run folder. The guard owns only its temporary benchmark objects/asset. Never retry a failed write; report the error. Do not read other trials or global configuration/secrets. No need to search the repository: these instructions and tool descriptions contain the complete scope. The orchestrator has performed the repository/Graphify prerequisites and will independently verify and clean up. Do not estimate tokens. Render verification is visual QA, not gameplay.
This run is {scenario}, route {route}. Only the underlying perception/validation route differs; the task, returned detail, model, effort and final checks are identical.'''
    if batched:
        prompt+='''
Use the already verified bindings below directly; no tool catalogue discovery is needed:
tools.mcp__construction__inspect_task({})
tools.mcp__construction__author_asset({})
tools.mcp__construction__observe({})
tools.mcp__construction__complete_plan({candidate_indexes: [...], positions_cm: [...]})
All return MCP results. Check isError, then read structuredContent when present, otherwise parse the text content as JSON.
Execute in two code-mode calls, preserving every guard/check:
1. Await inspect_task. If requires_blender, await author_asset. Await observe. Store the fresh observation locally with store(), and show only its compact accepted/rejected data. These dependencies are deterministic and already authorized; there is no need to return to the model between them.
2. Reason over the observed supported/rejected candidates. For this task all accepted candidates are requested. Build arguments directly from that stored observation (indexes and positions), await complete_plan, then await tools.view_image({path: result.render_path}) and emit image(imageResult.image_url) in the same code-mode call. Show the compact confirmation and the real image. On any error stop immediately and report it; never automatically repeat a write. Finish with the visual assessment.
This batches known serial operations; it does not bypass Shadow, native validation, Canonical synchronization, error handling or rendering. Do not dump raw MCP wrappers or receipt files when their compact evidence is sufficient.'''
        prompt+='''
Keep the orchestration code short. The guard already validates identities, fields, finite coordinates, revisions, geometry, native effects and ownership; do not duplicate those algorithms in agent code. A verified MCP decoding helper for each call is:
const call = async (name,args={}) => {const r=await tools[name](args); if(r.isError)throw Error(JSON.stringify(r.content)); return r.structuredContent ?? JSON.parse(r.content.find(x=>x.type==='text').text);};
First call: const task=await call('mcp__construction__inspect_task'); if(task.requires_blender)await call('mcp__construction__author_asset'); const obs=await call('mcp__construction__observe'); store('obs',obs); text(obs);
After your placement decision, second call: const obs=load('obs'); const result=await call('mcp__construction__complete_plan',{candidate_indexes:obs.accepted.map(x=>x.index),positions_cm:obs.accepted.map(x=>x.position)}); text(result); image((await tools.view_image({path:result.render_path})).image_url);
Use the same helper in that second call. These are code snippets, not another shell workflow. If a binding really is unavailable, do one bounded name lookup for construction tools; do not dump a full catalogue or claim a write occurred.'''
    (folder/'prompt.txt').write_text(prompt,encoding='utf-8')
    args=[str(cli),'exec','--ignore-user-config',*([] if record_session else ['--ephemeral']),'--json','--approve-for-me',
          '-c','sandbox_workspace_write.writable_roots=[]','-c','sandbox_workspace_write.exclude_tmpdir_env_var=true','-c','sandbox_workspace_write.exclude_slash_tmp=true',
          '--model','gpt-6.1-sol','-c','model_reasoning_effort="high"','-C',str(folder),
          '-c','mcp_servers.construction={command='+json.dumps(python.as_posix())+',args='+json.dumps([(ROOT/'tools/UnrealSpatialTwinMCP/production_benchmark_guard.py').as_posix(),'--spec',spec_path.as_posix(),'--route',route,'--scenario',scenario,'--name',name])+',startup_timeout_sec=40,required=true}',
          '-o',str(folder/'final.txt'),'-']
    started=time.perf_counter();events=[];counts={}
    with (folder/'stderr.log').open('w',encoding='utf-8') as errors,(folder/'events.jsonl').open('w',encoding='utf-8') as log:
        child=subprocess.Popen(args,cwd=folder,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=errors,text=True,encoding='utf-8',errors='replace',env={**os.environ,'DISABLE_TELEMETRY':'true','PYTHONDONTWRITEBYTECODE':'1'})
        child.stdin.write(prompt);child.stdin.close()
        for line in child.stdout:
            try:event=json.loads(line)
            except json.JSONDecodeError:continue
            kind=event.get('item',{}).get('type')
            if kind=='reasoning':continue
            log.write(json.dumps(event,ensure_ascii=False)+'\n');log.flush()
            if event.get('type') in ('thread.started','turn.completed','turn.failed','error'):events.append(event)
            if event.get('type')=='item.completed':
                counts[kind]=counts.get(kind,0)+1
                print(json.dumps({'name':name,'elapsed':round(time.perf_counter()-started,1),'item':kind}),flush=True)
        code=child.wait()
    result=dict(name=name,route=route,scenario=scenario,model='gpt-6.1-sol',effort='high',returncode=code,agent_seconds=time.perf_counter()-started,events=events,item_counts=counts,state='FAILED',usage=None,record_session=record_session,batched=batched,
                limits='Native main-agent counters include input/output and cached context. Reviewer/account-wide cost unavailable. Fixture setup, warmup and cleanup measured separately; no fabricated estimate. Reasoning breakdown is already part of output.')
    try:result['usage']=usage(events)
    except ValueError as error:result['usage_error']=str(error)
    receipt=folder/'result.json'
    if receipt.is_file():result['result_state']=json.loads(receipt.read_text())['state']
    if code==0 and result['usage'] and result.get('result_state')=='VERIFIED':result['state']='AWAITING_INDEPENDENT_VERIFICATION'
    (folder/'usage.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:result[k] for k in ('name','state','agent_seconds','usage')}),flush=True)
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--spec',required=True,type=Path);p.add_argument('--cli',required=True,type=Path)
    p.add_argument('--route',required=True,choices=['with','without']);p.add_argument('--scenario',required=True,choices=['batch','reuse','author']);p.add_argument('--name',required=True)
    p.add_argument('--record-session',action='store_true',help='Retain the CLI native session for tool-name/token diagnostics; never export reasoning content')
    p.add_argument('--batched',action='store_true',help='Batch known tool dependencies and emit the render alongside confirmation, equally for both arms')
    a=p.parse_args();run(a.spec,a.route,a.scenario,a.name,a.cli,a.record_session,a.batched)
