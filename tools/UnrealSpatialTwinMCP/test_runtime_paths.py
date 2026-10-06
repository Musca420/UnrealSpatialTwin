import os
from pathlib import Path
import tempfile
import unittest
from spatial_twin import native_library_path,file_signature


class RuntimePaths(unittest.TestCase):
    def test_legacy_library_is_independent_of_agent_working_directory(self):
        before=Path.cwd()
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve();engine=root/'Engine';binaries=engine/'Binaries/Win64';binaries.mkdir(parents=True)
            dll=root/'Game With Spaces/Plugins/Twin/Binaries/Win64/Core.dll';dll.parent.mkdir(parents=True);dll.write_bytes(b'native test identity')
            relative=os.path.relpath(dll,binaries)
            try:
                for folder in (root,root/'deep/agent/run'):
                    folder.mkdir(parents=True,exist_ok=True);os.chdir(folder)
                    self.assertEqual(native_library_path(engine,relative),dll)
                    self.assertEqual(file_signature(native_library_path(engine,relative)),file_signature(dll))
                    self.assertEqual(native_library_path('',str(dll)),dll)
            finally:os.chdir(before)

    def test_ambiguous_legacy_engine_is_rejected(self):
        with self.assertRaises(ValueError):native_library_path('relative/Engine','../../Core.dll')


if __name__=='__main__':unittest.main()
