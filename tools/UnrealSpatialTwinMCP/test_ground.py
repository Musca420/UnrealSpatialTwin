import sys,unittest,json
from pathlib import Path
root=Path(__file__).resolve().parents[2];sys.path.insert(0,str(root/'tools/UnrealSpatialTwinMCP'))
import test_spatial_twin as fixtures
from spatial_twin.server import create_server
from spatial_twin.store import Store
class GroundTests(unittest.TestCase):
 def setUp(self):
  self.f=fixtures.TwinTest();self.f.setUp()
  self.f.put({'id':'floor','kind':'Component','actor_id':'b','parent_id':'b','transform':{**fixtures.IDENTITY,'position':[0,0,-4]},'bounds':[[-3,-3,-5],[3,3,-3]],'local_bounds':[[-3,-3,-1],[3,3,1]],'collision':{'enabled':True,'shapes':[{'type':'box','extent':[3,3,1]}],'trace_mode':'UseSimpleAsComplex'}})
  self.f.db.commit();self.ground=create_server(self.f.store)._tool_manager.get_tool('patch_ground').fn
 def tearDown(self):self.f.tearDown()
 def test_actual_support_and_only_vertical_moves_full_proof_remains_available(self):
  original=Store.entity(self.f.db,'a');result=self.ground(['a'],1,10,include_operations=True,detail='full')
  self.assertEqual(result['accepted_count'],1);self.assertEqual(result['continuous_support']['state'],'PROVEN')
  op=result['operations'][0];self.assertEqual(op['type'],'MOVE_ACTOR');self.assertEqual(op['target'],'a');self.assertAlmostEqual(op['value'][2],-1.8)
  self.assertEqual(op['value'][:2],[0,0]);self.assertEqual(result['accepted'][0]['continuous_support']['component_id'],'floor')
  self.assertEqual(Store.entity(self.f.db,'a'),original);self.assertEqual(Store.revision(self.f.db),1)
  compact=self.ground(['a'],1,10);self.assertNotIn('accepted',compact)
 def test_moving_floor_missing_support_and_revision_refuse_without_partial_patch(self):
  floor=Store.entity(self.f.db,'floor');floor['actor_id']='a';self.f.put(floor);self.f.db.commit()
  result=self.ground(['a'],1,10);self.assertEqual(result['status'],'BLOCKED');self.assertEqual(result['rejected'][0]['reason'],'support_moves_with_plan');self.assertNotIn('id',result)
  with self.assertRaises(ValueError):self.ground(['a'],0,10)
  result=self.ground(['a'],1,.5);self.assertEqual(result['status'],'BLOCKED');self.assertEqual(result['rejected'][0]['reason'],'missing_support')
 def test_attachment_and_boundary_errors_are_rejected(self):
  for ids in ([],['a','a'],['c']):
   with self.assertRaises(ValueError):self.ground(ids,1,10)
  for args in ({'max_tilt_degrees':90},{'detail':'invalid'},{'clearance':float('nan')}):
   with self.assertRaises(ValueError):self.ground(['a'],1,10,**args)
  self.f.db.execute("INSERT INTO relationships VALUES('a','b','ATTACHED_TO')");self.f.db.commit()
  with self.assertRaises(ValueError):self.ground(['a','b'],1,10)
if __name__=='__main__':unittest.main(verbosity=2)
