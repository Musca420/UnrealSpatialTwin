"""Real official-MCP capacity correction in the owned CapacityFixture map."""
import argparse,asyncio,json,math,sys,time
from pathlib import Path
import apply_patch as executor
from recovery_checks import save_owned
from spatial_twin.store import Store
from spatial_twin.navigation import Navigation


async def native_projections(session,store,project,actor,points,extent,revision):
    with store.read() as db:engine=Path(db.execute("SELECT value FROM metadata WHERE key='engine_directory'").fetchone()[0])
    sys.path.insert(0,str(engine/'Plugins/Experimental/PythonScriptPlugin/Content/Python'))
    import remote_execution as remote
    ref={'refPath':'/Script/PythonScriptPlugin.Default__PythonScriptPluginSettings'}
    async def setting(value):
        assert await executor.call_tool(session,executor.OBJECT,'set_properties',{'instance':ref,'values':json.dumps({'bRemoteExecution':value})}) is True
    old=json.loads(await executor.call_tool(session,executor.OBJECT,'get_properties',{'instance':ref,'properties':['bRemoteExecution','RemoteExecutionMulticastBindAddress','RemoteExecutionMulticastTtl']}))
    assert old=={'bRemoteExecution':False,'RemoteExecutionMulticastBindAddress':'127.0.0.1','RemoteExecutionMulticastTtl':0}
    bridge=remote.RemoteExecution()
    try:
        await setting(True);bridge.start();deadline=time.monotonic()+8;nodes=[]
        while time.monotonic()<deadline:
            nodes=[n for n in bridge.remote_nodes if Path(n.get('project_root','')).resolve()==project.parent]
            if nodes:break
            await asyncio.sleep(.25)
        assert len(nodes)==1;bridge.open_command_connection(nodes[0]['node_id'])
        request=dict(path=actor['path'],package=actor['package'],points=points,extent=extent,revision=revision)
        code='import unreal,json\nrequest='+repr(request)+'''
world=unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
assert world.get_path_name()=='/Game/CapacityFixture.CapacityFixture'
tool=unreal.get_default_object(unreal.SpatialTwinToolset)
before=json.loads(tool.call_method('spatial_twin_status'))
assert before['revision']==request['revision'] and not before['pending'] and not before['navigation_building']
actor=unreal.find_object(None,request['path']);assert isinstance(actor,unreal.RecastNavMesh)
assert actor.get_package().get_name()==request['package']
points=[]
for p in request['points']:
 value=unreal.NavigationSystemV1.project_point_to_navigation(world,unreal.Vector(*p),actor,None,unreal.Vector(*request['extent']))
 points.append([value.x,value.y,value.z] if value is not None else None)
assert json.loads(tool.call_method('spatial_twin_status'))==before
print(json.dumps({'points':points,'tile_pool_size':actor.get_editor_property('tile_pool_size'),'fixed_tile_pool':actor.get_editor_property('fixed_tile_pool_size')}))
'''
        result=bridge.run_command(code,exec_mode=remote.MODE_EXEC_FILE);assert result.get('success'),result
        return json.loads(next(v['output'] for v in result['output'] if v['type']=='Info'))
    finally:
        bridge.stop();await setting(False)


async def ready(store,session,agent=None,expected_capacity=None,after_revision=None):
    deadline=time.monotonic()+120
    while True:
        value=executor.require_target(store,await executor.call_tool(session,executor.TWIN,'spatial_twin_status',{}))
        assert value['map']=='/Game/CapacityFixture'
        if value['ready'] and not any(value[k] for k in ('pending','scanning','navigation_building','navigation_refresh_pending','navigation_locked')):
            if expected_capacity is None:return value
            # A native property edit can settle before its deferred rebuild starts.
            # Observe the requested exported result, not only idle flags or MCP OK.
            exported=Navigation(store,agent).status()
            if (exported['state']=='READY' and exported['revision']>after_revision
                and exported['tile_capacity']==expected_capacity
                and (exported.get('capacity_settings') or {}).get('tile_pool_size')==expected_capacity):return value
        if time.monotonic()>=deadline:raise TimeoutError('Native capacity fixture did not settle')
        await asyncio.sleep(1)


