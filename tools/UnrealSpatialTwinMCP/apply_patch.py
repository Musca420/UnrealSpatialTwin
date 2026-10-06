"""Patch executor: use the project's existing official Unreal MCP client.

This is an explicit action CLI, separate from the offline read/analyze MCP.
No uncertain write is retried. APPLIED requires a newer matching Twin revision.
"""
import argparse
import asyncio
import hashlib
from contextlib import nullcontext
from datetime import timedelta
import json
import math
import os
from pathlib import Path
import sys
import time
from spatial_twin.store import Store,encode
from spatial_twin import require_current_runtime
from spatial_twin.shadow import validate,mesh_component
from spatial_twin.query import Queries
from spatial_twin.verification import matches,observed,recover

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
from unreal_mcp import ClientSession,streamablehttp_client,call_tool,error_message,UNREAL_MCP

SCENE='editor_toolset.toolsets.scene.SceneTools'
ACTOR='editor_toolset.toolsets.actor.ActorTools'
OBJECT='editor_toolset.toolsets.object.ObjectTools'
TWIN='UnrealSpatialTwin.SpatialTwinToolset'
PROGRAMMATIC='editor_toolset.toolsets.programmatic.ProgrammaticToolset'


def create_script(entries):
    """Registered official tools only; preserve partial effects on failure."""
    return 'import json\nENTRIES='+repr(entries)+'\nSCENE='+repr(SCENE)+'\nACTOR='+repr(ACTOR)+'\nOBJECT='+repr(OBJECT)+'''
def call(t,n,a):
    return execute_tool(t+'.'+n,json.dumps(a))['returnValue']
def run():
    created=[];completed=[]
    try:
        for entry in ENTRIES:
            actor=call(SCENE,entry['tool'],entry['arguments'])
            if not actor or not actor.get('refPath'):raise ValueError('Creation returned no actor reference')
            row={'operation_id':entry['operation_id'],'target':entry['target'],'actor':actor,'properties_confirmed':not entry['properties']}
            created.append(row)
            if entry['properties']:
                component=call(ACTOR,'get_root_component',{'actor':actor})
                if not component or not component.get('refPath'):raise ValueError('Created component unavailable')
                if not call(OBJECT,'set_properties',{'instance':component,'values':json.dumps(entry['properties'])}):raise ValueError('Component properties not confirmed')
                row['properties_confirmed']=True
            completed.append({'operation_id':entry['operation_id'],'state':'ACKNOWLEDGED'})
        return {'complete':True,'created':created,'completed':completed}
    except Exception as error:
        return {'complete':False,'created':created,'completed':completed,'uncertain_operation_id':entry['operation_id'],'error':str(error)}
'''


def xform(t):
    x,y,z,w=t['rotation']; s=z*x-w*y
    if abs(s)>.4999995:pitch=math.copysign(90,s);yaw=math.degrees(math.copysign(2,s)*math.atan2(x,w));roll=0
    else:pitch=math.degrees(math.asin(2*s));yaw=math.degrees(math.atan2(2*(w*z+x*y),1-2*(y*y+z*z)));roll=math.degrees(math.atan2(-2*(w*x+y*z),1-2*(x*x+y*y)))
    return {'location':dict(zip(('x','y','z'),t['position'])),
            'rotation':{'pitch':pitch,'yaw':yaw,'roll':roll},'scale':dict(zip(('x','y','z'),t['scale']))}


def mutation_script(entries):
    """Ordered official actions, including partial acknowledgements; never retry here."""
    return 'import json\nENTRIES='+repr(entries)+'''
def run():
    completed=[]
    try:
        for entry in ENTRIES:
            value=execute_tool(entry['toolset']+'.'+entry['tool'],json.dumps(entry['arguments']))['returnValue']
            if value is False or value is None:raise ValueError('Official action did not confirm: '+entry['operation_id'])
            completed.append({'operation_id':entry['operation_id'],'state':'ACKNOWLEDGED'})
        return {'complete':True,'completed':completed}
    except Exception as error:
        return {'complete':False,'completed':completed,'uncertain_operation_id':entry['operation_id'],'error':str(error)}
'''


