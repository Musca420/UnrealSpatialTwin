import base64,hashlib,json,tempfile,unittest
from pathlib import Path
from open_benchmark_audit import render_receipts

class RenderReceiptTests(unittest.TestCase):
    def test_yielded_image_requires_same_cell_and_render(self):
        raw=b'\x89PNG\r\n\x1a\nreceipt-test';digest=hashlib.sha256(raw).hexdigest()
        image={'type':'input_image','image_url':'data:image/png;base64,'+base64.b64encode(raw).decode()}
        def call(id,name,args):return {'type':'response_item','payload':{'type':'custom_tool_call','call_id':id,'name':name,'input':args}}
        def output(id,value):return {'type':'response_item','payload':{'type':'custom_tool_call_output','call_id':id,'output':value}}
        events=[call('origin','exec','image((await tools.view_image({path:render})).image_url)'),output('origin','Script running with cell ID 3\nWall time 1.0 seconds\nOutput:\n'),call('wrong','wait','{"cell_id":"4"}'),output('wrong',[image]),call('waiting','wait','{"cell_id":"3"}'),output('waiting','Script running with cell ID 3\nWall time 1.0 seconds\nOutput:\n'),call('finished','wait','{"cell_id":"3"}'),output('finished',[image])]
        with tempfile.TemporaryDirectory() as folder:
            trace=Path(folder)/'trace.jsonl'
            def write(items):trace.write_text('\n'.join(json.dumps(e) for e in items)+'\n')
            write(events)
            self.assertEqual(render_receipts(trace,digest),[{'call_id':'finished','render_sha256':digest}])
            self.assertEqual(render_receipts(trace,'0'*64),[])
            write(events[:4]);self.assertEqual(render_receipts(trace,digest),[])
            write([call('direct','exec','await tools.view_image({path:render})'),output('direct',[image])])
            self.assertEqual(render_receipts(trace,digest),[{'call_id':'direct','render_sha256':digest}])
            write([call('unrelated','exec','text(source)'),output('unrelated',[image])]);self.assertEqual(render_receipts(trace,digest),[])

if __name__=='__main__':unittest.main()
