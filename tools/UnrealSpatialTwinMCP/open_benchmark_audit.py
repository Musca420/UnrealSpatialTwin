"""Independently reconcile completed open benchmark trials, including failures.

Reads receipts and native client usage only; no engine calls or write replay.
Rejects incomplete, unequal or unrestored runs. Does not certify general release.
"""
import argparse
import base64
import hashlib
import json
import re
from pathlib import Path
import statistics


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def render_receipts(trace, render_hash):
    """Follow an image-producing exec through its exact yielded cell's waits."""
    calls=set();cells=set();receipts=[]
    with Path(trace).open(encoding='utf-8') as stream:
        for line in stream:
            event=json.loads(line);payload=event.get('payload',{})
            if event.get('type')!='response_item':continue
            kind=payload.get('type');call_id=payload.get('call_id')
            if kind in ('function_call','custom_tool_call'):
                arguments=payload.get('arguments',payload.get('input',''))
                if 'tools.view_image' in str(arguments):calls.add(call_id)
                elif payload.get('name','').rsplit('.',1)[-1]=='wait':
                    try:request=json.loads(arguments) if isinstance(arguments,str) else arguments
                    except json.JSONDecodeError:continue
                    if isinstance(request,dict) and request.get('cell_id') in cells:calls.add(call_id)
            if kind not in ('function_call_output','custom_tool_call_output') or call_id not in calls:continue
            output=payload.get('output',[])
            texts=[output] if isinstance(output,str) else [item.get('text','') for item in output if isinstance(item,dict)] if isinstance(output,list) else []
            for text in texts:
                yielded=re.match(r'Script running with cell ID ([A-Za-z0-9_-]+)\r?\n',text)
                if yielded:cells.add(yielded.group(1))
            for item in output if isinstance(output,list) else []:
                if item.get('type')=='input_image' and item.get('image_url','').startswith('data:image/png;base64,'):
                    raw=base64.b64decode(item['image_url'].split(',',1)[1],validate=True)
                    if hashlib.sha256(raw).hexdigest()==render_hash:
                        receipts.append({'call_id':call_id,'render_sha256':render_hash})
    return receipts