async def run(project,url,output,verify_saved=False):
    project=project.resolve();output=output.resolve();assert project.stem=='SpatialTwinFixture' and not output.exists()
    store=Store(project.parent/'Saved/SpatialTwin');setup=json.loads((store.root/'native-capacity-fixture.json').read_text())
    assert setup['state']=='READY_SATURATED_FIXTURE' and len(setup['agents'])==1
    agent=setup['agents'][0]['agent'];points=[p['position'] for p in setup['agents'][0]['probes']];nav=Navigation(store,agent)
    report={'state':'RUNNING','project':str(project),'agent':agent,'scope':'Owned technical map; no game edits'};started=time.perf_counter()
    def record():output.write_text(json.dumps(report,indent=2)+'\n')
    async def sample(session,expected_capacity=None,after_revision=None):
        state=await ready(store,session,agent,expected_capacity,after_revision);status=nav.status();assert status['state']=='READY'
        with store.read() as db:
            source,_=nav.source(db,include_tiles=False);actor=Store.entity(db,status['native_actor']['id'])
        assert actor['path']==status['native_actor']['path'] and actor['package']==status['native_actor']['package']
        native=await native_projections(session,store,project,actor,points,source['query_extent'],state['revision'])
        offline=[nav.query('nearest',p) for p in points]
        for expected,observed in zip(native['points'],offline):
            if expected is None:assert observed['state']=='UNKNOWN',observed
            else:assert observed['state']=='READY' and math.dist(expected,observed['position'])<.1,(expected,observed)
        return dict(status=status,native=native,offline=offline),actor
    try:
        async with executor.streamablehttp_client(url) as (r,w,_):
            async with executor.ClientSession(r,w) as session:
                await session.initialize();before,actor=await sample(session)
                if verify_saved:
                    assert before['status']['tile_capacity']==16 and not before['status']['tile_pool_full']
                    assert before['status']['capacity_settings']['tile_pool_size']==before['native']['tile_pool_size']==16
                    assert before['status']['active_tiles']>2 and all(before['native']['points'])
                    assert not actor['package_dirty']
                    report.update(state='PASS_SAVED_REOPEN',observed=before,seconds=time.perf_counter()-started)
                    print(json.dumps(dict(state=report['state'],revision=before['status']['revision'],points=len(points),seconds=report['seconds'])))
                    return
                assert before['status']['tile_pool_full'] and before['status']['tile_capacity']==2
                assert before['status']['capacity_settings']['tile_pool_size']==before['native']['tile_pool_size']==2
                assert before['status']['capacity_settings']['fixed_tile_pool'] is True
                report['before']=before;report['native_actor']=actor['path'];record()
                result=await executor.call_tool(session,executor.OBJECT,'set_properties',{'instance':{'refPath':actor['path']},'values':json.dumps({'TilePoolSize':16})})
                report['official_property_result']=result;record();assert result is True
                after,actor=await sample(session,16,before['status']['revision']);report['after']=after;record()
                assert after['status']['revision']>before['status']['revision']
                assert after['status']['capacity_settings']['tile_pool_size']==after['native']['tile_pool_size']==16
                assert after['status']['tile_capacity']==16 and not after['status']['tile_pool_full'] and after['status']['active_tiles']>2
                recovered=[i for i,(a,b) in enumerate(zip(before['native']['points'],after['native']['points'])) if a is None and b is not None]
                assert recovered,'Capacity change did not recover any native sampled coverage'
                report['saved']=await save_owned(session,store,project,actor,actor['label'])
                final=await ready(store,session)
                with store.read() as db:assert not Store.entity(db,actor['id'])['package_dirty']
                report.update(state='PASS',newly_covered_sample_indices=recovered,native_final=final,points=points,seconds=time.perf_counter()-started)
    except BaseException as error:
        report.update(state='FAILED',error=repr(error));raise
    finally:record()
    print(json.dumps(dict(state=report['state'],revision=report['native_final']['revision'],newly_covered=len(recovered),seconds=report['seconds'])))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--project',type=Path,required=True);p.add_argument('--url',required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--verify-saved',action='store_true');a=p.parse_args()
    asyncio.run(run(a.project,a.url,a.output,a.verify_saved))
