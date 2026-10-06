"""Compare exported and rebuilt offline navigation with a real native probe."""
import argparse
import json
import math
import uuid
from pathlib import Path
from spatial_twin.store import Store,encode
from spatial_twin.navigation import Navigation,invoke


def run(root,output=None):
    root=root.resolve();store=Store(root)
    if store.status().get('coverage')!='READ_ONLY_NAVIGATION_TEST_REGION':
        raise ValueError('This check requires an isolated native navigation probe')
    report=json.loads((root/'native-navigation-test.json').read_text())
    evidence=[]
    with store.read() as db:
        for sample in report['native_project_point']:
            agent=sample['agent'];nav=Navigation(store,agent)
            if not sample['found']:
                evidence.append({'agent':agent,'state':'UNKNOWN','reason':'Native ProjectPoint did not find navigation'});continue
            source,path=nav.source(db);position=sample['position']
            baseline=nav.query('nearest',position)
            assert baseline['state']=='READY',baseline
            tolerance=max(1,source['settings']['cell_height'])
            assert math.dist(baseline['position'],position)<=tolerance,baseline
            parameters=source['detour_parameters'];origin=parameters['origin']
            x=math.floor((-position[0]-origin[0])/parameters['tile_width'])
            y=math.floor((-position[1]-origin[2])/parameters['tile_height'])
            tiles=[t for t in source['tiles'] if t['x']==x and t['y']==y]
            assert tiles,'Projected point has no exported tile'
            affected={(x,y):[min(t['minimum_height'] for t in tiles),max(t['maximum_height'] for t in tiles)+source['settings']['height']]}
            patch_id=str(uuid.uuid5(uuid.NAMESPACE_URL,root.as_uri()+'/navigation-parity/'+agent))
            rebuilt=nav.rebuild_tiles(db,patch_id,affected)
            if rebuilt['state']!='READY':
                evidence.append({'agent':agent,'state':'UNKNOWN','rebuild':rebuilt});continue
            result=invoke(nav.native(db).STNavigationQuery,root/rebuilt['path'],encode({'mode':'nearest','start':position,'extent':source['query_extent'],'filter':source['filter']}))
            distance=math.dist(result['position'],position) if result.get('position') else None
            item={'agent':agent,'state':'PASS' if result['state']=='READY' and distance<=tolerance else 'FAILED',
                  'native_position':position,'exported':baseline,'rebuilt':result,'position_error_cm':distance,'tolerance_cm':tolerance}
            if sample.get('end_found'):
                end=sample['projected_end'];exported_path=nav.query('path',position,end)
                rebuilt_path=invoke(nav.native(db).STNavigationQuery,root/rebuilt['path'],encode({'mode':'path','start':position,'end':end,'extent':source['query_extent'],'filter':source['filter']}))
                expected=sample.get('reachable',False)
                item['path_parity']={'native':sample,'exported':exported_path,'rebuilt':rebuilt_path}
                for name,value in (('exported',exported_path),('rebuilt',rebuilt_path)):
                    passed=value.get('state')=='READY' and value.get('reachable')==expected
                    if expected:
                        cost_error=abs(value.get('cost',float('inf'))-sample['path_cost'])
                        end_error=math.dist(value['projected_end'],end) if value.get('projected_end') else float('inf')
                        item['path_parity'][name+'_cost_error_cm']=cost_error
                        item['path_parity'][name+'_end_error_cm']=end_error
                        passed=passed and cost_error<=tolerance and end_error<=tolerance
                    if not passed:item['state']='FAILED'
            else:item['path_parity']={'state':'NOT_TESTED','reason':'Native probe did not include a projected destination'}
            evidence.append(item)
    result={'state':'PASS' if evidence and all(e['state']=='PASS' for e in evidence) else 'INCOMPLETE',
            'scope':'actual native probe; one rebuilt tile per exported agent; no game writes', 'evidence':evidence}
    (output or root/'offline-navigation-parity.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args();print(encode(run(args.root,args.output)))
