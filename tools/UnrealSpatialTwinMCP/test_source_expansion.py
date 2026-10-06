import unittest
import test_spatial_twin as fixtures
from spatial_twin.server import create_server
from spatial_twin.store import Store

class SourceExpansionTests(unittest.TestCase):
    def test_bounded_atomic_children_match_existing_pages_and_cursors(self):
        f=fixtures.TwinTest();f.setUp()
        try:
            for i in range(7):
                f.put(dict(id=f'child{i}',kind='Component',parent_id='a',actor_id='a',transform=fixtures.IDENTITY,asset_id='asset:mesh'))
            f.db.commit()
            server=create_server(f.store);search=server._tool_manager.get_tool('world_search').fn
            children=server._tool_manager.get_tool('entity_children').fn
            fields=['transform','asset_id'];revision=Store.revision(f.db)
            result=search(kind='Actor',limit=1,fields=['label'],component_fields=fields,component_limit=2)
            actor=result['entities'][0];page=actor['components']
            self.assertEqual(page,children(actor['id'],limit=2,fields=fields))
            self.assertEqual(page['revision'],revision);self.assertEqual(len(page['entities']),2)
            self.assertTrue(page['cursor']);self.assertNotIn('collision',page['entities'][0])
            next_child=children(actor['id'],limit=2,fields=fields,cursor=page['cursor'])
            self.assertFalse(set(e['id'] for e in page['entities'])&set(e['id'] for e in next_child['entities']))
            next_actor=search(kind='Actor',limit=1,fields=['label'],component_fields=fields,component_limit=2,cursor=result['cursor'])
            self.assertNotEqual(next_actor['entities'][0]['id'],actor['id'])
            with self.assertRaises(ValueError):search(kind='Actor',limit=1,fields=['label'],component_fields=fields,component_limit=3,cursor=result['cursor'])
            self.assertEqual(Store.revision(f.db),revision)
            for arguments in (dict(kind='Component'),dict(limit=21),dict(component_limit=9),dict(component_fields=[])):
                with self.assertRaises(ValueError):search(**{**dict(kind='Actor',limit=2,component_fields=fields),**arguments})
        finally:f.tearDown()

if __name__=='__main__':unittest.main(verbosity=2)