def audit(protocol_path, output, sessions):
    if Path(output).exists():raise FileExistsError('Preserve existing benchmark evidence')
    protocol = read(protocol_path)
    rows = []
    for trial in protocol['plan']:
        movement=trial.get('scenario')=='batch'
        authoring=trial.get('scenario')=='author'
        expected_count=32 if movement else 9
        spec = read(trial['spec'])
        base = Path(spec['project']).parent
        folder = base/'runs'/trial['name']
        lifecycle = read(base/'startup'/trial['name']/'trial.json')
        result, usage, independent = (read(folder/name) for name in ('result.json','usage.json','independent.json'))
        if lifecycle['state'] != 'VERIFIED_AND_RESTORED_CLOSED':
            recovery=read(base/'startup'/trial['name']/'cleanup-recovery.json')
            assert lifecycle['state']=='FAILED_REQUIRES_INSPECTION'
            assert recovery['state']=='EXITED_LISTED_PROCESS' and recovery['exit_code']==0
            assert recovery['pid']==lifecycle['pid'] and recovery['original_trial_preserved'] is True
            assert recovery['actor_writes_replayed'] is False and recovery['verified_restored'] is True
            assert lifecycle['close_dispatch']['success'] is True
            assert 'Log file closed' in (base/'startup'/trial['name']/'engine.log').read_text(encoding='utf-8',errors='replace')[-1000:]
        assert result['state'] == 'VERIFIED' and independent['state'] == 'VERIFIED_AND_RESTORED'
        assert result['route'] == usage['route'] == trial['route']
        assert usage['state'] == 'AWAITING_INDEPENDENT_VERIFICATION' and usage['exit_code'] == 0
        poses = result['positions_cm']
        assert len(poses) == len(result['native_rows']) == len(independent['rows']) == expected_count
        assert result['candidate_indexes'] == (list(range(32)) if movement else [0,1,2,4,5,6,8,9,10])
        expected_asset=(f"/Game/BenchRuns/SM_ST_Rail_{trial['name']}.SM_ST_Rail_{trial['name']}" if authoring else '/Engine/BasicShapes/Cube.Cube')
        assert result['asset_path'] == expected_asset
        if authoring:
            parity=result['native_authoring_parity']
            assert parity['state']=='NATIVE_RENDER_UCX_PARITY_VERIFIED' and parity['convex_bodies']==6
            assert parity['render_vertices']==144 and parity['effective_trace_mode']=='ctfusesimpleandcomplex'
            assert independent['cleanup']['asset_deleted']==expected_asset
            manifest=read(folder/'manifest.json')
            geometry_hashes=[hashlib.sha256((folder/filename).read_bytes()).hexdigest() for filename in [manifest['geometry_file'],*manifest['collision_files']]]
        for row, own, pos in zip(result['native_rows'],result['actor_refs'],poses):
            transform = row['transform']
            assert all(abs(transform['location'][k]-v) < .001 for k,v in zip(('x','y','z'),pos))
            assert all(abs(transform['scale'][k]-1) < .001 for k in ('x','y','z'))
            assert all(abs(v) < .001 for v in transform['rotation'].values())
            props = json.loads(row['properties'])
            assert props['StaticMesh']['refPath'] == result['asset_path']
            if not movement:assert props['bCanEverAffectNavigation'] is True
        for own, overlaps in zip(result['actor_refs'],result['native_overlaps']):
            assert not any(hit['refPath'] != own['refPath'] for hit in overlaps)
        if movement:
            if protocol.get('native_navigation_defaults_required'):
                flags=result['native_navigation_defaults']
                assert flags['before']==flags['after'] and len(flags['after'])==expected_count
                assert all(type(v) is bool for v in flags['after'])
            journal=read(folder/'journal.json');support=journal['support']
            assert support['complete'] and not support['rejected'] and len(support['accepted'])==32
            assert [r['position'] for r in support['accepted']]==poses
            assert all(r['height_range'][1]-r['height_range'][0]<=1 for r in support['accepted'])
        else:
            assert len(result['native_support']) == 9
            expected_distance=100.2 if authoring else 150.2
            assert all(len(probes) == 9 and all(d is not None and abs(d-expected_distance) < .01 for d in probes) for probes in result['native_support'])
        assert (folder/'render.png').read_bytes().startswith(b'\x89PNG\r\n\x1a\n')
        thread = next(e['thread_id'] for e in usage['events'] if e['type'] == 'thread.started')
        traces = list(Path(sessions).rglob('*'+thread+'*.jsonl'))
        assert len(traces) == 1
        render_hash=hashlib.sha256((folder/'render.png').read_bytes()).hexdigest()
        # Prove the image reached this task agent, not merely that a filename
        # appeared in a tool receipt. Read public calls/results, never reasoning.
        image_receipts=render_receipts(traces[0],render_hash)
        assert image_receipts,'Actual benchmark render not received by this agent'
        previous, counts = {}, []
        # Do not export private reasoning content. Counters are native client
        # accounting, not an estimate from text lengths or bytes.
        with traces[0].open(encoding='utf-8') as stream:
            for line in stream:
                if '"token_count"' not in line:
                    continue
                event = json.loads(line)
                payload = event.get('payload',{})
                total = (payload.get('info') or {}).get('total_token_usage')
                if payload.get('type') != 'token_count' or not total or total == previous:
                    continue
                assert all(v >= previous.get(k,0) for k,v in total.items())
                counts.append({k:v-previous.get(k,0) for k,v in total.items()})
                previous = total
        for key in ('input_tokens','cached_input_tokens','output_tokens','reasoning_output_tokens','total_tokens'):
            assert sum(c[key] for c in counts) == usage['usage'][key]
        events = [json.loads(line) for line in (folder/'events.jsonl').read_text(encoding='utf-8').splitlines()]
        calls = [e['item'] for e in events if e['type']=='item.completed' and e.get('item',{}).get('type')=='mcp_tool_call']
        rows.append(dict(name=trial['name'],route=trial['route'],usage=usage['usage'],responses=len(counts),
                         request_usage=counts,agent_seconds=usage['seconds'],startup_seconds=lifecycle['startup_seconds'],
                         end_to_end_seconds=lifecycle['task_end_to_end_seconds'],
                         verification_cleanup_seconds=lifecycle.get('total_with_verification_cleanup_seconds',lifecycle.get('elapsed_seconds'))-lifecycle['task_end_to_end_seconds'],
                         cleanup_recovery=(recovery if lifecycle['state']=='FAILED_REQUIRES_INSPECTION' else None),
                         authored_geometry_sha256=geometry_hashes if authoring else None,
                         poses=poses,canonical_confirmed=trial['route']=='with',
                         call_count=len(calls),tools=[c['tool'] for c in calls],
                         render_sha256=render_hash,agent_render_receipts=image_receipts,
                         client_trace_sha256=hashlib.sha256(traces[0].read_bytes()).hexdigest(),
                         final_assessment=(folder/'final.txt').read_text(encoding='utf-8')))
    reference = sorted(rows[0]['poses'])
    assert all(max(abs(a-b) for p,q in zip(reference,sorted(r['poses'])) for a,b in zip(p,q)) < .001 for r in rows)
    if authoring:assert all(r['authored_geometry_sha256']==rows[0]['authored_geometry_sha256'] for r in rows)
    aggregate = {route:dict(token_median=statistics.median(r['usage']['total_tokens'] for r in rows if r['route']==route),
                            agent_seconds_median=statistics.median(r['agent_seconds'] for r in rows if r['route']==route),
                            end_to_end_seconds_median=statistics.median(r['end_to_end_seconds'] for r in rows if r['route']==route)) for route in ('without','with')}
    a,b = aggregate['without'],aggregate['with']
    report = dict(state='VERIFIED_SINGLE_SCENARIO_NOT_GENERAL_ACCEPTANCE',protocol=protocol,trials=rows,aggregate=aggregate,
                  token_reduction_percent=100*(1-b['token_median']/a['token_median']),
                  agent_time_reduction_percent=100*(1-b['agent_seconds_median']/a['agent_seconds_median']),
                  end_to_end_time_reduction_percent=100*(1-b['end_to_end_seconds_median']/a['end_to_end_seconds_median']),
                  all_successful_trials_tokens=sum(r['usage']['total_tokens'] for r in rows),
                  prior_failed_trials_tokens=protocol.get('failure_cost_tokens',protocol.get('failed_previous_native_tokens',0)),
                  general_goal_achieved=False,
                  limits=['One open '+('bulk movement' if movement else 'Blender authoring' if authoring else 'reused-mesh')+' scenario; representative multi-task/cold/warm general criterion remains open.',
                          'Full main-agent native counters include cached input; reasoning is a subset of output, never added twice.',
                          'Mandatory native SDK schema/environment reads and any failed reads retained. Route-specific instructions supplied before trials.',
                          'OS/DDC/model caches not controlled. Development/orchestrator and reviewer/account-wide billing separate.',
                          'Images independently captured and checked by each task agent; comparative visual review remains separate.'])
    if movement:report['limits'].append('Exact poses/scales/rotations/assets/support and new-overlap checks retained. '+('Native navigation-default flags directly checked unchanged before/after delivery.' if protocol.get('native_navigation_defaults_required') else 'These movement receipts do not directly sample the native navigation-default flag after delivery; do not claim that additional measurement.'))
    Path(output).write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    return {k:v for k,v in report.items() if k not in ('protocol','trials')}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--protocol',required=True,type=Path)
    p.add_argument('--output',required=True,type=Path)
    p.add_argument('--sessions',required=True,type=Path)
    a = p.parse_args()
    print(json.dumps(audit(a.protocol,a.output,a.sessions)))
