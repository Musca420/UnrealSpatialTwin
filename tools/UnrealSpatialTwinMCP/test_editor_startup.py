import json
from pathlib import Path
import runpy
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


class EditorStartup(unittest.TestCase):
    def test_pending_or_failed_sync_is_never_reported_ready(self):
        script = Path(__file__).resolve().parents[2] / 'Unreal/start_spatial_twin.py'
        for status, expected in [
            ({'ready': True, 'error': 'unloaded actor missing descriptor', 'pending': 3}, 'SYNC_FAILED'),
            ({'ready': True, 'error': '', 'pending': 2}, 'SYNC_PENDING'),
            ({'ready': True, 'error': '', 'pending': 0, 'navigation_building': True}, 'SYNC_PENDING'),
            ({'ready': True, 'error': '', 'pending': 0}, 'READY'),
        ]:
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as temp:
                root = Path(temp); project = root / 'Unreal/Game'; project.mkdir(parents=True)
                callbacks = []
                native = SimpleNamespace(call_method=lambda method: json.dumps(status))
                unreal = SimpleNamespace(
                    Paths=SimpleNamespace(project_dir=lambda: str(project)),
                    EditorPythonScripting=SimpleNamespace(set_keep_python_script_alive=lambda value: None),
                    SystemLibrary=SimpleNamespace(get_command_line=lambda: ''),
                    SpatialTwinToolset=object, get_default_object=lambda cls: native,
                    register_ticker_callback=lambda callback, delay: callbacks.append(callback), log=lambda text: None)
                with patch.dict(sys.modules, {'unreal': unreal}):
                    runpy.run_path(str(script)); callbacks[0](0)
                receipt = json.loads((root / 'Build/SpatialTwin/normal_startup.json').read_text())
                self.assertEqual(receipt['state'], expected)
                self.assertEqual(receipt['success'], expected == 'READY')
                self.assertFalse(receipt['full_scan_requested'])


if __name__ == '__main__':
    unittest.main()
