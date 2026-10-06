import unittest
import json
import struct
from place import check_placement,prepare_placement
import test_spatial_twin as fixtures


class PlacementActionTests(unittest.TestCase):
    def test_authored_placement_composes_existing_stage_with_identical_checks(self):
        from spatial_twin.server import create_server
        from spatial_twin.store import Store,encode
        from spatial_twin.staging import stage
        f=fixtures.TwinTest();f.setUp()
        try:
            folder=f.root/'author';folder.mkdir()
            vertices=[[-.5,-.5,0],[.5,-.5,0],[0,.5,0],[0,0,1]]
            triangles=[[0,2,1],[0,1,3],[1,2,3],[2,0,3]]
            (folder/'mesh.stg').write_bytes(b'STG1'+struct.pack('<III',1,4,4)+b''.join(struct.pack('<3d',*p) for p in vertices)+b''.join(struct.pack('<3I',*t) for t in triangles))
            (folder/'source.fbx').write_bytes(b'fixture; native FBX import tested separately')
            manifest=folder/'manifest.json'
            manifest.write_text(json.dumps(dict(schema_version=1,units='cm',coordinates='Unreal_LH_Zup',target_path='/Game/Probe/SM_Probe.SM_Probe',source_file='source.fbx',geometry_file='mesh.stg',collision_files=['mesh.stg'])))
            f.put(dict(id='asset:template',kind='StaticMesh',native_spawn_collision={'enabled':True},native_spawn_affects_navigation=False))
            component=Store.entity(f.db,'c');component['collision']={'enabled':True,'shapes':[{'type':'box','extent':[2,2,1]}],'trace_mode':'UseSimpleAsComplex'}
            f.put(component)
            f.db.execute('INSERT INTO class_schemas VALUES(?,?)',('/Script/Engine.StaticMeshActor',encode({'properties':{}})));f.db.commit()
            arguments=dict(candidates=[[0,0,5]],asset_id='asset:template',actor_class='/Script/Engine.StaticMeshActor',base_revision=1,max_distance=10,label_prefix='Probe_')
            with self.assertRaisesRegex(ValueError,'CONFLICTED'):
                prepare_placement(f.store,{**arguments,'manifest_file':str(manifest),'base_revision':0})
            self.assertFalse((f.root/'patches.sqlite').exists())
            composed=prepare_placement(f.store,{**arguments,'manifest_file':str(manifest)})
            staged=stage(f.store,manifest,'asset:template')
            separate=prepare_placement(f.store,{**arguments,'asset_id':staged['id']})
            self.assertEqual(composed['accepted'],separate['accepted'])
            self.assertEqual(composed['validation'],separate['validation'])
            self.assertEqual(composed['authoring']['asset_id'],staged['id'])
            for operation in composed['operations']:
                self.assertEqual(operation['asset'],staged['id']);self.assertTrue(operation['operation_id'])
            self.assertEqual(f.store.status()['canonical_revision'],1)
            call=create_server(f.store)._tool_manager.get_tool('patch_place').fn
            with self.assertRaisesRegex(ValueError,'canonical StaticMesh'):
                call(**{**arguments,'asset_id':staged['id'],'manifest_file':str(manifest)})
            blocked=call(**{**arguments,'candidates':[[100,0,5]],'manifest_file':str(manifest)})
            self.assertEqual(blocked['status'],'BLOCKED')
            self.assertEqual(f.store.status()['canonical_revision'],1)
        finally:f.tearDown()

    def valid(self):
        return {'status':'VALIDATED','validation':{'valid':True,'errors_count':0,'new_collisions_count':0},
                'operation_count':1,'accepted':[{'continuous_support':{'state':'PROVEN','normal':[0,0,1]}}],'rejected':[]}

    def test_unknown_support_collision_and_partial_group_cannot_apply(self):
        for key,value in [('status','INVALID'),('validation',{'valid':False}),('operation_count',2),('rejected',[{'index':1}])]:
            result=self.valid();result[key]=value
            with self.assertRaises(ValueError):check_placement(result)
        for field in ('errors_count','new_collisions_count'):
            result=self.valid();result['validation'][field]=1
            with self.assertRaises(ValueError):check_placement(result)
        result=self.valid();result['accepted'][0]['continuous_support']['state']='UNPROVEN'
        with self.assertRaises(ValueError):check_placement(result)

    def test_tilt_numeric_and_explicit_partial_policy(self):
        result=self.valid();self.assertIs(check_placement(result),result)
        result['accepted'][0]['continuous_support']['normal']=[.6,0,.8]
        with self.assertRaises(ValueError):check_placement(result)
        self.assertIs(check_placement(result,max_tilt_degrees=40),result)
        for normal in ([0,0,2],[0,0,float('nan')],[0,0,-1],[True,0,1]):
            result=self.valid();result['accepted'][0]['continuous_support']['normal']=normal
            with self.assertRaises(ValueError):check_placement(result)
        for value in (float('nan'),-1,90,True):
            with self.assertRaises(ValueError):check_placement(self.valid(),max_tilt_degrees=value)
        result=self.valid();result['rejected']=[{'index':1}]
        self.assertIs(check_placement(result,allow_rejections=True),result)

    def test_actual_tool_schema_rejects_unknown_input_without_shadow_write(self):
        fixture=fixtures.TwinTest();fixture.setUp()
        try:
            with self.assertRaises(TypeError):prepare_placement(fixture.store,{'unexpected':1})
            self.assertFalse((fixture.root/'patches.sqlite').exists())
        finally:fixture.tearDown()

    def test_manifest_route_refuses_no_save_before_staging_or_receipt(self):
        import asyncio
        from pathlib import Path
        from types import SimpleNamespace
        import tempfile
        from place import run
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);arguments=root/'arguments.json';output=root/'receipt.json'
            arguments.write_text(json.dumps({'manifest_file':'missing.json','asset_id':'asset:template'}))
            with self.assertRaisesRegex(ValueError,'scope-save'):
                asyncio.run(run(SimpleNamespace(arguments=arguments,output=output,camera=None,apply=True,no_save=True)))
            self.assertFalse(output.exists())


if __name__=='__main__':unittest.main(verbosity=2)
