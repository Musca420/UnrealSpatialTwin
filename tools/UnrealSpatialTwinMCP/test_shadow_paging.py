import copy,json,sys,unittest
from pathlib import Path
from unittest.mock import patch
root=Path(__file__).resolve().parents[2];sys.path.insert(0,str(root/'tools/UnrealSpatialTwinMCP'))
from spatial_twin.server import create_server
from spatial_twin.query import Queries
from spatial_twin.store import Store
sys.path.append(str(root/'tools/UnrealSpatialTwinMCP'))
import test_spatial_twin as fixtures

class PagingRegression(unittest.TestCase):
 def setUp(self):
  self.fixture=fixtures.TwinTest();self.fixture.setUp();self.store=self.fixture.store
  with self.store.read() as db:e=Store.entity(db,'b')
  e['local_bounds']=[[-1,-1,-1],[1,1,1]];self.fixture.put(e)
  for i in range(500):
   self.fixture.put({'id':f'dense:{i:04d}','kind':'Component','class':'Dense','bounds':[[i,-1,-1],[i+1,1,1]],
    'local_bounds':[[-.5,-1,-1],[.5,1,1]],'transform':{'position':[i+.5,0,0],'rotation':[0,0,0,1],'scale':[1,1,1]},
    'properties':{'large_unused_source':'x'*16384}})
  self.fixture.db.commit();self.server=create_server(self.store)
  self.ident=self.store.patch_create([{'type':'MOVE_ACTOR','target':'b','value':[40,0,0]}],base_revision=1)['id']
 def tearDown(self):self.fixture.tearDown()
 def test_first_page_decodes_only_requested_dense_sources(self):
  for name,args in [('world_region',{'center':[0,0,0],'radius':1000,'detail':'entities'}),
                    ('spatial_overlap',{'aabb':[[-1000,-1000,-1000],[1000,1000,1000]]})]:
   fn=self.server._tool_manager.get_tool(name).fn;loads=json.loads;count=0
   def counted(value,*a,**kw):
    nonlocal count
    if isinstance(value,str) and 'large_unused_source' in value:count+=1
    return loads(value,*a,**kw)
   with patch.object(json,'loads',side_effect=counted):
    result=fn(**args,kind='Component',world='shadow',patch_id=self.ident,limit=5,fields=['transform'])
   self.assertEqual(result['returned'],5);self.assertTrue(result['cursor'])
   self.assertLessEqual(count,5, f'{name}: decoded {count} dense sources for five results')
   self.assertTrue(all('properties' not in e for e in result['entities']))
 def test_overlay_creates_moves_deletes_and_pages_equal_indexed_oracle(self):
  with self.store.read() as db:
   e=Store.entity(db,'dense:0001');e['bounds']=[[2000,-1,-1],[2001,1,1]];e['transform']['position']=[2000.5,0,0]
   fresh=copy.deepcopy(e);fresh.update(id='fresh:0001',bounds=[[1,-1,-1],[2,1,1]]);fresh['transform']['position']=[1.5,0,0]
   q=Queries(self.store,db,{'dense:0000':None,'dense:0001':e,'fresh:0001':fresh})
   box=[[-2,-2,-2],[10,2,2]];expected=sorted(e['id'] for e in q.region(box,'Component'))
   ids=[];cursor=None
   while True:
    page=q.region_page(box,'Component',{'box':box},3,cursor,['transform'])
    ids.extend(e['id'] for e in page['entities']);cursor=page['cursor']
    if not cursor:break
   self.assertEqual(ids,expected);self.assertEqual(len(ids),len(set(ids)))

if __name__=='__main__':unittest.main(verbosity=2)
