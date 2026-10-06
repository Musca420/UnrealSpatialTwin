"""Benchmark-only independent native render/UCX observation, no Twin reader.

Uses Epic Geometry Script and the existing owned-fixture Python adapter; no
candidate selection or live Actor writes. Both benchmark arms run this check.
"""
import asyncio,json,re,sys
from pathlib import Path
from apply_patch import call_tool,OBJECT
from spatial_twin import geometry
from materialize import same_points

ROOT=Path(__file__).resolve().parents[2]

async def verify_native_asset(native,base,folder,name,target,attempt='native-geometry'):
    if base.name not in ('Cold137With','Cold137Without') or folder!=base/'runs'/name:
        raise ValueError('Independent owned fixture required')
    manifest=json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
    body=json.loads(await call_tool(native,OBJECT,'get_properties',{'instance':{'refPath':target},'properties':['BodySetup']}))['BodySetup']
    actual=json.loads(await call_tool(native,OBJECT,'get_properties',{'instance':body,'properties':['AggGeom','CollisionTraceFlag']}))
    aggregate=actual['AggGeom'];hulls=aggregate['convexElems']
    if any(elements for key,elements in aggregate.items() if key!='convexElems'):
        raise ValueError('Unexpected native collision primitives')
    expected=[geometry.load_mesh(str(folder/filename)) for filename in manifest['collision_files']]
    if len(expected)!=len(hulls):raise ValueError('Native UCX count differs')
    remaining=list(hulls)
    for mesh in expected:
        match=next((i for i,h in enumerate(remaining) if same_points(mesh.vertices,[[v[k] for k in ('x','y','z')] for v in h['vertexData']])),None)
        if match is None:raise ValueError('Native UCX vertices differ from authored source')
        remaining.pop(match)
    if not re.fullmatch(r'native-geometry(?:-recovery\d*)?',attempt):raise ValueError('Unknown observation attempt')
    probe=base/'startup'/name/attempt;probe.mkdir(parents=True,exist_ok=False)
    script=f'''mesh=unreal.load_asset({target!r})
assert isinstance(mesh,unreal.StaticMesh)
dynamic=unreal.DynamicMesh()
lod=unreal.GeometryScriptMeshReadLOD(lod_type=unreal.GeometryScriptLODType.RENDER_DATA,lod_index=0)
_,outcome=unreal.GeometryScript_AssetUtils.copy_mesh_from_static_mesh_v2(mesh,dynamic,unreal.GeometryScriptCopyMeshFromAssetOptions(),lod)
assert outcome==unreal.GeometryScriptOutcomePins.SUCCESS
_,positions,gaps=unreal.GeometryScript_MeshQueries.get_all_vertex_positions(dynamic,True)
assert not gaps
points=[[p.x,p.y,p.z] for p in unreal.GeometryScript_List.convert_vector_list_to_array(positions)]
default=unreal.get_default_object(unreal.PhysicsSettings).get_editor_property('default_shape_complexity')
Path({str(probe/'vertices.json')!r}).write_text(json.dumps({{'asset':mesh.get_path_name(),'points':points,'default_complexity':str(default)}})+'\\n',encoding='utf-8')
print(json.dumps({{'state':'NATIVE_RENDER_OBSERVED','vertices':len(points)}}))
'''
    (probe/'probe.py').write_text(script,encoding='utf-8')
    # Reuse the authenticated native session. A nested MCP subprocess stalled
    # while the parent tool was active; no subprocess/new HTTP session is needed.
    from cold_agent_benchmark import EDITOR
    sys.path.insert(0,str(EDITOR.parents[2]/'Plugins/Experimental/PythonScriptPlugin/Content/Python'))
    import remote_execution as remote
    ref={'refPath':'/Script/PythonScriptPlugin.Default__PythonScriptPluginSettings'}
    keys=['bRemoteExecution','RemoteExecutionMulticastBindAddress','RemoteExecutionMulticastTtl']
    old=json.loads(await call_tool(native,OBJECT,'get_properties',dict(instance=ref,properties=keys)))
    if old['bRemoteExecution'] is not False:raise ValueError('Another Python observation is active')
    bridge=remote.RemoteExecution()
    try:
        assert await call_tool(native,OBJECT,'set_properties',dict(instance=ref,values=json.dumps(dict(bRemoteExecution=True,RemoteExecutionMulticastBindAddress='127.0.0.1',RemoteExecutionMulticastTtl=0))))
        bridge.start()
        for _ in range(40):
            nodes=[n for n in bridge.remote_nodes if Path(n.get('project_root','')).resolve()==base.resolve()]
            if nodes:break
            await asyncio.sleep(.25)
        if len(nodes)!=1:raise ValueError('Owned native Python observation endpoint unavailable')
        await asyncio.to_thread(bridge.open_command_connection,nodes[0]['node_id'])
        code="import unreal,json\nfrom pathlib import Path\nassert Path(unreal.Paths.get_project_file_path()).resolve()==Path("+repr(str(base/'SpatialTwinBench.uproject'))+").resolve()\n"+script
        receipt=await asyncio.to_thread(bridge.run_command,code,exec_mode=remote.MODE_EXEC_FILE)
        (probe/'adapter.json').write_text(json.dumps(receipt,indent=2)+'\n',encoding='utf-8')
        if not receipt['success']:raise ValueError('Native geometry observation failed; inspect adapter receipt')
    finally:
        bridge.stop()
        if not await call_tool(native,OBJECT,'set_properties',dict(instance=ref,values=json.dumps(old))):raise ValueError('Native Python settings restoration failed')
    observed=json.loads((probe/'vertices.json').read_text(encoding='utf-8'))
    render=geometry.load_mesh(str(folder/manifest['geometry_file']))
    if observed['asset']!=target or not same_points(render.vertices,observed['points']):
        raise ValueError('Native render vertices differ from authored source')
    trace=actual['CollisionTraceFlag'].replace('_','').lower()
    default=observed['default_complexity'].split(':',1)[0].rsplit('.',1)[-1].lstrip('<').replace('_','').lower()
    effective=default if trace=='ctfusedefault' else trace
    if not effective.endswith('ctfusesimpleandcomplex'):raise ValueError('Different native collision trace mode')
    proof=dict(state='NATIVE_RENDER_UCX_PARITY_VERIFIED',convex_bodies=len(hulls),render_vertices=len(observed['points']),
        point_tolerance_cm=.05,effective_trace_mode=effective,source_manifest=str(folder/'manifest.json'),native_observation=str(probe/'vertices.json'))
    (probe/'parity.json').write_text(json.dumps(proof,indent=2)+'\n',encoding='utf-8')
    return proof
