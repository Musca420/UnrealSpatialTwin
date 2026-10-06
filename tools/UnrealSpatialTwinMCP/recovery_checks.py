"""Explicit live lost-response regression; owns one temporary actor and package.

Uses official MCP for authoring. The test's explicit scoped save uses native
Python because official save_actor cannot save external actor packages. It is
separate from recovery, which only reads Canonical and updates patch receipts.
"""
import argparse,asyncio,json,sys,time,uuid
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
import apply_patch as executor
from spatial_twin.store import Store
from spatial_twin.shadow import validate
from spatial_twin.server import patch_summary


async def save_owned(session,store,project,entity,label):
    with store.read() as db:engine=Path(db.execute("SELECT value FROM metadata WHERE key='engine_directory'").fetchone()[0])
    sys.path.insert(0,str(engine/'Plugins/Experimental/PythonScriptPlugin/Content/Python'))
    import remote_execution as remote
    ref={'refPath':'/Script/PythonScriptPlugin.Default__PythonScriptPluginSettings'}
    async def setting(value):
        assert await executor.call_tool(session,executor.OBJECT,'set_properties',{'instance':ref,'values':json.dumps({'bRemoteExecution':value})}) is True
    old=await executor.call_tool(session,executor.OBJECT,'get_properties',{'instance':ref,'properties':['bRemoteExecution','RemoteExecutionMulticastBindAddress','RemoteExecutionMulticastTtl']})
    assert json.loads(old)=={'bRemoteExecution':False,'RemoteExecutionMulticastBindAddress':'127.0.0.1','RemoteExecutionMulticastTtl':0}
    bridge=remote.RemoteExecution()
    try:
        await setting(True);bridge.start();deadline=time.monotonic()+8;nodes=[]
        while time.monotonic()<deadline:
            nodes=[n for n in bridge.remote_nodes if Path(n.get('project_root','')).resolve()==project.parent]
            if nodes:break
            await asyncio.sleep(.25)
        assert len(nodes)==1,'Exact test project connection required'
        bridge.open_command_connection(nodes[0]['node_id'])
        expected={'path':entity['path'],'package':entity['package'],'label':label,'position':entity['transform']['position']}
        code='import unreal,json\nexpected='+repr(expected)+'''
actor=unreal.find_object(None,expected['path'])
assert actor and actor.get_actor_label()==expected['label']
package=actor.get_package()
assert package.get_path_name()==expected['package']
position=actor.get_actor_location()
assert all(abs(a-b)<.01 for a,b in zip([position.x,position.y,position.z],expected['position']))
assert unreal.EditorLoadingAndSavingUtils.save_packages([package],True)
print(json.dumps({'saved_owned_package':package.get_path_name(),'position':[position.x,position.y,position.z]}))
'''
        result=bridge.run_command(code,exec_mode=remote.MODE_EXEC_FILE);assert result.get('success'),result
        return result
    finally:
        bridge.stop();await setting(False)


