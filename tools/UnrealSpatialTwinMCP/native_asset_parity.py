"""Compare closed native mesh/ISM replacement evidence against Shadow."""
import argparse
import json
from pathlib import Path
import time
from spatial_twin.store import Store
from spatial_twin.shadow import preview,validate
from spatial_twin.query import Queries


def run(root):
    started=time.perf_counter();store=Store(root)
    reference=json.loads((root/'native-asset-test.json').read_text())
    assert reference['cube_hit'] and not reference['sphere_hit'],reference
    patch=store.patch_create([{'type':'CHANGE_ASSET','target':reference['actor_id'],
                              'component_id':reference['component_id'],'asset':reference['asset_id']}],reference['baseline_revision'])
    checked=validate(store,patch['id']);assert checked['status']=='VALIDATED',checked
    compared=[]
    with store.read() as db:
        overlay,errors=preview(store,db,patch);assert not errors,errors
        native={e['id']:e for e in reference['expected']}
        for ident,e in overlay.items():
            for field in ('asset_id','bounds','collision_bounds','geometry_hash'):
                if field in native[ident]:assert e.get(field)==native[ident][field],(ident,field,e.get(field),native[ident][field])
            compared.append(ident)
        args=([345,45,200],[0,0,-1],400)
        before=Queries(store,db).raycast(*args);after=Queries(store,db,overlay).raycast(*args)
        assert before['complete'] and before['hit'],before
        assert after['complete'] and after['hit'] is None,after
        assert Store.revision(db)==reference['baseline_revision']
        assert Store.entity(db,reference['component_id'])['asset_id']=='asset:/Engine/BasicShapes/Cube.Cube'
    return {'state':'PASS','seconds':time.perf_counter()-started,'patch_id':patch['id'],
            'canonical_unchanged':True,'compared_entities':compared,'native_hits':[True,False],
            'offline_hits':[before,after]}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();result=run(args.root.resolve())
    args.output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:result[k] for k in ('state','seconds','canonical_unchanged','patch_id')}))
