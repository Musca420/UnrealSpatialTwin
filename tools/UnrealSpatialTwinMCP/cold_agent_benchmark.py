"""Process-cold end-to-end trials; never erase OS/DDC/model caches.

Uses the existing equal-output agent task and independent native verifier.
Only a prepared SpatialTwinBench project is launched. Stop on any failure;
an unfinished editor/action is retained for inspection, never replayed.
"""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from production_agent_benchmark import run
from verify_production_benchmark import verify

ROOT=Path(__file__).resolve().parents[2]
EDITOR=Path(os.environ.get('SPATIAL_TWIN_ENGINE','C:/Program Files/Epic Games/UE_5.8/Engine'))/'Binaries/Win64/UnrealEditor.exe'


def alive(pid):
    import ctypes
    from ctypes import wintypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
    kernel.OpenProcess.restype=wintypes.HANDLE
    kernel.GetExitCodeProcess.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    handle=kernel.OpenProcess(0x1000,False,pid)
    if not handle:
        if ctypes.get_last_error()==87:return False
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        code=wintypes.DWORD()
        if not kernel.GetExitCodeProcess(handle,ctypes.byref(code)):raise ctypes.WinError(ctypes.get_last_error())
        return code.value==259
    finally:kernel.CloseHandle(handle)


def ps_quote(text):
    return "'"+str(text).replace("'","''")+"'"


def run_agent(plan, item, spec_path, cli, native_ready=None):
    if plan.get('agent_workflow', 'bounded') == 'bounded':
        if native_ready:raise ValueError('Bounded legacy workflow cannot prepare offline')
        return run(spec_path,item['route'],item['scenario'],item['name'],cli,record_session=True,batched=True)
    if plan['agent_workflow'] != 'open':raise ValueError('Unknown benchmark workflow')
    scenario=plan['scenario']
    if scenario=='audit':
        from open_asset_audit_agent_benchmark import run as task
        return task(spec_path,item['route'],item['name'],cli,plan['runtime'],plan['verified_native_schemas'])
    if scenario=='batch':
        from open_move_agent_benchmark import run as task
    elif scenario in ('reuse','author'):
        from open_agent_benchmark import run as task
    else:raise ValueError('Unknown open scenario')
    return task(spec_path,item['route'],item['name'],cli,plan['runtime'],plan['verified_native_schemas'],native_ready=native_ready)


