"""Compare the native closed FillFixture and its offline Shadow movement."""
import argparse
import json
from pathlib import Path
import time
from spatial_twin.store import Store
from spatial_twin.shadow import validate
from spatial_twin.navigation import Navigation,invoke


def run(root):
    started=time.perf_counter();store=Store(root)
    reference=json.loads((root/'native-navigation-test.json').read_text())
    masked=reference.get('mask_fixture',False)
    assert [s['found'] for s in reference['fill_before']]==([True,True,False] if masked else [False,True,False]),reference
    assert [s['found'] for s in reference['fill_after']]==([False,True,False] if masked else [True,False,True]),reference
    with store.read() as db:
        revision=Store.revision(db);original=Store.entity(db,reference['changed_actor_id'])
        components=[json.loads(r[0]) for r in db.execute("SELECT source FROM entities WHERE actor_id=? AND kind IN ('Component','NavigationInput')",(original['id'],))]
        if masked:assert any(c.get('navigation_mask_fill_underneath') and any(m.get('mask_fill_underneath') for m in c.get('navigation_modifiers',[])) for c in components),components
        else:assert any(c.get('navigation_fill_underneath') and 'navigation_filled_convex' in c for c in components),components
    draft=store.patch_create([{'type':'MOVE_ACTOR','target':original['id'],'value':reference['changed_position']}],revision)
    checked=validate(store,draft['id']);assert checked['status']=='VALIDATED',checked
    results=[]
    with store.read() as db:
        for phase in ('before','after'):
            for sample in reference['fill_'+phase]:
                nav=Navigation(store,sample['agent']);source,path=nav.source(db)
                assert source['fill_underneath_mask_count']==int(masked),source
                if masked:assert source['rasterization_mask_version']==1,source
                if phase=='after':path=store.root/checked['validation']['navigation'][sample['agent']]['path']
                result=invoke(nav.native(db).STNavigationQuery,path,json.dumps({'mode':'nearest','start':sample['requested'],'extent':[20,20,20]}))
                assert (result['state']=='READY')==sample['found'],(phase,sample,result)
                results.append({'phase':phase,'native':sample,'offline':result})
        assert Store.revision(db)==revision and Store.entity(db,original['id'])==original
    return {'state':'PASS','seconds':time.perf_counter()-started,'canonical_unchanged':True,'patch_id':draft['id'],'results':results}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();result=run(args.root.resolve())
    args.output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:result[k] for k in ('state','seconds','canonical_unchanged','patch_id')}))
