"""Native startup receipt for the owned process-cold benchmark projects only."""
import hashlib
import json
import os
from pathlib import Path
import time
import unreal

request=Path(os.environ['SPATIAL_TWIN_COLD_REQUEST']).resolve()
spec=json.loads(request.read_text(encoding='utf-8'))
project=Path(unreal.Paths.get_project_file_path()).resolve()
assert project==Path(spec['project']).resolve() and project.stem=='SpatialTwinBench'
assert spec['route'] in ('with','without') and spec['map']=='/Game/Benchmark/Base'
output=request.with_name('startup.json');assert not output.exists()
unreal.EditorPythonScripting.set_keep_python_script_alive(True)
started=time.perf_counter();scanned=False;finished=False


def tick(_):
    global scanned,finished
    if finished:return False
    try:
        world=unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
        if not world or world.get_path_name()!=spec['map']+'.Base':return True
        if unreal.EditorLoadingAndSavingUtils.get_dirty_content_packages() or unreal.EditorLoadingAndSavingUtils.get_dirty_map_packages():raise RuntimeError('Unexpected dirty startup packages')
        status=None
        if spec['route']=='with':
            tool=unreal.get_default_object(unreal.SpatialTwinToolset)
            if spec['initial_scan'] and not scanned:
                scanned=True;status=json.loads(tool.call_method('spatial_twin_rebuild'))
            else:status=json.loads(tool.call_method('spatial_twin_status'))
            if status.get('error'):raise RuntimeError(status['error'])
            if not status['ready'] or any(status[k] for k in ('pending','scanning','navigation_refresh_pending','navigation_building')):return True
        else:
            assert not hasattr(unreal,'SpatialTwinToolset'),'Baseline unexpectedly loaded Twin'
        # Same native source readiness check in both arms. No answer table or
        # raycast warm-up: first support/geometry query belongs to the agent.
        actors=unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
        expected={}
        for i in range(32):
            x=i%8*260;y=i//8*260;top=20+i%5*8
            expected['Bench_BatchSupport_%02d'%i]=([x,y,top-20],[1.8,1.8,.4],0)
            expected['Bench_Move_%02d'%i]=([x,y,350+i%3*30],[1,1,1],0)
        for scenario,y in [('reuse',-1700),('author',1700)]:
            for i in range(12):expected['Bench_%s_Support_%02d'%(scenario,i)]=([i*210,y,10+i%3*10],[1.8,1.4,.4],5 if i%4==3 else 0)
        for i in range(256):expected['Bench_Background_%03d'%i]=([10000+i%16*240,10000+i//16*240,100],[1.2,1.2,2],0)
        found={a.get_actor_label():a for a in actors.get_all_level_actors() if a.get_actor_label().startswith('Bench')}
        assert set(found)==set(expected)
        for name,(p,s,pitch) in expected.items():
            a=found[name];v=a.get_actor_location();scale=a.get_actor_scale3d();rot=a.get_actor_rotation();r=unreal.Rotator(pitch,0,0)
            assert all(abs(x-y)<.001 for x,y in zip([v.x,v.y,v.z],p)),name
            assert all(abs(x-y)<.001 for x,y in zip([scale.x,scale.y,scale.z],s)),name
            assert all(abs(getattr(rot,k)-getattr(r,k))<.001 for k in ('pitch','yaw','roll')),name
            assert a.static_mesh_component.static_mesh.get_path_name()=='/Engine/BasicShapes/Cube.Cube'
        metrics=project.parent/'Saved/SpatialTwin/last_scan_metrics.json'
        record=dict(state='READY',pid=os.getpid(),project=str(project),route=spec['route'],native_plugin_loaded=spec['route']=='with',baseline_actors=len(expected),initial_scan=scanned,
                    seconds_in_bootstrap=time.perf_counter()-started,native_status=status,scan_sha256=hashlib.sha256(metrics.read_bytes()).hexdigest() if metrics.exists() else None,
                    dirty_content=[],dirty_maps=[])
        temporary=output.with_suffix('.tmp');temporary.write_text(json.dumps(record,indent=2)+'\n',encoding='utf-8');temporary.replace(output);finished=True;return False
    except Exception as error:
        temporary=output.with_suffix('.tmp');temporary.write_text(json.dumps(dict(state='FAILED',error=repr(error),pid=os.getpid()),indent=2)+'\n',encoding='utf-8');temporary.replace(output);finished=True;return False

handle=unreal.register_ticker_callback(tick,delay=.5)
