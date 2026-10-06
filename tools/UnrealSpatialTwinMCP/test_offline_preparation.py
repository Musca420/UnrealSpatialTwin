"""Offline planning must not open native transport; live readiness is scoped."""
import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from benchmark_native import wait_ready
from open_benchmark_guard import server as placement
from open_move_benchmark_guard import server as movement


class OfflinePreparationTest(unittest.IsolatedAsyncioTestCase):
    async def test_real_guard_lifespans_without_native_endpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            spec = base / 'spec.json'
            spec.write_text(json.dumps(dict(project=str(base/'SpatialTwinBench.uproject'),
                map='/Game/Benchmark/Base', url='http://127.0.0.1:1/mcp',
                fixture_format=1, template_asset='/Engine/BasicShapes/Cube.Cube',
                scenarios={'batch': {'kind': 'move'}}, cameras={'batch': {}})))
            for build, name in ((placement, 'Place'), (movement, 'Move')):
                (base/'runs'/name).mkdir(parents=True)
                app = build(spec, 'without', name)
                async with app.settings.lifespan(app) as live:
                    self.assertIsNone(live['native'])
                    self.assertEqual((live.get('state') or live)['stage'], 'CONNECTED')

    async def test_waits_for_exact_receipt_and_rejects_wrong_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            spec = dict(project=str(base/'SpatialTwinBench.uproject'))
            receipt = base/'startup'/'Owned'/'startup.json'
            receipt.parent.mkdir(parents=True)
            ready = dict(state='READY', pid=os.getpid(), project=spec['project'],
                         route='with', native_plugin_loaded=True)
            with patch.dict(os.environ, SPATIAL_TWIN_NATIVE_READY=str(receipt),
                            SPATIAL_TWIN_NATIVE_PID=str(os.getpid())):
                waiting = asyncio.create_task(wait_ready(spec, 'with', 'Owned'))
                await asyncio.sleep(.01)
                self.assertFalse(waiting.done())
                receipt.write_text(json.dumps(ready))
                await waiting
                for key, wrong in (('pid', -1), ('route', 'without'),
                                   ('project', str(base/'Other.uproject')),
                                   ('state', 'FAILED'), ('native_plugin_loaded', False)):
                    receipt.write_text(json.dumps({**ready, key: wrong}))
                    with self.assertRaises(ValueError):
                        await wait_ready(spec, 'with', 'Owned')
                with self.assertRaises(ValueError):
                    await wait_ready(spec, 'with', 'Another')


if __name__ == '__main__':
    unittest.main()
