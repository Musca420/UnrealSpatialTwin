"""Verify native-exported point links through the real offline Shadow pipeline.

Requires SpatialTwinTest's native-test.json. Uses a disposable SQLite test world;
never writes the scanned fixture or any project's canonical database.
"""
import argparse
import copy
import json
from pathlib import Path
import struct
import uuid
from test_spatial_twin import TwinTest, IDENTITY
from spatial_twin.navigation import Navigation
from spatial_twin.shadow import validate
from spatial_twin.store import Store, encode


def run(engine, library, source_file):
    evidence=json.loads(source_file.read_text())
    assert evidence['state']=='PASS' and evidence['native_point_link_endpoints_flags_move_property']
    link=evidence['native_point_link_source']
    assert link['start']==[6800,0,0] and link['end']==[7100,0,0],link
    fixture=TwinTest();fixture.setUp()
    try:
        store,db,root=fixture.store,fixture.db,fixture.root
        box=[[6300,-400,-50],[7150,400,50]]
        actor={'id':'a','kind':'Actor','class':'Fixture','transform':copy.deepcopy(IDENTITY),
               'bounds':[[6750,-50,-50],[7150,50,50]],'local_bounds':[[6750,-50,-50],[7150,50,50]]}
        fixture.put(actor)
        fixture.put({'id':'floor','kind':'Actor','class':'Fixture','transform':IDENTITY,'bounds':box})
        fixture.put({'id':'c','kind':'Component','parent_id':'floor','actor_id':'floor','transform':IDENTITY,
                     'bounds':box,'affects_navigation':True,'navigation_geometry_hash':'plane','collision':{'enabled':False}})
        fixture.put({'id':'a:navigation','kind':'NavigationInput','parent_id':'a','actor_id':'a','transform':copy.deepcopy(IDENTITY),
                     'bounds':actor['bounds'],'local_bounds':actor['bounds'],'affects_navigation':True,
                     'navigation_has_links':True,'navigation_links':[link],'navigation_link_coverage':'point_links'})
        vertices=[[x,y,0] for lo,hi in ((6300,6900),(7000,7150)) for x,y in ((lo,-400),(hi,-400),(hi,400),(lo,400))]
        triangles=[[0,2,1],[0,3,2],[4,6,5],[4,7,6]]
        (root/'geometry/plane.stg').write_bytes(b'STG1'+struct.pack('<III',1,8,4)+struct.pack('<24d',*(v for p in vertices for v in p))+struct.pack('<12I',*(v for t in triangles for v in t)))
        (root/'navigation').mkdir()
        (root/'navigation/empty.stn').write_bytes(b'STN1'+struct.pack('<I5d2i6di',2,-7168,-100,-512,1024,1024,32,2147483647,100,16,20,1/16,1/16,1/16,0))
        source={'id':'nav:Default','kind':'NavRegion','agent':'Default','path':'navigation/empty.stn',
                'settings':{'cell_size':16,'cell_height':4,'height':100,'radius':16,'climb':20,'slope':45,'simplification_error':1.3,'agent_index':0},
                'detour_parameters':{'origin':[-7168,-100,-512],'tile_width':1024,'tile_height':1024,'max_tiles':32},
                'active_tiles':1,'tiles':[{'x':0,'y':0,'minimum_height':-100,'maximum_height':200}],
                'areas':{link['area_class']:{'id':63,'flags':1}},'off_mesh_link_flag':32768,
                'link_area_order':list(range(64)),'input_coverage':'octree_audited','input_gap_count':0}
        fixture.put(source)
        for key,value in (('engine_directory',str(engine)),('native_library',str(library)),('navigation_owners_version','2')):
            db.execute('INSERT OR REPLACE INTO metadata VALUES(?,?)',(key,value))
        db.commit();nav=Navigation(store,'Default')
        with store.read() as read:
            built=nav.rebuild_tiles(read,str(uuid.uuid4()),{(0,0):[-100,200]})
        assert built['state']=='READY',built
        source['path']=built['path'];fixture.put(source);db.commit()
        before=nav.query('path',[7100,0,0],[6800,0,0])
        reverse=nav.query('path',[6800,0,0],[7100,0,0])
        assert before['state']=='READY' and before['reachable'] and not reverse['reachable'],(before,reverse)
        original=Store.entity(db,'a')
        draft=store.patch_create([{'type':'MOVE_ACTOR','target':'a','value':[300,0,0]}])
        checked=validate(store,draft['id']);assert checked['status']=='VALIDATED',checked
        after=nav.query('path',[7100,0,0],[6800,0,0],world='shadow',patch_id=draft['id'])
        assert after['state']=='READY' and not after['reachable'],after
        assert Store.entity(db,'a')==original and Store.revision(db)==1
        return {'state':'PASS','native_source':str(source_file),'canonical_revision_unchanged':1,
                'before':before,'reverse':reverse,'after':after,'validation':checked['status']}
    finally:fixture.tearDown()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('engine','library','source','output'):parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args();result=run(args.engine.resolve(),args.library.resolve(),args.source.resolve())
    args.output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(encode({'state':result['state'],'validation':result['validation'],'canonical_unchanged':True}))
