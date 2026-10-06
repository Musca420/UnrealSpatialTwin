"""Compare a closed native LinkFixture with its offline Shadow movement."""
import argparse
import json
from pathlib import Path
import time
from spatial_twin.store import Store
from spatial_twin.shadow import validate
from spatial_twin.navigation import Navigation


def run(root):
    started=time.perf_counter();store=Store(root)
    native=json.loads((root/'native-navigation-test.json').read_text())
    assert native['state']=='MEASURED' and native['native_after_moving_link']
    with store.read() as db:
        original=Store.entity(db,native.get('changed_actor_id',native['link_actor_id']));revision=Store.revision(db)
        if native.get('projected_link_fixture'):
            sources=[json.loads(r[0]) for r in db.execute("SELECT source FROM entities WHERE actor_id=? AND kind='NavigationInput'",(native['link_actor_id'],))]
            links=[link for e in sources for link in e.get('navigation_links',[])]
            assert links and all(l['requires_projection'] and abs(l['start'][2])<.01 and abs(l['end'][2])<.01 for l in links),links
    draft=store.patch_create([{'type':'MOVE_ACTOR','target':original['id'],'value':native['link_moved_position']}],revision)
    checked=validate(store,draft['id']);assert checked['status']=='VALIDATED',checked
    results=[]
    for sample in native['native_project_point']:
        assert sample['found'] and sample['end_found'] and sample['reachable'],sample
        nav=Navigation(store,sample['agent'])
        before=nav.query('path',sample['position'],sample['requested_end'])
        after=nav.query('path',sample['position'],sample['requested_end'],world='shadow',patch_id=draft['id'])
        reference=next(x for x in native['native_after_moving_link'] if x['agent']==sample['agent'])
        assert before['state']=='READY' and before['reachable']==sample['reachable'],before
        assert after['state']=='READY' and after['reachable']==reference['reachable']==False,(after,reference)
        assert abs(before['cost']-sample['path_cost'])<.1,(before,sample)
        results.append({'agent':sample['agent'],'native_before':sample,'native_after':reference,'offline_before':before,'shadow_after':after})
    with store.read() as db:
        assert Store.revision(db)==revision and Store.entity(db,original['id'])==original
    changed_owner=None
    if native.get('projected_link_fixture'):
        with store.read() as db:owner=Store.entity(db,native['link_actor_id'])
        position=owner['transform']['position']
        moved=store.patch_create([{'type':'MOVE_ACTOR','target':owner['id'],'value':[position[0]+10,*position[1:]]}],revision)
        checked=validate(store,moved['id'])
        assert checked['status']=='INVALID' and 'geometry reprojection' in json.dumps(checked['validation']),checked
        changed_owner={'patch_id':moved['id'],'status':checked['status'],'native_projection_required':True}
    return {'state':'PASS','seconds':time.perf_counter()-started,'canonical_unchanged':True,'patch_id':draft['id'],'results':results,'changed_projected_owner':changed_owner}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();result=run(args.root.resolve())
    args.output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:result[k] for k in ('state','seconds','canonical_unchanged','patch_id')}))
