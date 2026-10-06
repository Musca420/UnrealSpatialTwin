"""Real-agent open planning through verified native delivery, both routes.

The agent receives region and construction requirements, never candidates or a
solution. The no-Twin arm uses the official Unreal server directly and is told
how to batch its existing tools efficiently. The common delivery guard supplies
no perception answers. Run in an independently prepared disposable editor.
"""
import json
import os
from pathlib import Path
import subprocess
import time
from agent_benchmark import usage
from construction_benchmark import SCENE, ACTOR, OBJECT, MESH, PROGRAMMATIC

ROOT = Path(__file__).resolve().parents[2]


def run(spec_path, route, name, cli, plugin, source_schemas=None,*,native_ready=None):
    spec_path, plugin = Path(spec_path).resolve(), Path(plugin).resolve()
    spec = json.loads(spec_path.read_text())
    scenario=spec.get('benchmark_scenario','reuse')
    if scenario not in ('reuse','author'):raise ValueError('Unsupported open construction scenario')
    profile=spec.get('twin_tool_profile','workflow')
    if profile not in ('workflow','focused'):raise ValueError('Unsupported Twin tool profile')
    base = Path(spec['project']).resolve().parent
    if base != spec_path.parent or Path(spec['project']).stem != 'SpatialTwinBench':
        raise ValueError('Disposable project required')
    folder = base/'runs'/name
    folder.mkdir(parents=True, exist_ok=False)
    python = ROOT/'Build/MCP/venv/Scripts/python.exe'
    prompt = f'''Complete this authorized construction task in the independent disposable benchmark project.
In world AABB min[-500,-2200,-100], max[2800,-1000,1000] cm, identify every sufficiently large flat platform top. Place one upright 100x100x100cm cube centered on every suitable top, with 0.2cm clearance. Leave tilted platforms empty. Discover source actors/components/assets and poses from the supplied current tools, never assume positions or counts. Reuse the existing cube StaticMesh asset and native StaticMeshActor class. Preserve navigation defaults. All positions/units are Unreal cm.
Prepare a complete plan locally: operations with type='CREATE_ACTOR' (the field is type, not op), unique temporary targets, unique labels beginning BenchRun_{name}_ in ascending XYZ order, native class /Script/Engine.StaticMeshActor, asset:<actual object path>, transforms with position, quaternion [0,0,0,1], scale [1,1,1]. No component/property overrides. Confirm footprint support and collision; AABB alone is not exact geometry proof. Missing coverage is UNKNOWN, never approval.
The exact CREATE operation fields are {{"type":"CREATE_ACTOR","target":"<unique temporary ID>","label":"<owned unique label>","class":"/Script/Engine.StaticMeshActor","asset":"asset:<actual asset path>","transform":{{"position":[x,y,z],"rotation":[0,0,0,1],"scale":[1,1,1]}}}}. The input schema's class and asset keys are required. Map observed source asset_id to patch asset explicitly; retain exact operation_id if provided by a validated Twin patch.
After planning call the common delivery tool exactly once with operations and patch_id (validated Twin patch ID for the Twin route; empty string without Twin). It independently verifies native supports/collisions, applies through official MCP, confirms source/defaults, synchronizes Canonical on the Twin route and returns a real Unreal render. View that image with view_image and give an honest visual assessment. A tool OK alone is not native success. Report BLOCKED on conflict/unknown/failure; never repeat a write.
This is a technical benchmark, not game/lore work. The orchestrator performed repository/Graphify prerequisites and independently cleans up after readback. No Computer Use, shell, browser, other projects, secrets/global settings, old trial files, direct DB access, map saves/restarts or cleanup. No direct live modifications outside the delivery tool. Use source tools for perception only. Keep large results in code-mode store/load; emit only compact decision facts. No need to discover the whole catalogue; known bindings are below. Reason over the fresh source yourself; do not treat a proposed patch as live success.
The known delivery binding is the single ALL_TOOLS entry whose name ends __deliver. Call through tools[binding.name], fail on isError; structuredContent or the JSON text contains the result. Render via tools.view_image and image().
'''
    guard = 'mcp_servers.delivery={command='+json.dumps(python.as_posix())+',args='+json.dumps([
        (ROOT/'tools/UnrealSpatialTwinMCP/open_benchmark_guard.py').as_posix(), '--spec',spec_path.as_posix(),
        '--route',route,'--name',name,'--scenario',scenario])+',required=true,startup_timeout_sec=40}'
    configs = [guard]
    if native_ready:
        configs[0] = configs[0][:-1] + ',env={SPATIAL_TWIN_NATIVE_READY='+json.dumps(str(native_ready['receipt']).replace('\\','/'))+',SPATIAL_TWIN_NATIVE_PID='+json.dumps(str(native_ready['pid']))+'}}'
    prompt += '''
Batch known deterministic dependencies in code mode; preserve all checks and stop on error. Read source once with bounded fields, keep IDs/raw receipts in store/load, emit only decision-relevant facts. After choosing the candidates, validated planning/preflight and the already authorized delivery/render may run serially in the same code-mode call. Return to the model only for a semantic decision or unresolved result, not to copy coordinates or request another conclusive check.
The delivery guard owns the reviewed execution procedure: target/map/plugin check, source and footprint/collision validation, official writes, native readback/defaults, Canonical confirmation on Twin, and real capture. These verified wrapper instructions satisfy execution-procedure lookup for this run. The agent must still inspect the actual returned image. No duplicate native action or bypass is authorized.
Resolve the supplied known bindings and call them together; do not send an inventory-only call or print tool metadata. After reading any mandatory unresolved SDK instructions, keep the complete source-to-delivery chain in one execution. Missing/ambiguous bindings stop that execution. Resolve and invoke known tools together. The exact delivery result is {state,actor_count,render_path,receipt,canonical_confirmed,map_saved}; after state='VERIFIED' and the expected count, view_image(render_path) and image() in that same action execution, before returning to assess the image. No extra turn is needed to discover these keys. On the Twin route canonical_confirmed must be true; both routes map_saved=false until independent cleanup. This result contract is identical for both arms.
For this technical task the selection rule is exact: a platform top must fit the requested object's footprint and have slope at most0.1degrees; physical continuous support and no blocking penetration remain required. Evaluate these source-derived dimensions/poses in code. You may compose source reading, that deterministic selection, strict validation, authorized delivery and image in one execution when all contracts and source are complete. Do not insert a model turn solely to confirm sorting, coordinate arithmetic or this supplied predicate. Never assume counts/positions, and stop before writes on semantic ambiguity, incomplete pages, missing coverage or failed checks. These rules apply equally to both routes; the native route must still request and obey the actual execution environment before Programmatic use.
The delivery receipt retains every exact operation and check. After viewing the render, finish with at most120 words: result/count, confirmed revision/save status when applicable, actual visual assessment and unresolved limits. Do not repeat JSON, operations, source tables or tool transcripts in the final answer. This output requirement is identical for both routes.
'''
    if route == 'with':
        configs.append('mcp_servers.spatial_twin={command='+json.dumps(python.as_posix())+',args='+json.dumps([
            (plugin/'server.py').as_posix(),'--root',(base/'Saved/SpatialTwin').as_posix(),'--tool-profile',profile])+',required=true,startup_timeout_sec=40,env={SPATIAL_TWIN_PROJECT='+json.dumps(spec['project'].replace('\\','/'))+',SPATIAL_TWIN_HOME='+json.dumps((plugin/'runtime').as_posix())+'}}')
        skill = plugin/'skills/unreal-spatial/SKILL.md'
        prompt += '\nTwin route: use the following installed skill (already provided; do not reread). Only delivery needs live Unreal. Relative references are at '+skill.parent.as_posix()+'.\n'+skill.read_text(encoding='utf-8')
        prompt += '''
Resolved source workflow: in the complete Code Mode execution, call world_read with world_search(text='Bench_reuse_Support_',kind='Actor',limit=20,fields=['label','class','coverage'],component_fields=['transform','bounds','asset_id'],component_limit=4). This exact read contract is supplied here; patch_place is already advertised. Do not request either schema again. Page if needed at the same revision. Retain the actors and bounded source child pages locally; inspect child kind and follow any child cursor with entity_children (request only that missing schema if needed). Do not scan every unrelated component in the region. Select the observed platforms within the supplied region whose footprint fits the requested object and whose source orientation is sufficiently upright; source support geometry is still checked by the product helper. Use the observed mesh asset ID/native Actor class and derive candidate ray origins at the selected top centres, just above actual world bounds. Keep candidates in ascending XYZ order. Retain source and exact parameters locally; check the supplied predicate in code and continue to strict planning and delivery in this execution. Return only an unresolved decision or the checked receipt/image.
After checking the source predicate in code, use the unique __place_and_deliver binding once with arguments containing those actual patch_place parameters, base_revision, max_distance, label_prefix='BenchRun_<actual run name>_', rotation=[0,0,0,1],scale=[1,1,1],size_cm=[100,100,100],clearance=.2. That existing composed product procedure prepares strict Shadow, requires PROVEN continuous support/no rejected candidate/no new collisions, applies the same patch through official MCP, confirms Canonical/defaults and captures. No operation/coordinate copying or second conclusive preview is needed. Fail on errors; never replay. View/emit the returned render in this same action execution, then assess it. The helper supplies no candidate table or semantic selection. Use it instead of separately regenerating CREATE operations and deliver; retain operation IDs internally. These verified instructions resolve execution lookup for this trial.
'''
    else:
        configs.append('mcp_servers.unreal={url='+json.dumps(spec['url'])+',required=true,startup_timeout_sec=40}')
        prompt += f'''
Without-Twin route: the official Unreal MCP is the source. It has call_tool, describe_toolset and list_toolsets; inspect only an unknown schema. Efficient native batching is available through {PROGRAMMATIC}.execute_tool_script. It executes registered tools, not arbitrary editor Python.
Read the mandatory current execution instructions first. Then keep source/support reads, exact predicate evaluation, strict checks, once-only delivery and view_image/image in one Code Mode execution; retain full source locally. Keep every check; do not spend model turns copying source arrays.
Use one programmatic read to find_actors in the supplied bounds, fetch labels/transforms/root StaticMesh for relevant actors, and mesh bounds once per distinct asset. Filter by source dimensions/class/pose before returning data. A second batched read can query all nine footprint support rays and native blocking overlaps. Derive placements from actual mesh bounds, transforms and ray distances, retaining the whole source locally. Avoid one model round trip per Actor or ray; this is the efficient baseline.
Verified Toolsets: Scene={SCENE}; Actor={ACTOR}; Object={OBJECT}; Mesh={MESH}; Programmatic={PROGRAMMATIC}.
Verified first environment request: call_tool with toolset_name='{PROGRAMMATIC}', tool_name='get_execution_environment', arguments={{}}. This is a Programmatic toolset method, not a top-level tool. Resolve the unique Unreal call_tool binding and invoke this known request in the same execution. Read its instructions, then use execute_tool_script; do not describe the toolset again unless this exact request reports a changed/missing schema.
Verified read signatures: Scene.find_actors(name:'',tag:'',bounds:{{min:{{x,y,z}},max:{{x,y,z}},isValid:true}},collision_channels:[]); Actor.get_label(actor:{{refPath}}); Actor.get_actor_transform(actor); Actor.get_root_component(actor); Object.get_properties(instance:root,properties:['StaticMesh']); Mesh.get_bounds(mesh:{{refPath}}); Scene.trace_world(start:{{x,y,z}},end:{{x,y,z}}) returns a float distance from start in cm, or None for no hit, not a point or hit object. For a vertical downward ray, hit_z=start.z-distance. This is the installed SceneTools contract; do not reinterpret the result as end-relative distance. Collision volumes use Scene.find_actors with channels ['ObjectTypeQuery1','ObjectTypeQuery2']. Never call native write tools.
Programmatic script grammar: import json; define call(t,n,a) as execute_tool(t+'.'+n,json.dumps(a))['returnValue']; define run() returning a dict with the source results. No semicolons needed; script is normal multiline Python over registered calls; Python boolean literals are True/False, and json.dumps serializes the tool arguments. Official call_tool input is {{toolset_name,tool_name,arguments}}. Its JSON text has returnValue, sometimes another JSON string. Parse it without printing the wrapper. All source facts must be fresh; do not hardcode a candidate table.
'''
        if source_schemas:
            prompt += '\nThese exact method input/output schemas were verified from the current installed UE5.8.2 toolsets. Reuse them; do not describe entire toolsets again. Still call get_execution_environment once in this conversation and read its current instructions before using Programmatic. Native dict-like objects are strict: index known schema keys with []; .get(key,default) is unsupported. Only inspect a missing/changed schema.\n'+json.dumps(source_schemas,separators=(',',':'))
    if scenario=='author':
        prompt=prompt.replace('min[-500,-2200,-100], max[2800,-1000,1000]','min[-500,1000,-100], max[2800,2200,1000]')
        prompt=prompt.replace('100x100x100cm cube','152x18x100cm rail with a bottom-centred pivot')
        prompt=prompt.replace('Reuse the existing cube StaticMesh asset and native StaticMeshActor class.','Author the specified Blender rail first through the known author_asset delivery tool; use its actual source bounds and target asset. Reuse the observed native StaticMeshActor class.')
        prompt=prompt.replace('Bench_reuse_Support_','Bench_author_Support_').replace('size_cm=[100,100,100]','size_cm=[152,18,100]')
        prompt+='''
Authoring procedure for both arms: call the unique __author_asset delivery binding during the complete execution. It creates editable Blender source, FBX, render geometry and six UCX bodies using the same verified fixed design; it supplies no world positions/counts. Retain its actual manifest/path/bounds. Exact authored output: local_bounds_cm=[[minX,minY,minZ],[maxX,maxY,maxZ]], two arrays of three finite numbers; use local_bounds_cm[0] and local_bounds_cm[1], never .min/.max. The native Mesh.get_bounds output separately uses {min:{x,y,z},max:{x,y,z},isValid:true}; keep these two contracts distinct. Then discover current source platforms from your route and choose positions yourself. Do not import or spawn directly. Delivery independently verifies both arms' native render vertices, every UCX body's vertices, collision mode, source hashes, physical footprint/collisions, navigation defaults and final poses/assets, then returns the real image.
Twin route only: after obtaining the actual source mesh template ID and authored manifest, pass manifest_file and that observed canonical template ID as asset_id to place_and_deliver along with your source-derived placement arguments and current base_revision. The existing product patch_place composes immutable staging with Shadow planning in the same call. Do not call asset_stage separately for this surface-placement route. It supplies no candidates or semantic choice. The product materialize procedure imports through official MCP, checks native geometry/UCX/navigation parity, retains operation IDs in its rebound canonical patch, revalidates and applies it. Original authored and rebound canonical patch identities are distinct and recorded. Do not manually create another patch or strip operation IDs.
Without-Twin route: use the authored asset:<actual target path> in CREATE operations. Derive its footprint and bottom offset from author_asset's actual local_bounds_cm; do not mistakenly add half the rail height to a bottom-centred pivot. Use native rays/overlaps for perception. The same delivery authoring/native quality checks run before Actor writes. Staging/Twin must remain unused.
'''
    if route=='with':
        prompt+='\nExact place_and_deliver request envelope: tools[binding.name]({arguments: placementArguments}). All patch_place inputs, including candidates/asset_id/base_revision and authored manifest_file when present, go inside that arguments object. Do not flatten these fields at the request root. The handler schema returned by world_read describes placementArguments; the delivery tool schema describes the outer request. Keep both contracts distinct.\n'
    if route=='with' and profile=='focused':
        prompt=prompt.replace('patch_place is already advertised. Do not request either schema again.',
            "the focused profile advertises world_read, shadow_plan and tool_describe only. Include schemas=['patch_place'] in that first world_read alongside source; it returns the exact existing handler contract. No inventory-only or later schema turn.")
    prompt+='''
Execution rule for this exact-predicate technical task, on BOTH routes: keep fresh source reading, source-derived selection/arithmetic, strict planning/preflight, the authorized once-only delivery and view_image/image in a single Code Mode execution after any genuinely unresolved SDK instructions have been read. Do not deliberately end an execution after printing source facts to ask the model to confirm this already supplied predicate. Earlier references to "after reasoning" mean evaluation of the observed task predicate in code; there is no additional semantic choice in this task. Missing/ambiguous coverage, contracts, support or validation still stop before writes. For native Programmatic, first read its mandatory actual environment instructions; then keep the remaining resolved chain together. Twin needs no such live SDK read for perception. Authoring and source discovery are independent: they may be awaited together while retaining/checking both actual results. Never skip or weaken a check, hardcode an answer, bypass a refusal, rebase or replay. After the returned real image is inspected, finish from the already checked receipt; no extra tool call just to decode status/count/save fields.
'''
    (folder/'prompt.txt').write_text(prompt, encoding='utf-8')
    args = [str(cli),'exec','--ignore-user-config','--json','--approve-for-me','--model','gpt-6.1-sol',
            '-c','model_reasoning_effort="high"','-c','features.code_mode=true','-c','features.code_mode_only=true','-C',str(folder),
            '-c','sandbox_workspace_write.writable_roots=[]',
            '-c','sandbox_workspace_write.exclude_tmpdir_env_var=true',
            '-c','sandbox_workspace_write.exclude_slash_tmp=true']
    for config in configs:
        args += ['-c',config]
    args += ['-o',str(folder/'final.txt'),'-']
    started = time.perf_counter()
    events = []
    with (folder/'stderr.log').open('w',encoding='utf-8') as err, (folder/'events.jsonl').open('w',encoding='utf-8') as log:
        child = subprocess.Popen(args, cwd=folder, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=err,
                                 text=True, encoding='utf-8', errors='replace', env={**os.environ,'DISABLE_TELEMETRY':'true','PYTHONDONTWRITEBYTECODE':'1'})
        (folder/'process.json').write_text(json.dumps(dict(pid=child.pid,route=route,name=name)))
        child.stdin.write(prompt)
        child.stdin.close()
        for line in child.stdout:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get('item',{}).get('type') == 'reasoning':
                continue
            log.write(json.dumps(event,ensure_ascii=False)+'\n')
            log.flush()
            if event.get('type') in ('thread.started','turn.completed','turn.failed','error'):
                events.append(event)
            if event.get('type') == 'item.completed':
                print(json.dumps(dict(name=name,elapsed=round(time.perf_counter()-started,2),item=event['item']['type'])),flush=True)
        code = child.wait()
    report = dict(state='FAILED',route=route,name=name,seconds=time.perf_counter()-started,exit_code=code,events=events)
    try:
        report['usage'] = usage(events)
    except ValueError as error:
        report['usage_error'] = str(error)
    if (folder/'result.json').exists():
        result = json.loads((folder/'result.json').read_text())
        if code == 0 and result['state'] == 'VERIFIED' and report.get('usage'):
            report['state'] = 'AWAITING_INDEPENDENT_VERIFICATION'
    (folder/'usage.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k != 'events'}),flush=True)
    return report
