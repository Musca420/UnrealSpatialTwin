import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


@unittest.skipUnless(os.name == 'nt', 'Windows development junction installer')
class PluginLink(unittest.TestCase):
    def test_alias_open_idempotence_and_existing_install_preserved(self):
        shell = shutil.which('pwsh') or shutil.which('powershell')
        self.assertIsNotNone(shell)
        installer = Path(__file__).resolve().parents[2] / 'scripts/Link-SpatialTwinPlugin.ps1'
        with tempfile.TemporaryDirectory(prefix='Twin link ') as temp:
            root = Path(temp)
            project = root / 'Real Project'
            source = root / 'Shared Plugin'
            project.mkdir(); source.mkdir()
            (project / 'Game.uproject').write_text('{}')
            (source / 'UnrealSpatialTwin.uplugin').write_text('{}')
            alias = root / 'Alias Project'
            quote = lambda p: "'" + str(p).replace("'", "''") + "'"
            subprocess.run([shell, '-NoProfile', '-Command',
                            f'New-Item -ItemType Junction -Path {quote(alias)} -Target {quote(project)} | Out-Null'],
                           check=True, capture_output=True)
            def run(folder):
                return subprocess.run([shell, '-NoProfile', '-File', str(installer),
                                       '-Project', str(folder / 'Game.uproject'), '-PluginSource', str(source)],
                                      capture_output=True, text=True)
            for folder in (project, alias, project):
                result = run(folder)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)['state'], 'LINKED')
                self.assertEqual((folder / 'Plugins/UnrealSpatialTwin/UnrealSpatialTwin.uplugin').read_text(), '{}')
            other = root / 'Existing Project'
            (other / 'Plugins/UnrealSpatialTwin').mkdir(parents=True)
            (other / 'Game.uproject').write_text('{}')
            sentinel = other / 'Plugins/UnrealSpatialTwin/keep.txt'
            sentinel.write_text('existing installation')
            self.assertNotEqual(run(other).returncode, 0)
            self.assertEqual(sentinel.read_text(), 'existing installation')


if __name__ == '__main__':
    unittest.main()
