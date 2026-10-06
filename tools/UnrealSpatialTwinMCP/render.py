"""Request a real Unreal render through official MCP and pair it with Twin state."""
import argparse
import asyncio
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
from spatial_twin.store import Store, vector
from spatial_twin.server import patch_summary
from apply_patch import matches, observed,TWIN,require_target,sync_ready

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'scripts'))
from unreal_mcp import ClientSession,streamablehttp_client,call_tool,save_capture,capture_viewport,UNREAL_MCP,EDITOR


def confirmed_state(store,patch_id,native=None):
    status=store.status(include_counts=False)
    if native is not None:
        require_target(store,native)
        if not native.get('ready') or native.get('pending') or native.get('error') or native.get('revision')!=status['canonical_revision']:raise ValueError('Fresh native synchronization does not match Canonical')
    else:
        if not status.get('editor_connected'):raise ValueError('Live render requires the connected Unreal editor')
        if status.get('synchronizer',{}).get('pending_actors') or status.get('synchronizer',{}).get('error'):raise ValueError('Synchronize pending changes before rendering')
    patch=store.patch_get(patch_id)
    if patch['status']!='APPLIED':raise ValueError('Render verification requires an APPLIED patch')
    receipts=[r for r in patch['receipts'] if r.get('state')=='CANONICAL_VERIFIED']
    if not receipts:raise ValueError('Canonical verification receipt missing')
    resolved=receipts[-1]['actor_ids']
    with store.read() as db:
        entities={}
        for ident,expected in patch['validation']['expected'].items():
            actual=observed(db,ident,expected,{},resolved)
            if not matches(expected,actual,resolved):raise ValueError('Canonical state changed since application: '+ident)
            entities[ident]=None if actual is None else {key:actual[key] for key in ('id','kind','label','class','asset_id','bounds','transform','affects_navigation','package','package_dirty') if key in actual}
    return {'revision':status['canonical_revision'],'map':status['map'],'engine_version':status['engine_version'],
            'patch':patch_summary(patch),'entities':entities}


def check_camera(camera):
    if not isinstance(camera,dict):raise ValueError('Camera must be a native FTransform object')
    for key in ('location','scale'):
        vector([camera[key][axis] for axis in ('x','y','z')])
    vector([camera['rotation'][axis] for axis in ('pitch','yaw','roll')])


def render_summary(result):
    """All facts needed for handoff without opening the full entity receipt."""
    before=result['before'];patch=before['patch']
    return {'state':result['state'],'image':result['capture']['image_path'],'canonical_revision':before['revision'],
            'map':before['map'],'patch_id':patch['id'],'canonical_verified':patch['status']=='APPLIED',
            'saved':patch.get('last_receipt',{}).get('saved'),
            'note':result['note']}


async def render(store,patch_id,camera,output,url=UNREAL_MCP,session=None):
    output=Path(output).resolve();check_camera(camera)
    borrowed=session is not None
    async with (nullcontext((None,None,None)) if borrowed else streamablehttp_client(url)) as (read,write,_):
        async with (nullcontext(session) if borrowed else ClientSession(read,write,read_timeout_seconds=timedelta(seconds=120))) as session:
            if not borrowed:await session.initialize()
            fresh=await sync_ready(store,session)
            before=confirmed_state(store,patch_id,json.loads(fresh) if isinstance(fresh,str) else fresh)
            pixels=await capture_viewport(lambda t,n,a:call_tool(session,t,n,a),camera)
            capture=save_capture(pixels,output.with_suffix('.png'))
            fresh=await call_tool(session,TWIN,'spatial_twin_sync',{})
            after=confirmed_state(store,patch_id,json.loads(fresh) if isinstance(fresh,str) else fresh)
    result={'state':'VERIFIED' if before==after else 'UNPAIRED','captured_at_utc':datetime.now(timezone.utc).isoformat(),
            'source':'Unreal official CaptureViewport','camera_requested':camera,'capture':capture,
            'before':before,'after_revision':after['revision'],'note':'Source and pixels sampled sequentially; no atomic frame assertion.'}
    result.update(render_summary(result))
    output.parent.mkdir(parents=True,exist_ok=True);output.with_suffix('.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    if result['state']!='VERIFIED':raise ValueError('World changed while rendering; image preserved but verification must be repeated')
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--patch',required=True);parser.add_argument('--camera',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--url',default=UNREAL_MCP)
    args=parser.parse_args()
    result=asyncio.run(render(Store(args.root),args.patch,json.loads(args.camera.read_text(encoding='utf-8')),args.output,args.url))
    print(json.dumps(render_summary(result)))
