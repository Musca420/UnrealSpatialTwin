import functools,sys,unittest
from pathlib import Path
root=Path(__file__).resolve().parents[2];sys.path.insert(0,str(root/'tools/UnrealSpatialTwinMCP'))
import test_spatial_twin as fixtures
from spatial_twin.store import Store
from spatial_twin.server import create_server

class ShadowReadRegression(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.TwinTest();self.fixture.setUp();self.store=self.fixture.store
        with self.store.read() as db:e=Store.entity(db,'b')
        e['local_bounds']=[[-1,-1,-1],[1,1,1]];self.fixture.put(e);self.fixture.db.commit()
        self.server=create_server(self.store)
    def tearDown(self):self.fixture.tearDown()
    def draft(self):return self.store.patch_create([{'type':'MOVE_ACTOR','target':'b','value':[40,0,0]}],base_revision=1)['id']
    def test_region_and_overlap_cursor_refuse_changed_shadow(self):
        for name,args in [('world_region',{'center':[0,0,0],'radius':100,'detail':'entities'}),
                          ('spatial_overlap',{'aabb':[[-100,-100,-100],[100,100,100]]})]:
            ident=self.draft();fn=self.server._tool_manager.get_tool(name).fn
            args={**args,'kind':'Actor','world':'shadow','patch_id':ident,'limit':1,'fields':['transform']}
            first=fn(**args);self.assertTrue(first['cursor'])
            self.store.patch_update(ident,[{'type':'MOVE_ACTOR','target':'b','value':[60,0,0]}])
            with self.assertRaises(ValueError):fn(**args,cursor=first['cursor'])
    def test_batch_discards_concurrent_patch_update_with_unchanged_canonical(self):
        ident=self.draft();tool=self.server._tool_manager.get_tool('entity_get');original=tool.fn.__wrapped__
        calls=0
        @functools.wraps(original)
        def interleaved(**args):
            nonlocal calls
            result=original(**args);calls+=1
            if calls==1:self.store.patch_update(ident,[{'type':'MOVE_ACTOR','target':'b','value':[60,0,0]}])
            return result
        tool.fn.__wrapped__=interleaved
        try:
            result=self.server._tool_manager.get_tool('world_read').fn(queries=[
                {'tool':'entity_get','arguments':{'entity_id':'b','world':'shadow','patch_id':ident,'fields':['transform']}}
                for _ in range(2)])
        finally:tool.fn.__wrapped__=original
        self.assertEqual(result['state'],'CONFLICTED')
        self.assertNotIn('results',result)
        self.assertEqual(self.store.status(False)['canonical_revision'],1)

if __name__=='__main__':unittest.main(verbosity=2)
