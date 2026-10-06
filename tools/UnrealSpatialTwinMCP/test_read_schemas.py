import sys,unittest
from pathlib import Path
import json
root=Path(__file__).resolve().parents[2];sys.path.insert(0,str(root/'tools/UnrealSpatialTwinMCP'))
from spatial_twin.server import create_server
from spatial_twin.store import Store
sys.path.append(str(root/'tools/UnrealSpatialTwinMCP'))
import test_spatial_twin as fixtures

class SourceSchemaBatch(unittest.TestCase):
 def setUp(self):
  self.f=fixtures.TwinTest();self.f.setUp();self.server=create_server(self.f.store)
 def tearDown(self):self.f.tearDown()
 def test_asset_impact_summary_deduplicates_owners_and_preserves_unknowns_and_limits(self):
  mesh='asset:summary';package='asset_package:/Summary'
  self.f.put({'id':mesh,'kind':'StaticMesh','path':'/Summary.Mesh'})
  self.f.put({'id':package,'kind':'Asset','path':'/Summary','asset_type':'Package','dependencies_state':'CURRENT'})
  self.f.db.execute('INSERT INTO relationships VALUES(?,?,?)',(mesh,package,'IN_PACKAGE'))
  for ident,label in [('a','Bench_Move_00'),('b','Bench_Tree_01')]:
   e=Store.entity(self.f.db,ident);e['label']=label;self.f.put(e)
  self.f.put({'id':'other','kind':'Actor','label':'Other_00'})
  for ident in ('a','c','b','other'):self.f.db.execute('INSERT INTO relationships VALUES(?,?,?)',(ident,mesh,'USES_ASSET'))
  for i in range(21):
   dep='package:'+str(i);self.f.put({'id':dep,'kind':'Asset','path':'/Dependency'+str(i)})
   self.f.db.execute('INSERT INTO relationships VALUES(?,?,?)',(package,dep,'DEPENDS_ON'))
  self.f.db.commit();read=self.server._tool_manager.get_tool('world_read').fn
  calls=[{'tool':'asset_get','arguments':{'asset_id':mesh,'fields':['path'],'include_dependencies':True}},
   {'tool':'asset_usage','arguments':{'asset_id':mesh,'kind':'Actor','detail':'summary','label_prefix':'Bench_','group_by':'label_prefix','label_group_depth':2}}]
  rows=read(calls)['results'];usage=rows[1]['data'];asset=rows[0]['data']
  self.assertEqual(usage['total'],2);self.assertEqual(usage['groups'],{'Bench_Move':1,'Bench_Tree':1})
  self.assertEqual(usage['bounds'],[[-1,-1,-1],[21,1,1]]);self.assertTrue(usage['bounds_complete']);self.assertTrue(usage['groups_complete'])
  self.assertEqual(asset['dependency_count'],21);self.assertEqual(len(asset['dependencies']),20);self.assertFalse(asset['dependencies_complete'])
  self.assertEqual(asset['dependency_query']['arguments']['entity_id'],package)
  args=dict(calls[1]['arguments'],limit=1);self.assertFalse(read([{'tool':'asset_usage','arguments':args}])['results'][0]['data']['groups_complete'])
  args=dict(calls[1]['arguments']);args.pop('label_prefix');self.assertFalse(read([{'tool':'asset_usage','arguments':args}])['results'][0]['data']['bounds_complete'])
  self.f.db.execute('DELETE FROM relationships WHERE source=? AND kind=?',(package,'DEPENDS_ON'));self.f.db.commit()
  self.assertTrue(read([calls[0]])['results'][0]['data']['dependencies_complete'])
  self.f.db.execute('UPDATE entities SET source=json_set(source,\'$.dependencies_state\',\'UNKNOWN\') WHERE id=?',(package,));self.f.db.commit()
  self.assertFalse(read([calls[0]])['results'][0]['data']['dependencies_complete'])
 def test_asset_dependencies_never_treat_missing_or_unknown_registry_as_empty(self):
  asset=self.server._tool_manager.get_tool('asset_get').fn
  relations=self.server._tool_manager.get_tool('entity_relationships').fn
  ident='asset:dependency-test'
  self.f.put({'id':ident,'kind':'Asset','path':'/DependencyTest'});self.f.db.commit()
  self.assertEqual(asset(ident,fields=['path'])['dependency_coverage']['state'],'UNKNOWN')
  package={'id':'asset_package:/Known','kind':'Asset','path':'/Known','asset_type':'Package','dependencies_state':'UNKNOWN'}
  self.f.put(package)
  self.f.db.execute('INSERT INTO relationships VALUES(?,?,?)',(ident,package['id'],'IN_PACKAGE'));self.f.db.commit()
  self.assertFalse(relations(package['id'],'outgoing','DEPENDS_ON')['complete'])
  package['dependencies_state']='CURRENT'
  self.f.db.execute('UPDATE entities SET source=? WHERE id=?',(json.dumps(package),package['id']));self.f.db.commit()
  self.assertTrue(relations(package['id'],'outgoing','DEPENDS_ON')['complete'])
  self.assertEqual(asset(ident,include_dependencies=True)['dependency_coverage']['live_state'],'UNKNOWN')
  package.update(live_dependencies_state='UNKNOWN_UNSAVED',package_dirty=True)
  self.f.db.execute('UPDATE entities SET source=? WHERE id=?',(json.dumps(package),package['id']));self.f.db.commit()
  result=asset(ident,include_dependencies=True)
  self.assertTrue(result['dependencies_complete']) # Complete disk projection, not an empty/live proof.
  self.assertEqual(result['dependency_coverage'],{'state':'CURRENT','package_id':package['id'],'source':'asset_registry_disk','live_state':'UNKNOWN_UNSAVED','package_dirty':True})
  graph=relations(package['id'],'outgoing','DEPENDS_ON')
  self.assertTrue(graph['complete']);self.assertEqual(graph['live_dependency_state'],'UNKNOWN_UNSAVED')
  self.assertEqual(graph['dependency_source'],'asset_registry_disk');self.assertTrue(graph['package_dirty'])
  package.update(live_dependencies_state='CURRENT',package_dirty=False)
  self.f.db.execute('UPDATE entities SET source=? WHERE id=?',(json.dumps(package),package['id']));self.f.db.commit()
  self.assertEqual(asset(ident,include_dependencies=True)['dependency_coverage']['live_state'],'CURRENT')

  read=self.server._tool_manager.get_tool('world_read').fn
  batch=read([{'tool':'entity_relationships','arguments':{'entity_id':package['id'],'direction':'outgoing','kind':'DEPENDS_ON'}}])
  self.assertTrue(batch['results'][0]['data']['complete'])
  with self.assertRaises(ValueError):read([{'tool':'entity_relationships','arguments':{'entity_id':package['id'],'direction':'invalid'}}])
  with self.assertRaises(ValueError):read([{'tool':'spatial_raycast','arguments':{'origin':[0,0,0],'direction':[float('nan'),0,1],'max_distance':10}}])
  self.assertEqual(asset(ident)['dependency_coverage'],{'state':'CURRENT','package_id':package['id'],'source':'asset_registry_disk','live_state':'CURRENT','package_dirty':False})
  with self.assertRaises(ValueError):relations('does-not-exist')
 def test_next_query_preserves_selection_and_refuses_stale_revision(self):
  read=self.server._tool_manager.get_tool('world_read').fn
  args={'kind':'Actor','limit':1,'fields':['label','bounds'],'component_fields':['actor_id'],'component_limit':1}
  first=read([{'tool':'world_search','arguments':args}])['results'][0]
  following=first['next_query']
  self.assertEqual(following,{'tool':'world_search','arguments':{**args,'cursor':first['data']['cursor']}})
  last=read([following])['results'][0]
  self.assertNotIn('next_query',last)
  self.assertNotEqual(first['data']['entities'][0]['id'],last['data']['entities'][0]['id'])
  self.assertEqual(last['data']['revision'],first['data']['revision'])
  self.f.db.execute("UPDATE metadata SET value='2' WHERE key='world_revision'");self.f.db.commit()
  with self.assertRaises(ValueError):read([following])
 def test_source_and_only_missing_schemas_match_registered_contract(self):
  read=self.server._tool_manager.get_tool('world_read').fn
  describe=self.server._tool_manager.get_tool('tool_describe').fn
  result=read([{'tool':'entity_get','arguments':{'entity_id':'a','fields':['transform']}}],schemas=['patch_place'])
  self.assertEqual(result['state'],'READY');self.assertEqual(result['status']['canonical_revision'],1)
  self.assertEqual(result['tool_schemas'],describe(['patch_place'])['tools'])
  self.assertEqual(result['results'][0]['data']['entity']['id'],'a')
  self.assertEqual(result['model_calls'],0)
  with self.f.store.read() as db:self.assertEqual(Store.revision(db),1)
 def test_unknown_duplicate_or_oversized_schema_request_fails(self):
  read=self.server._tool_manager.get_tool('world_read').fn
  for names in ([],['missing'],['patch_place','patch_place'],['world_status']*9):
   with self.assertRaises(ValueError):read([{'tool':'entity_get','arguments':{'entity_id':'a'}}],schemas=names)
 def test_compact_support_preserves_proof_and_keeps_bulk_read_inline(self):
  from unittest.mock import patch
  component=Store.entity(self.f.db,'c')
  component['collision']={'enabled':True,'shapes':[{'type':'box','extent':[2,2,1]}],'trace_mode':'UseSimpleAsComplex'}
  self.f.put(component);self.f.db.commit()
  support=self.server._tool_manager.get_tool('spatial_support').fn
  args={'candidates':[[0,0,5]]*32,'offsets':[[-.5,-.5],[-.5,.5],[.5,-.5],[.5,.5],[0,0]],'max_distance':10}
  full=support(**args,detail='full');compact=support(**args)
  for a,b in zip(full['accepted'],compact['accepted']):
   self.assertEqual({k:v for k,v in a.items() if k!='continuous_support'},{k:v for k,v in b.items() if k!='continuous_support'})
   self.assertEqual(b['continuous_support'],{k:v for k,v in a['continuous_support'].items() if k!='scope'})
   self.assertEqual(b['continuous_support']['state'],'PROVEN')
   self.assertIn('primitive_index',b['continuous_support'])
  self.assertEqual(full['rejected'],compact['rejected']);self.assertEqual(full['unknown_geometry'],compact['unknown_geometry'])
  result=self.server._tool_manager.get_tool('world_read').fn([{'tool':'spatial_support','arguments':args}])
  self.assertEqual(result['results'][0]['data'],compact);self.assertLess(result['response_bytes'],12000)
  with patch.object(self.f.store,'read',side_effect=AssertionError('Must reject before opening database')):
   with self.assertRaises(ValueError):support(**args,detail='invalid')
 def test_large_requested_metadata_keeps_source_bounded_and_schema_exact_on_disk(self):
  tool=self.server._tool_manager.get_tool('patch_place');tool.parameters['description']='x'*20000
  read=self.server._tool_manager.get_tool('world_read').fn
  result=read([{'tool':'entity_get','arguments':{'entity_id':'a','fields':['transform']}}],schemas=['patch_place'])
  self.assertEqual(result['state'],'READY');self.assertNotIn('tool_schemas',result)
  self.assertLess(len(json.dumps(result).encode()),12000)
  self.assertEqual(result['results'][0]['data']['entity']['id'],'a')
  saved=json.loads(Path(result['tool_schemas_reference']).read_text())
  self.assertEqual(saved,self.server._tool_manager.get_tool('tool_describe').fn(['patch_place'])['tools'])

if __name__=='__main__':unittest.main(verbosity=2)
