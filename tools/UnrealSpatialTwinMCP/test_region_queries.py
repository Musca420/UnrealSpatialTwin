import copy,sys,unittest
from pathlib import Path
from unittest.mock import patch
root=Path(__file__).resolve().parents[2];sys.path.insert(0,str(root/'tools/UnrealSpatialTwinMCP'))
import test_spatial_twin as fixtures
from spatial_twin.store import Store
from spatial_twin.query import Queries
from spatial_twin.server import create_server

class RegionRegression(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.TwinTest();self.fixture.setUp()
        self.store=self.fixture.store
        self.region=create_server(self.store)._tool_manager.get_tool('world_region').fn
    def tearDown(self):self.fixture.tearDown()
    def offset_collision(self):
        with self.store.read() as db:e=Store.entity(db,'c')
        e['collision_bounds']=[[99,-1,-1],[101,1,1]]
        e['collision_local_bounds']=copy.deepcopy(e['collision_bounds'])
        self.fixture.put(e);self.fixture.db.commit()
    def draft(self,operations):return self.store.patch_create(operations,base_revision=1)['id']
    def test_unchanged_collision_bounds_identical_in_shadow_summary_and_page(self):
        self.offset_collision()
        ident=self.draft([{'type':'MOVE_ACTOR','target':'b','value':[40,0,0]}])
        for detail in ('summary','entities'):
            canonical=self.region([100,0,0],2,kind='Component',detail=detail)
            shadow=self.region([100,0,0],2,kind='Component',detail=detail,world='shadow',patch_id=ident)
            if detail=='summary':
                self.assertEqual(canonical['counts'],{'Component':1})
                self.assertEqual(shadow['counts'],canonical['counts'])
            else:
                self.assertEqual([e['id'] for e in shadow['entities']],['c'])
    def test_moved_and_deleted_collision_bounds_not_counted_twice(self):
        self.offset_collision()
        moved=self.draft([{'type':'MOVE_ACTOR','target':'a','value':[1000,0,0]}])
        self.assertEqual(self.region([100,0,0],2,kind='Component',world='shadow',patch_id=moved)['total'],0)
        self.assertEqual(self.region([1100,0,0],2,kind='Component',world='shadow',patch_id=moved)['counts'],{'Component':1})
        deleted=self.draft([{'type':'DELETE_ACTOR','target':'a'}])
        self.assertEqual(self.region([100,0,0],2,kind='Component',world='shadow',patch_id=deleted)['total'],0)
    def test_large_shadow_summary_does_not_decode_every_source(self):
        for i in range(500):
            x=100+i
            self.fixture.put({'id':f'dense:{i}','kind':'Component','class':'Dense',
                'bounds':[[x-1,-1,-1],[x+1,1,1]],'local_bounds':[[-1,-1,-1],[1,1,1]],
                'transform':{'position':[x,0,0],'rotation':[0,0,0,1],'scale':[1,1,1]},
                'properties':{'large_unused_source':'x'*4096}})
        self.fixture.db.commit()
        ident=self.draft([{'type':'MOVE_ACTOR','target':'b','value':[40,0,0]}])
        with patch.object(Queries,'region',side_effect=AssertionError('Summary must aggregate indexed rows, not decode all source blobs')):
            result=self.region([0,0,0],1000,kind='Component',world='shadow',patch_id=ident)
        self.assertEqual(result['counts'],{'Component':1,'Dense':500})

if __name__=='__main__':unittest.main(verbosity=2)
