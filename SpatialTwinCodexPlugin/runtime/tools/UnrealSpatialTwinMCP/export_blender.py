"""Blender background adapter: export selected authored mesh + UCX to FBX/STG1."""
import argparse
import json
from pathlib import Path
import struct
import sys
import bpy


def export(mesh, collision, out, target_path):
    out=Path(out).resolve();out.mkdir(parents=True,exist_ok=True)
    objects=[mesh,*collision]
    if not collision or any(o.type!='MESH' for o in objects):raise ValueError('Supply mesh and explicit UCX convex bodies')
    if any(not o.name.startswith('UCX_'+mesh.name+'_') for o in collision):raise ValueError('Collision names must be UCX_<mesh>_<index>')
    def binary(obj,name):
        evaluated=obj.evaluated_get(bpy.context.evaluated_depsgraph_get());data=evaluated.to_mesh()
        try:
            data.calc_loop_triangles()
            vertices=[evaluated.matrix_world @ v.co for v in data.vertices]
            # Native FBX importer uses -Y forward/Z up, then changes RH to LH.
            points=[(p.x*100,-p.y*100,p.z*100) for p in vertices]
            triangles=[tuple(reversed(t.vertices)) for t in data.loop_triangles]
            blob=b'STG1'+struct.pack('<III',1,len(points),len(triangles))
            blob+=b''.join(struct.pack('<3d',*p) for p in points)
            blob+=b''.join(struct.pack('<3I',*t) for t in triangles)
            (out/name).write_bytes(blob)
        finally:evaluated.to_mesh_clear()
        return name
    if bpy.context.scene.unit_settings.scale_length!=1:raise ValueError('This exporter expects Blender metre scale_length=1')
    geometries=[binary(o,o.name+'.stg') for o in objects]
    bpy.ops.object.select_all(action='DESELECT')
    for o in objects:o.select_set(True)
    bpy.context.view_layer.objects.active=mesh
    fbx=out/(mesh.name+'.fbx')
    bpy.ops.export_scene.fbx(filepath=str(fbx),use_selection=True,object_types={'MESH'},bake_anim=False,
                             axis_forward='-Y',axis_up='Z',apply_unit_scale=True,use_mesh_modifiers=True,mesh_smooth_type='FACE')
    manifest={'schema_version':1,'units':'cm','coordinates':'Unreal_LH_Zup','target_path':target_path,
              'source_file':fbx.name,'geometry_file':geometries[0],'collision_files':geometries[1:],
              'blender_version':bpy.app.version_string,'source_blend':bpy.data.filepath,
              'blender_to_unreal_matrix':[[100,0,0,0],[0,-100,0,0],[0,0,100,0],[0,0,0,1]],
              'material_slots':[slot.name for slot in mesh.material_slots],
              'import_contract':'Native bounds/collision parity must be checked before canonical rebinding'}
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    return manifest


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--blend',required=True);parser.add_argument('--mesh',required=True)
    parser.add_argument('--out',required=True);parser.add_argument('--target',required=True)
    args=parser.parse_args(sys.argv[sys.argv.index('--')+1:])
    bpy.ops.wm.open_mainfile(filepath=str(Path(args.blend).resolve()))
    mesh=bpy.data.objects[args.mesh]
    export(mesh,[o for o in bpy.context.scene.objects if o.name.startswith('UCX_'+args.mesh+'_')],args.out,args.target)