def dependent_script(entries,paths,roots):
    """Resolve new references in one client request; native tools may yield."""
    return 'import json\nENTRIES='+repr(entries)+'\nPATHS='+repr(paths)+'\nROOTS='+repr(roots)+'\nACTOR='+repr(ACTOR)+'\nOBJECT='+repr(OBJECT)+'''
def call(t,n,a):
    value=execute_tool(t+'.'+n,json.dumps(a))['returnValue']
    if value is False or value is None:raise ValueError('Official action did not confirm: '+n)
    return value
def root(target):
    if target not in ROOTS:
        value=call(ACTOR,'get_root_component',{'actor':{'refPath':PATHS[target]}})
        if not value.get('refPath'):raise ValueError('Created root component unavailable')
        ROOTS[target]=value
    return ROOTS[target]
def run():
    completed=[];created=[]
    try:
        for entry in ENTRIES:
            args=entry['arguments']
            for name,target in entry.get('actor_arguments',{}).items():args[name]={'refPath':PATHS[target]}
            for name,target in entry.get('component_arguments',{}).items():args[name]=root(target) if target else None
            value=call(entry['toolset'],entry['tool'],args)
            if entry.get('create_target'):
                target=entry['create_target']
                if not value.get('refPath'):raise ValueError('Creation returned no actor reference')
                PATHS[target]=value['refPath']
                row={'target':target,'operation_id':entry['operation_id'],'actor':value,'properties_confirmed':not entry['properties']}
                created.append(row)
                if entry['properties']:
                    call(OBJECT,'set_properties',{'instance':root(target),'values':json.dumps(entry['properties'])})
                    row['properties_confirmed']=True
            completed.append({'operation_id':entry['operation_id'],'state':'ACKNOWLEDGED'})
        return {'complete':True,'completed':completed,'created':created}
    except Exception as error:
        return {'complete':False,'completed':completed,'created':created,'uncertain_operation_id':entry['operation_id'],'error':str(error)}
'''


def journal_script(script,ident,digest):
    """Persist a terminal batch result inside Unreal before the transport reply."""
    return script+'\n_run_actions=run\nPATCH_ID='+repr(ident)+'\nDIGEST='+repr(digest)+'\nTWIN='+repr(TWIN)+'''
def run():
    result=_run_actions()
    receipt=execute_tool(TWIN+'.spatial_twin_record_patch_result',json.dumps({
        'patch_id':PATCH_ID,'operations_digest':DIGEST,'result_json':json.dumps(result)}))['returnValue']
    receipt=json.loads(receipt) if isinstance(receipt,str) else receipt
    if receipt.get('state')!='RECORDED':raise ValueError('Batch effects may exist; result journal failed: '+str(receipt))
    return result
'''


def update(store,ident,status=None,receipts=None,error=None):
    with store.patches() as db:
        if status:db.execute('UPDATE patches SET status=?,updated=? WHERE id=?',(status,time.time(),ident))
        if receipts is not None:db.execute('UPDATE patches SET receipts=?,updated=? WHERE id=?',(encode(receipts),time.time(),ident))
        if error:
            row=db.execute('SELECT validation FROM patches WHERE id=?',(ident,)).fetchone()
            value=json.loads(row[0]) if row and row[0] else {};value['application_error']=error
            db.execute('UPDATE patches SET validation=? WHERE id=?',(encode(value),ident))


def require_target(store,native):
    """A revision number alone cannot identify an editor or a map."""
    if isinstance(native,str):native=json.loads(native)
    if 'root' not in native or 'project_id' not in native:
        raise ValueError('CONFLICTED: install the matching native Spatial Twin plugin; this editor lacks project/store identity checks')
    status=store.status(include_counts=False)
    if (not native.get('ready') or native.get('error') or
        native.get('map')!=status.get('map') or native.get('project_id')!=status.get('project_id') or
        not native.get('root') or Path(native['root']).resolve()!=store.root or
        native.get('revision')!=status.get('canonical_revision')):
        raise ValueError('CONFLICTED: live editor project/map/store/revision differs from the validated Twin')
    return native


