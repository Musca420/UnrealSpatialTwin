"""Unreal Python bootstrap for a disposable, game-independent benchmark map.

Launch only in the project created by prepare_benchmark.py, with its spec path
in SPATIAL_TWIN_BENCH_SPEC. Never opens or saves another project.
"""
import json
import os
from pathlib import Path
import time
import unreal

spec_path=Path(os.environ['SPATIAL_TWIN_BENCH_SPEC']).resolve()
spec=json.loads(spec_path.read_text(encoding='utf-8'))
project=Path(unreal.Paths.get_project_file_path()).resolve()
assert project==Path(spec['project']).resolve() and project.stem=='SpatialTwinBench'
assert spec.get('fixture_format')==1 and spec['map']=='/Game/Benchmark/Base'
receipt=project.parent/'setup-result.json'
assert not receipt.exists() and not unreal.EditorAssetLibrary.does_asset_exist(spec['map'])
started=time.perf_counter();world=unreal.EditorLoadingAndSavingUtils.new_blank_map(False)
actors=unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
cube=unreal.load_asset('/Engine/BasicShapes/Cube.Cube')
assert cube
def box(label,position,size,pitch=0):
    actor=actors.spawn_actor_from_class(unreal.StaticMeshActor,unreal.Vector(*position),unreal.Rotator(pitch,0,0))
    actor.set_actor_label(label);actor.static_mesh_component.set_static_mesh(cube)
    actor.set_actor_scale3d(unreal.Vector(*[v/100 for v in size]));return actor

targets=[]
for i in range(32):
    x=(i%8)*260;y=(i//8)*260;top=20+(i%5)*8
    box('Bench_BatchSupport_%02d'%i,[x,y,top-20],[180,180,40])
    a=box('Bench_Move_%02d'%i,[x,y,350+(i%3)*30],[100,100,100]);targets.append(a)
for scenario,y in (('reuse',-1700),('author',1700)):
    for i in range(12):
        x=i*210;top=30+(i%3)*10
        box('Bench_%s_Support_%02d'%(scenario,i),[x,y,top-20],[180,140,40],5 if i%4==3 else 0)
# Context outside the edited regions, still part of the indexed world.
for i in range(256):box('Bench_Background_%03d'%i,[10000+(i%16)*240,10000+(i//16)*240,100],[120,120,200])
sun=actors.spawn_actor_from_class(unreal.DirectionalLight,unreal.Vector(1000,0,2000),unreal.Rotator(-50,-30,0))
sun.light_component.set_editor_property('intensity',6)
sky=actors.spawn_actor_from_class(unreal.SkyLight,unreal.Vector(0,0,2000));sky.light_component.set_editor_property('intensity',1)
assert unreal.EditorLoadingAndSavingUtils.save_map(world,spec['map'])
assert not unreal.EditorLoadingAndSavingUtils.get_dirty_content_packages() and not unreal.EditorLoadingAndSavingUtils.get_dirty_map_packages()
tool=unreal.get_default_object(unreal.SpatialTwinToolset)
scan=json.loads(tool.call_method('spatial_twin_rebuild'));assert scan['ready'] and not scan.get('error'),scan
receipt.write_text(json.dumps(dict(state='READY',project=str(project),map=spec['map'],targets=[a.get_path_name() for a in targets],scan=scan,seconds=time.perf_counter()-started,pid=os.getpid()),indent=2),encoding='utf-8')
unreal.log('SPATIAL_TWIN_BENCH_READY '+str(receipt))