async def check(project,root,url,output,position,fault_operation='MOVE_ACTOR'):
    assert not output.exists(),'Use a new evidence file'
    store=Store(root);label='_TwinRecovery_'+uuid.uuid4().hex[:12]
    report={'state':'RUNNING','label':label,'live_calls':[]};original=executor.call_tool
    def record():output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    async def measured(session,toolset,tool,args):
        result=await original(session,toolset,tool,args);report['live_calls'].append(tool);record();return result
    executor.call_tool=measured;record()
    try:
      async with executor.streamablehttp_client(url) as (r,w,_):
       async with executor.ClientSession(r,w) as session:
        await session.initialize();native=await executor.sync_ready(store,session)
        if fault_operation=='CREATE_ACTOR':assert native.get('patch_result_journal')==1,'Native result journal required'
        if fault_operation in ('SAVE','SAVE_DELETE'):assert native.get('patch_save_journal')==1,'Native save journal required'
        with store.read() as db:
            report['initial_actors']=db.execute("SELECT count(*) FROM entities WHERE kind='Actor'").fetchone()[0]
            asset=Store.entity(db,'asset:/Engine/BasicShapes/Cube.Cube');assert asset.get('native_spawn_collision')
        create=store.patch_create([{'type':'CREATE_ACTOR','target':'probe','label':label,'class':'/Script/Engine.StaticMeshActor',
            'asset':asset['id'],'component_properties':{'bCanEverAffectNavigation':False},
            'transform':{'position':position,'rotation':[0,0,0,1],'scale':[1,1,1]}}])
        report['create_id']=create['id'];record()
        if fault_operation in ('MOVE_ACTOR','SAVE_DELETE'):
            created=await executor.execute(store,create['id'],url,30,True,session)
            ident=created['receipts'][-1]['actor_ids']['probe'];report['actor_id']=ident;report['create']=patch_summary(created);record()
            await executor.sync_ready(store,session)
            interrupted=store.patch_create([{'type':'DELETE_ACTOR','target':ident}] if fault_operation=='SAVE_DELETE' else [{'type':'MOVE_ACTOR','target':ident,'value':[position[0]+200,*position[1:]]}])
        else:interrupted=create
        report['interrupted_id']=interrupted['id'];report['fault_operation']=fault_operation;record()
        async def lose_response(session,toolset,tool,args):
            result=await measured(session,toolset,tool,args)
            if tool==('spatial_twin_save_patch' if fault_operation in ('SAVE','SAVE_DELETE') else 'execute_tool_script'):
                report['discarded_native_response']=result;report['fault_writes']=report.get('fault_writes',0)+1;record()
                raise ConnectionError('Injected loss after real official MCP action response')
            return result
        executor.call_tool=lose_response
        try:
            await executor.execute(store,interrupted['id'],url,30,fault_operation in ('SAVE','SAVE_DELETE'),session)
            raise AssertionError('Lost response was not injected')
        except ConnectionError:pass
        finally:executor.call_tool=measured
        failed=store.patch_get(interrupted['id']);assert failed['status']=='FAILED' and failed['receipts'][-1]['state']=='SENT'
        if fault_operation=='CREATE_ACTOR':
            with store.patches() as db:
                row=db.execute('SELECT * FROM patch_results WHERE patch_id=?',(interrupted['id'],)).fetchone()
                assert row and row['operations_digest']==failed['validation']['operations_digest']
                report['native_journal']=dict(row)
            args={'patch_id':interrupted['id'],'operations_digest':row['operations_digest'],'result_json':row['result']}
            async def journal(arguments):
                value=await measured(session,executor.TWIN,'spatial_twin_record_patch_result',arguments)
                return json.loads(value) if isinstance(value,str) else value
            assert (await journal(args))['state']=='RECORDED'
            assert 'error' in await journal({**args,'operations_digest':'0'*64})
            conflict={**json.loads(row['result']),'complete':False,'error':'Conflicting terminal result'}
            assert 'error' in await journal({**args,'result_json':json.dumps(conflict)})
            report['native_journal_guards']='idempotent, mismatched digest and conflicting result verified'
        report['failed']=patch_summary(failed);await executor.sync_ready(store,session)
        if fault_operation in ('SAVE','SAVE_DELETE'):
            with store.patches() as db:
                row=db.execute('SELECT * FROM patch_saves WHERE patch_id=?',(interrupted['id'],)).fetchone()
                assert row;report['native_save_journal']=dict(row)
        before=store.status(False)['canonical_revision'];calls=len(report['live_calls'])
        params=StdioServerParameters(command=sys.executable,args=[str(Path(__file__).with_name('server.py')),'--project',str(project),'--root',str(root)])
        async with stdio_client(params) as (read,write):
         async with ClientSession(read,write) as twin:
            await twin.initialize()
            result=await twin.call_tool('patch_status',{'patch_id':interrupted['id'],'recover':True});assert not result.isError,result.content
            recovered=result.structuredContent or json.loads('\n'.join(c.text for c in result.content if c.type=='text'))
            assert recovered['status']=='APPLIED' and recovered['last_receipt']['recovered'] and recovered['saved']==(fault_operation in ('SAVE','SAVE_DELETE')),recovered
            repeated=await twin.call_tool('patch_status',{'patch_id':interrupted['id'],'recover':True});assert not repeated.isError
            assert repeated.structuredContent==result.structuredContent
        assert len(report['live_calls'])==calls and store.status(False)['canonical_revision']==before and report['fault_writes']==1
        report['recovery']=recovered;report['recovery_editor_calls']=0;report['recovery_canonical_unchanged']=before;record()
        if fault_operation in ('CREATE_ACTOR','SAVE'):
            final=store.patch_get(interrupted['id'])
            assert final['receipts'][-1]['native_result_recorded']
            ident=final['receipts'][-1]['actor_ids']['probe'];report['actor_id']=ident;record()
        if fault_operation!='SAVE_DELETE':
            with store.read() as db:entity=Store.entity(db,ident)
            assert entity['transform']['position']==([position[0]+200,*position[1:]] if fault_operation=='MOVE_ACTOR' else position)
            if fault_operation!='SAVE':report['owned_package_save']=await save_owned(session,store,project,entity,label);record()
            await executor.sync_ready(store,session)
            cleanup=store.patch_create([{'type':'DELETE_ACTOR','target':ident}]);report['cleanup_id']=cleanup['id'];record()
            cleaned=await executor.execute(store,cleanup['id'],url,30,True,session);await executor.sync_ready(store,session)
            report['cleanup']=patch_summary(cleaned)
        else:report['cleanup']=recovered
        with store.read() as db:
            assert not db.execute('SELECT 1 FROM entities WHERE id=? OR actor_id=?',(ident,ident)).fetchone()
            report['final_actors']=db.execute("SELECT count(*) FROM entities WHERE kind='Actor'").fetchone()[0]
        assert report['final_actors']==report['initial_actors']
        report['state']='PASS_SCOPED';record();print(json.dumps({k:report[k] for k in ('state','fault_writes','recovery_editor_calls','recovery_canonical_unchanged','final_actors')}))
    finally:executor.call_tool=original


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('project','root','output'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--url',required=True);p.add_argument('--position',type=float,nargs=3,required=True)
    p.add_argument('--fault-operation',choices=('MOVE_ACTOR','CREATE_ACTOR','SAVE','SAVE_DELETE'),default='MOVE_ACTOR');a=p.parse_args()
    asyncio.run(check(a.project.resolve(),a.root.resolve(),a.url,a.output.resolve(),a.position,a.fault_operation))