async def sync_ready(store,session,timeout=120):
    deadline=time.monotonic()+timeout
    fresh=await call_tool(session,TWIN,'spatial_twin_sync',{})
    while True:
        fresh=require_target(store,fresh)
        if not fresh.get('pending'):return fresh
        if time.monotonic()>=deadline:raise TimeoutError('Native synchronization did not settle')
        await asyncio.sleep(.5)
        fresh=await call_tool(session,TWIN,'spatial_twin_status',{})


async def execute(store,ident,url,timeout,save,session=None):
    with store.application_lock():
        return await _execute(store,ident,url,timeout,save,session)


async def _execute(store,ident,url,timeout,save,session=None):
    patch=validate(store,ident)
    if patch['status']!='VALIDATED':raise ValueError('Patch is not VALIDATED')
    status=store.status(include_counts=False)
    if not status['editor_connected']:raise ValueError('Twin synchronizer is disconnected')
    heartbeat=status.get('synchronizer',{})
    if heartbeat.get('error'):raise ValueError('Canonical synchronization failed; inspect Twin status before applying')
    source={}; receipts=[]; paths={}; roots={}; journal=False
    created={op['target'] for op in patch['operations'] if op['type']=='CREATE_ACTOR'}
    with store.read() as db:
        if Store.revision(db)!=patch['base_revision']:raise ValueError('CONFLICTED: revision changed')
        for op in patch['operations']:
            if op.get('asset','').startswith('staged:'):
                raise ValueError('Import, synchronize and rebind authored assets before applying to Unreal')
            if op['target'] not in created and op['target'] not in source:
                e=Store.entity(db,op['target']);source[e['id']]=e
                owner=Store.entity(db,e['actor_id']) if e['kind']=='Instance' else e
                if e['kind']!='Instance':paths[e['id']]=e['path']
                if save and owner.get('package_dirty'):raise ValueError('CONFLICTED: touched package already contains unsaved work; save it explicitly first or use --no-save')
    receipts.append({'state':'APPLICATION_STARTED','lease_version':1,'process_id':os.getpid(),'save_requested':save})
    with store.patches() as db:
        if db.execute("UPDATE patches SET status='APPLYING',receipts=?,updated=? WHERE id=? AND status='VALIDATED' AND operations=?",
                      (encode(receipts),time.time(),ident,encode(patch['operations']))).rowcount!=1:raise ValueError('Patch changed concurrently')
    async def action(session,toolset,tool,arguments,opid):
        if journal and toolset==PROGRAMMATIC and tool=='execute_tool_script':
            arguments={**arguments,'script':journal_script(arguments['script'],ident,patch['validation']['operations_digest'])}
        receipt={'operation_id':opid,'toolset':toolset,'tool':tool,'state':'SENT','sent_at':time.time()};receipts.append(receipt);update(store,ident,receipts=receipts)
        value=await call_tool(session,toolset,tool,arguments)
        if value is False or value is None:raise ValueError('Official Unreal tool did not confirm the operation: '+tool)
        receipt.update(state='ACKNOWLEDGED',result=value);update(store,ident,receipts=receipts);return value
    try:
        borrowed=session is not None
        async with (nullcontext((None,None,None)) if borrowed else streamablehttp_client(url)) as (read,write,_):
            async with (nullcontext(session) if borrowed else ClientSession(read,write,read_timeout_seconds=timedelta(seconds=timeout))) as session:
                if not borrowed:await session.initialize()
                native=await sync_ready(store,session,timeout)
                if any(op['type']=='SET_INSTANCE_TRANSFORM' for op in patch['operations']) and native.get('instance_transform_version')!=1:
                    raise ValueError('Install the native instance-transform adapter before applying this patch')
                journal=native.get('patch_result_journal')==1
                with store.read() as db:
                    if Store.revision(db)!=patch['base_revision']:raise ValueError('CONFLICTED: native flush changed the validated revision')
                pending=patch['operations']
                if len(pending)>1 and all(op['type']=='CREATE_ACTOR' for op in pending):
                    entries=[]
                    for op in pending:
                        args={'name':op.get('label',op['target']),'xform':xform(op.get('transform',{'position':[0,0,0],'rotation':[0,0,0,1],'scale':[1,1,1]})),'parent':None,'snap_to_ground':False}
                        if op.get('asset'):
                            with store.read() as db:asset=Store.entity(db,op['asset'])
                            args['asset_path']=asset['path'];tool='add_to_scene_from_asset'
                        else:args['actor_type']={'refPath':op['class']};tool='add_to_scene_from_class'
                        entries.append({'target':op['target'],'operation_id':op['operation_id'],'tool':tool,'arguments':args,'properties':op.get('component_properties',{})})
                    value=await action(session,PROGRAMMATIC,'execute_tool_script',{'script':create_script(entries)},'batch:'+ident)
                    value=json.loads(value) if isinstance(value,str) else value
                    rows=value.get('created',[])
                    if [r.get('target') for r in rows]!=[op['target'] for op in pending] or not value.get('complete') or not all(r.get('properties_confirmed') for r in rows):
                        raise ValueError('Batch incomplete; inspect acknowledged partial creation references: '+str(value.get('error')))
                    for op,row in zip(pending,rows):
                        paths[op['target']]=row['actor']['refPath']
                    pending=[]
                if len(pending)>1 and all(op['type'] in ('MOVE_ACTOR','ROTATE_ACTOR','SCALE_ACTOR','SET_PROPERTY','DELETE_ACTOR') for op in pending):
                    entries=[]
                    for op in pending:
                        typ,target=op['type'],op['target']
                        actor={'refPath':paths[target]}
                        if typ=='DELETE_ACTOR':
                            toolset,tool,args=SCENE,'remove_from_scene',{'actor':actor}
                        elif typ=='SET_PROPERTY':
                            toolset,tool,args=OBJECT,'set_properties',{'instance':actor,'values':encode({op['property']:op['value']})}
                        else:
                            toolset,tool,args=ACTOR,'set_actor_transform',{'actor':actor,'xform':xform(patch['validation']['operation_transforms'][op['operation_id']]),'worldspace':True}
                        entries.append({'operation_id':op['operation_id'],'toolset':toolset,'tool':tool,'arguments':args})
                    value=await action(session,PROGRAMMATIC,'execute_tool_script',{'script':mutation_script(entries)},'batch:'+ident)
                    value=json.loads(value) if isinstance(value,str) else value
                    if not value.get('complete') or [r.get('operation_id') for r in value.get('completed',[])]!=[op['operation_id'] for op in pending]:
                        raise ValueError('Batch incomplete; inspect partial acknowledgements and uncertain operation: '+str(value.get('error')))
                    pending=[]
                entries=[]
                for op in pending:
                    typ,target=op['type'],op['target'];oid=op['operation_id']
                    entry={'operation_id':oid}
                    if typ=='CREATE_ACTOR':
                        args={'name':op.get('label',target),'xform':xform(op.get('transform',{'position':[0,0,0],'rotation':[0,0,0,1],'scale':[1,1,1]})),'parent':None,'snap_to_ground':False}
                        if op.get('asset'):
                            with store.read() as db:asset=Store.entity(db,op['asset'])
                            args['asset_path']=asset['path'];tool='add_to_scene_from_asset'
                        else:args['actor_type']={'refPath':op['class']};tool='add_to_scene_from_class'
                        entry.update(toolset=SCENE,tool=tool,arguments=args,create_target=target,properties=op.get('component_properties',{}))
                    elif typ=='DELETE_ACTOR':
                        entry.update(toolset=SCENE,tool='remove_from_scene',arguments={},actor_arguments={'actor':target})
                    elif typ in ('MOVE_ACTOR','ROTATE_ACTOR','SCALE_ACTOR'):
                        entry.update(toolset=ACTOR,tool='set_actor_transform',arguments={'xform':xform(patch['validation']['operation_transforms'][oid]),'worldspace':True},actor_arguments={'actor':target})
                    elif typ=='SET_INSTANCE_TRANSFORM':
                        transforms=patch['validation']['operation_transforms'][oid]
                        entry.update(toolset=TWIN,tool='spatial_twin_transform_instance',arguments={'instance_id':target,
                            'expected_transform':xform(transforms['before']),'transform':xform(transforms['after'])})
                    elif typ=='SET_PROPERTY':
                        entry.update(toolset=OBJECT,tool='set_properties',arguments={'values':encode({op['property']:op['value']})},actor_arguments={'instance':target})
                    elif typ in ('ATTACH','DETACH'):
                        refs={'component':target,'parent':op['parent'] if typ=='ATTACH' else None}
                        with store.read() as db:
                            for ident_ref in refs.values():
                                if ident_ref and ident_ref not in created and ident_ref not in roots:
                                    e=source.get(ident_ref) or Store.entity(db,ident_ref);roots[ident_ref]={'refPath':e['root_component_path']}
                        entry.update(toolset=ACTOR,tool='set_parent_component',arguments={},component_arguments=refs)
                    elif typ=='CHANGE_ASSET':
                        with store.read() as db:
                            asset=Store.entity(db,op['asset'])
                            if target not in created:e=mesh_component(Queries(store,db),target,op.get('component_id'))
                        args={'values':encode({'StaticMesh':{'refPath':asset['path']}})}
                        if target in created:entry['component_arguments']={'instance':target}
                        else:args['instance']={'refPath':e['path']}
                        entry.update(toolset=OBJECT,tool='set_properties',arguments=args)
                    entries.append(entry)
                if entries:
                    value=await action(session,PROGRAMMATIC,'execute_tool_script',{'script':dependent_script(entries,paths,roots)},'batch:'+ident)
                    value=json.loads(value) if isinstance(value,str) else value
                    if (not value.get('complete') or [r.get('operation_id') for r in value.get('completed',[])]!=[op['operation_id'] for op in pending]
                        or not all(r.get('properties_confirmed') for r in value.get('created',[]))):
                        raise ValueError('Batch incomplete; inspect partial references and uncertain operation: '+str(value.get('error')))
                    paths.update({row['target']:row['actor']['refPath'] for row in value.get('created',[])})
                # Force a flush through the registered Twin tool, never write canonical state.
                await call_tool(session,TWIN,'spatial_twin_sync',{})
                deadline=time.monotonic()+timeout;expected=patch['validation']['expected'];resolved={}
                while True:
                    with store.read() as db:
                        revision=Store.revision(db);ok=revision>patch['base_revision']
                        for target,e in sorted(expected.items(),key=lambda pair:bool(pair[1] and pair[1]['kind']!='Actor')):
                            actual=observed(db,target,e,paths,resolved)
                            if actual:resolved[target]=actual['id']
                            ok=ok and matches(e,actual,resolved)
                    if ok:break
                    if time.monotonic()>=deadline:raise TimeoutError('Expected state did not appear in a newer Canonical revision')
                    await asyncio.sleep(.25)
                if save:
                    receipts.append({'state':'CANONICAL_VERIFIED','canonical_revision':revision,'actor_ids':resolved,'saved':False})
                    update(store,ident,receipts=receipts)
                    # Deletions require the native package finalizer; never SaveAll.
                    saved=await action(session,TWIN,'spatial_twin_save_patch',{'patch_id':ident},'save:'+ident)
                    saved=json.loads(saved) if isinstance(saved,str) else saved
                    if not isinstance(saved,dict) or saved.get('state')!='SAVED':raise ValueError('Touched package saving was not confirmed: '+str(saved))
                    with store.read() as db:
                        revision=Store.revision(db)
                        if not all(matches(e,observed(db,target,e,paths,resolved),resolved) for target,e in expected.items()):
                            raise ValueError('Expected canonical state changed during package saving')
                receipts.append({'state':'CANONICAL_VERIFIED','canonical_revision':revision,'actor_ids':resolved,'saved':save})
                update(store,ident,'APPLIED',receipts)
    except BaseException as error:
        update(store,ident,'FAILED',receipts,error_message(error));raise
    return store.patch_get(ident)