def trial(plan,index,cli,close_script):
    cli=Path(cli).resolve();close_script=Path(close_script).resolve()
    if not cli.is_file() or not close_script.is_file():
        raise FileNotFoundError('Verified agent executable and cleanup script required before starting Unreal')
    item=plan['plan'][index];spec_path=Path(item['spec']).resolve();spec=json.loads(spec_path.read_text());project=Path(spec['project']).resolve()
    assert project.parent==spec_path.parent and project.stem=='SpatialTwinBench'
    assert spec['map']=='/Game/Benchmark/Base' and spec['url']=='http://127.0.0.1:8013/mcp'
    settings=json.loads(project.read_text());enabled=next(p['Enabled'] for p in settings['Plugins'] if p['Name']=='UnrealSpatialTwin')
    assert enabled==(item['route']=='with')
    folder=project.parent/'startup'/item['name'];folder.mkdir(parents=True,exist_ok=False)
    request=dict(project=str(project),route=item['route'],map=spec['map'],initial_scan=enabled and not (project.parent/'Saved/SpatialTwin/world.sqlite').exists())
    request_path=folder/'request.json';request_path.write_text(json.dumps(request,indent=2)+'\n')
    report=dict(state='LAUNCHING',plan=item,index=index,cache_policy=plan['cache_policy'],timing=plan['timing'],initial_scan=request['initial_scan'])
    def record(): (folder/'trial.json').write_text(json.dumps(report,indent=2)+'\n')
    started=time.perf_counter();record()
    worker=None;future=None
    try:
        arguments=[str(project),spec['map'],'-unattended','-nop4','-nosplash','-ddc=InstalledNoZenLocalFallback','-DDC-ForceMemoryCache',
                   '-ExecutePythonScript='+str(ROOT/'tools/UnrealSpatialTwinMCP/cold_benchmark_startup.py'),'-abslog='+str(folder/'engine.log')]
        script='(Start-Process -FilePath '+ps_quote(EDITOR)+' -ArgumentList '+ps_quote(subprocess.list2cmdline(arguments))+' -WindowStyle Hidden -PassThru).Id'
        env={**os.environ,'SPATIAL_TWIN_COLD_REQUEST':str(request_path)}
        pid=int(subprocess.check_output(['powershell.exe','-NoProfile','-Command',script],text=True,env=env).strip())
        report.update(state='WAITING_FOR_NATIVE_STARTUP',pid=pid);record();deadline=time.monotonic()+240
        receipt=folder/'startup.json';last_liveness=0
        overlap=bool(plan.get('overlap_native_preparation')) and enabled and not request['initial_scan']
        if overlap:
            if plan.get('agent_workflow')!='open':raise ValueError('Overlap requires the offline-capable open workflow')
            worker=ThreadPoolExecutor(max_workers=1)
            report['agent_start_offset_seconds']=time.perf_counter()-started
            future=worker.submit(run_agent,plan,item,spec_path,cli,dict(receipt=receipt,pid=pid))
            report['state']='OFFLINE_AGENT_AND_NATIVE_STARTUP';record()

        while not receipt.exists():
            if time.monotonic()-last_liveness>5:
                assert alive(pid),'Native process exited before readiness receipt';last_liveness=time.monotonic()
            if time.monotonic()>deadline:raise TimeoutError('Startup timed out; preserve live process for diagnosis')
            time.sleep(.5)
        startup=json.loads(receipt.read_text());assert startup['state']=='READY' and startup['pid']==pid,startup
        report.update(state='AGENT_RUNNING',startup=startup,startup_seconds=time.perf_counter()-started);record()
        print(json.dumps(dict(name=item['name'],state=report['state'],pid=pid,startup_seconds=report['startup_seconds'],initial_scan=request['initial_scan'])),flush=True)
        result=future.result() if future else run_agent(plan,item,spec_path,cli)
        report.update(agent=result,overlap_native_preparation=overlap,task_end_to_end_seconds=time.perf_counter()-started);record()
        assert result['state']=='AWAITING_INDEPENDENT_VERIFICATION','Inspect failed agent; do not retry writes'
        report['state']='VERIFYING_AND_RESTORING';record()
        if plan.get('agent_workflow')=='open' and plan['scenario']=='audit':
            from open_asset_audit_agent_benchmark import verify as verify_task
        else:verify_task=verify
        asyncio.run(verify_task(spec_path,item['name']))
        report['independent']=json.loads((project.parent/'runs'/item['name']/'independent.json').read_text());record()
        # Existing ownership guard verifies all baseline actors before saving
        # only the disposable map and scheduling native shutdown.
        close_started=time.perf_counter()
        with (folder/'close.log').open('w',encoding='utf-8') as log:
            subprocess.run([sys.executable,str(close_script),'close',str(project.parent),str(folder)],stdout=log,stderr=subprocess.STDOUT,check=True)
        report['close_dispatch']=json.loads((folder/'control-close.json').read_text());assert report['close_dispatch']['success']
        while alive(pid):
            if time.perf_counter()-close_started>60:raise TimeoutError('Owned editor remains live after clean exit request; inspect same PID')
            time.sleep(1)
        if enabled:
            metrics=project.parent/'Saved/SpatialTwin/last_scan_metrics.json'
            assert hashlib.sha256(metrics.read_bytes()).hexdigest()==startup['scan_sha256'],'Unexpected full rescan during task'
        report.update(state='VERIFIED_AND_RESTORED_CLOSED',close_seconds=time.perf_counter()-close_started,total_with_verification_cleanup_seconds=time.perf_counter()-started);record()
        print(json.dumps(dict(name=item['name'],state=report['state'],startup_seconds=report['startup_seconds'],agent_seconds=result.get('seconds',result.get('agent_seconds')),end_to_end_seconds=report['task_end_to_end_seconds'],usage=result['usage'])),flush=True)
        return report
    except BaseException as error:
        report.update(state='FAILED_REQUIRES_INSPECTION',error=repr(error),elapsed_seconds=time.perf_counter()-started);record();raise
    finally:
        if worker:worker.shutdown(wait=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--plan',required=True,type=Path);parser.add_argument('--index',required=True,type=int);parser.add_argument('--cli',required=True,type=Path);parser.add_argument('--close-script',required=True,type=Path);args=parser.parse_args()
    plan=json.loads(args.plan.read_text());assert plan['state']=='PREPARED';trial(plan,args.index,args.cli,args.close_script.resolve())
