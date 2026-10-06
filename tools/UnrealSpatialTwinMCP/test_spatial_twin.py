"""Run: Build/MCP/venv/Scripts/python.exe -m unittest discover -s tools/UnrealSpatialTwinMCP -v"""
import json
from pathlib import Path
import sqlite3
import struct
import tempfile
import unittest
from spatial_twin.store import Store, encode, check_operations
from spatial_twin.query import Queries
from spatial_twin.shadow import preview, validate
from spatial_twin.server import create_server,patch_summary
from spatial_twin import geometry as g
from spatial_twin.navigation import Navigation


IDENTITY={'position':[0,0,0],'rotation':[0,0,0,1],'scale':[1,1,1]}


class TwinTest(unittest.TestCase):
    def test_status_distinguishes_legacy_nested_container_coverage(self):
        self.assertEqual(self.store.status(False)['nested_container_state'],'LEGACY_UNVERIFIED')
        for version, expected in [('1','LEGACY_UNVERIFIED'),('2','CURRENT')]:
            self.db.execute("INSERT OR REPLACE INTO metadata VALUES('nested_container_export_version',?)",(version,))
            self.db.commit()
            self.assertEqual(self.store.status(False)['nested_container_state'],expected)

    def test_empty_collision_requires_native_evidence_and_respects_trace_mode(self):
        from spatial_twin.collision import bodies
        from spatial_twin.shadow import bind_asset
        e=Store.entity(self.db,'c')
        e['collision'].update(simple=[],complex=['plane'],simple_coverage='EMPTY',complex_coverage='AVAILABLE')
        self.put(e);self.db.commit()
        with self.store.read() as db:
            q=Queries(self.store,db)
            self.assertEqual(bodies(q,e),[])
            simple=q.raycast([-2,0,0],[1,0,0],4)
            self.assertTrue(simple['complete']);self.assertIsNone(simple['hit'])
            complex_hit=q.raycast([-2,0,0],[1,0,0],4,complex=True)
            self.assertTrue(complex_hit['complete']);self.assertEqual(complex_hit['hit']['distance'],2)
            for state in (None,'UNKNOWN'):
                e['collision']['simple_coverage']=state
                q=Queries(self.store,db,{'c':e})
                self.assertFalse(q.raycast([-2,0,0],[1,0,0],4)['complete'])
                with self.assertRaises(ValueError):bodies(q,e)
            e['collision'].update(simple_coverage='EMPTY',trace_mode='UseComplexAsSimple')
            self.assertEqual(Queries(self.store,db,{'c':e}).raycast([-2,0,0],[1,0,0],4)['hit']['distance'],2)
            e['collision'].update(trace_mode='UseSimpleAsComplex')
            self.assertIsNone(Queries(self.store,db,{'c':e}).raycast([-2,0,0],[1,0,0],4,complex=True)['hit'])
            # Binding a different asset must not retain the previous EMPTY proof.
            bind_asset(e,{'id':'other','local_bounds':[[-1,-1,-1],[1,1,1]]})
            self.assertNotIn('simple_coverage',e['collision'])
            with self.assertRaises(ValueError):bodies(q,e)

    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.root=Path(self.directory.name); self.store=Store(self.root)
        self.db=sqlite3.connect(self.root/'world.sqlite')
        schema=Path(__file__).resolve().parents[2]/'Plugins/UnrealSpatialTwin/Resources/schema.sql'
        self.db.executescript(schema.read_text())
        self.db.execute("UPDATE metadata SET value='1' WHERE key='world_revision'")
        self.db.execute("INSERT INTO metadata VALUES('collision_bounds_version','2')")
        self.db.execute("INSERT INTO snapshots VALUES('first',1,'READY','2026-10-02T00:00:00Z','/Fixture')")
        self.put({'id':'a','kind':'Actor','label':'PlayerStart','class':'Fixture','bounds':[[-1,-1,-1],[1,1,1]],'local_bounds':[[-1,-1,-1],[1,1,1]],'transform':IDENTITY})
        self.put({'id':'c','kind':'Component','parent_id':'a','actor_id':'a','bounds':[[-1,-1,-1],[1,1,1]],'local_bounds':[[-1,-1,-1],[1,1,1]],'transform':IDENTITY,'collision':{'enabled':True,'simple':['plane'],'responses':{'Visibility':'Block'}}})
        self.put({'id':'b','kind':'Actor','label':'Door','class':'Fixture','bounds':[[19,-1,-1],[21,1,1]],'transform':{**IDENTITY,'position':[20,0,0]}})
        self.db.execute("INSERT INTO class_schemas VALUES('Fixture',?)",(encode({'properties':{'enabled':{'type':'bool','spatial_effect':'none'}}}),))
        self.db.execute("INSERT INTO relationships VALUES('a','c','HAS_COMPONENT')")
        self.db.execute("INSERT INTO geometry VALUES('plane','geometry/plane.stg',?)",(encode({'triangles':1,'vertices':3}),))
        (self.root/'geometry').mkdir()
        (self.root/'geometry/plane.stg').write_bytes(b'STG1'+struct.pack('<III',1,3,1)+struct.pack('<9d',0,-1,-1,0,1,-1,0,0,1)+struct.pack('<III',0,1,2))
        self.db.commit()

    def put(self,e):
        box=g.spatial_bounds(e); vals=[v for i in range(3) for v in (box[0][i],box[1][i])] if box else [None]*6
        self.db.execute('INSERT OR REPLACE INTO entities(id,kind,parent_id,actor_id,label,class,x0,x1,y0,y1,z0,z1,source,generation,revision) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        [e['id'],e['kind'],e.get('parent_id'),e.get('actor_id'),e.get('label'),e.get('class'),*vals,encode(e),'first',1])

    def tearDown(self):
        self.db.close(); g.load_mesh.cache_clear(); self.directory.cleanup()

    def test_nested_field_projection_preserves_source_unknowns_and_parent_selection(self):
        from spatial_twin.store import select_fields
        source={'id':'c','kind':'Component','collision':{'enabled':True,'optional':None,'geometry':['large']*1000},'literal.name':9}
        before=encode(source)
        result=select_fields(source,['collision.enabled','collision.optional','collision.absent','missing.leaf','literal.name'])
        self.assertEqual(result,{'id':'c','kind':'Component','collision':{'enabled':True,'optional':None},'literal.name':9})
        for fields in (['collision','collision.enabled'],['collision.enabled','collision']):
            self.assertEqual(select_fields(source,fields)['collision'],source['collision'])
        self.assertEqual(select_fields(source,['collision.geometry.0']),{'id':'c','kind':'Component'})
        self.assertEqual(encode(source),before)
        self.assertLess(len(encode(result)),len(before)/10)

    def test_nested_fields_agree_across_actual_read_and_shadow_tools(self):
        server=create_server(self.store)
        def call(name,**args):return server._tool_manager.get_tool(name).fn(**args)
        fields=['collision.enabled','collision.responses.Visibility','collision.missing']
        expected={'id':'c','kind':'Component','collision':{'enabled':True,'responses':{'Visibility':'Block'}}}
        self.assertEqual(call('entity_get',entity_id='c',fields=fields)['entity'],expected)
        self.assertEqual(call('asset_get',asset_id='c',fields=fields)['entity'],expected)
        self.assertIn('simple',call('asset_get',asset_id='c')['entity']['collision'])
        region=call('spatial_overlap',aabb=[[-2,-2,-2],[2,2,2]],kind='Component',fields=fields)
        self.assertEqual(region['entities'],[expected])
        patch=call('patch_prepare',operations=[{'type':'MOVE_ACTOR','target':'a','value':[10,0,0]}],base_revision=1)
        entities=call('patch_preview',patch_id=patch['id'],fields=fields)['entities']
        self.assertEqual(next(e for e in entities if e['id']=='c'),expected)
        self.assertEqual(call('entity_get',entity_id='c',fields=['collision'])['entity']['collision']['simple'],['plane'])
        with self.assertRaises(ValueError):call('world_search',text='no matches',fields=[1])

    def test_readonly_and_closes_connection(self):
        with self.store.read() as db:
            with self.assertRaises(sqlite3.OperationalError): db.execute('DELETE FROM entities')
        with self.assertRaises(sqlite3.ProgrammingError): db.execute('SELECT 1')

    def test_material_binding_cold_cache_is_targeted_and_requires_canonical_proof(self):
        import asyncio
        from unittest.mock import patch
        import materialize
        path='/Engine/Unused.Material';receipt={};writes=[]
        async def publish(session,toolset,tool,args):
            self.assertEqual((tool,args),('spatial_twin_cache_asset',{'object_path':path}))
            self.put({'id':'asset:'+path,'kind':'Material','class':'/Script/Engine.Material'})
            self.db.commit()
            return {'ready':True}
        with patch.object(materialize,'require_target'),patch.object(materialize,'call_tool',side_effect=publish) as call:
            asyncio.run(materialize.ensure_materials(self.store,{'a':path,'b':path},None,receipt,lambda:writes.append(encode(receipt))))
            self.assertEqual(call.await_count,1)
            self.assertEqual(receipt['material_cache'][0]['state'],'CANONICAL_VERIFIED')
            self.assertIn('SENT',writes[0])
            asyncio.run(materialize.ensure_materials(self.store,{'a':path},None,{},lambda:None))
            self.assertEqual(call.await_count,1)
        self.put({'id':'asset:bad','kind':'StaticMesh','class':'/Script/Engine.StaticMesh'});self.db.commit()
        with patch.object(materialize,'call_tool') as call:
            with self.assertRaisesRegex(ValueError,'not a canonical material'):
                asyncio.run(materialize.ensure_materials(self.store,{'a':'bad'},None,{},lambda:None))
            call.assert_not_called()
        with patch.object(materialize,'require_target'),patch.object(materialize,'call_tool',return_value={'ready':True}):
            with self.assertRaisesRegex(ValueError,'Entity not found'):
                asyncio.run(materialize.ensure_materials(self.store,{'a':'missing'},None,{},lambda:None))

    def instance_fixture(self,collision=False):
        c=Store.entity(self.db,'c');c.update(instance_count=2,collision={'enabled':collision,'aggregate_only':True});self.put(c)
        for i,x in enumerate((0,20)):
            self.put({'id':'i'+str(i),'kind':'Instance','actor_id':'a','parent_id':'c','component_id':'c','instance_index':i,
                'transform':{**IDENTITY,'position':[x,0,0]},'instance_transform':{**IDENTITY,'position':[x,0,0]},
                'bounds':[[x-1,-1,-1],[x+1,1,1]],'local_bounds':[[-1,-1,-1],[1,1,1]],
                'collision':{'enabled':collision,'shapes':[{'type':'box','extent':[1,1,1]}]}})
        self.db.commit()

    def test_single_instance_transform_and_sequential_preconditions(self):
        self.instance_fixture()
        patch=self.store.patch_create([{'type':'SET_INSTANCE_TRANSFORM','target':'i0','transform':{**IDENTITY,'position':[50,0,0]}},
            {'type':'SET_INSTANCE_TRANSFORM','target':'i0','transform':{**IDENTITY,'position':[60,0,0],'scale':[1,1,2]}}])
        checked=validate(self.store,patch['id']);self.assertEqual(checked['status'],'VALIDATED',checked['validation'])
        v=checked['validation'];self.assertEqual(set(v['expected']),{'a','c','i0'})
        self.assertEqual(v['operation_transforms']['1']['before']['position'],[50,0,0])
        with self.store.read() as db:
            overlay,errors=preview(self.store,db,patch);self.assertEqual(errors,[])
            self.assertEqual(overlay['i0']['instance_transform']['position'],[60,0,0])
            self.assertEqual(overlay['a']['transform'],IDENTITY)
            self.assertEqual(overlay['c']['bounds'],[[19,-1,-2],[61,1,2]])
            found=list(Queries(self.store,db,overlay).region([[59,-1,-2],[61,1,2]],'Instance'))
            self.assertEqual([e['id'] for e in found],['i0'])
            self.assertEqual(Store.entity(db,'i0')['transform']['position'],[0,0,0])
            self.assertEqual(Store.entity(db,'i1')['transform']['position'],[20,0,0])
        wrong=self.store.patch_create([{'type':'SET_INSTANCE_TRANSFORM','target':'a','transform':IDENTITY}])
        self.assertEqual(validate(self.store,wrong['id'])['status'],'INVALID')
        with self.assertRaises(ValueError):check_operations([{'type':'SET_INSTANCE_TRANSFORM','target':'i0'}])

    def test_instance_collision_checks_siblings_and_missing_coverage(self):
        self.instance_fixture(collision=True)
        patch=self.store.patch_create([{'type':'SET_INSTANCE_TRANSFORM','target':'i0','transform':{**IDENTITY,'position':[20,0,0]}}])
        checked=validate(self.store,patch['id'])
        self.assertIn(['i0','i1'],checked['validation']['aabb_candidates'])
        # This standalone fixture lacks the native collision backend: siblings
        # must be checked and unknown coverage must refuse, never certify free.
        self.assertEqual(checked['status'],'INVALID')
        self.assertTrue(any(e['error']=='Collision coverage unknown' for e in checked['validation']['errors']))

    def test_one_instance_navigation_does_not_rebuild_distant_group_bounds(self):
        import copy
        from unittest.mock import patch
        from spatial_twin.navigation import input_changed
        self.instance_fixture()
        c=Store.entity(self.db,'c');c.update(affects_navigation=True,bounds=[[-1,-1,-1],[1000001,1,1]],local_bounds=[[-1,-1,-1],[1000001,1,1]]);self.put(c)
        for ident,x in (('i0',0),('i1',1000000)):
            e=Store.entity(self.db,ident);e.update(affects_navigation=True,transform={**IDENTITY,'position':[x,0,0]},bounds=[[x-1,-1,-1],[x+1,1,1]]);self.put(e)
        self.db.commit()
        draft=self.store.patch_create([{'type':'SET_INSTANCE_TRANSFORM','target':'i0','transform':{**IDENTITY,'position':[2,0,0]}}])
        source={'settings':{'radius':1,'cell_size':1,'height':10},'active_tiles':1,
                'detour_parameters':{'origin':[0,0,0],'tile_width':100,'tile_height':100}}
        nav=Navigation(self.store,'Default')
        with self.store.read() as db:
            overlay,errors=preview(self.store,db,draft);self.assertEqual(errors,[])
            self.assertFalse(input_changed(c,overlay['c']))
            # A genuine parent navigation-property change still invalidates it.
            changed=copy.deepcopy(overlay['c']);changed['navigation_fill_underneath']=True
            self.assertTrue(input_changed(c,changed))
            with patch.object(nav,'source',return_value=(source,self.root/'base.stn')),patch.object(nav,'rebuild_tiles') as rebuild:
                nav.simulate(db,draft['id'],overlay)
            affected=rebuild.call_args.args[2]
            self.assertEqual(set(affected),{(-1,-1),(-1,0),(0,-1),(0,0)})

    def test_indexed_search_preserves_literal_unicode_short_and_paged_results(self):
        labels=['Door','Door_100%','a\\b','a "quoted" door','ÉÉÉ ééé','名門口','😀🏠👍','a b c','StaticMesh','x\x00tail']
        for i,label in enumerate(labels):
            self.put({'id':f'search:{i:02}','kind':'Actor' if i%2 else 'Material','label':label,'class':'/Script/Fixture.MixedCase'})
        self.db.execute("UPDATE entities SET path='/Game/Textures/Metal_100%' WHERE id='search:00'");self.db.commit()
        tool=create_server(self.store)._tool_manager.get_tool('world_search').fn
        for text in labels+['door','_','%','\\','"','ÉéÉ','/Game/Textures','ixedCa','i','OR','MixedCase','xy','tail','']:
            for kind in (None,'Actor','Material'):
                escaped=text.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
                sql="SELECT id FROM entities WHERE (label LIKE ? ESCAPE CHAR(92) OR path LIKE ? ESCAPE CHAR(92) OR class LIKE ? ESCAPE CHAR(92))"
                args=['%'+escaped+'%']*3
                if kind:sql+=' AND kind=?';args.append(kind)
                expected=[r[0] for r in self.db.execute(sql+' ORDER BY id',args)]
                actual=[];cursor=None
                while True:
                    page=tool(text=text,kind=kind,limit=2,fields=[],cursor=cursor)
                    actual.extend(e['id'] for e in page['entities']);cursor=page['cursor']
                    if cursor is None:break
                self.assertEqual(actual,expected,(text,kind))
        with self.store.read() as db:
            for text,index in [('missing-door','VIRTUAL TABLE INDEX'),('xy','COVERING INDEX entity_search')]:
                sql,args=Store.search_sql(db,text)
                plan=' '.join(r[3] for r in db.execute('EXPLAIN QUERY PLAN '+sql,args))
                self.assertIn(index,plan)

        for i in range(300):self.put({'id':f'broad:{i:04}','kind':'Actor','label':'Broad common candidate'})
        self.db.commit()
        with self.store.read() as db:
            sql,args=Store.search_sql(db,'common')
            plan=' '.join(r[3] for r in db.execute('EXPLAIN QUERY PLAN '+sql,args))
            self.assertIn('COVERING INDEX entity_search',plan)
            self.assertNotIn('VIRTUAL TABLE INDEX',plan)
        self.assertEqual([e['id'] for e in tool(text='common',limit=2)['entities']],['broad:0000','broad:0001'])

    def test_search_index_migration_incremental_changes_rollback_and_idempotence(self):
        schema=Path(__file__).resolve().parents[2]/'Plugins/UnrealSpatialTwin/Resources/schema.sql'
        for name in ('insert','update','delete'):self.db.execute('DROP TRIGGER entity_text_'+name)
        self.db.execute('DROP TABLE entity_text');self.db.execute('DROP INDEX entity_search')
        self.db.execute("DELETE FROM metadata WHERE key='text_search_version'");self.db.commit()
        tool=create_server(self.store)._tool_manager.get_tool('world_search').fn
        self.assertEqual(tool(text='Door')['entities'][0]['id'],'b')
        self.db.executescript(schema.read_text())
        self.assertEqual(tool(text='Door')['entities'][0]['id'],'b')
        self.db.execute("UPDATE entities SET label='RenamedGate',path='/Game/NEW' WHERE id='b'");self.db.commit()
        self.assertEqual(tool(text='Door')['returned'],0)
        self.assertEqual(tool(text='RenamedGate')['entities'][0]['id'],'b')
        self.db.execute("DELETE FROM entities WHERE id='b'")
        self.assertEqual(self.db.execute("SELECT count(*) FROM entity_text WHERE entity_text MATCH '\"Ren\"'").fetchone()[0],0)
        self.db.rollback()
        self.assertEqual(tool(text='RenamedGate')['entities'][0]['id'],'b')
        before=self.db.execute('SELECT * FROM entity_text_data ORDER BY id').fetchall()
        self.db.execute("UPDATE entities SET label=label,source=source,revision=revision+1 WHERE id='b'");self.db.commit()
        self.db.executescript(schema.read_text())
        self.assertEqual(self.db.execute('SELECT * FROM entity_text_data ORDER BY id').fetchall(),before)
        self.db.execute("INSERT INTO entity_text(entity_text,rank) VALUES('integrity-check',1)")
        self.assertEqual(self.db.execute("SELECT value FROM metadata WHERE key='world_revision'").fetchone()[0],'1')

    def test_asset_swap_clears_old_navigation_and_keeps_sibling_extent(self):
        c=Store.entity(self.db,'c');c.update(asset_id='old',navigation_geometry_hash='plane',navigation_input_coverage='native_recast_geometry')
        self.put(c)
        self.put({'id':'new','kind':'StaticMesh','geometry_hash':'plane','local_bounds':[[-2,-2,-2],[2,2,2]],
                  'collision_local_bounds':[[-3,-3,-3],[3,3,3]],'collision_shapes':[{'type':'box','extent':[3,3,3]}]})
        self.put({'id':'sibling','kind':'Component','actor_id':'a','parent_id':'a','bounds':[[99,-1,-1],[101,1,1]],
                  'local_bounds':[[-1,-1,-1],[1,1,1]],'transform':{**IDENTITY,'position':[100,0,0]}})
        self.db.commit()
        patch={'base_revision':1,'operations':[{'type':'CHANGE_ASSET','target':'a','asset':'new','operation_id':'swap'}]}
        with self.store.read() as db:
            overlay,errors=preview(self.store,db,patch);self.assertFalse(errors)
            self.assertNotIn('navigation_geometry_hash',overlay['c'])
            self.assertNotIn('navigation_input_coverage',overlay['c'])
            self.assertEqual(overlay['a']['bounds'],[[-2,-2,-2],[101,2,2]])
            self.assertEqual(overlay['a']['collision_bounds'],[[-3,-3,-3],[3,3,3]])
            self.assertNotIn('sibling',overlay)
            self.assertEqual(Store.entity(db,'c')['navigation_geometry_hash'],'plane')

    def test_instanced_asset_swap_updates_each_instance_and_index(self):
        c=Store.entity(self.db,'c');c.update(asset_id='old',instance_count=2);c['collision']['aggregate_only']=True;self.put(c)
        for number,x in enumerate((0,100)):
            self.put({'id':f'i{number}','kind':'Instance','actor_id':'a','parent_id':'c','component_id':'c',
                      'asset_id':'old','transform':{**IDENTITY,'position':[x,0,0]},'bounds':[[x-1,-1,-1],[x+1,1,1]],
                      'local_bounds':[[-1,-1,-1],[1,1,1]],'collision':{'enabled':True,'shapes':[{'type':'box','extent':[1,1,1]}]}})
        self.put({'id':'new','kind':'StaticMesh','local_bounds':[[-4,-4,-4],[4,4,4]],
                  'collision_local_bounds':[[-4,-4,-4],[4,4,4]],'collision_shapes':[{'type':'box','extent':[4,4,4]}]});self.db.commit()
        with self.store.read() as db:
            overlay,errors=preview(self.store,db,{'base_revision':1,'operations':[{'type':'CHANGE_ASSET','target':'a','component_id':'c','asset':'new','operation_id':'swap'}]})
            self.assertFalse(errors);self.assertEqual(overlay['i1']['asset_id'],'new')
            self.assertEqual(overlay['c']['bounds'],[[-4,-4,-4],[104,4,4]])
            self.assertEqual(overlay['a']['bounds'],overlay['c']['bounds'])
            hit=Queries(self.store,db,overlay).raycast([110,0,0],[-1,0,0],20)['hit']
            self.assertEqual(hit['instance_id'],'i1');self.assertEqual(hit['distance'],6)

    def test_compact_changes_never_read_source_payloads(self):
        from contextlib import contextmanager
        from unittest.mock import patch
        before={'id':'nav','tiles':list(range(10000))};after={'id':'nav','tiles':list(range(10001))}
        self.db.execute('INSERT INTO changes VALUES(1,?,?,?,?)',('nav','PROPERTY',encode(before),encode(after)));self.db.commit()
        read=self.store.read
        @contextmanager
        def metadata_only():
            with read() as db:
                db.set_authorizer(lambda op,table,column,*args: sqlite3.SQLITE_DENY if op==sqlite3.SQLITE_READ and table=='changes' and column in ('before_json','after_json') else sqlite3.SQLITE_OK)
                yield db
        with patch.object(self.store,'read',metadata_only):
            compact=self.store.changes(0,fields=['revision','entity_id'])
        self.assertEqual(compact['entities'],[{'id':'nav:1','kind':'PROPERTY','revision':1,'entity_id':'nav'}])
        self.assertEqual(self.store.changes(0)['entities'][0]['after'],after)
        diff=create_server(self.store)._tool_manager.get_tool('world_diff').fn
        self.assertEqual(diff(0,1)['entities'],[{'id':'nav','kind':'DIFF'}])
        self.assertEqual(diff(0,1,fields=['before','after'])['entities'][0]['before'],before)

    def test_legacy_collision_index_cannot_certify_free_space_or_patch(self):
        self.db.execute("DELETE FROM metadata WHERE key='collision_bounds_version'");self.db.commit()
        self.assertEqual(self.store.status()['collision_bounds_state'],'LEGACY_REQUIRES_UPGRADE')
        with self.store.read() as db:
            result=Queries(self.store,db).raycast([100,100,100],[0,0,-1],10)
            self.assertIsNone(result['hit']);self.assertFalse(result['complete']);self.assertIn('upgrade',result['coverage_error'])
        draft=self.store.patch_create([{'type':'MOVE_ACTOR','target':'a','value':[500,0,0]}])
        self.assertEqual(validate(self.store,draft['id'])['status'],'INVALID')

    def test_shadow_navigation_publication_is_immutable_and_failure_keeps_previous(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        agent='Vehicle/Heavy:Local'
        self.put({'id':'nav:test','kind':'NavRegion','agent':agent,'path':'navigation/base.stn','settings':{}});self.db.commit()
        draft=self.store.patch_create([{'type':'MOVE_ACTOR','target':'a','value':[10,0,0]}])
        nav=Navigation(self.store,agent)
        def write(function,source,request,destination):
            Path(destination).write_bytes(json.loads(request)['tiles'][0]['marker'].encode());return {'state':'READY'}
        with patch.object(nav,'native',return_value=SimpleNamespace(STBuildNavigation=None)),patch('spatial_twin.navigation.invoke',side_effect=write):
            with self.store.read() as db:
                first=nav.rebuild(db,draft['id'],[{'marker':'first'}]);second=nav.rebuild(db,draft['id'],[{'marker':'second'}])
                same=nav.rebuild(db,draft['id'],[{'marker':'first'}])
        self.assertNotEqual(first['path'],second['path']);self.assertEqual(first['path'],same['path'])
        self.assertEqual((self.root/first['path']).read_bytes(),b'first')
        with patch.object(nav,'native',return_value=SimpleNamespace(STBuildNavigation=None)),patch('spatial_twin.navigation.invoke',side_effect=RuntimeError('native failure')):
            with self.store.read() as db:
                with self.assertRaisesRegex(RuntimeError,'native failure'):nav.rebuild(db,draft['id'],[{'marker':'failed'}])
        self.assertFalse(list((self.root/'patches').rglob('*.tmp')))
        self.assertEqual((self.root/first['path']).read_bytes(),b'first')

    def test_navigation_rebuild_indexes_layers_once_and_preserves_input(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        class CountedTiles(list):
            visits=0
            def __iter__(self):
                for tile in super().__iter__():
                    self.visits+=1
                    yield tile
        tiles=CountedTiles([{'x':x,'y':0,'minimum_height':-layer*10,'maximum_height':layer*50}
                            for x in range(1000) for layer in range(2)])
        source={'settings':{'radius':1,'cell_size':1,'height':10},'active_tiles':len(tiles),
                'detour_parameters':{'origin':[0,0,0],'tile_width':100,'tile_height':100},'tiles':tiles,'input_coverage':'octree_audited'}
        # A nonspatial Actor in the same indexed region must never be decoded.
        self.db.execute("UPDATE entities SET source='[]' WHERE id='a'")
        c=Store.entity(self.db,'c');c.update(affects_navigation=True,navigation_geometry_hash='plane',navigation_fill_underneath=False,navigation_filled_convex=False);self.put(c);self.db.commit()
        nav=Navigation(self.store,'Default');received=[]
        def native(function,path,request,destination):
            received.append(json.loads(request));Path(destination).write_bytes(b'native-test-output');return {'state':'READY'}
        affected={(x,0):[0,1] for x in range(10)}
        with patch.object(nav,'source',return_value=(source,self.root/'base.stn')) as read, \
             patch.object(nav,'native',return_value=SimpleNamespace(STBuildNavigation=None)), \
             patch('spatial_twin.navigation.invoke',side_effect=native):
            with self.store.read() as db:nav.rebuild_tiles(db,'00000000-0000-0000-0000-000000000001',affected)
        self.assertEqual(read.call_count,1);self.assertEqual(tiles.visits,len(tiles))
        self.assertEqual(affected,{(x,0):[0,1] for x in range(10)})
        self.assertTrue(all(t['minimum_height']==-10 and t['maximum_height']==60 for t in received[0]['tiles']))
        self.assertEqual(len(received[0]['tiles'][0]['triangles']),1)
        with self.store.read() as db:
            with self.assertRaisesRegex(ValueError,'tile budget'):
                nav.rebuild_tiles(db,'unused',{(i,0):[0,1] for i in range(65537)})

    def test_native_empty_navigation_retains_modifiers_and_unknown_refuses(self):
        from unittest.mock import patch
        source={'settings':{'radius':1,'cell_size':1,'height':10},'active_tiles':1,
                'detour_parameters':{'origin':[0,0,0],'tile_width':100,'tile_height':100},
                'input_coverage':'octree_audited','areas':{'Null':{'id':0,'flags':0}}}
        e=Store.entity(self.db,'c');e.update(affects_navigation=True,navigation_input_coverage='native_empty_geometry',
            navigation_modifiers=[{'shape':2,'mode':0,'area_class':'Null','bounds':[[-1,-1,-1],[1,1,1]]}])
        self.put(e);self.db.commit();nav=Navigation(self.store,'Default')
        with self.store.read() as db,patch.object(nav,'rebuild',return_value={'state':'READY'}) as rebuild:
            nav.rebuild_tiles(db,'unused',{(0,0):[-5,20]},_source=(source,self.root/'base.stn'))
            tile=rebuild.call_args.args[2][0]
            self.assertEqual(tile['triangles'],[]);self.assertEqual(len(tile['modifiers']),1)
            e.pop('navigation_input_coverage')
            with self.assertRaisesRegex(ValueError,'Native navigation input missing'):
                nav.rebuild_tiles(db,'unused',{(0,0):[-5,20]},{'c':e},_source=(source,self.root/'base.stn'))

    def test_nondefault_slope_is_not_silently_treated_as_global_agent_slope(self):
        from unittest.mock import patch
        source={'settings':{'radius':1,'cell_size':1,'height':10},'active_tiles':1,
                'detour_parameters':{'origin':[0,0,0],'tile_width':100,'tile_height':100},'input_coverage':'octree_audited'}
        e=Store.entity(self.db,'c');e.update(affects_navigation=True,navigation_geometry_hash='plane',navigation_slope_behavior=1,navigation_slope_angle=70,
            navigation_fill_underneath=False,navigation_filled_convex=False)
        self.put(e);self.db.commit();nav=Navigation(self.store,'Default')
        with self.store.read() as db,patch.object(nav,'rebuild',side_effect=AssertionError('Unknown slope must not publish')):
            with self.assertRaisesRegex(ValueError,'slope override'):
                nav.rebuild_tiles(db,'unused',{(0,0):[-5,20]},_source=(source,self.root/'base.stn'))
        with self.store.read() as db,patch.object(nav,'rebuild',return_value={'state':'READY'}) as rebuild:
            source['walkable_slope_policy']='ue5.8.2-56702186-global-agent-slope'
            nav.rebuild_tiles(db,'unused',{(0,0):[-5,20]},_source=(source,self.root/'base.stn'))
            self.assertTrue(rebuild.call_args.args[2][0]['triangles'])
            self.assertEqual(Store.entity(db,'c')['navigation_slope_behavior'],1)
            source['walkable_slope_policy']='unknown-custom-engine'
            with self.assertRaisesRegex(ValueError,'slope override'):
                nav.rebuild_tiles(db,'unused',{(0,0):[-5,20]},_source=(source,self.root/'base.stn'))

    def test_navigation_border_rounds_up_agent_radius_before_gathering_geometry(self):
        from unittest.mock import patch
        source={'settings':{'radius':1.1,'cell_size':1,'height':10},'active_tiles':1,
                'detour_parameters':{'origin':[0,0,0],'tile_width':100,'tile_height':100},'input_coverage':'octree_audited'}
        e=Store.entity(self.db,'c');e.update(affects_navigation=True,navigation_geometry_hash='plane',
            navigation_fill_underneath=False,navigation_filled_convex=False,
            bounds=[[4.5,-1,-1],[4.7,1,1]],transform={**IDENTITY,'position':[4.6,0,0]})
        self.put(e);self.db.commit();nav=Navigation(self.store,'Default')
        with self.store.read() as db,patch.object(nav,'rebuild',return_value={'state':'READY'}) as rebuild:
            nav.rebuild_tiles(db,'unused',{(0,0):[-5,20]},_source=(source,self.root/'base.stn'))
            self.assertEqual(len(rebuild.call_args.args[2][0]['triangles']),1)

    def test_nonprimitive_navigation_input_shadow_and_regional_coverage(self):
        from unittest.mock import patch
        source={'settings':{'radius':1,'cell_size':1,'height':10},'active_tiles':1,
                'detour_parameters':{'origin':[0,0,0],'tile_width':100,'tile_height':100},
                'input_coverage':'octree_audited','areas':{'Null':{'id':0,'flags':0}}}
        modifier={'shape':2,'mode':0,'area_class':'Null','bounds':[[-30,-30,-1],[-10,-10,10]]}
        self.put({'id':'a:navigation','kind':'NavigationInput','actor_id':'a','parent_id':'a',
                  'transform':IDENTITY,'affects_navigation':True,'bounds':modifier['bounds'],
                  'local_bounds':modifier['bounds'],'navigation_modifiers':[modifier]})
        self.db.commit();nav=Navigation(self.store,'Default');affected={(0,0):[-5,20]}
        with self.store.read() as db,patch.object(nav,'rebuild',return_value={'state':'READY'}) as rebuild:
            nav.rebuild_tiles(db,'unused',affected,_source=(source,self.root/'base.stn'))
            self.assertEqual(rebuild.call_args.args[2][0]['modifiers'][0]['area_id'],0)
            plan={'base_revision':1,'operations':[{'type':'MOVE_ACTOR','target':'a','value':[500,0,0]}]}
            overlay,errors=preview(self.store,db,plan);self.assertFalse(errors)
            moved=overlay['a:navigation']
            self.assertEqual(moved['navigation_modifiers'][0]['bounds'],[[470,-30,-1],[490,-10,10]])
            nav.rebuild_tiles(db,'unused',affected,overlay,_source=(source,self.root/'base.stn'))
            self.assertEqual(rebuild.call_args.args[2][0]['modifiers'],[])
            source['input_gap_count']=1;source['input_gaps']=[{'object_path':'/MissingOwner','bounds':modifier['bounds']}]
            with self.assertRaisesRegex(ValueError,'MissingOwner'):
                nav.rebuild_tiles(db,'unused',affected,_source=(source,self.root/'base.stn'))
            source['input_gaps'][0]['bounds']=[[500,500,500],[600,600,600]]
            nav.rebuild_tiles(db,'unused',affected,_source=(source,self.root/'base.stn'))
            source['input_gap_count']=257
            with self.assertRaisesRegex(ValueError,'truncated'):
                nav.rebuild_tiles(db,'unused',affected,_source=(source,self.root/'base.stn'))
            source.pop('input_coverage')
            with self.assertRaisesRegex(ValueError,'coverage refresh'):
                nav.rebuild_tiles(db,'unused',affected,_source=(source,self.root/'base.stn'))

    def test_point_links_preserve_agents_flags_and_shadow_endpoints(self):
        from unittest.mock import patch
        source={'settings':{'radius':1,'cell_size':1,'height':10,'climb':21,'cell_height':10,'agent_index':1},
                'active_tiles':1,'off_mesh_link_flag':32768,'input_coverage':'octree_audited',
                'detour_parameters':{'origin':[0,0,0],'tile_width':100,'tile_height':100},
                'areas':{'Walk':{'id':4,'flags':1}}}
        link={'start':[-80,-50,0],'end':[-20,-50,0],'radius':7,'height':12,'use_snap_height':False,
              'supported_agents':[1],'area_class':'Walk','user_id':'18446744073709551614',
              'bidirectional':False,'reversed':True,'snap_to_cheapest_area':True,'generated':False,'requires_projection':False}
        entity={'id':'a:navigation','kind':'NavigationInput','actor_id':'a','parent_id':'a','transform':IDENTITY,
                'affects_navigation':True,'bounds':[[-90,-60,-10],[-10,-40,20]],'local_bounds':[[-90,-60,-10],[-10,-40,20]],
                'navigation_has_links':True,'navigation_links':[link],'navigation_link_coverage':'point_links'}
        self.put(entity);self.db.commit();nav=Navigation(self.store,'Default')
        with self.store.read() as db,patch.object(nav,'rebuild',return_value={'state':'READY'}) as rebuild:
            def request(overlay=None):
                nav.rebuild_tiles(db,'unused',{(0,0):[-10,20]},overlay,_source=(source,self.root/'base.stn'))
                return rebuild.call_args.args[2][0]['links']
            result=request()[0]
            self.assertEqual((result['height'],result['flags'],result['area_id']),(30,32769,4))
            self.assertEqual(result['user_id'],link['user_id']);self.assertTrue(result['reversed'])
            overlay,errors=preview(self.store,db,{'base_revision':1,'operations':[{'type':'MOVE_ACTOR','target':'a','value':[10,0,0]}]})
            self.assertFalse(errors);moved=request(overlay)[0]
            self.assertEqual(moved['start'],[-70,-50,0]);self.assertEqual(moved['end'],[-10,-50,0]);self.assertEqual(moved['radius'],7)
            overlay['a:navigation']['navigation_links'][0]['use_snap_height']=True
            self.assertEqual(request(overlay)[0]['height'],12)
            overlay['a:navigation']['navigation_links'][0]['requires_projection']=True
            with self.assertRaisesRegex(ValueError,'geometry reprojection'):request(overlay)
            source['settings']['agent_index']=0
            self.assertEqual(request(overlay),[])
            source['settings']['agent_index']=1
            overlay['a:navigation']['navigation_link_coverage']='segment_links_require_native_rebuild'
            with self.assertRaisesRegex(ValueError,'link source incomplete'):request(overlay)
            overlay['a:navigation']['collision']={'aggregate_only':True}
            with self.assertRaisesRegex(ValueError,'per-instance native source'):request(overlay)
        link['requires_projection']=True;self.put(entity);self.db.commit()
        with self.store.read() as db,patch.object(nav,'rebuild',return_value={'state':'READY'}) as rebuild:
            # Existing processed endpoints remain source data, even when a
            # neighboring tile changes. Moving their owner requires reprojection.
            nav.rebuild_tiles(db,'unused',{(0,0):[-10,20]},_source=(source,self.root/'base.stn'))
            self.assertEqual(rebuild.call_args.args[2][0]['links'][0]['start'],link['start'])
            moved,errors=preview(self.store,db,{'base_revision':1,'operations':[{'type':'MOVE_ACTOR','target':'a','value':[10,0,0]}]})
            self.assertFalse(errors)
            with self.assertRaisesRegex(ValueError,'geometry reprojection'):
                nav.rebuild_tiles(db,'unused',{(0,0):[-10,20]},moved,_source=(source,self.root/'base.stn'))

    def test_navigation_rasterization_groups_require_native_flags_mask_audit_and_backend(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        c=Store.entity(self.db,'c');c.update(affects_navigation=True,navigation_geometry_hash='plane',navigation_fill_underneath=True,navigation_filled_convex=False)
        self.put(c);self.db.commit()
        source={'settings':{'radius':1,'cell_size':1,'height':10},'active_tiles':1,'input_coverage':'octree_audited','fill_underneath_mask_count':0,
                'navigation_bounds':[[[-100,-100,-100],[100,100,500]]],
                'detour_parameters':{'origin':[0,0,0],'tile_width':100,'tile_height':100}}
        nav=Navigation(self.store,'Default')
        with self.store.read() as db,patch.object(nav,'rebuild',return_value={'state':'READY'}) as rebuild:
            nav.rebuild_tiles(db,'unused',{(0,0):[250,444]},_source=(source,self.root/'base.stn'))
            tiles=rebuild.call_args.args[2]
            self.assertEqual((tiles[0]['minimum_height'],tiles[0]['maximum_height']),(-100,500))
            self.assertEqual(tiles[0]['rasterization_groups'],[{'first_triangle':0,'triangle_count':1,'flags':1}])
            source['fill_underneath_mask_count']=1
            with self.assertRaisesRegex(ValueError,'mask audit'):nav.rebuild_tiles(db,'unused',{(0,0):[-10,20]},_source=(source,self.root/'base.stn'))
            source['rasterization_mask_version']=1
            nav.rebuild_tiles(db,'unused',{(0,0):[-10,20]},_source=(source,self.root/'base.stn'))
            source.pop('fill_underneath_mask_count')
            with self.assertRaisesRegex(ValueError,'mask audit'):nav.rebuild_tiles(db,'unused',{(0,0):[-10,20]},_source=(source,self.root/'base.stn'))
        with self.store.read() as db,patch.object(nav,'native',return_value=SimpleNamespace(STBuildNavigation=None)):
            with self.assertRaisesRegex(ValueError,'grouped rasterization support'):
                nav.rebuild(db,'unused',tiles,_source=(source,self.root/'base.stn'))
        tiles[0]['modifiers']=[{'mask_fill_underneath':True}]
        with self.store.read() as db,patch.object(nav,'native',return_value=SimpleNamespace(STBuildNavigation=None,STNavigationRasterizationVersion=lambda:1)):
            with self.assertRaisesRegex(ValueError,'with masks'):nav.rebuild(db,'unused',tiles,_source=(source,self.root/'base.stn'))
        # Native cache enrichment updates the shared owner, not every old ISM
        # row. Its current flags must override stale per-instance copies.
        instance=dict(c,id='instance',kind='Instance',parent_id='c',navigation_fill_underneath=False)
        c['collision']={'aggregate_only':True};self.put(c);self.put(instance);self.db.commit()
        source['fill_underneath_mask_count']=0
        with self.store.read() as db,patch.object(nav,'rebuild',return_value={'state':'READY'}) as rebuild:
            nav.rebuild_tiles(db,'unused',{(0,0):[-10,20]},_source=(source,self.root/'base.stn'))
            self.assertEqual(rebuild.call_args.args[2][0]['rasterization_groups'],[{'first_triangle':0,'triangle_count':1,'flags':1}])
        c['navigation_fill_underneath']=False;c.pop('navigation_filled_convex');self.put(c);self.db.commit()
        with self.store.read() as db,patch.object(nav,'rebuild',side_effect=AssertionError('Unknown flags must not publish')):
            with self.assertRaisesRegex(ValueError,'scoped cache refresh'):
                nav.rebuild_tiles(db,'unused',{(0,0):[-10,20]},_source=(source,self.root/'base.stn'))

    def test_full_navigation_pool_missing_region_fails_before_geometry_preparation(self):
        from unittest.mock import patch
        source={'settings':{'radius':1,'cell_size':1,'height':10},'active_tiles':1,
                'detour_parameters':{'origin':[0,0,0],'tile_width':100,'tile_height':100,'max_tiles':1},
                'input_coverage':'octree_audited','tiles':[{'x':0,'y':0,'minimum_height':0,'maximum_height':10}]}
        nav=Navigation(self.store,'Default')
        with self.store.read() as db,patch.object(Queries,'region',side_effect=AssertionError('Geometry should not be read')):
            with self.assertRaisesRegex(ValueError,'pool full; affected region has no baseline'):
                nav.rebuild_tiles(db,'unused',{(10,10):[0,20]},_source=(source,self.root/'base.stn'))
            # One covered coordinate must not hide a second, unobserved region.
            with self.assertRaisesRegex(ValueError,'pool full; affected region has no baseline'):
                nav.rebuild_tiles(db,'unused',{(0,0):[0,20],(10,10):[0,20]},_source=(source,self.root/'base.stn'))
        # Full does not forbid replacement of existing tiles, or empty work.
        with self.store.read() as db,patch.object(nav,'rebuild',return_value={'state':'READY'}) as rebuild:
            nav.rebuild_tiles(db,'unused',{(0,0):[0,20]},_source=(source,self.root/'base.stn'))
            self.assertEqual(len(rebuild.call_args.args[2]),1)
            nav.rebuild_tiles(db,'unused',{},_source=(source,self.root/'base.stn'))
            source['detour_parameters']['max_tiles']=2
            nav.rebuild_tiles(db,'unused',{(10,10):[0,20]},_source=(source,self.root/'base.stn'))

    def test_navigation_query_carries_baseline_capacity_without_changing_native_answer(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        nav=Navigation(self.store,'Default')
        source={'active_tiles':2,'detour_parameters':{'max_tiles':2},'coverage':'exported_active_tiles'}
        native_result={'state':'READY','reachable':True,'path':[[0,0,0],[1,0,0]]}
        with patch.object(nav,'source',return_value=(source,self.root/'base.stn')) as read, \
             patch.object(nav,'native',return_value=SimpleNamespace(STNavigationQuery=None)), \
             patch('spatial_twin.navigation.invoke',return_value=native_result) as call:
            result=nav.query('path',[0,0,0],[1,0,0])
            self.assertTrue(result['baseline_tile_pool_full'])
            self.assertEqual({k:result[k] for k in native_result},native_result)
            self.assertFalse(read.call_args.kwargs['include_tiles'])
            self.assertEqual(call.call_count,1) # No extra status/native call per query.
            source['detour_parameters']['max_tiles']=4
            self.assertFalse(nav.query('nearest',[0,0,0])['baseline_tile_pool_full'])
            source['detour_parameters'].clear()
            self.assertIsNone(nav.query('navigable',[0,0,0])['baseline_tile_pool_full'])
            source['detour_parameters']['max_tiles']=2
            unknown={'state':'UNKNOWN','reachable':False,'path':[],'reason':'No projection'}
            call.return_value=unknown
            result=nav.query('path',[0,0,0],[1,0,0])
            self.assertEqual(result['state'],'UNKNOWN')
            self.assertEqual(result['path'],[])
            self.assertTrue(result['baseline_tile_pool_full'])

    def test_navigation_capacity_source_is_offline_and_legacy_unknown(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        nav=Navigation(self.store,'Default');path=self.root/'capacity.stn';path.write_bytes(b'fixture')
        source={'active_tiles':2,'detour_parameters':{'max_tiles':2},'capacity_settings':{'fixed_tile_pool':True,'tile_pool_size':2,'tile_number_hard_limit':64},
                'source_actor_id':'navactor','source_object_path':'/Fixture.Nav','source_package':'/Fixture'}
        with patch.object(nav,'source',return_value=(source,path)) as read,patch.object(nav,'native',return_value=SimpleNamespace(STNavigationQuery=None)),patch('spatial_twin.navigation.invoke',return_value={'state':'READY','active_tiles':2}) as query:
            result=nav.status();self.assertTrue(result['tile_pool_full'])
            self.assertEqual(result['capacity_settings'],source['capacity_settings'])
            self.assertEqual(result['native_actor'],{'id':'navactor','path':'/Fixture.Nav','package':'/Fixture'})
            self.assertFalse(read.call_args.kwargs['include_tiles']);self.assertEqual(query.call_count,1)
            for key in ('capacity_settings','source_actor_id','source_object_path','source_package'):source.pop(key)
            result=nav.status();self.assertIsNone(result['capacity_settings']);self.assertEqual(result['native_actor'],dict(id=None,path=None,package=None))

    def test_convex_navigation_prism_allows_yaw_but_does_not_guess_tilt(self):
        import copy,math
        from spatial_twin.shadow import move_spatial_data
        entity={'navigation_modifiers':[{'shape':3,'bounds':[[0,0,20],[10,10,40]],'points':[[0,0,0],[10,0,0],[0,10,0]]}]}
        yaw={**IDENTITY,'rotation':[0,0,math.sqrt(.5),math.sqrt(.5)],'position':[100,0,5]}
        moved=copy.deepcopy(entity);move_spatial_data(moved,IDENTITY,yaw)
        self.assertNotIn('navigation_preview_error',moved)
        self.assertAlmostEqual(moved['navigation_modifiers'][0]['bounds'][0][2],25)
        pitch={**IDENTITY,'rotation':[0,math.sqrt(.5),0,math.sqrt(.5)]}
        moved=copy.deepcopy(entity);move_spatial_data(moved,IDENTITY,pitch)
        self.assertIn('native modifier regeneration',moved['navigation_preview_error'])
        moved=copy.deepcopy(entity);move_spatial_data(moved,pitch,{**pitch,'scale':[2,1,1]})
        self.assertIn('native modifier regeneration',moved['navigation_preview_error'])

    def test_live_action_target_rejects_another_map_project_or_store(self):
        from apply_patch import require_target
        self.db.execute("INSERT INTO metadata VALUES('project_id','GenericGame')");self.db.commit()
        native={'ready':True,'map':'/Fixture','project_id':'GenericGame','root':str(self.root),'revision':1}
        self.assertEqual(require_target(self.store,native),native)
        for key,value in [('map','/Other'),('project_id','OtherGame'),('root',str(self.root/'other')),('revision',2)]:
            with self.assertRaisesRegex(ValueError,'CONFLICTED'):require_target(self.store,{**native,key:value})

    def test_map_switch_pins_each_mcp_request_and_keeps_previous_world(self):
        from spatial_twin.worlds import select_world
        other=self.root/'maps'/'second';other.mkdir(parents=True)
        from contextlib import closing
        with closing(sqlite3.connect(other/'world.sqlite')) as copy:
            self.db.backup(copy);copy.execute("UPDATE snapshots SET map='/Other'");copy.commit()
        active=self.root/'active_world.json'
        active.write_text(encode({'map':'/Fixture','directory':'.'}))
        server=create_server(self.store,follow_active=True)
        status=server._tool_manager.get_tool('world_status').fn
        self.assertEqual(status()['map'],'/Fixture')
        active.write_text(encode({'map':'/Other','directory':'maps/second'}))
        self.assertEqual(status()['map'],'/Other')
        self.assertEqual(select_world(self.root,'/Fixture'),self.root)
        self.assertEqual(self.store.status()['map'],'/Fixture')
        self.assertEqual(len(server._tool_manager.get_tool('world_maps').fn()['maps']),2)
        original=server._tool_manager.get_tool('world_search').fn
        def switch_mid_request(**args):
            active.write_text(encode({'map':'/Fixture','directory':'.'}))
            return original(**args)
        server._tool_manager.get_tool('world_search').fn=switch_mid_request
        result=server._tool_manager.get_tool('world_read').fn([{'tool':'world_search','arguments':{'text':'Door'}}])
        self.assertEqual(result['status']['map'],'/Other')
        self.assertEqual(status()['map'],'/Fixture')
        active.write_text(encode({'directory':'../escaped'}))
        with self.assertRaisesRegex(ValueError,'escapes'):status()

    def test_collision_outside_render_bounds_in_canonical_and_shadow(self):
        component=Store.entity(self.db,'c')
        component.update(collision_bounds=[[9,-1,-1],[11,1,1]],collision_local_bounds=[[9,-1,-1],[11,1,1]],
                         collision={'enabled':True,'shapes':[{'type':'sphere','center':[10,0,0],'radius':1}],'simple':[]})
        self.put(component);self.db.commit()
        patch=self.store.patch_create([{'type':'MOVE_ACTOR','target':'a','value':[20,0,0]}])
        with self.store.read() as db:
            canonical=Queries(self.store,db);hit=canonical.raycast([10,0,5],[0,0,-1],10)
            self.assertEqual(hit['hit']['component_id'],'c');self.assertAlmostEqual(hit['hit']['distance'],4)
            overlay,errors=preview(self.store,db,patch);self.assertFalse(errors)
            shadow=Queries(self.store,db,overlay);hit=shadow.raycast([30,0,5],[0,0,-1],10)
            self.assertEqual(hit['hit']['component_id'],'c');self.assertAlmostEqual(hit['hit']['distance'],4)
            self.assertEqual(Store.entity(db,'c')['bounds'],[[-1,-1,-1],[1,1,1]])

    def test_prefetched_rays_preserve_hits_unknown_channels_and_shadow(self):
        self.put({'id':'unknown','kind':'Component','bounds':[[3,-1,-1],[4,1,1]],
                  'transform':IDENTITY,'collision':{'enabled':True,'responses':{'Camera':'Ignore'}}})
        self.put({'id':'disabled','kind':'Component','bounds':[[6,-1,-1],[7,1,1]],
                  'transform':IDENTITY,'collision':{'enabled':'NoCollision'}})
        self.db.commit()
        patch=self.store.patch_create([{'type':'MOVE_ACTOR','target':'a','value':[2,0,0]}])
        with self.store.read() as db:
            overlay,errors=preview(self.store,db,patch);self.assertFalse(errors)
            for shadow in (None,overlay):
                q=Queries(self.store,db,shadow)
                for channel in ('Visibility','Camera'):
                    candidates=tuple(q.ray_candidates([[-10,-5,-5],[10,5,5]],channel))
                    with self.assertRaises(OverflowError):tuple(q.ray_candidates([[-10,-5,-5],[10,5,5]],channel,limit=0))
                    self.assertNotIn('disabled',[e['id'] for e in candidates])
                    for y in (-3,0,.4,3):
                        direct=q.raycast([-10,y,0],[1,0,0],20,channel)
                        self.assertEqual(direct,q.raycast([-10,y,0],[1,0,0],20,channel,candidates=candidates))
                    self.assertEqual(q.raycast([-10,0,0],[1,0,0],20,channel)['complete'],channel=='Camera')
        self.db.execute("DELETE FROM entities WHERE id='unknown'");self.db.commit()
        with self.store.read() as db:self.assertTrue(Queries(self.store,db).raycast([-10,0,0],[1,0,0],20)['complete'])

    def test_prepare_is_compact_validated_and_revision_guarded(self):
        prepare=create_server(self.store)._tool_manager.get_tool('patch_prepare').fn
        operations=[{'type':'MOVE_ACTOR','target':'a','value':[10,0,0]}]
        value=prepare(operations,1)
        self.assertEqual(value['status'],'VALIDATED');self.assertNotIn('expected',value['validation'])
        self.assertEqual(value['operation_count'],1)
        updated=prepare([{'type':'MOVE_ACTOR','target':'a','value':[11,0,0]}],1,value['id'])
        self.assertEqual(updated['id'],value['id']);self.assertEqual(updated['status'],'VALIDATED')
        self.db.execute("UPDATE metadata SET value='2' WHERE key='world_revision'");self.db.commit()
        with self.assertRaises(ValueError):prepare(operations,1,value['id'])
        self.assertEqual(self.store.patch_get(value['id'])['operations'][0]['value'],[11,0,0])
        with self.assertRaises(ValueError):prepare(operations,1)

    def test_read_bundle_rejects_mixed_revisions_and_mutations(self):
        self.db.execute('PRAGMA journal_mode=WAL')
        server=create_server(self.store);read=server._tool_manager.get_tool('world_read').fn
        get=server._tool_manager.get_tool('entity_get').fn
        self.assertNotIn('local_bounds',get('a')['entity'])
        self.assertEqual(get('a',fields=['local_bounds'])['entity']['local_bounds'],[[-1,-1,-1],[1,1,1]])
        self.assertNotIn('counts',server._tool_manager.get_tool('world_status').fn())
        calls=[{'tool':'entity_get','arguments':{'entity_id':'a','fields':['label']}},
               {'tool':'spatial_nearest','arguments':{'position':[19,0,0],'fields':['label']}}]
        result=read(calls);self.assertEqual(result['state'],'READY');self.assertEqual(result['model_calls'],0)
        self.assertEqual(result['results'][0]['data']['entity']['label'],'PlayerStart')
        self.assertEqual(result['results'][1]['data']['results'][0]['entity']['label'],'Door')
        self.put({'id':'asset:large','kind':'Asset','properties':{'large':'x'*13000}});self.db.commit()
        bounded=read([{'tool':'asset_get','arguments':{'asset_id':'asset:large'}}])
        self.assertNotIn('results',bounded)
        retained=json.loads(Path(bounded['results_reference']).read_text(encoding='utf-8'))
        self.assertEqual(retained[0]['data']['entity']['properties']['large'],'x'*13000)
        with self.assertRaises(ValueError):read([{'tool':'patch_validate','arguments':{'patch_id':'unknown'}}])
        with self.assertRaises(ValueError):read([{'tool':'world_read','arguments':{'queries':calls}}])
        with self.assertRaisesRegex(ValueError,'recovery explicitly'):
            read([{'tool':'patch_status','arguments':{'patch_id':'unused','recover':True}}])
        fn=server._tool_manager.get_tool('entity_get');original=fn.fn
        def changed(*args,**kwargs):
            value=original(*args,**kwargs)
            self.db.execute("UPDATE metadata SET value='2' WHERE key='world_revision'");self.db.commit()
            return value
        fn.fn=changed
        conflict=read(calls);self.assertEqual(conflict['state'],'CONFLICTED');self.assertNotIn('results',conflict)

    def test_execute_created_targets_and_parents_in_one_patch(self):
        import asyncio,copy
        from unittest.mock import patch
        import apply_patch as executor
        klass='/Script/Engine.StaticMeshActor'
        self.db.execute('INSERT INTO class_schemas VALUES(?,?)',(klass,encode({'properties':{}})))
        for ident in ('mesh','replacement'):
            self.put({'id':ident,'kind':'StaticMesh','path':'/Game/'+ident,'local_bounds':[[-1,-1,-1],[1,1,1]],
                      'native_spawn_collision':{'enabled':False},'native_spawn_affects_navigation':False})
        self.db.commit()
        operations=[{'type':'CREATE_ACTOR','target':ident,'class':klass,'asset':'mesh',
                     'transform':{**IDENTITY,'position':[x,0,0]}} for ident,x in [('new-child',100),('new-parent',200)]]
        operations += [{'type':'MOVE_ACTOR','target':'new-child','value':[110,0,0]},
                       {'type':'SCALE_ACTOR','target':'new-child','value':[2,2,2]},
                       {'type':'CHANGE_ASSET','target':'new-child','asset':'replacement'},
                       {'type':'ATTACH','target':'new-child','parent':'new-parent'},
                       {'type':'MOVE_ACTOR','target':'new-parent','value':[700,0,0]},
                       {'type':'ROTATE_ACTOR','target':'new-child','value':[0,0,1,0]}]
        created=self.store.patch_create(operations);checked=validate(self.store,created['id'])
        self.assertEqual(checked['status'],'VALIDATED',checked.get('validation'))
        calls=[];mapping={ident:'native:'+ident for ident in checked['validation']['expected']}
        def native_value(toolset,tool,args):
            calls.append((tool,args))
            if tool=='execute_tool_script':
                def registered(name,arguments):
                    group,method=name.rsplit('.',1);return {'returnValue':native_value(group,method,json.loads(arguments))}
                scope={'execute_tool':registered};exec(args['script'],scope);return scope['run']()
            if tool.startswith('add_to_scene_'):return {'refPath':'/World.'+args['name']}
            if tool=='get_root_component':return {'refPath':args['actor']['refPath']+'.ActualRoot'}
            if tool=='spatial_twin_sync':
                for ident,expected in checked['validation']['expected'].items():
                    value=copy.deepcopy(expected);value['id']=mapping[ident]
                    for field in ('actor_id','parent_id','attached_to'):
                        if field in value:value[field]=mapping.get(value[field],value[field])
                    value['path']='/World.'+ident if value['kind']=='Actor' else '/Component.'+ident
                    self.put(value)
                    self.db.execute('UPDATE entities SET path=? WHERE id=?',(value['path'],value['id']))
                self.db.execute("UPDATE metadata SET value='2' WHERE key='world_revision'");self.db.commit()
            if tool=='spatial_twin_save_patch':return {'state':'SAVED'}
            return True
        async def native(session,toolset,tool,args):return native_value(toolset,tool,args)
        async def ready(*args):return {}
        with patch.object(executor,'call_tool',native),patch.object(executor,'sync_ready',ready),patch.object(self.store,'status',return_value={'editor_connected':True}):
            done=asyncio.run(executor.execute(self.store,created['id'],'unused',.1,True,object()))
        self.assertEqual(done['status'],'APPLIED')
        self.assertEqual(next(args for tool,args in calls if tool=='set_parent_component'),
                         {'component':{'refPath':'/World.new-child.ActualRoot'},'parent':{'refPath':'/World.new-parent.ActualRoot'}})
        self.assertEqual(next(args for tool,args in calls if tool=='set_properties')['instance'],{'refPath':'/World.new-child.ActualRoot'})
        self.assertEqual([args for tool,args in calls if tool=='set_actor_transform'][-1]['xform']['location'],{'x':610,'y':0,'z':0})
        self.assertEqual(done['receipts'][-1]['actor_ids']['new-child'],mapping['new-child'])
        self.assertEqual(sum(tool=='execute_tool_script' for tool,args in calls),1)

    def test_recovery_confirms_lost_ack_without_replay_or_save(self):
        from spatial_twin.verification import recover
        import apply_patch as executor
        patch=validate(self.store,self.store.patch_create([{'type':'MOVE_ACTOR','target':'b','value':[30,0,0]}])['id'])
        self.assertEqual(patch['status'],'VALIDATED')
        receipts=[{'state':'SENT','operation_id':'batch:'+patch['id'],'tool':'execute_tool_script'}]
        executor.update(self.store,patch['id'],'FAILED',receipts,'Lost response')
        with self.store.patches() as db:db.execute('UPDATE patches SET operations=? WHERE id=?',(encode([{**patch['operations'][0],'value':[40,0,0]}]),patch['id']))
        with self.assertRaisesRegex(ValueError,'unchanged operation plan'):recover(self.store,patch['id'])
        with self.store.patches() as db:db.execute('UPDATE patches SET operations=? WHERE id=?',(encode(patch['operations']),patch['id']))
        self.db.execute("UPDATE metadata SET value='2' WHERE key='world_revision'");self.db.commit()
        with self.assertRaisesRegex(ValueError,'incomplete or changed'):recover(self.store,patch['id'])
        self.assertEqual(self.store.patch_get(patch['id'])['receipts'],receipts)
        for expected in patch['validation']['expected'].values():self.put(expected)
        self.db.commit()
        with self.store.application_lock():
            with self.assertRaisesRegex(ValueError,'active'):recover(self.store,patch['id'])
        done=create_server(self.store)._tool_manager.get_tool('patch_status').fn(patch['id'],recover=True)
        self.assertEqual(done['status'],'APPLIED');self.assertFalse(done['saved']);self.assertTrue(done['last_receipt']['recovered'])
        recovered=self.store.patch_get(patch['id']);self.assertEqual(recovered['receipts'][0],receipts[0])
        self.assertEqual(recovered['receipts'][-1]['prior_application_error'],'Lost response')
        self.assertNotIn('application_error',recovered['validation'])
        self.assertEqual(recover(self.store,patch['id']),recovered)
        self.assertEqual(self.db.execute("SELECT value FROM metadata WHERE key='world_revision'").fetchone()[0],'2')

    def test_recovery_resolves_only_recorded_unique_creation_identity(self):
        import copy
        import apply_patch as executor
        from spatial_twin.verification import recover
        klass='/Script/Engine.StaticMeshActor'
        self.db.execute('INSERT INTO class_schemas VALUES(?,?)',(klass,encode({'properties':{}})))
        self.put({'id':'mesh','kind':'StaticMesh','path':'/Game/mesh','local_bounds':[[-1,-1,-1],[1,1,1]],
                  'native_spawn_collision':{'enabled':False},'native_spawn_affects_navigation':False});self.db.commit()
        patch=validate(self.store,self.store.patch_create([{'type':'CREATE_ACTOR','target':'new','class':klass,'asset':'mesh',
                                                          'transform':{**IDENTITY,'position':[100,0,0]}}])['id'])
        self.assertEqual(patch['status'],'VALIDATED')
        mapping={ident:'native:'+ident for ident in patch['validation']['expected']}
        for ident,expected in patch['validation']['expected'].items():
            value=copy.deepcopy(expected);value['id']=mapping[ident]
            for field in ('actor_id','parent_id'):value[field]=mapping.get(value.get(field),value.get(field))
            value['path']='/World.Renamed' if value['kind']=='Actor' else '/World.Component'
            self.put(value);self.db.execute('UPDATE entities SET path=?,asset_id=? WHERE id=?',(value['path'],value.get('asset_id'),value['id']))
        self.db.execute("UPDATE metadata SET value='2' WHERE key='world_revision'");self.db.commit()
        sent=[{'state':'SENT','operation_id':'batch:'+patch['id']}];executor.update(self.store,patch['id'],'FAILED',sent)
        with self.assertRaisesRegex(ValueError,'identity is unknown'):recover(self.store,patch['id'])
        result={'complete':True,'created':[{'target':'new','operation_id':patch['operations'][0]['operation_id'],'actor':{'refPath':'/World.Original'}}]}
        executor.update(self.store,patch['id'],receipts=[{'state':'ACKNOWLEDGED','result':result}])
        created={**patch['validation']['expected']['new'],'id':mapping['new'],'path':'/World.Original'}
        for ident in (mapping['new'],'other-guid'):
            self.db.execute('INSERT INTO changes VALUES(?,?,?,?,?)',(2,ident,'CREATE',None,encode({**created,'id':ident})))
        self.db.commit()
        with self.assertRaisesRegex(ValueError,'identity is unknown or ambiguous'):recover(self.store,patch['id'])
        self.db.execute("DELETE FROM changes WHERE entity_id='other-guid'");self.db.commit()
        # Native terminal journal survives an overwritten/lost client ACK.
        executor.update(self.store,patch['id'],receipts=sent)
        with self.store.patches() as db:
            db.execute('INSERT INTO patch_results VALUES(?,?,?,?)',(patch['id'],'wrong','batch:'+patch['id'],encode(result)))
        with self.assertRaisesRegex(ValueError,'journal does not match'):recover(self.store,patch['id'])
        with self.store.patches() as db:
            db.execute('UPDATE patch_results SET operations_digest=? WHERE patch_id=?',(patch['validation']['operations_digest'],patch['id']))
        done=recover(self.store,patch['id']);self.assertEqual(done['status'],'APPLIED')
        self.assertEqual(done['receipts'][-1]['actor_ids']['new'],mapping['new'])
        self.assertTrue(done['receipts'][-1]['native_result_recorded'])
        self.assertEqual(done['receipts'][0],sent[0])

    def test_native_journal_runs_before_reply_even_on_partial_batch(self):
        from apply_patch import journal_script,mutation_script,TWIN
        entries=[{'operation_id':str(i),'toolset':'Registered','tool':'action','arguments':{'index':i}} for i in range(2)]
        for failed,journal_ok in ((False,True),(True,True),(False,False)):
            calls=[];saved=[]
            def native(name,arguments):
                calls.append(name);args=json.loads(arguments)
                if name==TWIN+'.spatial_twin_record_patch_result':
                    self.assertEqual(args['patch_id'],'patch');self.assertEqual(args['operations_digest'],'digest')
                    saved.append(json.loads(args['result_json']))
                    return {'returnValue':encode({'state':'RECORDED'} if journal_ok else {'error':'Disk failure'})}
                if failed and args['index']==1:raise RuntimeError('Native failure')
                return {'returnValue':True}
            scope={'execute_tool':native};exec(journal_script(mutation_script(entries),'patch','digest'),scope)
            if journal_ok:self.assertEqual(scope['run'](),saved[0])
            else:
                with self.assertRaisesRegex(ValueError,'effects may exist'):scope['run']()
            self.assertEqual(saved[0]['complete'],not failed)
            self.assertEqual(calls,['Registered.action','Registered.action',TWIN+'.spatial_twin_record_patch_result'])

    def test_recovery_execution_lease_releases_after_process_death(self):
        import subprocess,sys
        import apply_patch as executor
        from spatial_twin.verification import recover
        patch=validate(self.store,self.store.patch_create([{'type':'MOVE_ACTOR','target':'b','value':[30,0,0]}])['id'])
        for expected in patch['validation']['expected'].values():self.put(expected)
        self.db.execute("UPDATE metadata SET value='2' WHERE key='world_revision'");self.db.commit()
        sent=[{'state':'SENT'}];executor.update(self.store,patch['id'],'APPLYING',sent)
        with self.assertRaisesRegex(ValueError,'Legacy APPLYING'):recover(self.store,patch['id'])
        executor.update(self.store,patch['id'],receipts=[{'state':'APPLICATION_STARTED','lease_version':1},*sent])
        script="import sys,os;sys.path.insert(0,sys.argv[1]);from spatial_twin.store import Store\nwith Store(sys.argv[2]).application_lock():\n print('locked',flush=True)\n sys.stdin.readline()\n os._exit(0)"
        child=subprocess.Popen([sys.executable,'-c',script,str(Path(__file__).parent),str(self.root)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(),'locked')
            with self.assertRaisesRegex(ValueError,'active'):recover(self.store,patch['id'])
            child.communicate('exit\n',timeout=10);self.assertEqual(child.returncode,0)
        finally:
            if child.poll() is None:child.kill();child.communicate()
        self.assertEqual(recover(self.store,patch['id'])['status'],'APPLIED')

    def test_lost_save_response_requires_native_scope_content_and_clean_state(self):
        import hashlib,os
        import apply_patch as executor
        from spatial_twin.verification import recover,saved_completion
        package=self.root/'Owned.uasset';package.write_bytes(b'saved source')
        stat=package.stat();signature=f'{stat.st_size}|{621355968000000000+stat.st_mtime_ns//1000000000*10000000}'
        patch=validate(self.store,self.store.patch_create([{'type':'MOVE_ACTOR','target':'b','value':[30,0,0]}])['id'])
        for expected in patch['validation']['expected'].values():self.put({**expected,'package':'/Game/Owned','package_dirty':False})
        self.db.execute("UPDATE metadata SET value='2' WHERE key='world_revision'")
        self.db.execute('INSERT INTO package_sources VALUES(?,?)',(str(package),signature));self.db.commit()
        receipts=[{'state':'SENT','operation_id':'batch:'+patch['id']},{'state':'SENT','operation_id':'save:'+patch['id']}]
        executor.update(self.store,patch['id'],'FAILED',receipts,'Lost save response')
        result={'state':'SAVED','canonical_revision':2,'packages':[{'package':'/Game/Owned','path':str(package),'signature':signature,'content_md5':hashlib.md5(package.read_bytes()).hexdigest()}]}
        journal={'patch_id':patch['id'],'operations_digest':patch['validation']['operations_digest'],'dispatch_id':'save:'+patch['id'],'result':encode(result)}
        with self.store.read() as db:
            self.assertEqual(saved_completion(db,patch,receipts,journal,{},2),(True,2))
            with self.assertRaisesRegex(ValueError,'does not match'):saved_completion(db,patch,[],journal,{},2)
            with self.assertRaisesRegex(ValueError,'scope differs'):
                saved_completion(db,patch,receipts,{**journal,'result':encode({**result,'packages':[{**result['packages'][0],'package':'/Game/Other'}]})},{},2)
            package.write_bytes(b'other source');os.utime(package,ns=(stat.st_atime_ns,stat.st_mtime_ns))
            self.assertIn('content changed',saved_completion(db,patch,receipts,journal,{},2)[1])
            package.write_bytes(b'saved source');os.utime(package,ns=(stat.st_atime_ns,stat.st_mtime_ns))
        actor=Store.entity(self.db,'b');self.put({**actor,'package_dirty':True});self.db.commit()
        with self.store.read() as db:self.assertIn('dirty state',saved_completion(db,patch,receipts,journal,{},2)[1])
        self.put(actor);self.db.commit()
        unsaved=recover(self.store,patch['id'])
        self.assertEqual(unsaved['status'],'APPLIED');self.assertFalse(unsaved['receipts'][-1]['saved'])
        # A late native completion can promote saved evidence without replay,
        # even after an earlier offline recovery confirmed only the live effects.
        with self.store.patches() as db:db.execute('INSERT INTO patch_saves VALUES(?,?,?,?)',tuple(journal.values()))
        done=recover(self.store,patch['id'])
        self.assertTrue(done['receipts'][-1]['saved']);self.assertEqual(done['receipts'][-1]['native_saved_revision'],2)
        self.assertTrue(any(r.get('prior_application_error')=='Lost save response' for r in done['receipts']))

    def test_partial_save_finalization_conflicts_and_never_replays_actor_operations(self):
        import asyncio,hashlib
        from unittest.mock import patch as mock
        import apply_patch as executor
        draft=validate(self.store,self.store.patch_create([{'type':'MOVE_ACTOR','target':'b','value':[30,0,0]}])['id'])
        expected=draft['validation']['expected']
        for e in expected.values():self.put({**e,'package':'/Game/Owned','package_dirty':True})
        self.db.execute("UPDATE metadata SET value='2' WHERE key='world_revision'");self.db.commit()
        receipts=[{'state':'SENT','operation_id':'batch:'+draft['id']},{'state':'ACKNOWLEDGED','operation_id':'save:'+draft['id'],'result':{'state':'PARTIAL'}},
                  {'state':'CANONICAL_VERIFIED','canonical_revision':2,'actor_ids':{},'saved':False}]
        executor.update(self.store,draft['id'],'FAILED',receipts,'Partial save')
        with self.store.patches() as db:
            db.execute('INSERT INTO patch_save_progress VALUES(?,?,?)',(draft['id'],draft['validation']['operations_digest'],encode({'state':'PARTIAL'})))
        calls=[]
        async def finish(session,toolset,tool,args):
            calls.append((toolset,tool,args))
            self.assertEqual((toolset,tool,args),(executor.TWIN,'spatial_twin_save_patch',{'patch_id':draft['id'],'resume':True}))
            disk=self.root/'Owned.uasset';disk.write_bytes(b'saved after terminal partial result')
            stat=disk.stat();signature=f'{stat.st_size}|{621355968000000000+stat.st_mtime_ns//1000000000*10000000}'
            self.db.execute('INSERT INTO package_sources VALUES(?,?)',(str(disk),signature))
            for e in expected.values():self.put({**e,'package':'/Game/Owned','package_dirty':False})
            self.db.execute("UPDATE metadata SET value='3' WHERE key='world_revision'");self.db.commit()
            result={'state':'SAVED','canonical_revision':3,'packages':[{'package':'/Game/Owned','path':str(disk),'signature':signature,'content_md5':hashlib.md5(disk.read_bytes()).hexdigest()}]}
            with self.store.patches() as db:
                db.execute('INSERT INTO patch_saves VALUES(?,?,?,?)',(draft['id'],draft['validation']['operations_digest'],'save:'+draft['id'],encode(result)))
            return result
        checks=[]
        def heartbeat(*args,**kwargs):
            checks.append(1)
            return {'editor_connected':True,'synchronizer':{'pending_actors':1 if len(checks)==1 else 0}}
        with mock.object(executor,'sync_ready',return_value={'patch_partial_save':1}),mock.object(executor,'call_tool',side_effect=finish),mock.object(executor,'execute',side_effect=AssertionError('Authoring must never be replayed')),mock.object(self.store,'status',side_effect=heartbeat):
            original=Store.entity(self.db,'b');changed=json.loads(encode(original));changed['transform']['position'][0]+=1;self.put(changed);self.db.commit()
            with self.assertRaisesRegex(ValueError,'effects changed'):
                asyncio.run(executor.finalize_save(self.store,draft['id'],'unused',1,object()))
            self.assertEqual(calls,[])
            self.put(original);self.db.commit()
            done=asyncio.run(executor.finalize_save(self.store,draft['id'],'unused',1,object()))
            self.assertEqual(done['status'],'APPLIED');self.assertTrue(done['receipts'][-1]['saved'])
            again=asyncio.run(executor.finalize_save(self.store,draft['id'],'unused',1,object()))
            self.assertEqual(again,done);self.assertEqual(len(calls),1)

    def test_partial_save_requires_terminal_journal_before_editor_access(self):
        import asyncio
        from unittest.mock import patch as mock
        import apply_patch as executor
        draft=validate(self.store,self.store.patch_create([{'type':'MOVE_ACTOR','target':'b','value':[30,0,0]}])['id'])
        executor.update(self.store,draft['id'],'FAILED',[{'state':'SENT','operation_id':'save:'+draft['id']}])
        with mock.object(executor,'call_tool',side_effect=AssertionError('No editor access for uncertain save')),mock.object(executor,'sync_ready',side_effect=AssertionError('No sync either')):
            for state in (None,'STARTED','UNCERTAIN'):
                with self.store.patches() as db:
                    db.execute('DELETE FROM patch_save_progress')
                    if state:db.execute('INSERT INTO patch_save_progress VALUES(?,?,?)',(draft['id'],draft['validation']['operations_digest'],encode({'state':state})))
                with self.assertRaisesRegex(ValueError,'No terminal native partial save'):
                    asyncio.run(executor.finalize_save(self.store,draft['id'],'unused',1))

    def test_continuation_proves_uncertain_effect_and_never_repeats_prefix(self):
        import apply_patch as executor
        from spatial_twin.continuation import prepare_continuation
        patch=validate(self.store,self.store.patch_create([{'type':'MOVE_ACTOR','target':'b','value':[x,0,0]} for x in (30,40,50)])['id'])
        ids=[op['operation_id'] for op in patch['operations']]
        effects=patch['validation']['operation_effects']
        self.assertEqual([effects[i]['b']['transform']['position'][0] for i in ids],[30,40,50])
        self.assertNotIn('bounds',effects[ids[0]]['b'])
        read=create_server(self.store)._tool_manager.get_tool('world_read').fn
        with self.assertRaisesRegex(ValueError,'bounded Twin reads'):
            read([{'tool':'patch_continue','arguments':{'patch_id':patch['id']}}])
        receipts=[{'state':'SENT','operation_id':'batch:'+patch['id']}]
        executor.update(self.store,patch['id'],'FAILED',receipts)
        result={'complete':False,'completed':[{'operation_id':ids[0],'state':'ACKNOWLEDGED'}],'uncertain_operation_id':ids[1],'created':[]}
        with self.store.patches() as db:db.execute('INSERT INTO patch_results VALUES(?,?,?,?)',(patch['id'],patch['validation']['operations_digest'],'batch:'+patch['id'],encode(result)))
        self.put(effects[ids[0]]['b']);self.db.execute("UPDATE metadata SET value='2' WHERE key='world_revision'");self.db.commit()
        with self.assertRaisesRegex(ValueError,'uncertain or changed'):prepare_continuation(self.store,patch['id'])
        self.put(effects[ids[1]]['b']);self.db.commit()
        with self.store.application_lock():
            with self.assertRaisesRegex(ValueError,'active'):prepare_continuation(self.store,patch['id'])
        child=prepare_continuation(self.store,patch['id'])
        self.assertEqual(child['status'],'VALIDATED');self.assertEqual(child['operations'],patch['operations'][2:])
        self.assertEqual(child['base_revision'],2);self.assertEqual(child['continuation']['prefix_count'],2)
        self.assertEqual(prepare_continuation(self.store,patch['id']),child)
        original=self.store.patch_get(patch['id']);self.assertEqual(original['status'],'FAILED');self.assertEqual(original['receipts'],receipts)
        self.assertEqual(original['continued_by'],child['id'])
        self.assertEqual(Store.entity(self.db,'b')['transform']['position'],[40,0,0])

    def test_continuation_preserves_native_creation_identity_and_parent_proof(self):
        import copy
        import apply_patch as executor
        from spatial_twin.continuation import prepare_continuation
        from spatial_twin.verification import recover
        klass='/Script/Engine.StaticMeshActor'
        self.db.execute('INSERT INTO class_schemas VALUES(?,?)',(klass,encode({'properties':{}})))
        self.put({'id':'mesh','kind':'StaticMesh','path':'/Game/mesh','local_bounds':[[-1,-1,-1],[1,1,1]],'native_spawn_collision':{'enabled':False},'native_spawn_affects_navigation':False});self.db.commit()
        create=lambda target,x:{'type':'CREATE_ACTOR','target':target,'class':klass,'asset':'mesh','transform':{**IDENTITY,'position':[x,0,0]}}
        patch=validate(self.store,self.store.patch_create([create('first',100),{'type':'MOVE_ACTOR','target':'first','value':[200,0,0]},create('second',300)])['id'])
        self.assertEqual(patch['status'],'VALIDATED');ids=[op['operation_id'] for op in patch['operations']]
        def canonical(effects,revision):
            for ident,expected in effects.items():
                value=copy.deepcopy(expected);value['id']='native:'+ident
                for field in ('actor_id','parent_id'):
                    if value.get(field):value[field]='native:'+value[field]
                value['path']='/World.'+ident;self.put(value)
                self.db.execute('UPDATE entities SET path=?,asset_id=? WHERE id=?',(value['path'],value.get('asset_id'),value['id']))
                if value['kind']=='Actor':self.db.execute('INSERT INTO changes VALUES(?,?,?,?,?)',(revision,value['id'],'CREATE',None,encode(value)))
            self.db.execute('UPDATE metadata SET value=? WHERE key=?',(str(revision),'world_revision'));self.db.commit()
        canonical(patch['validation']['operation_effects'][ids[1]],2)
        result={'complete':False,'completed':[{'operation_id':ids[0],'state':'ACKNOWLEDGED'}],'uncertain_operation_id':ids[1],
                'created':[{'target':'first','operation_id':ids[0],'actor':{'refPath':'/World.first'},'properties_confirmed':True}]}
        executor.update(self.store,patch['id'],'FAILED',[{'state':'SENT','operation_id':'batch:'+patch['id']}])
        with self.store.patches() as db:db.execute('INSERT INTO patch_results VALUES(?,?,?,?)',(patch['id'],patch['validation']['operations_digest'],'batch:'+patch['id'],encode(result)))
        child=prepare_continuation(self.store,patch['id']);self.assertEqual(child['status'],'VALIDATED')
        self.assertEqual(child['operations'],patch['operations'][2:])
        with self.assertRaisesRegex(ValueError,'not been canonically confirmed'):recover(self.store,patch['id'])
        canonical(child['validation']['expected'],3)
        result={'complete':True,'created':[{'target':'second','operation_id':ids[2],'actor':{'refPath':'/World.second'},'properties_confirmed':True}]}
        executor.update(self.store,child['id'],'APPLIED',[{'state':'ACKNOWLEDGED','operation_id':'batch:'+child['id'],'result':result}])
        done=recover(self.store,patch['id']);self.assertEqual(done['status'],'APPLIED')
        self.assertEqual(done['receipts'][-1]['actor_ids']['first'],'native:first');self.assertEqual(done['receipts'][-1]['actor_ids']['second'],'native:second')

    def test_native_mutation_batch_preserves_order_and_partial_failure(self):
        from apply_patch import mutation_script
        entries=[{'operation_id':str(i),'toolset':'Registered','tool':'action','arguments':{'index':i}} for i in range(4)]
        for failure in (None,False,'exception'):
            calls=[]
            def native(name,arguments):
                self.assertEqual(name,'Registered.action');index=json.loads(arguments)['index'];calls.append(index)
                if index==2:
                    if failure=='exception':raise RuntimeError('Lost acknowledgement')
                    if failure is False:return {'returnValue':False}
                return {'returnValue':True}
            scope={'execute_tool':native};exec(mutation_script(entries),scope);result=scope['run']()
            self.assertEqual(result['complete'],failure is None)
            self.assertEqual(calls,[0,1,2,3] if failure is None else [0,1,2])
            self.assertEqual([v['operation_id'] for v in result['completed']],['0','1','2','3'] if failure is None else ['0','1'])
            if failure is not None:self.assertEqual(result['uncertain_operation_id'],'2')

    def test_dependent_batch_retains_created_ref_if_properties_fail(self):
        from apply_patch import dependent_script,SCENE,OBJECT
        entries=[{'operation_id':'create','toolset':SCENE,'tool':'spawn','arguments':{},'create_target':'new','properties':{'enabled':False}},
                 {'operation_id':'later','toolset':OBJECT,'tool':'never','arguments':{}}]
        calls=[]
        def native(name,args):
            calls.append(name)
            if name.endswith('.spawn'):return {'returnValue':{'refPath':'/World.Actor'}}
            if name.endswith('.get_root_component'):return {'returnValue':{'refPath':'/World.Actor.Root'}}
            return {'returnValue':False}
        scope={'execute_tool':native};exec(dependent_script(entries,{},{}),scope);result=scope['run']()
        self.assertFalse(result['complete']);self.assertEqual(result['completed'],[])
        self.assertEqual(result['uncertain_operation_id'],'create')
        self.assertEqual(result['created'],[{'operation_id':'create','target':'new','actor':{'refPath':'/World.Actor'},'properties_confirmed':False}])
        self.assertEqual(len(calls),3);self.assertFalse(any(n.endswith('.never') for n in calls))

    def test_authored_asset_shadow_is_separate_immutable_and_raycastable(self):
        from spatial_twin.staging import stage,get
        folder=self.root/'author';folder.mkdir()
        vertices=[[-1,-1,0],[1,-1,0],[0,1,0],[0,0,2]];tris=[[0,2,1],[0,1,3],[1,2,3],[2,0,3]]
        data=b'STG1'+struct.pack('<III',1,4,4)+b''.join(struct.pack('<3d',*p) for p in vertices)+b''.join(struct.pack('<3I',*t) for t in tris)
        (folder/'mesh.stg').write_bytes(data);(folder/'source.fbx').write_bytes(b'authoring fixture')
        manifest={'schema_version':1,'units':'cm','coordinates':'Unreal_LH_Zup','target_path':'/Game/Probe/SM_Probe.SM_Probe',
                  'source_file':'source.fbx','geometry_file':'mesh.stg','collision_files':['mesh.stg']}
        path=folder/'manifest.json';path.write_text(encode(manifest))
        self.put({'id':'asset:template','kind':'StaticMesh','native_spawn_collision':{'enabled':True},'native_spawn_affects_navigation':True})
        self.db.execute('INSERT INTO class_schemas VALUES(?,?)',('/Script/Engine.StaticMeshActor',encode({'properties':{}})));self.db.commit()
        before=self.store.status()['canonical_revision'];asset=stage(self.store,path,'asset:template')
        self.assertEqual(stage(self.store,path,'asset:template')['id'],asset['id'])
        self.assertEqual(get(self.store,asset['id'])['provenance'],'AUTHORED')
        self.assertNotIn('navigation_geometry_hash',asset)
        self.put({'id':'asset:template','kind':'StaticMesh','native_spawn_collision':{'enabled':True},'native_spawn_affects_navigation':True,
                  'native_spawn_navigation':{'authored_ucx_adapter':1,'navigation_fill_underneath':False,'navigation_filled_convex':False}})
        self.db.commit();predicted=stage(self.store,path,'asset:template')
        self.assertNotEqual(predicted['id'],asset['id'])
        self.assertEqual(predicted['navigation_input_coverage'],'authored_ucx_prediction_v1')
        nav_patch=self.store.patch_create([{'type':'CREATE_ACTOR','target':'nav','class':'/Script/Engine.StaticMeshActor','asset':predicted['id'],'transform':IDENTITY}])
        with self.store.read() as db:
            nav_overlay,errors=preview(self.store,db,nav_patch);self.assertFalse(errors)
            component=nav_overlay['nav:component:StaticMeshComponent']
            self.assertTrue(component['affects_navigation']);self.assertIs(component['navigation_fill_underneath'],False)
            self.assertIs(component['navigation_filled_convex'],False)
            self.assertEqual(component['navigation_geometry_hash'],predicted['navigation_geometry_hash'])
        patch=self.store.patch_create([{'type':'CREATE_ACTOR','target':'new','class':'/Script/Engine.StaticMeshActor','asset':asset['id'],
                                       'transform':{**IDENTITY,'position':[50,0,0]},'component_properties':{'bCanEverAffectNavigation':False}}])
        with self.store.read() as db:
            with self.assertRaises(ValueError):Store.entity(db,asset['id'])
            with self.assertRaises(ValueError):Queries(self.store,db).entity(asset['id'])
            overlay,errors=preview(self.store,db,patch);self.assertEqual(errors,[])
            q=Queries(self.store,db,overlay);hit=q.raycast([45,0,.5],[1,0,0],10)
            self.assertEqual(hit['hit']['actor_id'],'new');self.assertTrue(hit['complete'])
            self.assertFalse(q.entity('new:component:StaticMeshComponent')['affects_navigation'])
        self.assertEqual(validate(self.store,patch['id'])['status'],'VALIDATED')
        self.assertEqual(before,self.store.status()['canonical_revision'])
        manifest['collision_files']=['../geometry/plane.stg'];path.write_text(encode(manifest))
        with self.assertRaises(ValueError):stage(self.store,path,'asset:template')

    def test_authored_convex_float32_roundoff_does_not_allow_real_concavity(self):
        from spatial_twin.staging import stage
        folder=self.root/'quantized';folder.mkdir();(folder/'source.fbx').write_bytes(b'authoring fixture')
        self.put({'id':'asset:template','kind':'StaticMesh','native_spawn_collision':{'enabled':True},'native_spawn_affects_navigation':True});self.db.commit()
        triangles=[[0,2,1],[0,3,2],[4,5,6],[4,6,7],[0,1,5],[0,5,4],[1,2,6],[1,6,5],[2,3,7],[2,7,6],[3,0,4],[3,4,7]]
        for name,drift,accepted in (('roundoff',.000003,True),('concave',.1,False),('offset_roundoff',.000005,True),('offset_concave',.1,False)):
            vertices=[[-50,-50,0],[50,-50,0],[50,50,0],[-50,50,0],[-50,-50,100-drift],[50,-50,100],[50,50,100],[-50,50,100]]
            if name.startswith('offset_'):
                vertices=[[p[0]*.1+1000,p[1]*.1+1000,p[2]*.1+100] for p in vertices]
                vertices[4][2]=110-drift
            path=folder/(name+'.stg');path.write_bytes(b'STG1'+struct.pack('<III',1,8,12)+b''.join(struct.pack('<3d',*p) for p in vertices)+b''.join(struct.pack('<3I',*t) for t in triangles))
            manifest={'schema_version':1,'units':'cm','coordinates':'Unreal_LH_Zup','target_path':'/Game/Probe/SM_Probe.SM_Probe','source_file':'source.fbx','geometry_file':path.name,'collision_files':[path.name]}
            source=folder/(name+'.json');source.write_text(encode(manifest))
            if accepted:self.assertEqual(stage(self.store,source,'asset:template')['provenance'],'AUTHORED')
            else:
                with self.assertRaisesRegex(ValueError,'not convex'):stage(self.store,source,'asset:template')

    def test_import_parity_and_render_require_canonical_receipts(self):
        from materialize import same_points
        from render import confirmed_state,render_summary
        patch={'id':'p','base_revision':1,'status':'APPLIED','updated':0,'operations':[{}],
               'receipts':[{'state':'CANONICAL_VERIFIED','canonical_revision':2,'saved':False}]}
        compact=patch_summary(patch)
        self.assertEqual((compact['canonical_revision'],compact['saved'],compact['canonical_verified']),(2,False,True))
        summary=render_summary({'state':'VERIFIED','before':{'revision':2,'map':'/Fixture','patch':compact},
                               'capture':{'image_path':'actual.png'},'note':'Sequential sampling'})
        self.assertEqual((summary['canonical_revision'],summary['saved'],summary['canonical_verified']),(2,False,True))
        self.assertTrue(same_points([[0,0,0],[1,2,3]],[[.001,0,0],[1,2,3],[1,2,3]]))
        self.assertFalse(same_points([[0,0,0]],[[100,0,0]]))
        patch=self.store.patch_create([{'type':'MOVE_ACTOR','target':'a','value':[10,0,0]}])
        with self.assertRaisesRegex(ValueError,'connected'):confirmed_state(self.store,patch['id'])
        for native in ({'ready':False,'revision':1}, {'ready':True,'revision':2},
                       {'ready':True,'revision':1,'pending':1},
                       {'ready':True,'revision':1,'error':'Incomplete synchronization'}):
            with self.assertRaisesRegex(ValueError,'synchronization|CONFLICTED'):confirmed_state(self.store,patch['id'],native)
        # A fresh native flush can replace stale heartbeat evidence, but never patch receipts.
        with self.assertRaisesRegex(ValueError,'APPLIED'):confirmed_state(self.store,patch['id'],{'ready':True,'revision':1,'pending':0,'error':'','map':'/Fixture','project_id':None,'root':str(self.root)})

    def test_guided_queries_bound_tools_and_revision_without_model_inference(self):
        from spatial_twin.assist import prepare,dispatch,query
        server=create_server(self.store)
        with self.assertRaises(ValueError):prepare(server,[{'tool':'spatial_twin_apply_patch','arguments':{}}])
        with self.assertRaises(ValueError):prepare(server,[{'tool':'world_search','arguments':{'limit':500}}])
        with self.assertRaises(ValueError):prepare(server,[{'tool':'world_region','arguments':{'center':[0,0,0],'radius':float('inf')}}])
        summary=prepare(server,[{'tool':'world_region','arguments':{'center':[0,0,0],'radius':2,'detail':'summary'}}])
        self.assertNotIn('fields',summary[0]['arguments']);self.assertNotIn('limit',summary[0]['arguments'])
        batch=prepare(server,[{'tool':'spatial_raycast_batch','arguments':{'rays':[{'origin':[-10,0,0],'direction':[1,0,0],'max_distance':20}]}}])
        self.assertEqual(batch[0]['arguments']['fields'],['distance','actor_id','component_id'])
        self.assertEqual(dispatch(self.store,server,batch,{'choice':'0','confidence':1},1)['state'],'EXECUTED')
        calls=prepare(server,[{'tool':'entity_get','arguments':{'entity_id':'a','fields':['label']}}])
        self.assertEqual(prepare(server,[{'tool':'world_changes','arguments':{'since_revision':0}}])[0]['arguments']['fields'],['revision','entity_id'])
        for detail in ('operations','collisions'):
            self.assertNotIn('fields',prepare(server,[{'tool':'patch_preview','arguments':{'patch_id':'p','detail':detail}}])[0]['arguments'])
        waiting=dispatch(self.store,server,calls,{'choice':'NONE','confidence':1},1)
        self.assertEqual(waiting['state'],'NEEDS_REASONING')
        with self.assertRaises(ValueError):dispatch(self.store,server,calls,{'choice':'99','confidence':1},1)
        self.db.execute("UPDATE metadata SET value='2' WHERE key='world_revision'");self.db.commit()
        self.assertEqual(dispatch(self.store,server,calls,{'choice':'0','confidence':1},1)['state'],'CONFLICTED')
        result=query(self.store,server,'Source identity',[{'tool':'entity_get','arguments':{'entity_id':'a','fields':['label']}}])
        self.assertEqual(result['state'],'EXECUTED');self.assertEqual(result['model_usage']['input_tokens'],0)
        self.assertEqual(result['data']['entity']['id'],'a')

    def test_complex_collision_bvh_empty_region_is_a_confirmed_miss(self):
        from spatial_twin.collision import penetration
        complex_body={'id':'terrain','kind':'Component','transform':IDENTITY,'bounds':[[-100]*3,[100]*3],
                      'collision':{'enabled':True,'trace_mode':'UseComplexAsSimple','complex':['plane']}}
        box={'id':'candidate','kind':'Component','transform':{**IDENTITY,'position':[50,0,0]},'bounds':[[49,-1,-1],[51,1,1]],
             'collision':{'enabled':True,'shapes':[{'type':'box','extent':[1,1,1]}]}}
        with self.store.read() as db:
            result=penetration(Queries(self.store,db),complex_body,box)
            self.assertEqual(result['state'],'READY');self.assertFalse(result['overlap']);self.assertTrue(result['pruned_by_geometry_bvh'])
            complex_body['collision']['complex']=[]
            self.assertEqual(penetration(Queries(self.store,db),complex_body,box)['state'],'UNKNOWN')

    def test_validation_reuse_is_bound_to_operations_revision_and_validator(self):
        patch=self.store.patch_create([{'type':'DELETE_ACTOR','target':'b'}])
        first=validate(self.store,patch['id']);self.assertEqual(first['status'],'VALIDATED')
        second=validate(self.store,patch['id']);self.assertEqual(second['updated'],first['updated'])
        with self.store.patches() as db:
            bad={**first['validation'],'validator_fingerprint':'old-validator'}
            db.execute('UPDATE patches SET validation=? WHERE id=?',(encode(bad),patch['id']))
        refreshed=validate(self.store,patch['id'])
        self.assertNotEqual(refreshed['validation']['validator_fingerprint'],'old-validator')
        self.store.patch_update(patch['id'],[{'type':'DELETE_ACTOR','target':'a'}])
        changed=validate(self.store,patch['id'])
        self.assertNotEqual(changed['validation']['operations_digest'],first['validation']['operations_digest'])
        self.db.execute("UPDATE metadata SET value='2' WHERE key='world_revision'");self.db.commit()
        self.assertEqual(validate(self.store,patch['id'])['status'],'CONFLICTED')

    def test_running_mcp_refuses_replaced_source_before_certifying_patch(self):
        import subprocess,sys,shutil
        package=self.root/'runtime'/'spatial_twin';package.mkdir(parents=True)
        for source in (Path(__file__).parent/'spatial_twin').glob('*.py'):
            shutil.copy2(source,package/source.name)
        script=r'''
import sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from spatial_twin.store import Store
from spatial_twin.shadow import validate
from spatial_twin.server import create_server
store=Store(sys.argv[2]);patch=store.patch_create([{'type':'DELETE_ACTOR','target':'b'}])
first=validate(store,patch['id']);assert first['status']=='VALIDATED'
tool=create_server(store)._tool_manager.get_tool('world_status').fn
source=Path(sys.argv[1])/'spatial_twin/shadow.py'
with source.open('a') as f:f.write('\ndef validate(*args):raise ValueError("New validation rules")\n')
for call in (lambda:validate(store,patch['id']),tool):
    try:call()
    except ValueError as error:assert 'restart' in str(error).lower(),str(error)
    else:raise AssertionError('Old loaded runtime accepted replaced source')
assert store.patch_get(patch['id'])['validation']==first['validation']
'''
        result=subprocess.run([sys.executable,'-c',script,str(package.parent),str(self.root)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_cached_native_backend_refuses_replaced_library(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        from spatial_twin import file_signature
        from spatial_twin.navigation import backend
        path=self.root/'native.dll';path.write_bytes(b'loaded version')
        loaded=SimpleNamespace(_source_signature=file_signature(path))
        with patch('spatial_twin.navigation._load_backend',return_value=loaded):
            self.assertIs(backend('engine',str(path)),loaded)
            path.write_bytes(b'replacement version')
            with self.assertRaisesRegex(ValueError,'restart MCP'):
                backend('engine',str(path))

    def test_ray_batch_matches_individual_hits_and_rejects_invalid_batch(self):
        server=create_server(self.store);batch=server._tool_manager.get_tool('spatial_raycast_batch').fn
        single=server._tool_manager.get_tool('spatial_raycast').fn
        rays=[{'origin':[-10,0,0],'direction':[1,0,0],'max_distance':20},
              {'origin':[-10,10,0],'direction':[1,0,0],'max_distance':20}]
        result=batch(rays,fields=['distance','actor_id'])
        self.assertTrue(result['complete']);self.assertEqual(result['revision'],1)
        compact=batch(rays,detail='distances')
        self.assertEqual(compact['distances'],[v['hit']['distance'] if v['hit'] else None for v in result['results']])
        self.assertEqual(compact['incomplete'],[])
        with self.assertRaises(ValueError):batch(rays,detail='distances',fields=['actor_id'])
        for ray,value in zip(rays,result['results']):
            hit=single(**ray)['hit']
            self.assertEqual(value['hit'],None if hit is None else {k:hit[k] for k in ('distance','actor_id')})
        for invalid in ([],rays*65,[{**rays[0],'direction':[0,0,0]}],[{**rays[0],'max_distance':float('nan')}],[{**rays[0],'extra':1}]):
            with self.assertRaises(ValueError):batch(invalid)

    def test_ready_only_and_atomic_reader(self):
        self.db.execute("UPDATE snapshots SET state='BUILDING'"); self.db.commit()
        with self.assertRaisesRegex(ValueError,'READY'): self.store.read()
        self.assertEqual(self.store.status()['state'],'NO_READY_SNAPSHOT')
        self.db.execute("UPDATE snapshots SET state='READY'"); self.db.commit()
        with self.store.read() as reader:
            self.db.execute("DELETE FROM entities WHERE id='b'"); self.db.commit()
            self.assertEqual(Store.entity(reader,'b')['label'],'Door')
        with self.store.read() as reader:
            with self.assertRaises(ValueError): Store.entity(reader,'b')

    def test_spatial_index_and_double_refinement(self):
        offset=1e11
        self.put({'id':'large','kind':'Actor','bounds':[[offset+.1,0,0],[offset+.2,1,1]]}); self.db.commit()
        with self.store.read() as db:
            q=Queries(self.store,db)
            self.assertEqual([e['id'] for e in q.region([[offset+.15,0,0],[offset+.16,1,1]])],['large'])
            self.assertEqual(list(q.region([[offset+.3,0,0],[offset+.4,1,1]])),[])
            plan=list(db.execute('EXPLAIN QUERY PLAN SELECT rowid FROM spatial_bounds WHERE x0<=? AND x1>=?',(10,0)))
            self.assertTrue(any('VIRTUAL TABLE INDEX' in row[3] for row in plan))
            sql,args=Queries.region_sql([[0,0,0],[10,10,10]],('Component','Instance'))
            plan=[row[3] for row in db.execute('EXPLAIN QUERY PLAN '+sql,args)]
            self.assertIn('s VIRTUAL TABLE INDEX',plan[0])
            self.assertIn('INTEGER PRIMARY KEY',plan[1])

    def test_nearest_overlap_and_ray(self):
        with self.store.read() as db:
            q=Queries(self.store,db)
            self.assertEqual(q.nearest([18,0,0])['results'][0]['entity']['id'],'b')
            self.assertEqual(q.nearest([18,0,0],max_distance=.1)['results'],[])
            self.assertEqual(len(q.overlap([[-2]*3,[2]*3])['entities']),1)
            hit=q.raycast([-5,0,0],[1,0,0],10)
            self.assertTrue(hit['complete']); self.assertEqual(hit['hit']['actor_id'],'a')
            self.assertAlmostEqual(hit['hit']['distance'],5)
            self.assertEqual(hit['hit']['triangle'],0)
            self.assertAlmostEqual(g.length(hit['hit']['normal']),1)

    def test_support_summary_keeps_missing_and_unknown_distinct(self):
        component=Store.entity(self.db,'c')
        component['collision']={'enabled':True,'shapes':[{'type':'box','extent':[1,1,1]}],'trace_mode':'UseSimpleAsComplex'}
        self.put(component);self.db.commit()
        support=create_server(self.store)._tool_manager.get_tool('spatial_support').fn
        result=support([[0,0,5],[20,0,5]],[[0,0]],10)
        self.assertTrue(result['complete']);self.assertEqual(result['accepted'][0]['position'],[0,0,1.2])
        self.assertEqual(result['rejected'],[{'index':1,'reason':'missing_support'}])
        self.assertEqual(result['sample_count'],2);self.assertNotIn('distances',result)
        component['collision']={'enabled':True};self.put(component);self.db.commit()
        result=support([[0,0,5]],[[0,0]],10)
        self.assertFalse(result['complete']);self.assertEqual(result['rejected'][0]['reason'],'unknown_geometry')
        self.assertEqual(result['unknown_geometry'],['c'])
        with self.assertRaises(ValueError):support([[0,0,5]],[[0,0]],float('nan'))
        with self.assertRaises(ValueError):support([[0,0,5]]*128,[[0,0]]*512,10)

    def test_continuous_support_requires_one_actual_box_face_and_all_rectangle_corners(self):
        component=Store.entity(self.db,'c')
        component['collision']={'enabled':True,'shapes':[{'type':'box','extent':[2,2,1]}],
                                'trace_mode':'UseSimpleAsComplex'}
        self.put(component);self.db.commit()
        support=create_server(self.store)._tool_manager.get_tool('spatial_support').fn
        corners=[[-.5,-.5],[-.5,.5],[.5,-.5],[.5,.5],[0,0]]
        result=support([[0,0,5]],corners,10)
        evidence=result['accepted'][0]['continuous_support']
        self.assertEqual(evidence['state'],'PROVEN')
        self.assertEqual(evidence['primitive'],'box')
        self.assertEqual(evidence['component_id'],'c')
        self.assertEqual(evidence['normal'],[0,0,1])
        # Samples along a diamond do not cover the enclosing rectangle corners.
        incomplete=support([[0,0,5]],[[-.5,0],[0,-.5],[.5,0],[0,.5]],10)
        self.assertEqual(incomplete['accepted'][0]['continuous_support']['state'],'UNPROVEN')
        inside=support([[0,0,0]],corners,10)
        self.assertEqual(inside['accepted'],[])
        self.assertEqual(inside['rejected'],[{'index':0,'reason':'origin_inside_collision'}])
        # Several small disconnected boxes hit every sample but leave holes
        # between them. Sampling must not become continuous support proof.
        component['collision']['shapes']=[{'type':'box','extent':[.1,.1,1],
            'transform':{**IDENTITY,'position':[x,y,0]}} for x,y in corners]
        self.put(component);self.db.commit()
        disconnected=support([[0,0,5]],corners,10)
        self.assertTrue(disconnected['complete'])
        self.assertEqual(disconnected['accepted'][0]['continuous_support']['state'],'UNPROVEN')

    def test_mesh_hit_normal_preserves_source_face_under_reflected_nonuniform_scale(self):
        from spatial_twin import geometry as g
        mesh=g.Mesh([[-10,-10,0],[10,-10,0],[0,10,0]],[[0,1,2]])
        transform={**IDENTITY,'scale':[1,-2,3]}
        hit=g.mesh_hit(mesh,transform,[0,0,5],[0,0,-1],10)
        for coordinate in hit['position']:self.assertAlmostEqual(coordinate,0,places=12)
        self.assertEqual(hit['normal'],[0,0,1])
        self.assertEqual(hit['triangle'],0)

    def test_continuous_mesh_certificate_rejects_holes_concavity_nonmanifold_and_large_meshes(self):
        vertices=[[-1,-1,-1],[1,-1,-1],[1,1,-1],[-1,1,-1],
                  [-1,-1,1],[1,-1,1],[1,1,1],[-1,1,1]]
        faces=[[0,2,1],[0,3,2],[4,5,6],[4,6,7],[0,1,5],[0,5,4],
               [1,2,6],[1,6,5],[2,3,7],[2,7,6],[3,0,4],[3,4,7]]
        # Independent render vertices on every triangle model real UV seams;
        # topology is welded by source coordinates, not memory/index identity.
        seam_vertices=[vertices[i] for face in faces for i in face]
        seam_faces=[list(range(i,i+3)) for i in range(0,len(seam_vertices),3)]
        certificate=g.closed_convex_planes(g.Mesh(seam_vertices,seam_faces))
        self.assertIsNotNone(certificate)
        self.assertEqual(len(certificate[0]),12)
        self.assertIsNone(g.closed_convex_planes(g.Mesh(vertices,faces[:-1])))
        self.assertIsNone(g.closed_convex_planes(g.Mesh(vertices,faces+[faces[0]])))
        concave=[list(p) for p in vertices];concave[6]=[0,0,0]
        self.assertIsNone(g.closed_convex_planes(g.Mesh(concave,faces)))
        self.assertIsNone(g.closed_convex_planes(g.Mesh(vertices,faces*22)))

    def test_shadow_moves_contents_without_canonical_mutation(self):
        patch=self.store.patch_create([{'type':'MOVE_ACTOR','target':'a','value':[10,0,0]}])
        with self.store.read() as db:
            overlay,errors=preview(self.store,db,patch)
            self.assertEqual(errors,[])
            q=Queries(self.store,db,overlay)
            self.assertEqual(q.entity('c')['transform']['position'],[10,0,0])
            self.assertAlmostEqual(q.raycast([5,0,0],[1,0,0],10)['hit']['distance'],5)
            self.assertEqual(Store.entity(db,'a')['transform']['position'],[0,0,0])
        self.assertEqual(validate(self.store,patch['id'])['status'],'VALIDATED')

    def test_primitive_ray_initial_overlap_has_no_invented_surface_normal(self):
        for shape in ({'type':'sphere','radius':2},{'type':'box','extent':[2,2,2]},
                      {'type':'capsule','radius':2,'length':4}):
            hit=g.shape_hit(shape,IDENTITY,[0,0,0],[1,0,0],10)
            self.assertEqual(hit['distance'],0)
            self.assertIsNone(hit['normal'])
            self.assertTrue(hit['initial_overlap'])

    def test_conflict_and_invalid_inputs(self):
        patch=self.store.patch_create([{'type':'MOVE_ACTOR','target':'a','value':[10,0,0]}])
        self.db.execute("UPDATE metadata SET value='2' WHERE key='world_revision'"); self.db.commit()
        self.assertEqual(validate(self.store,patch['id'])['status'],'CONFLICTED')
        for value in ([float('nan'),0,0],[True,0,0],[1,2]):
            with self.assertRaises(ValueError): check_operations([{'type':'MOVE_ACTOR','target':'a','value':value}])

    def test_pagination_is_revision_and_query_bound(self):
        with self.store.read() as db:
            result=Store.page(db,'SELECT source FROM entities ORDER BY id',[],{'query':'all'},1)
            self.assertEqual(result['entities'][0]['id'],'a')
            second=Store.page(db,'SELECT source FROM entities ORDER BY id',[],{'query':'all'},1,result['cursor'])
            self.assertEqual(second['entities'][0]['id'],'b')
            with self.assertRaisesRegex(ValueError,'cursor'): Store.page(db,'SELECT source FROM entities',[],{'query':'other'},1,result['cursor'])
        self.db.execute("UPDATE metadata SET value='2' WHERE key='world_revision'"); self.db.commit()
        with self.store.read() as db:
            with self.assertRaises(ValueError): Store.page(db,'SELECT source FROM entities',[],{'query':'all'},1,result['cursor'])

    def test_source_changes_and_offline_status(self):
        self.db.execute("INSERT INTO changes VALUES(1,'a','CREATE',NULL,?)",(encode({'id':'a'}),)); self.db.commit()
        result=self.store.changes(0)
        self.assertEqual(result['entities'][0]['after'],{'id':'a'})
        self.db.execute("INSERT INTO changes VALUES(1,'c','PROPERTY',?,?)",(encode({'collision':{'enabled':False,'geometry':['large']*20}}),encode({'collision':{'enabled':True,'geometry':['large']*20}})));self.db.commit()
        fields=['before.collision.enabled','after.collision.enabled']
        changes=self.store.changes(0,fields=fields)
        record=next(e for e in changes['entities'] if e['id']=='c:1')
        self.assertEqual(record['before'],{'collision':{'enabled':False}})
        self.assertEqual(record['after'],{'collision':{'enabled':True}})
        diff=create_server(self.store)._tool_manager.get_tool('world_diff').fn(0,1,fields=fields)
        record=next(e for e in diff['entities'] if e['id']=='c')
        self.assertEqual(record['after'],{'collision':{'enabled':True}})
        self.assertFalse(self.store.status()['editor_connected'])
        self.assertEqual(self.store.status()['canonical_revision'],1)
        full=self.store.status();compact=self.store.status(include_counts=False)
        self.assertNotIn('counts',compact)
        self.assertEqual(compact,{k:v for k,v in full.items() if k!='counts'})

    def test_stale_navigation_never_uses_old_tiles(self):
        from unittest.mock import patch
        for state in ('BUILDING','STALE_NATIVE_REBUILD_PENDING'):
            self.db.execute("INSERT OR REPLACE INTO metadata VALUES('navigation_state',?)",(state,));self.db.commit()
            with patch.object(Navigation,'native',side_effect=AssertionError('Stale tiles must not reach Detour')):
                self.assertEqual(Navigation(self.store,'Human').status()['state'],'UNKNOWN')
                result=Navigation(self.store,'Human').query('path',[0,0,0],[1,0,0])
                self.assertEqual(result['state'],'UNKNOWN');self.assertEqual(result['path'],[])

    def test_portable_launcher_project_selection(self):
        import importlib.util
        launcher=Path(__file__).resolve().parents[2]/'SpatialTwinCodexPlugin/server.py'
        spec=importlib.util.spec_from_file_location('twin_launcher',launcher);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        workspace=self.root/'unrelated workspace';workspace.mkdir()
        project=workspace/'RacingGame.uproject';project.write_text('{}')
        self.assertEqual(module.project_path(workspace),project)
        content=workspace/'Content';content.mkdir();self.assertEqual(module.project_path(content),project)
        other=workspace/'Puzzle.uproject';other.write_text('{}')
        with self.assertRaisesRegex(ValueError,'Multiple'):module.project_path(workspace)
        self.assertEqual(module.project_path(workspace,str(other)),other)
        with self.assertRaises(ValueError):module.project_path(workspace,str(workspace/'absent.uproject'))
        from unittest.mock import patch
        with patch.dict(module.os.environ,{'SPATIAL_TWIN_PROJECT':str(project),'SPATIAL_TWIN_HOME':str(Path(__file__).resolve().parents[2]),'SPATIAL_TWIN_PYTHON':module.sys.executable}),patch.object(module.sys,'argv',['server.py']),patch.object(module.subprocess,'call',return_value=0) as call:
            self.assertEqual(module.main(),0)
            args=call.call_args.args[0];self.assertEqual(args[args.index('--tool-profile')+1],'focused')
        # Explicit CLI selection must precede discovery in an ambiguous/unrelated
        # workspace and override a different environment-selected project.
        with patch.dict(module.os.environ,{'SPATIAL_TWIN_PROJECT':str(project),'SPATIAL_TWIN_HOME':str(Path(__file__).resolve().parents[2]),'SPATIAL_TWIN_PYTHON':module.sys.executable}),patch.object(module.Path,'cwd',return_value=workspace),patch.object(module.sys,'argv',['server.py','--project',str(other),'--status']),patch.object(module.subprocess,'call',return_value=0) as call:
            self.assertEqual(module.main(),0)
            args=call.call_args.args[0];self.assertEqual(args.count('--project'),1)
            self.assertEqual(args[args.index('--project')+1],str(other))
            self.assertEqual(args[-1],'--status')

    def test_source_distribution_is_project_independent(self):
        import package_plugin
        distribution=self.root/'distribution';result=package_plugin.package(distribution)
        self.assertFalse(result['game_content_included'])
        manifest=json.loads((distribution/'manifest.json').read_text())
        self.assertTrue((distribution/'SpatialTwinCodexPlugin/runtime/tools/UnrealSpatialTwinMCP/server.py').is_file())
        self.assertFalse(any('/Binaries/' in p or '/Content/' in p or '__pycache__' in p for p in manifest['files']))
        config=json.loads((distribution/'SpatialTwinCodexPlugin/mcp.json').read_text())
        self.assertNotIn('env',config['mcpServers']['spatial-twin'])
        with self.assertRaises(ValueError):package_plugin.package(distribution)

    def test_public_receipts_redact_nested_local_paths_without_changing_measurements(self):
        from public_repository import public_text
        root=Path('C:/Synthetic/PrivateOwner/PrivateProject')
        payload={'tokens':74977,'seconds':88.4238097,'nested':json.dumps({'path':str(root/'output.json')})}
        public=json.loads(public_text(json.dumps(payload),root))
        self.assertEqual(public['tokens'],payload['tokens']);self.assertEqual(public['seconds'],payload['seconds'])
        self.assertNotIn('PrivateOwner',json.dumps(public));self.assertNotIn('PrivateProject',json.dumps(public))
        self.assertIn('<qualification-checkout>',json.loads(public['nested'])['path'])

    def test_navigation_selects_project_agent_without_game_specific_default(self):
        self.put({'id':'nav:Default','kind':'NavRegion','agent':'Default','path':'navigation/default.stn','tiles':[{'x':i,'y':0} for i in range(10000)]})
        self.db.commit()
        with self.store.read() as db:
            nav=Navigation(self.store,None);source,_=nav.source(db)
            self.assertEqual(source['agent'],'Default');self.assertEqual(nav.agent,'Default')
            compact,compact_path=nav.source(db,include_tiles=False)
            self.assertNotIn('tiles',compact);self.assertEqual(len(source['tiles']),10000)
            self.assertEqual(compact,{k:v for k,v in source.items() if k!='tiles'})
            self.assertEqual(compact_path,self.root/'navigation/default.stn')
        self.put({'id':'nav:Vehicle','kind':'NavRegion','agent':'Vehicle','path':'navigation/vehicle.stn'})
        self.db.commit()
        with self.store.read() as db:
            with self.assertRaisesRegex(ValueError,'Select an exported navigation agent'):Navigation(self.store,None).source(db)
            self.assertEqual(Navigation(self.store,'Vehicle').source(db)[0]['agent'],'Vehicle')

    def test_missing_navigation_owners_or_descriptor_components_cannot_validate(self):
        self.put({'id':'nav:Default','kind':'NavRegion','agent':'Default','path':'navigation/unused.stn'})
        self.db.commit()
        # No known component affects navigation. The old exporter omitted the
        # owner, so the absence of nav_changes must not certify this movement.
        proposal=self.store.patch_create([{'type':'MOVE_ACTOR','target':'a','value':[1000,0,0]}])
        result=validate(self.store,proposal['id'])
        self.assertEqual(result['status'],'INVALID')
        self.assertTrue(any('navigation owner coverage' in str(e) for e in result['validation']['errors']))
        self.db.execute("INSERT INTO metadata VALUES('navigation_owners_version','1')")
        a=Store.entity(self.db,'a');a['coverage']='descriptor_only';self.put(a);self.db.commit()
        result=validate(self.store,proposal['id'])
        self.assertEqual(result['status'],'INVALID')
        self.assertTrue(any('Descriptor-only' in str(e) for e in result['validation']['errors']))

    def test_empty_navigation_is_unknown_and_blocks_shadow_validation(self):
        self.put({'id':'nav:Human','kind':'NavRegion','agent':'Human','path':'navigation/empty.stn','active_tiles':0,
                  'detour_parameters':{},'settings':{}})
        (self.root/'navigation').mkdir();(self.root/'navigation/empty.stn').write_bytes(b'')
        component=Store.entity(self.db,'c');component['affects_navigation']=True;self.put(component);self.db.commit()
        self.assertEqual(Navigation(self.store,'Human').status()['state'],'UNKNOWN')
        patch=self.store.patch_create([{'type':'MOVE_ACTOR','target':'a','value':[10,0,0]}])
        result=validate(self.store,patch['id'])
        self.assertEqual(result['status'],'INVALID')
        self.assertEqual(result['validation']['navigation']['Human']['state'],'UNKNOWN')

    def test_mcp_exposes_offline_and_patch_surface(self):
        import asyncio
        names={tool.name for tool in asyncio.run(create_server(self.store).list_tools())}
        self.assertTrue({'world_status','world_region','spatial_raycast','navigation_path','patch_validate','world_diff'} <= names)
        self.assertFalse({'spawn_actor','execute_python','apply_patch'} & names)

    def test_patch_cognitive_lod_and_cursor(self):
        operations=[{'type':'MOVE_ACTOR','target':'a','value':[i,0,0]} for i in range(100)]
        patch=self.store.patch_create(operations)
        summary=patch_summary(patch)
        self.assertEqual(summary['operation_count'],100)
        self.assertNotIn('operations',summary)
        server=create_server(self.store)
        tool=server._tool_manager.get_tool('patch_preview').fn
        first=tool(patch['id'],limit=2,detail='operations')
        self.assertEqual(first['returned'],2)
        second=tool(patch['id'],limit=2,cursor=first['cursor'],detail='operations')
        self.assertEqual(second['entities'][0]['id'],'2')
        self.store.patch_update(patch['id'],operations[:3])
        with self.assertRaisesRegex(ValueError,'cursor'):
            tool(patch['id'],limit=2,cursor=first['cursor'],detail='operations')
        nearest=server._tool_manager.get_tool('spatial_nearest').fn([0,0,0],fields=['label'])
        self.assertEqual(nearest['results'][0]['entity'],{'id':'a','kind':'Actor','label':'PlayerStart'})

    def test_region_sql_pagination_and_sphere_summary(self):
        server=create_server(self.store)
        tool=server._tool_manager.get_tool('world_region').fn
        summary=tool([0,0,0],2)
        self.assertEqual(summary['total'],1)
        first=tool([10,0,0],20,detail='entities',limit=1,fields=['label'])
        self.assertEqual(first['entities'][0]['id'],'a')
        second=tool([10,0,0],20,detail='entities',limit=1,fields=['label'],cursor=first['cursor'])
        self.assertEqual(second['entities'][0]['id'],'b')
        overlap=server._tool_manager.get_tool('spatial_overlap').fn([[-2]*3,[2]*3],fields=['label'])
        self.assertEqual(overlap['entities'][0]['id'],'c')

    def test_asset_actor_usage_is_deduplicated(self):
        self.put({'id':'mesh','kind':'StaticMesh','path':'/Fixture.Mesh'})
        self.put({'id':'unused','kind':'Asset','path':'/Fixture.Unused'})
        self.put({'id':'d','kind':'Component','parent_id':'a','actor_id':'a','transform':IDENTITY})
        self.db.executemany('INSERT INTO relationships VALUES(?,?,?)',[('c','mesh','USES_ASSET'),('d','mesh','USES_ASSET')]);self.db.commit()
        tool=create_server(self.store)._tool_manager.get_tool('asset_usage').fn
        with self.assertRaisesRegex(ValueError,'Unknown asset ID'):tool('/Fixture.Mesh',detail='summary')
        with self.assertRaisesRegex(ValueError,'Unknown asset ID'):tool('missing')
        with self.assertRaisesRegex(ValueError,'Expected an asset ID'):tool('a')
        self.assertEqual(tool('unused',detail='summary')['total'],0)
        self.assertEqual(tool('mesh',kind='Actor',detail='summary')['total'],1)
        self.assertEqual(tool('mesh',kind='Component',detail='summary')['total'],2)
        self.assertEqual(tool('mesh',kind='Actor',fields=['label'])['entities'],[{'id':'a','kind':'Actor','label':'PlayerStart'}])


if __name__=='__main__': unittest.main()