async def workflow(store,ident,url,timeout,save,camera=None,output=None):
    """Existing assets: apply, confirm, save and render through one official session."""
    if camera is None:return await execute(store,ident,url,timeout,save)
    if output is None:raise ValueError('Render output is required')
    from render import render,check_camera,render_summary
    check_camera(camera)  # Reject bad camera input before any world write.
    async with streamablehttp_client(url) as (read,write,_):
        async with ClientSession(read,write,read_timeout_seconds=timedelta(seconds=timeout)) as session:
            await session.initialize()
            patch=await execute(store,ident,url,timeout,save,session)
            capture=await render(store,ident,camera,output,url,session)
            return {**patch,'render':{**render_summary(capture),'receipt':str(Path(output).with_suffix('.json').resolve())}}


async def finalize_save(store,ident,url,timeout,session=None):
    """Explicit continuation of a terminal partial save; never replay authoring."""
    require_current_runtime()
    with store.application_lock():
        patch=store.patch_get(ident);validation=patch.get('validation') or {}
        receipts=list(patch.get('receipts') or [])
        if patch['status'] not in ('FAILED','APPLYING','APPLIED'):
            raise ValueError('Only an already executed patch can finalize its save')
        if (not validation.get('valid') or not validation.get('expected') or
            validation.get('base_revision')!=patch['base_revision'] or
            validation.get('operations_digest')!=hashlib.sha256(encode(patch['operations']).encode()).hexdigest()):
            raise ValueError('Save finalization requires the original unchanged validated plan')
        with store.patches() as db:
            complete=db.execute('SELECT 1 FROM patch_saves WHERE patch_id=?',(ident,)).fetchone()
            progress=db.execute('SELECT * FROM patch_save_progress WHERE patch_id=?',(ident,)).fetchone()
        if not complete:
            if (not progress or progress['operations_digest']!=validation['operations_digest'] or
                json.loads(progress['result']).get('state')!='PARTIAL'):
                raise ValueError('No terminal native partial save; do not retry an uncertain write')
            if not any(r.get('state') in ('SENT','ACKNOWLEDGED') and r.get('operation_id')=='save:'+ident for r in receipts):
                raise ValueError('Original save dispatch is missing')
            async with (streamablehttp_client(url) if session is None else nullcontext(None)) as streams:
                async with (ClientSession(streams[0],streams[1],read_timeout_seconds=timedelta(seconds=timeout)) if session is None else nullcontext(session)) as active:
                    if session is None:await active.initialize()
                    native=await sync_ready(store,active,timeout)
                    if native.get('patch_partial_save')!=1:raise ValueError('Native plugin does not support guarded save finalization')
                    resolved={}
                    for r in receipts:
                        if r.get('state')=='CANONICAL_VERIFIED':resolved.update(r.get('actor_ids',{}))
                    with store.read() as db:
                        revision=Store.revision(db)
                        if revision<=patch['base_revision']:raise ValueError('No newer Canonical effects')
                        for target,expected in validation['expected'].items():
                            if not matches(expected,observed(db,target,expected,{},resolved),resolved):
                                raise ValueError('Canonical effects changed before save finalization: '+target)
                    receipts.append({'state':'CANONICAL_VERIFIED','canonical_revision':revision,'actor_ids':resolved,'saved':False,'save_finalization':True})
                    # Stable logical save ID; each explicit attempt remains a
                    # separate receipt. It never shares the authoring batch ID.
                    dispatch='save:'+ident
                    receipts.append({'state':'SENT','operation_id':dispatch,'save_finalization':True,'attempt':len(receipts),
                                     'tool':TWIN+'.spatial_twin_save_patch','arguments':{'patch_id':ident,'resume':True}})
                    update(store,ident,'APPLYING',receipts)
                    try:
                        result=await call_tool(active,TWIN,'spatial_twin_save_patch',{'patch_id':ident,'resume':True})
                        result=json.loads(result) if isinstance(result,str) else result
                        receipts.append({'state':'ACKNOWLEDGED','operation_id':dispatch,'result':result})
                        if not isinstance(result,dict) or result.get('state')!='SAVED':raise ValueError('Scoped save finalization not confirmed: '+str(result))
                        # Recovery verifies the native completion journal, file hashes
                        # and fresh canonical effects. An ACK alone never sets saved.
                        update(store,ident,'FAILED',receipts)
                    except BaseException as error:
                        update(store,ident,'FAILED',receipts,error_message(error));raise
        # Package-save delegates can leave the last heartbeat pending until the
        # next editor tick, after the native canonical commit has already returned.
        # Wait on that local signal; never retry the save or spam editor RPCs.
        deadline=time.monotonic()+timeout
        while True:
            freshness=store.status(False);heartbeat=freshness.get('synchronizer',{})
            if heartbeat.get('error'):raise ValueError('Canonical synchronization failed after saving')
            if not freshness.get('editor_connected') or not heartbeat.get('pending_actors'):break
            if time.monotonic()>=deadline:raise TimeoutError('Wait for canonical synchronization before save confirmation')
            await asyncio.sleep(.05)
    result=recover(store,ident)
    if not result.get('receipts') or not result['receipts'][-1].get('saved'):
        raise ValueError('Save completion remains unconfirmed; inspect the canonical recovery receipt')
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root',type=Path,required=True);parser.add_argument('--patch',required=True)
    parser.add_argument('--url',default=UNREAL_MCP);parser.add_argument('--timeout',type=float,default=120);parser.add_argument('--no-save',action='store_true')
    parser.add_argument('--recover',action='store_true',help='Confirm interrupted effects and recorded save completion; requires --no-save and sends no editor actions')
    parser.add_argument('--finalize-save',action='store_true',help='Explicitly finish a journaled partial save; never replay actor operations')
    parser.add_argument('--camera',type=Path);parser.add_argument('--output',type=Path,help='Render receipt/image prefix, required with --camera')
    args=parser.parse_args()
    if not math.isfinite(args.timeout) or args.timeout<=0:parser.error('timeout must be positive')
    if bool(args.camera)!=bool(args.output):parser.error('--camera and --output must be supplied together')
    if args.recover and (not args.no_save or args.camera):parser.error('--recover requires --no-save and cannot render; it only verifies recorded effects/save receipts')
    if args.finalize_save and (args.recover or args.no_save or args.camera):parser.error('--finalize-save cannot combine with --recover, --no-save or render')
    try:
        if args.finalize_save:result=asyncio.run(finalize_save(Store(args.root),args.patch,args.url,args.timeout))
        elif args.recover:result=recover(Store(args.root),args.patch)
        else:result=asyncio.run(workflow(Store(args.root),args.patch,args.url,args.timeout,not args.no_save,
                                  json.loads(args.camera.read_text(encoding='utf-8')) if args.camera else None,args.output))
    except BaseException as error:
        print(error_message(error),file=sys.stderr);return 1
    from spatial_twin.server import patch_summary
    print(encode({**patch_summary(result),**({'render':result['render']} if 'render' in result else {})}));return 0


if __name__=='__main__':sys.exit(main())
