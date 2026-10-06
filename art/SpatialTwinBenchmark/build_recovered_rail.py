"""Editable recovered-metal roof safety rail, authored through direct bpy/MCP."""
from pathlib import Path
import sys
import bpy

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'Tools/UnrealSpatialTwinMCP'))
from export_blender import export

def build(name,folder,target):
    assert bpy.context.scene.name.startswith('SpatialTwinBenchmark')
    folder=Path(folder).resolve()
    assert folder.is_relative_to(ROOT/'art/SpatialTwinBenchmark') or folder.is_relative_to(ROOT/'Build/SpatialTwinBench')
    assert name.startswith('SM_ST_Rail_') and not (folder/'manifest.json').exists()
    scene=bpy.context.scene
    scene.unit_settings.system='METRIC';scene.unit_settings.scale_length=1
    old=bpy.data.collections.get('ST_BenchmarkAuthoring')
    if old:
        for obj in list(old.objects):bpy.data.objects.remove(obj,do_unlink=True)
        bpy.data.collections.remove(old)
    collection=bpy.data.collections.new('ST_BenchmarkAuthoring');scene.collection.children.link(collection)
    materials=[]
    for label,color in [('RecoveredMetal',(.17,.20,.22,1)),('SafetyAmber',(.65,.36,.055,1))]:
        mat=bpy.data.materials.get(label) or bpy.data.materials.new(label)
        node=next(n for n in mat.node_tree.nodes if n.type=='BSDF_PRINCIPLED')
        node.inputs['Base Color'].default_value=color
        node.inputs['Metallic'].default_value=.65 if label=='RecoveredMetal' else .2
        node.inputs['Roughness'].default_value=.72
        mat.diffuse_color=color;materials.append(mat)
    parts=[];collision=[]
    specs=[((-.68,0,.5),(.06,.06,1),0),((.68,0,.5),(.06,.06,1),0),
           ((0,0,.96),(1.42,.06,.06),1),((0,0,.52),(1.42,.05,.05),0),
           ((-.68,0,.025),(.16,.18,.05),0),((.68,0,.025),(.16,.18,.05),0)]
    for i,(center,size,slot) in enumerate(specs):
        bpy.ops.mesh.primitive_cube_add(size=1,location=center)
        obj=bpy.context.object;obj.dimensions=size
        bpy.ops.object.transform_apply(location=False,rotation=False,scale=True)
        for c in list(obj.users_collection):c.objects.unlink(obj)
        collection.objects.link(obj);obj.data.materials.append(materials[slot]);parts.append(obj)
        body=obj.copy();body.data=obj.data.copy();body.name=f'UCX_{name}_{i:02d}';collection.objects.link(body);collision.append(body)
    bpy.ops.object.select_all(action='DESELECT')
    for obj in parts:obj.select_set(True)
    bpy.context.view_layer.objects.active=parts[0];bpy.ops.object.join()
    rail=bpy.context.object;rail.name=name
    scene.cursor.location=(0,0,0);bpy.ops.object.origin_set(type='ORIGIN_CURSOR')
    folder.mkdir(parents=True,exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(folder/(name+'.blend')))
    manifest=export(rail,collision,folder,target)
    assert len(manifest['collision_files'])==6
    return {'name':name,'manifest':str(folder/'manifest.json'),'bounds_cm':manifest['bounds'] if 'bounds' in manifest else None,
            'convex_bodies':6,'blender_version':bpy.app.version_string}
