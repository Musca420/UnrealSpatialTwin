"""Test-only clean shutdown of a verified disposable SpatialTwinBench run.

No gameplay/editor authoring commands or arbitrary Python payload are accepted.
Reuses the qualified loopback compatibility bridge; restores original settings.
"""
import argparse,asyncio,json,sys,time
from pathlib import Path


def scope(base, output):
    base,output=Path(base).resolve(),Path(output).resolve()
    project=base/'SpatialTwinBench.uproject'
    if not project.is_file() or output.parent!=base/'startup':
        raise ValueError('Exact owned benchmark project/startup run required')
    if (output/'control-close.json').exists():
        raise ValueError('Close already attempted; inspect instead of replaying')
    request=json.loads((output/'request.json').read_text(encoding='utf-8'))
    ready=json.loads((output/'startup.json').read_text(encoding='utf-8'))
    independent=json.loads((base/'runs'/output.name/'independent.json').read_text(encoding='utf-8'))
    if (Path(request['project']).resolve()!=project or request['map']!='/Game/Benchmark/Base'
            or ready.get('state')!='READY' or Path(ready['project']).resolve()!=project
            or ready.get('route')!=request['route'] or request['route'] not in ('with','without')
            or ready.get('native_plugin_loaded')!=(request['route']=='with')
            or type(ready.get('pid')) is not int or ready['pid']<=0
            or independent.get('state')!='VERIFIED_AND_RESTORED'):
        raise ValueError('Unverified restoration or mismatched native ownership')
    return base,output,project,ready['pid']


CLOSE_WORLD = "\nexpected={}\nfor i in range(32):\n x=(i%8)*260;y=(i//8)*260;top=20+(i%5)*8\n expected['Bench_BatchSupport_%02d'%i]=([x,y,top-20],[1.8,1.8,.4],0)\n expected['Bench_Move_%02d'%i]=([x,y,350+(i%3)*30],[1,1,1],0)\nfor scenario,y in [('reuse',-1700),('author',1700)]:\n for i in range(12):expected['Bench_%s_Support_%02d'%(scenario,i)]=([i*210,y,30+(i%3)*10-20],[1.8,1.4,.4],5 if i%4==3 else 0)\nfor i in range(256):expected['Bench_Background_%03d'%i]=([10000+(i%16)*240,10000+(i//16)*240,100],[1.2,1.2,2],0)\nfound={a.get_actor_label():a for a in actors.get_all_level_actors() if a.get_actor_label().startswith('Bench')}\nassert set(found)==set(expected)\nfor label,(p,s,pitch) in expected.items():\n a=found[label];v=a.get_actor_location();scale=a.get_actor_scale3d();rot=a.get_actor_rotation()\n assert all(abs(v1-v2)<.001 for v1,v2 in zip([v.x,v.y,v.z],p)),label\n assert all(abs(v1-v2)<.001 for v1,v2 in zip([scale.x,scale.y,scale.z],s)),label\n expected_rot=unreal.Rotator(pitch,0,0)\n assert all(abs(getattr(rot,k)-getattr(expected_rot,k))<.001 for k in ('pitch','yaw','roll')),label\n assert a.static_mesh_component.static_mesh.get_path_name()=='/Engine/BasicShapes/Cube.Cube'\nassert unreal.EditorLoadingAndSavingUtils.save_map(world,'/Game/Benchmark/Base')\ndef _bench_exit(dt):\n assert not unreal.EditorLoadingAndSavingUtils.get_dirty_content_packages() and not unreal.EditorLoadingAndSavingUtils.get_dirty_map_packages()\n unreal.SystemLibrary.quit_editor();return False\n_bench_exit_handle=unreal.register_ticker_callback(_bench_exit,delay=5)\nprint(json.dumps({'state':'EXIT_SCHEDULED','pid':os.getpid()}))\n"


async def close(base,output):
    base,output,project,pid=scope(base,output)
    from cold_agent_benchmark import EDITOR
    sys.path.insert(0,str(EDITOR.parents[2]/'Plugins/Experimental/PythonScriptPlugin/Content/Python'))
    import remote_execution as remote
    from construction_benchmark import ClientSession,streamablehttp_client,OBJECT
    from apply_patch import call_tool
    async with streamablehttp_client('http://127.0.0.1:8013/mcp') as (r,w,_):
        async with ClientSession(r,w) as session:
            await session.initialize()
            ref={'refPath':'/Script/PythonScriptPlugin.Default__PythonScriptPluginSettings'}
            keys=['bRemoteExecution','RemoteExecutionMulticastBindAddress','RemoteExecutionMulticastTtl']
            old=json.loads(await call_tool(session,OBJECT,'get_properties',{'instance':ref,'properties':keys}))
            if old['bRemoteExecution'] is not False:raise ValueError('Unexpected preexisting remote execution')
            async def setting(value):
                if await call_tool(session,OBJECT,'set_properties',{'instance':ref,'values':json.dumps(value)}) is not True:
                    raise RuntimeError('Remote setting write not confirmed')
            bridge=remote.RemoteExecution()
            try:
                await setting(dict(bRemoteExecution=True,RemoteExecutionMulticastBindAddress='127.0.0.1',RemoteExecutionMulticastTtl=0))
                bridge.start();deadline=time.monotonic()+10;nodes=[]
                while time.monotonic()<deadline:
                    nodes=[n for n in bridge.remote_nodes if Path(n.get('project_root','')).resolve()==base]
                    if nodes:break
                    await asyncio.sleep(.25)
                if len(nodes)!=1:raise ValueError('Expected one exact owned native project')
                bridge.open_command_connection(nodes[0]['node_id'])
                code="import unreal,json,os\nfrom pathlib import Path\nassert os.getpid()=="+repr(pid)+"\nassert Path(unreal.Paths.get_project_file_path()).resolve()==Path("+repr(str(project))+").resolve()\nworld=unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()\nassert world.get_path_name()=='/Game/Benchmark/Base.Base'\nactors=unreal.get_editor_subsystem(unreal.EditorActorSubsystem)\ndirty=list(unreal.EditorLoadingAndSavingUtils.get_dirty_content_packages())+list(unreal.EditorLoadingAndSavingUtils.get_dirty_map_packages())\nassert all(p.get_path_name()=='/Game/Benchmark/Base' for p in dirty),[p.get_path_name() for p in dirty]\n"+CLOSE_WORLD
                result=bridge.run_command(code,exec_mode=remote.MODE_EXEC_FILE)
                (output/'control-close.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
                if not result['success']:raise RuntimeError('Native close rejected; inspect receipt, never replay')
                return result
            finally:
                try:bridge.stop()
                finally:await setting(old)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['close']);p.add_argument('base',type=Path);p.add_argument('output',type=Path)
    a=p.parse_args();print(json.dumps(asyncio.run(close(a.base,a.output))))
