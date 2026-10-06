import json
from pathlib import Path
import tempfile
import unittest

from cold_benchmark_control import scope


class ColdScopeTest(unittest.TestCase):
    def test_requires_exact_owned_ready_restored_run_and_no_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);project=base/'SpatialTwinBench.uproject'
            project.write_text('{}')
            output=base/'startup'/'Owned';output.mkdir(parents=True)
            run=base/'runs'/'Owned';run.mkdir(parents=True)
            request=dict(project=str(project),map='/Game/Benchmark/Base',route='with')
            ready=dict(project=str(project),state='READY',route='with',native_plugin_loaded=True,pid=123)
            def write(file,value):file.write_text(json.dumps(value))
            write(output/'request.json',request);write(output/'startup.json',ready)
            write(run/'independent.json',dict(state='VERIFIED_AND_RESTORED'))
            self.assertEqual(scope(base,output),(base,output,project,123))
            for wrong in ({'pid':0},{'pid':True},{'route':'without'},
                          {'native_plugin_loaded':False},{'state':'FAILED'},
                          {'project':str(base/'Other.uproject')}):
                write(output/'startup.json',{**ready,**wrong})
                with self.assertRaises(ValueError):scope(base,output)
            write(output/'startup.json',ready)
            write(run/'independent.json',dict(state='FAILED'))
            with self.assertRaises(ValueError):scope(base,output)
            write(run/'independent.json',dict(state='VERIFIED_AND_RESTORED'))
            with self.assertRaises(ValueError):scope(base,base/'Outside')
            (output/'control-close.json').write_text('{}')
            with self.assertRaises(ValueError):scope(base,output)


if __name__=='__main__':unittest.main()
