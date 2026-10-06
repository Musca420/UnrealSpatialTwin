"""Resume the existing Twin. A full scan requires explicit -SpatialTwinRebuild."""
import json
import re
import time
import traceback
from pathlib import Path
import unreal

unreal.EditorPythonScripting.set_keep_python_script_alive(True)
target = Path(unreal.Paths.project_dir()).resolve().parents[1] / 'Build/SpatialTwin/normal_startup.json'
target.parent.mkdir(parents=True, exist_ok=True)
rebuild = bool(re.search(r'(?i)(?:^|\s)-SpatialTwinRebuild(?:\s|$)', unreal.SystemLibrary.get_command_line()))
started = time.perf_counter()
receipt = {'state': 'WAITING', 'package_saves': [], 'full_scan_requested': rebuild}
target.write_text(json.dumps(receipt), encoding='utf-8')
busy = False


def tick(_):
    global busy
    if busy:
        return True
    busy = True
    try:
        toolset = unreal.get_default_object(unreal.SpatialTwinToolset)
        status = json.loads(toolset.call_method('spatial_twin_status'))
        if rebuild:
            receipt['state'] = 'BUILDING'
            target.write_text(json.dumps(receipt), encoding='utf-8')
            status = json.loads(toolset.call_method('spatial_twin_rebuild'))
        elif not status['ready'] and not status.get('error') and time.perf_counter() - started < 60:
            busy = False
            return True
        synced = status['ready'] and not status.get('error') and not any(
            status.get(k) for k in ('pending', 'navigation_refresh_pending', 'navigation_building'))
        receipt.update(status=status, success=synced,
                       state='READY' if synced else 'SYNC_FAILED' if status.get('error') else 'SYNC_PENDING' if status['ready'] else 'RECONCILE_REQUIRED',
                       elapsed_seconds=time.perf_counter() - started)
    except Exception:
        receipt.update(state='FAILED', error=traceback.format_exc())
    target.write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    unreal.log('SPATIAL_TWIN_NORMAL_STARTUP ' + json.dumps(receipt))
    return False


# Let the native subsystem's first tick verify and resume its saved baseline.
handle = unreal.register_ticker_callback(tick, delay=.2)
