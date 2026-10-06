import unittest
from unittest.mock import AsyncMock,patch
import navigation_capacity_checks as checks


class CapacityWaitTests(unittest.IsolatedAsyncioTestCase):
    async def test_idle_before_deferred_native_rebuild_is_not_confirmation(self):
        # Observed real failure: actor edit committed and idle flags cleared,
        # but the exported navigation still described the old pool.
        native={'map':'/Game/CapacityFixture','ready':True,'pending':0,
                'scanning':False,'navigation_building':False,
                'navigation_refresh_pending':False,'navigation_locked':False}
        observations=[
            {'state':'READY','revision':11,'tile_capacity':2,'capacity_settings':{'tile_pool_size':2}},
            {'state':'READY','revision':12,'tile_capacity':16,'capacity_settings':None},
            {'state':'UNKNOWN'},
            {'state':'READY','revision':13,'tile_capacity':16,'capacity_settings':{'tile_pool_size':16}}]
        replies=[dict(native,revision=r) for r in (11,12,12,13)]
        with patch.object(checks.executor,'call_tool',new=AsyncMock(side_effect=replies)) as call, \
             patch.object(checks.executor,'require_target',side_effect=lambda store,value:value), \
             patch.object(checks,'Navigation') as navigation, \
             patch.object(checks.asyncio,'sleep',new=AsyncMock()) as sleep:
            navigation.return_value.status.side_effect=observations
            result=await checks.ready(object(),object(),'Default',16,10)
        self.assertEqual(result['revision'],13)
        self.assertEqual(call.await_count,4)
        self.assertEqual(sleep.await_count,3)
        self.assertTrue(all(c.args[2]=='spatial_twin_status' for c in call.call_args_list))
