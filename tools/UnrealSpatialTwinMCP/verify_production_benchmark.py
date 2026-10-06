"""Independent native readback and scoped restoration for benchmark runs."""
import argparse,asyncio,json,time
from pathlib import Path
from construction_benchmark import ClientSession,streamablehttp_client,program,PROGRAMMATIC,ACTOR,SCENE,OBJECT,ASSET
from apply_patch import call_tool,execute,xform
from spatial_twin.store import Store

async def verify(spec_path,name):
    if not name.isalnum() or len(name)>24:raise ValueError('Invalid name')
    spec=json.loads(spec_path.read_text());base=Path(spec['project']).parent
    assert base.resolve()==spec_path.resolve().parent and Path(spec['project']).stem=='SpatialTwinBench'
    folder=base/'runs'/name;output=folder/'independent.json'
    if output.exists():raise ValueError('Already verified; do not replay cleanup')
    result=json.loads((folder/'result.json').read_text());journal=json.loads((folder/'journal.json').read_text())
    assert result['state']==journal['stage']=='VERIFIED' and result['actor_refs']==journal['created']
    assert result['scenario']==journal['scenario'] and result['route']==journal['route']
    task=spec['scenarios'][result['scenario']];started=time.perf_counter()
    async with streamablehttp_client(spec['url']) as (r,w,_):
      async with ClientSession(r,w) as native:
        await native.initialize()
        async def batch(body,values):
            value=await call_tool(native,PROGRAMMATIC,'execute_tool_script',{'script':program(body,values)})
            return (json.loads(value) if isinstance(value,str) else value)['value']
        assert await call_tool(native,SCENE,'get_current_level',{})==spec['map']
        body="    return [{'label':call("+repr(ACTOR)+",'get_label',{'actor':a}),'transform':call("+repr(ACTOR)+",'get_actor_transform',{'actor':a}),'mesh':call("+repr(OBJECT)+",'get_properties',{'instance':call("+repr(ACTOR)+",'get_root_component',{'actor':a}),'properties':['StaticMesh']})} for a in DATA]\n"
        rows=await batch(body,result['actor_refs'])
        for i,(row,pos,ref) in enumerate(zip(rows,result['positions_cm'],result['actor_refs'])):
            assert ref['refPath'].startswith(spec['map']+'.')
            expected_label=(task['label_prefix']+f'{i:02d}' if task['kind']=='move' else f'BenchRun_{name}_{i:02d}')
            if result.get('expected_labels'):
                assert len(set(result['expected_labels']))==len(rows)
                assert all(label.startswith(f'BenchRun_{name}_') for label in result['expected_labels'])
                expected_label=result['expected_labels'][i]
            assert row['label']==expected_label
            assert all(abs(row['transform']['location'][k]-v)<.001 for k,v in zip(('x','y','z'),pos))
            assert json.loads(row['mesh'])['StaticMesh']['refPath']==result['asset_path']
        expected_count=32 if task['kind']=='move' else 9
        assert len(rows)==expected_count and len(result['candidate_indexes'])==expected_count
        assert result['candidate_indexes']==(list(range(32)) if task['kind']=='move' else [i for i in range(12) if i%4!=3])
        proof=dict(state='NATIVE_VERIFIED',rows=rows,render=str(folder/'render.png'),cleanup=None)
        if result['route']=='with':
            store=Store(base/'Saved/SpatialTwin');applied=store.patch_get(result['patch_id']);assert applied['status']=='APPLIED'
        proof['verification_seconds']=time.perf_counter()-started
        output.with_suffix('.pending.json').write_text(json.dumps(proof,indent=2))
        cleanup_started=time.perf_counter()
        if result['route']=='with':
            if task['kind']=='move':ops=[dict(type='MOVE_ACTOR',target=e['id'],value=e['transform']['position']) for e in journal['targets']]
            else:
                ids=next(x['actor_ids'] for x in reversed(applied['receipts']) if x['state']=='CANONICAL_VERIFIED')
                ops=[dict(type='DELETE_ACTOR',target=ids[op['target']]) for op in applied['operations']]
            patch=store.patch_create(ops);cleanup=await execute(store,patch['id'],spec['url'],60,False,native)
            assert cleanup['status']=='APPLIED';proof['cleanup']=dict(patch_id=patch['id'],canonical_confirmed=True)
        elif task['kind']=='move':
            values=[dict(actor=dict(refPath=e['path']),xform=e['native_transform'],worldspace=True) for e in journal['targets']]
            assert all(await batch("    return [call("+repr(ACTOR)+",'set_actor_transform',v) for v in DATA]\n",values))
            proof['cleanup']=dict(restored=len(values))
        else:
            assert all(await batch("    return [call("+repr(SCENE)+",'remove_from_scene',{'actor':a}) for a in DATA]\n",result['actor_refs']))
            proof['cleanup']=dict(deleted=len(result['actor_refs']))
        if task['kind']=='move':
            restored=await batch(body,result['actor_refs'])
            for i,row in enumerate(restored):
                expected=[i%8*260,i//8*260,350+(i%3)*30]
                assert all(abs(row['transform']['location'][k]-v)<.001 for k,v in zip(('x','y','z'),expected))
        else:
            remaining=await call_tool(native,SCENE,'find_actors',dict(name='BenchRun_'+name,tag='',collision_channels=[]))
            assert not remaining
        if task['kind']=='author':
            expected=f'/Game/BenchRuns/SM_ST_Rail_{name}.SM_ST_Rail_{name}'
            assert result['asset_path']==expected
            assert await call_tool(native,ASSET,'delete',dict(path=expected.split('.')[0]))
            assert not await call_tool(native,ASSET,'exists',dict(path=expected))
            assert not (base/'Content/BenchRuns'/('SM_ST_Rail_'+name+'.uasset')).exists()
            proof['cleanup']['asset_deleted']=expected
        proof.update(state='VERIFIED_AND_RESTORED',seconds=time.perf_counter()-started,cleanup_seconds=time.perf_counter()-cleanup_started)
        output.write_text(json.dumps(proof,indent=2)+'\n');print(json.dumps(dict(name=name,state=proof['state'],seconds=proof['seconds'])))

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--spec',required=True,type=Path);p.add_argument('--name',required=True);a=p.parse_args()
    asyncio.run(verify(a.spec,a.name))
