"""Check emitted instructions without launching or charging an agent."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import open_agent_benchmark as placement
import open_move_agent_benchmark as movement
import open_asset_audit_agent_benchmark as asset_audit


class PromptContract(unittest.TestCase):
    def test_asset_audit_has_equal_readonly_output_and_completeness_contract(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp);(base/'runs').mkdir();spec=base/'spec.json'
            spec.write_text(json.dumps({'project':str(base/'SpatialTwinBench.uproject'),'url':'http://127.0.0.1:1/mcp'}))
            for route in ('with','without'):
                child=type('Child',(),{'pid':123,'stdin':io.StringIO(),'stdout':[],'wait':lambda self:1})()
                with patch.object(asset_audit.subprocess,'Popen',return_value=child) as launch,contextlib.redirect_stdout(io.StringIO()):
                    result=asset_audit.run(spec,route,route,Path('unused'),placement.ROOT/'SpatialTwinCodexPlugin')
                self.assertEqual(result['state'],'FAILED')
                self.assertIn('features.code_mode_only=true',launch.call_args.args[0])
                prompt=(base/'runs'/route/'prompt.txt').read_text()
                self.assertIn('No edits, Shadow patch, render',prompt)
                self.assertIn('owners_total',prompt)
                self.assertNotIn('344',prompt)
                self.assertIn('Require known dependency coverage',prompt)
                self.assertIn('CURRENT' if route=='with' else 'get_execution_environment',prompt)

    def test_equal_routes_keep_resolved_chain_and_verified_native_distance(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp);(base/'runs').mkdir()
            spec=base/'spec.json'
            spec.write_text(json.dumps({'project':str(base/'SpatialTwinBench.uproject'),
                'url':'http://127.0.0.1:1/mcp','twin_tool_profile':'focused'}))
            plugin=placement.ROOT/'SpatialTwinCodexPlugin'
            for module, scenarios in ((placement,('reuse','author')), (movement,('batch',))):
                for scenario in scenarios:
                    for route in ('with','without'):
                        name=scenario+route
                        data=json.loads(spec.read_text());data['benchmark_scenario']=scenario
                        spec.write_text(json.dumps(data))
                        def child(*args,**kwargs):
                            self.assertIn('features.code_mode_only=true',args[0])
                            return type('Child',(),{'pid':123,'stdin':io.StringIO(),
                                'stdout':[],'wait':lambda self:1})()
                        with patch.object(module.subprocess,'Popen',side_effect=child), contextlib.redirect_stdout(io.StringIO()):
                            module.run(spec,route,name,Path('unused'),plugin)
                        prompt=(base/'runs'/name/'prompt.txt').read_text(encoding='utf-8')
                        for obsolete in ('ONE Code Mode perception execution','first perception execution',
                            'After reasoning, deliver','reason on observed facts, then deliver',
                            'After reasoning about the actual plan'):
                            self.assertNotIn(obsolete,prompt)
                        self.assertIn('once-only delivery',prompt)
                        self.assertIn('Never',prompt)
                        if scenario=='author':
                            self.assertIn('local_bounds_cm=[[minX,minY,minZ],[maxX,maxY,maxZ]]',prompt)
                            self.assertIn('never .min/.max',prompt)
                            self.assertIn('native Mesh.get_bounds output separately',prompt)
                        if route=='without':
                            self.assertIn('hit_z=start.z-distance',prompt)
                            self.assertIn('True/False',prompt)
                            self.assertIn('get_execution_environment',prompt)
                        else:
                            self.assertIn('PROVEN',prompt)
                            self.assertIn('base_revision',prompt)


if __name__=='__main__':unittest.main()
