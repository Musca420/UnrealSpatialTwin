import argparse,json,math,time,uuid
from pathlib import Path

from spatial_twin.store import Store,encode
from spatial_twin.navigation import Navigation,invoke
parser=argparse.ArgumentParser(description='Compare the native eight-ramp slope fixture with a complete offline tile reconstruction')
parser.add_argument('--root',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args();folder=args.root;store=Store(folder)
evidence=json.loads((folder/'slope-probe.json').read_text());assert evidence['state']=='OBSERVED_NATIVE'
started=time.perf_counter();rows=[]
with store.read() as db:
    revision=Store.revision(db)
    for agent in sorted({r['agent'] for r in evidence['projections']}):
        nav=Navigation(store,agent);source,path=nav.source(db);params=source['detour_parameters'];origin=params['origin'];affected={}
        assert source.get('walkable_slope_policy')=='ue5.8.2-56702186-global-agent-slope',source.keys()
        for lo,hi in source['navigation_bounds']:
            for x in range(math.floor((-hi[0]-origin[0])/params['tile_width']),math.floor((-lo[0]-origin[0])/params['tile_width'])+1):
                for y in range(math.floor((-hi[1]-origin[2])/params['tile_height']),math.floor((-lo[1]-origin[2])/params['tile_height'])+1):
                    affected[x,y]=[lo[2],hi[2]]
        rebuilt=nav.rebuild_tiles(db,str(uuid.uuid4()),affected,_source=(source,path));assert rebuilt['state']=='READY',rebuilt
        for probe in (r for r in evidence['projections'] if r['agent']==agent):
            components=[json.loads(r[0]) for r in db.execute("SELECT source FROM entities WHERE kind='Component' AND json_extract(source,'$.actor_id')=?",(probe['actor_id'],))]
            component=next(e for e in components if e.get('navigation_geometry_hash'))
            assert component['navigation_slope_behavior']==probe['behavior'],component
            request=encode(dict(mode='nearest',start=probe['position'],extent=[20,20,40],filter=source['filter']))
            baseline=invoke(nav.native(db).STNavigationQuery,path,request)
            shadow=invoke(nav.native(db).STNavigationQuery,store.root/rebuilt['path'],request)
            assert (baseline['state']=='READY')==probe['found'] and (shadow['state']=='READY')==probe['found'],(probe,baseline,shadow)
            if probe['found']:
                assert math.dist(shadow['position'],probe['projected'])<.001,(probe,shadow)
            rows.append(dict(**probe,source_behavior=component['navigation_slope_behavior'],native_cache=baseline,offline_rebuilt=shadow))
        assert len(affected)>source['active_tiles'],'Probe must reconstruct empty coordinates as well'
    assert Store.revision(db)==revision
assert {p['found'] for p in rows if p['surface_angle']==30}=={True}
assert {p['found'] for p in rows if p['surface_angle']==60}=={False}
report=dict(state='PASS_NATIVE_OFFLINE_PARITY',engine=evidence['engine_version'],revision=revision,seconds=time.perf_counter()-started,rows=rows,rebuilt_coordinates=len(affected),source_policy=source['walkable_slope_policy'],scope='Recast navigation only; CharacterMovement slope behavior is distinct; other engine builds unknown')
args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
print(json.dumps(dict(state=report['state'],probes=len(rows),coordinates=report['rebuilt_coordinates'],seconds=report['seconds'],output=str(args.output))))
