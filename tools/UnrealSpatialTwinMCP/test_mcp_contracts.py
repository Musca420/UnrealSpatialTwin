import tempfile
import unittest
from pydantic import TypeAdapter, ValidationError
from spatial_twin.contracts import PatchOperation
from spatial_twin.server import create_server
from spatial_twin.store import Store, OPERATIONS, check_operations


class MCPContractsTest(unittest.IsolatedAsyncioTestCase):
    async def test_advertised_outputs_preserve_projected_source_and_ground_proof(self):
        import json
        from test_ground import GroundTests
        fixture=GroundTests();fixture.setUp()
        try:
            server=create_server(fixture.f.store)
            tools={t.name:t for t in await server.list_tools()}
            description=server._tool_manager.get_tool('tool_describe').fn(['world_search','patch_ground'])['tools']
            for item in description:self.assertEqual(item['outputSchema'],tools[item['name']].outputSchema)
            envelope=tools['world_read'].outputSchema['$defs']
            self.assertIn('tool_schemas',envelope['ReadBatch']['properties'])
            self.assertEqual(envelope['ReadBatch']['properties']['tool_schemas']['type'],'array')
            self.assertIn('next_query',envelope['ReadResult']['properties'])
            validation=tools['patch_ground'].outputSchema['$defs']['PatchValidation']
            self.assertTrue({'valid','base_revision','errors_count','new_collisions_count','errors','navigation'}<=set(validation['required']))
            batch_args={'queries':[{'tool':'world_search','arguments':{'kind':'Actor','limit':1,'fields':['bounds']}}],'schemas':['patch_ground']}
            expected=server._tool_manager.get_tool('world_read').fn(**batch_args)
            content,structured=await server.call_tool('world_read',batch_args)
            self.assertEqual(structured,expected);self.assertEqual(json.loads(content[0].text),expected)
            self.assertEqual(structured['tool_schemas'][0]['name'],'patch_ground')
            self.assertNotIn('schemas',structured)
            if structured['results'][0]['data']['cursor']:
                self.assertIn('next_query',structured['results'][0])
                self.assertNotIn('next_query',structured['results'][0]['data'])
            args={'kind':'Actor','fields':['bounds','transform','label']}
            direct=server._tool_manager.get_tool('world_search').fn(**args)
            content,structured=await server.call_tool('world_search',args)
            self.assertEqual(structured,direct);self.assertEqual(json.loads(content[0].text),direct)
            ground=server._tool_manager.get_tool('patch_ground').fn
            for distance in (10,.5):
                params={'entity_ids':['a'],'base_revision':1,'max_distance':distance,'include_operations':True}
                content,structured=await server.call_tool('patch_ground',params)
                self.assertEqual(json.loads(content[0].text),structured)
                self.assertEqual(structured['status'],'VALIDATED' if distance==10 else 'BLOCKED')
                if distance==10:
                    self.assertTrue(structured['validation']['valid'])
                    self.assertEqual(structured['validation']['errors_count'],0)
                    self.assertEqual(structured['validation']['new_collisions_count'],0)
                    self.assertNotIn('errors',structured)
                    self.assertEqual(structured['continuous_support']['state'],'PROVEN')
                    self.assertEqual(structured['accepted_count'],1)
                    self.assertEqual(structured['rejected'],[])
                    self.assertEqual(fixture.f.store.patch_get(structured['id'])['operations'],structured['operations'])
                    for invented in ('state','patch_id','rejected_count','uprightness'):
                        self.assertNotIn(invented,structured)
                    self.assertEqual(structured['operations'][0]['value'],ground(['a'],1,10,include_operations=True)['operations'][0]['value'])
                else:self.assertNotIn('id',structured)
            self.assertEqual(fixture.f.store.status(False)['canonical_revision'],1)
            for malformed in ({'world_search':{'kind':'Actor'}},
                              {'queries':[{'world_search':{'kind':'Actor'}}]},
                              {'queries':[{'tool':'world_search','arguments':{},'invented':True}]}):
                with self.assertRaises(Exception):await server.call_tool('world_read',malformed)
            reads=tools['world_read'].inputSchema
            self.assertIn('queries',reads['required'])
            self.assertEqual(reads['$defs']['ReadQuery']['required'],['tool'])
            unknown=Store.entity(fixture.f.db,'a');unknown['bounds']=None
            fixture.f.put(unknown);fixture.f.db.commit()
            _,structured=await server.call_tool('world_search',args)
            self.assertEqual(structured,server._tool_manager.get_tool('world_search').fn(**args))
            self.assertIsNone(next(e for e in structured['entities'] if e['id']=='a')['bounds'])
        finally:fixture.tearDown()

    async def test_focused_shadow_route_preserves_validation_and_map_scope(self):
        import json
        from test_spatial_twin import TwinTest
        fixture=TwinTest();fixture.setUp()
        def data(result):return result[1] if isinstance(result,tuple) else json.loads(result[0].text)
        try:
            server=create_server(fixture.store,tool_profile='focused')
            self.assertEqual({t.name for t in await server.list_tools()},{'world_read','shadow_plan','tool_describe'})
            route=server._tool_manager.get_tool('shadow_plan').fn
            args={'operations':[{'type':'MOVE_ACTOR','target':'a','value':[10,0,0]}],'base_revision':1}
            for tool,arguments,map_id in [('patch_prepare',args,'/Wrong'),('patch_prepare',{**args,'unexpected':True},'/Fixture'),('patch_prepare',{**args,'base_revision':True},'/Fixture'),('spawn_actor',{},'/Fixture')]:
                with self.assertRaises(Exception):route(tool,arguments,map_id)
                self.assertFalse((fixture.root/'patches.sqlite').exists())
            described=data(await server.call_tool('world_read',{'queries':[{'tool':'world_status','arguments':{}}],'schemas':['patch_prepare']}))
            self.assertEqual(described['tool_schemas'][0]['name'],'patch_prepare')
            result=data(await server.call_tool('shadow_plan',{'tool':'patch_prepare','arguments':args,'map_id':'/Fixture'}))
            self.assertEqual(result['status'],'VALIDATED')
            status=route('patch_status',{'patch_id':result['id']},'/Fixture')
            self.assertEqual(status['id'],result['id'])
            self.assertEqual(fixture.store.status(False)['canonical_revision'],1)
        finally:fixture.tearDown()

    async def test_workflow_profile_preserves_registered_schemas_and_validated_reads(self):
        import json
        from test_spatial_twin import TwinTest
        from spatial_twin.assist import READ_TOOLS
        fixture=TwinTest();fixture.setUp()
        def data(result):return result[1] if isinstance(result,tuple) else json.loads(result[0].text)
        try:
            full=create_server(fixture.store)
            workflow=create_server(fixture.store,tool_profile='workflow')
            schemas={t.name:t.inputSchema for t in await full.list_tools()}
            advertised={t.name:t.inputSchema for t in await workflow.list_tools()}
            self.assertEqual(set(advertised),{'world_status','world_read','world_query','tool_describe',
                             'asset_stage','patch_prepare','patch_place','patch_ground','patch_preview','patch_status'})
            self.assertTrue(all(schema==schemas[name] for name,schema in advertised.items()))
            self.assertTrue(all(workflow._tool_manager.get_tool(name) for name in READ_TOOLS))
            description=data(await workflow.call_tool('tool_describe',{'names':['entity_get','spatial_overlap']}))
            for item in description['tools']:self.assertEqual(item['inputSchema'],schemas[item['name']])
            batch=data(await workflow.call_tool('world_read',{'queries':[
                {'tool':'entity_get','arguments':{'entity_id':'a','fields':['transform']}},
                {'tool':'world_maps','arguments':{'limit':2}}]}))
            direct=data(await full.call_tool('entity_get',{'entity_id':'a','fields':['transform']}))
            self.assertEqual(batch['state'],'READY')
            self.assertEqual(batch['results'][0]['data'],direct)
            self.assertEqual(fixture.store.status(False)['canonical_revision'],1)
            for names in ([],['unknown'],['entity_get','entity_get'],['entity_get']*9):
                with self.assertRaises(Exception):await workflow.call_tool('tool_describe',{'names':names})
            with self.assertRaises(Exception):
                await workflow.call_tool('world_read',{'queries':[{'tool':'entity_get','arguments':{'entity_id':True}}]})
            with self.assertRaises(ValueError):create_server(fixture.store,tool_profile='invented')
        finally:fixture.tearDown()

    async def test_cached_content_addressed_mesh_refuses_replacement_and_deletion(self):
        import hashlib,struct
        from pathlib import Path
        from spatial_twin import geometry as g
        with tempfile.TemporaryDirectory() as root:
            data=b'STG1'+struct.pack('<III',1,3,1)+struct.pack('<9d',0,0,0,1,0,0,0,1,0)+struct.pack('<III',0,1,2)
            path=Path(root)/(hashlib.sha1(data).hexdigest()+'.stg');path.write_bytes(data)
            first=g.load_mesh(path)
            self.assertIs(g.load_mesh(path),first)
            changed=bytearray(data);struct.pack_into('<d',changed,16,5)
            path.write_bytes(changed)
            with self.assertRaisesRegex(ValueError,'content hash mismatch'):g.load_mesh(path)
            path.write_bytes(data)
            self.assertEqual(g.load_mesh(path).vertices,first.vertices)
            path.unlink()
            with self.assertRaisesRegex(ValueError,'Geometry unavailable'):g.load_mesh(path)
            g.load_mesh.cache_clear()

    async def test_placement_handles_rotated_negative_scale_and_blocks_unknown_support(self):
        import json,struct,math
        from test_spatial_twin import TwinTest,IDENTITY
        from spatial_twin.store import encode
        fixture=TwinTest();fixture.setUp()
        try:
            mesh={'id':'placement-mesh','kind':'StaticMesh','local_bounds':[[-1,-2,-4],[1,2,6]],
                  'native_spawn_collision':{'enabled':False},'native_spawn_affects_navigation':False}
            fixture.put(mesh)
            fixture.db.execute('INSERT INTO class_schemas VALUES(?,?)',('/Script/Engine.StaticMeshActor',encode({'properties':{}})))
            vertices=[[-100,-100,0],[100,-100,0],[100,100,0],[-100,100,0]]
            data=b'STG1'+struct.pack('<III',1,4,2)+b''.join(struct.pack('<3d',*p) for p in vertices)+struct.pack('<6I',0,1,2,0,2,3)
            path=fixture.root/'geometry/placement-floor.stg';path.write_bytes(data)
            fixture.db.execute('INSERT INTO geometry VALUES(?,?,?)',('placement-floor','geometry/placement-floor.stg',encode({'vertices':4,'triangles':2})))
            fixture.put({'id':'floor-a','kind':'Actor','transform':IDENTITY,'bounds':[[-100,-100,0],[100,100,0]]})
            fixture.put({'id':'floor-c','kind':'Component','actor_id':'floor-a','parent_id':'floor-a',
                'transform':{**IDENTITY,'scale':[1,-1,1]},'bounds':[[-100,-100,0],[100,100,0]],'collision':{
                    'enabled':True,'simple':['placement-floor'],'complex':['placement-floor'],
                    'complex_coverage':'AVAILABLE','simple_coverage':'AVAILABLE','responses':{'Visibility':'Block'}}})
            fixture.db.commit();server=create_server(fixture.store)
            args={'candidates':[[10,0,30],[1000,0,30]],'asset_id':mesh['id'],
                  'actor_class':'/Script/Engine.StaticMeshActor','base_revision':1,
                  'max_distance':100,'label_prefix':'Placement_',
                  'rotation':[0,0,math.sqrt(.5),math.sqrt(.5)],'scale':[-2,1,2]}
            result=await server.call_tool('patch_place',args)
            result=json.loads(result[0].text)
            self.assertEqual(result['status'],'VALIDATED')
            self.assertEqual(result['accepted'][0]['index'],0)
            self.assertEqual(result['accepted'][0]['position'],[10,0,8.2])
            self.assertEqual(result['accepted'][0]['continuous_support']['state'],'PROVEN')
            self.assertEqual(result['accepted'][0]['continuous_support']['primitive'],'triangle_face')
            self.assertEqual(result['accepted'][0]['continuous_support']['normal'],[0,0,1])
            self.assertEqual(result['accepted'][0]['continuous_support']['triangle'],0)
            across=await server.call_tool('spatial_support',{'candidates':[[0,0,30]],
                'offsets':[[-5,-5],[-5,5],[5,-5],[5,5]],'max_distance':100})
            self.assertEqual(json.loads(across[0].text)['accepted'][0]['continuous_support']['state'],'UNPROVEN')
            self.assertEqual(result['rejected'],[{'index':1,'reason':'missing_support'}])
            patch=fixture.store.patch_get(result['id']);operation=patch['operations'][0]
            self.assertEqual(operation['transform']['rotation'],args['rotation'])
            self.assertEqual(operation['transform']['scale'],args['scale'])
            self.assertNotIn('component_properties',operation)
            self.assertEqual(fixture.store.status(False)['canonical_revision'],1)
            self.assertNotIn('operations',result)
            resized=await server.call_tool('patch_place',{**args,'candidates':[[10,0,30]],'size_cm':[4,8,10], 'include_operations':True})
            resized=json.loads(resized[0].text)
            self.assertEqual(resized['status'],'VALIDATED')
            resized_operation=fixture.store.patch_get(resized['id'])['operations'][0]
            self.assertEqual(resized['operations'],fixture.store.patch_get(resized['id'])['operations'])
            self.assertEqual(resized_operation['transform']['scale'],[-2,2,1])
            self.assertEqual(resized_operation['transform']['position'],[10,0,4.2])
            with self.assertRaises(Exception):
                await server.call_tool('patch_place',{**args,'size_cm':[0,1,1]})
            path.unlink()
            blocked=await server.call_tool('patch_place',{**args,'candidates':[[10,0,30]]})
            blocked=json.loads(blocked[0].text)
            self.assertEqual(blocked['status'],'BLOCKED')
            self.assertNotIn('id',blocked)
            self.assertFalse(blocked['support']['complete'])
            with self.assertRaises(Exception):
                await server.call_tool('patch_place',{**args,'base_revision':2})
            with self.assertRaises(Exception):
                await server.call_tool('patch_place',{**args,'rotation':[0,0,0,0]})
            with fixture.store.patches() as db:
                self.assertEqual(db.execute('SELECT count(*) FROM patches').fetchone()[0],2)
        finally:fixture.tearDown()

    async def test_compact_catalog_keeps_typed_prepare_and_legacy_call_compatibility(self):
        from test_spatial_twin import TwinTest
        fixture=TwinTest();fixture.setUp()
        try:
            full=create_server(fixture.store)
            compact=create_server(fixture.store,compact_tools=True)
            all_tools={t.name:t.inputSchema for t in await full.list_tools()}
            compact_tools={t.name:t.inputSchema for t in await compact.list_tools()}
            self.assertEqual(set(all_tools)-set(compact_tools),{'patch_create','patch_update','patch_validate'})
            self.assertEqual(compact_tools['patch_prepare'],all_tools['patch_prepare'])
            self.assertEqual(len(compact_tools),32)
            result=await compact.call_tool('patch_create',{'base_revision':1,'operations':[
                {'type':'MOVE_ACTOR','target':'a','value':[10,0,0]}]})
            import json
            data=result[1] if isinstance(result,tuple) else json.loads(result[0].text)
            self.assertEqual(data['status'],'DRAFT')
            self.assertEqual(fixture.store.status(False)['canonical_revision'],1)
        finally:fixture.tearDown()

    async def test_transport_json_preserves_structured_data_and_error_contract(self):
        import json
        from test_spatial_twin import TwinTest
        fixture=TwinTest();fixture.setUp()
        try:
            server=create_server(fixture.store)
            result=await server.call_tool('world_read',{'queries':[
                {'tool':'entity_get','arguments':{'entity_id':'a','fields':['transform','bounds']}},
                {'tool':'spatial_raycast_batch','arguments':{'rays':[
                    {'origin':[-2,0,0],'direction':[1,0,0],'max_distance':4}]}}]})
            if isinstance(result,tuple):content,structured=result
            else:
                content=result
                structured=server._tool_manager.get_tool('world_read').fn([
                    {'tool':'entity_get','arguments':{'entity_id':'a','fields':['transform','bounds']}},
                    {'tool':'spatial_raycast_batch','arguments':{'rays':[
                        {'origin':[-2,0,0],'direction':[1,0,0],'max_distance':4}]}}])
            self.assertEqual(len(content),1)
            self.assertEqual(json.loads(content[0].text),structured)
            self.assertNotIn('\n',content[0].text)
            self.assertLess(len(content[0].text),len(json.dumps(structured,indent=2)))
            with self.assertRaisesRegex(Exception,'Entity not found'):
                await server.call_tool('entity_get',{'entity_id':'missing'})
            self.assertEqual(fixture.store.status(False)['canonical_revision'],1)
        finally:fixture.tearDown()

    async def test_batch_uses_one_snapshot_and_discards_concurrent_writer_results(self):
        from unittest.mock import patch
        from test_spatial_twin import TwinTest
        fixture=TwinTest();fixture.setUp()
        try:
            fixture.db.execute('PRAGMA journal_mode=WAL')
            server=create_server(fixture.store)
            revisions=[];original=Store.entity
            def entity(db,ident):
                revisions.append(Store.revision(db))
                value=original(db,ident)
                if len(revisions)==1:
                    fixture.db.execute("UPDATE metadata SET value='2' WHERE key='world_revision'")
                    fixture.db.commit()
                return value
            with patch.object(Store,'entity',side_effect=entity):
                result=server._tool_manager.get_tool('world_read').fn([
                    {'tool':'entity_get','arguments':{'entity_id':ident}}
                    for ident in ('a','b')])
            self.assertEqual(revisions,[1,1])
            self.assertEqual(result['state'],'CONFLICTED')
            self.assertEqual(result['status']['canonical_revision'],2)
            self.assertNotIn('results',result)
            self.assertEqual(fixture.store.status(False)['canonical_revision'],2)
        finally:fixture.tearDown()

    async def test_snapshot_releases_on_error_and_isolated_thread_never_borrows_connection(self):
        from concurrent.futures import ThreadPoolExecutor
        from test_spatial_twin import TwinTest
        fixture=TwinTest();fixture.setUp()
        try:
            with fixture.store.read_snapshot() as outer:
                with fixture.store.read_snapshot() as nested:
                    self.assertIs(outer,nested)
                self.assertEqual(Store.revision(outer),1)
                def other_thread():
                    with fixture.store.read() as separate:
                        return separate is outer,Store.revision(separate)
                with ThreadPoolExecutor(max_workers=1) as worker:
                    self.assertEqual(worker.submit(other_thread).result(),(False,1))
                with self.assertRaisesRegex(RuntimeError,'cancelled'):
                    with fixture.store.read_snapshot():raise RuntimeError('cancelled')
                self.assertEqual(Store.revision(outer),1)
            server=create_server(fixture.store)
            with self.assertRaisesRegex(ValueError,'Entity not found'):
                server._tool_manager.get_tool('world_read').fn([
                    {'tool':'entity_get','arguments':{'entity_id':'missing'}}])
            self.assertIsNone(fixture.store._snapshot_reader.get())
            self.assertEqual(fixture.store.status(False)['canonical_revision'],1)
        finally:fixture.tearDown()

    async def test_support_footprints_expose_two_coordinates_and_fail_before_io(self):
        from pathlib import Path
        from spatial_twin.assist import prepare
        with tempfile.TemporaryDirectory() as root:
            server=create_server(Store(root));schemas={t.name:t.inputSchema for t in await server.list_tools()}
            props=schemas['spatial_support']['properties']
            self.assertEqual(props['offsets']['items']['minItems'],2)
            self.assertEqual(props['offsets']['items']['maxItems'],2)
            self.assertEqual(props['candidates']['items']['minItems'],3)
            args={'candidates':[[0,0,100]],'offsets':[[0,0]],'max_distance':200}
            prepare(server,[{'tool':'spatial_support','arguments':args}])
            for change in ({'offsets':[[0,0,0]]},{'offsets':[[True,0]]},{'candidates':[[0,0]]}):
                with self.assertRaises(ValidationError):prepare(server,[{'tool':'spatial_support','arguments':{**args,**change}}])
                with self.assertRaises(Exception):await server.call_tool('spatial_support',{**args,**change})
            self.assertEqual(list(Path(root).iterdir()),[])

    async def test_batch_and_direct_pagination_share_numeric_normalization(self):
        from test_spatial_twin import TwinTest
        fixture=TwinTest();fixture.setUp()
        try:
            server=create_server(fixture.store)
            async def call(name,args):
                result=await server.call_tool(name,args)
                import json
                return result[1] if isinstance(result,tuple) else json.loads(result[0].text)
            for name,args in (
                ('world_region',{'center':[0,0,0],'radius':100,'detail':'entities'}),
                ('spatial_overlap',{'aabb':[[-100,-100,-100],[100,100,100]]}),
            ):
                args={**args,'kind':'Actor','fields':['id','label'],'limit':1}
                batch=await call('world_read',{'queries':[{'tool':name,'arguments':args}]})
                first=batch['results'][0]['data'];self.assertTrue(first['cursor'])
                second=await call(name,{**args,'cursor':first['cursor']})
                self.assertEqual([e['id'] for e in first['entities']+second['entities']],['a','b'])
                first=await call(name,args)
                batch=await call('world_read',{'queries':[{'tool':name,'arguments':{**args,'cursor':first['cursor']}}]})
                self.assertEqual(batch['results'][0]['data']['entities'][0]['id'],'b')
                changed={**args,'fields':['id']}
                with self.assertRaises(Exception):await call(name,{**changed,'cursor':first['cursor']})
            first=await call('world_region',args={'center':[0,0,0],'radius':100,'detail':'entities','kind':'Actor','limit':1})
            fixture.db.execute("UPDATE metadata SET value='2' WHERE key='world_revision'");fixture.db.commit()
            with self.assertRaises(Exception):
                await call('world_region',{'center':[0,0,0],'radius':100,'detail':'entities','kind':'Actor','limit':1,'cursor':first['cursor']})
        finally:fixture.tearDown()

    async def test_ray_modes_fields_and_vectors_are_exposed_and_rejected_before_io(self):
        from pathlib import Path
        from spatial_twin.assist import prepare
        with tempfile.TemporaryDirectory() as root:
            server=create_server(Store(root))
            schemas={t.name:t.inputSchema for t in await server.list_tools()}
            props=schemas['spatial_raycast_batch']['properties']
            self.assertEqual(props['detail']['enum'],['hits','distances'])
            self.assertEqual(props['world']['enum'],['canonical','shadow'])
            self.assertEqual(schemas['patch_preview']['properties']['detail']['enum'],['entities','operations','collisions'])
            allowed=props['fields']['anyOf'][0]['items']['enum']
            self.assertIn('actor_id',allowed);self.assertNotIn('entity_id',allowed)
            ray=schemas['spatial_raycast_batch']['$defs']['RayRequest']
            self.assertEqual(set(ray['required']),{'origin','direction','max_distance'})
            self.assertFalse(ray['additionalProperties'])
            args={'rays':[{'origin':[0,0,10],'direction':[0,0,-1],'max_distance':20}]}
            prepare(server,[{'tool':'spatial_raycast_batch','arguments':args}])
            for change in ({'detail':'entities'},{'fields':['entity_id']},{'world':'preview'},
                           {'rays':[{'origin':[False,0,10],'direction':[0,0,-1],'max_distance':20}]},
                           {'rays':[{**args['rays'][0],'extra':1}]}):
                with self.assertRaises(ValidationError):
                    prepare(server,[{'tool':'spatial_raycast_batch','arguments':{**args,**change}}])
                with self.assertRaises(Exception):
                    await server.call_tool('spatial_raycast_batch',{**args,**change})
            self.assertEqual(list(Path(root).iterdir()),[])
            with self.assertRaises(Exception):
                await server.call_tool('patch_preview',{'patch_id':'not-read','detail':'compact'})
            self.assertEqual(list(Path(root).iterdir()),[])

    async def test_actual_tool_schemas_expose_operation_and_direction_contracts(self):
        with tempfile.TemporaryDirectory() as root:
            server=create_server(Store(root))
            tools={t.name:t.inputSchema for t in await server.list_tools()}
            for name in ('patch_prepare','patch_create','patch_update'):
                definition=tools[name]['$defs']['PatchOperation']
                self.assertEqual(set(definition['properties']['type']['enum']),OPERATIONS)
                self.assertEqual(set(definition['required']),{'type','target'})
                self.assertIn('class',definition['properties'])
                self.assertIn('asset',definition['properties'])
                self.assertFalse(definition['additionalProperties'])
            self.assertEqual(tools['entity_relationships']['properties']['direction']['enum'],['both','incoming','outgoing'])

    async def test_contract_rejects_guessed_dialects_and_keeps_real_operations(self):
        adapter=TypeAdapter(PatchOperation)
        create={'type':'CREATE_ACTOR','target':'new:cube','class':'/Script/Engine.StaticMeshActor','asset':'asset:/Game/Cube.Cube',
                'transform':{'position':[0,0,50.2],'rotation':[0,0,0,1],'scale':[1,1,1]}}
        accepted=adapter.validate_python(create)
        self.assertEqual(accepted,create)
        self.assertEqual(check_operations([accepted])[0]['type'],'CREATE_ACTOR')
        for change in ({'type':'spawn_actor'},{'op':'spawn'},{'class_path':'Wrong'},
                       {'transform':{**create['transform'],'position':[True,0,0]}},
                       {'transform':{**create['transform'],'rotation':[0,0,0]}}):
            with self.assertRaises(ValidationError):adapter.validate_python({**create,**change})
        # An invalid call through the real MCP boundary must fail before touching
        # either database, not silently reinterpret an operation.
        with tempfile.TemporaryDirectory() as root:
            from pathlib import Path
            server=create_server(Store(root))
            with self.assertRaises(Exception):
                await server.call_tool('patch_create',{'operations':[{'op':'spawn','target':'new:cube'}]})
            self.assertEqual(list(Path(root).iterdir()),[])

    async def test_advertised_spatial_values_reject_coordinate_objects_without_restricting_properties(self):
        import jsonschema
        with tempfile.TemporaryDirectory() as root:
            server=create_server(Store(root))
            schemas={t.name:t.inputSchema for t in await server.list_tools()}
            for name in ('patch_prepare','patch_create','patch_update'):
                schema=schemas[name];jsonschema.Draft202012Validator.check_schema(schema)
                for operation,size in (('MOVE_ACTOR',3),('SCALE_ACTOR',3),('ROTATE_ACTOR',4)):
                    args={'operations':[{'type':operation,'target':'actor:observed','value':[0,0,0,1] if size==4 else [1.0]*size}]}
                    if name=='patch_prepare':args['base_revision']=1
                    if name=='patch_update':args['patch_id']='observed-patch'
                    jsonschema.validate(args,schema)
                    for value in ({'x':0,'y':1,'z':2},[0]*(size-1),[0]*(size+1),[0]*(size-1)+['guessed']):
                        with self.assertRaises(jsonschema.ValidationError):
                            jsonschema.validate({**args,'operations':[{'type':operation,'target':'actor:observed','value':value}]},schema)
                args={'operations':[{'type':'SET_PROPERTY','target':'actor:observed','property':'ObservedStruct','value':{'x':0,'nested':[True,'text']}}]}
                if name=='patch_prepare':args['base_revision']=1
                if name=='patch_update':args['patch_id']='observed-patch'
                jsonschema.validate(args,schema)
            self.assertEqual(list(__import__('pathlib').Path(root).iterdir()),[])


if __name__=='__main__':unittest.main()
