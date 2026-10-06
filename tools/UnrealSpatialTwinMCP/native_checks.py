"""Run real Chaos and layered Recast checks outside Unreal Editor.

Build the plugin first. This test writes only inside --output.
"""
import argparse
import ctypes
import json
from pathlib import Path
import struct
import hashlib
import math
import time
from spatial_twin.navigation import backend, invoke
from spatial_twin.store import encode


def run(engine, library, output):
    output.mkdir(parents=True,exist_ok=True)
    native=backend(str(engine),str(library))
    native.STCollisionQuery.argtypes=[ctypes.c_char_p,ctypes.c_void_p,ctypes.c_int]
    native.STCollisionQuery.restype=ctypes.c_int
    evidence=[]
    for distance,overlap in ((0,True),(1,True),(2,False),(3,False)):
        request={'origin':[1e11,0,0],'a':[{'vertices':[[1e11,0,0]],'margin':1}],
                 'b':[{'vertices':[[1e11+distance,0,0]],'margin':1}]}
        result=invoke(native.STCollisionQuery,encode(request))
        assert result['state']=='READY' and result['overlap']==overlap,result
        evidence.append({'test':'sphere','distance':distance,'result':result})
    base=output/'empty.stn'
    # UE uses INT32_MAX as maxPolys in real project navmeshes; it sizes ref bits,
    # not a per-tile allocation. Individual tile counts remain uint16.
    base.write_bytes(b'STN1'+struct.pack('<I5d2i6di',2,-1024,-100,-1024,1024,1024,32,2147483647,100,16,20,1/16,1/16,1/16,0))
    settings={'cell_size':16,'cell_height':4,'height':100,'radius':16,'climb':20,'slope':45,
              'simplification_error':1.3,'layer_partitioning':1,'region_partitioning':1}
    vertices=[[x,y,z] for z in (0,300) for x,y in ((0,0),(1024,0),(1024,1024),(0,1024))]
    triangles=[[0,2,1],[0,3,2],[4,6,5],[4,7,6]]
    tile={'x':0,'y':0,'layer':0,'minimum_height':-100,'maximum_height':600,
          'vertices':vertices,'triangles':triangles}
    for partition in (0,1,2):
        settings.update(layer_partitioning=partition,region_partitioning=partition)
        path=output/f'layers-{partition}.stn'
        result=invoke(native.STBuildNavigation,base,encode({'settings':settings,'tiles':[tile]}),path)
        assert result['state']=='READY',result
        for height in (0,300):
            query=invoke(native.STNavigationQuery,path,encode({'mode':'path','start':[200,200,height],'end':[800,800,height]}))
            assert query['state']=='READY' and query['reachable'],query
            assert abs(query['position'][2]-height)<=4.01,query
            assert query['requested_end']==[800,800,height] and len(query['projected_end'])==3,query
            evidence.append({'test':'layered_path','partition':partition,'height':height,'result':query})
            short=invoke(native.STNavigationQuery,path,encode({'mode':'path','start':[250,350,height],'end':[251,350,height],
                         'filter':{'costs':[3]*64,'fixed_costs':[17]*64}}))
            assert short['reachable'] and math.isclose(short['cost'],3*math.dist(short['projected_start'],short['projected_end']),abs_tol=1e-6),short
            evidence.append({'test':'single_polygon_weighted_cost_no_entry_charge','partition':partition,'height':height,'result':short})
        query=invoke(native.STNavigationQuery,path,encode({'mode':'path','start':[200,200,0],'end':[800,800,300]}))
        assert not query['reachable'],query
        # Corrupt the tile while retaining a well-formed outer container.
        # Native addTile trusts these counts/indices; the Twin validates first.
        raw=path.read_bytes();assert struct.unpack_from('<I',raw,4)[0]==2
        for name,offset,value,format in (('empty_link_pool',120+16,0,'H'),
                                        ('oversized_vertices',120+6,65535,'H'),
                                        ('wrong_tile_version',120,65535,'H')):
            damaged=bytearray(raw);struct.pack_into('<'+format,damaged,offset,value)
            broken=output/(name+'.stn');broken.write_bytes(damaged)
            answer=invoke(native.STNavigationQuery,broken,encode({'mode':'status'}))
            assert answer['state']=='UNKNOWN',(name,answer)
            evidence.append({'test':'corrupt_tile','case':name,'result':answer})
    # Per-object fill flags must not fill the space between separate objects.
    assert native.STNavigationRasterizationVersion()==2
    for label,groups,lower in (
        ('upper_fill',[{'first_triangle':0,'triangle_count':2,'flags':0},{'first_triangle':2,'triangle_count':2,'flags':1}],False),
        ('separate_convex',[{'first_triangle':0,'triangle_count':2,'flags':2},{'first_triangle':2,'triangle_count':2,'flags':2}],True),
        ('single_convex',[{'first_triangle':0,'triangle_count':4,'flags':2}],False)):
        path=output/(label+'.stn')
        built=invoke(native.STBuildNavigation,base,encode({'settings':settings,'tiles':[{**tile,'rasterization_groups':groups}]}),path)
        assert built['state']=='READY',built
        projected=[]
        for height,expected in ((0,lower),(300,True)):
            value=invoke(native.STNavigationQuery,path,encode({'mode':'nearest','start':[200,200,height],'extent':[20,20,20]}))
            assert (value['state']=='READY')==expected,(label,height,value)
            projected.append(value)
        evidence.append({'test':'per_object_rasterization','case':label,'projections':projected})
    for shape in (1,2,3):
        mask={'shape':shape,'mode':0,'area_id':63,'mask_fill_underneath':True,
              'bounds':[[100,100,-100],[500,500,600]],'origin':[300,300,250],'radius':200,
              'points':[[100,100,0],[500,100,0],[500,500,0],[100,500,0]]}
        path=output/f'masked-fill-{shape}.stn'
        groups=[{'first_triangle':0,'triangle_count':2,'flags':0},{'first_triangle':2,'triangle_count':2,'flags':1}]
        result=invoke(native.STBuildNavigation,base,encode({'settings':settings,'tiles':[{**tile,'modifiers':[mask],'rasterization_groups':groups}]}),path)
        assert result['state']=='READY',result
        checks=[]
        for point,expected in (([200,200,0],shape!=1),([800,800,0],False),([200,200,300],True)):
            value=invoke(native.STNavigationQuery,path,encode({'mode':'nearest','start':point,'extent':[20,20,20]}))
            assert (value['state']=='READY')==expected,(shape,point,value);checks.append(value)
        evidence.append({'test':'fill_mask','shape':shape,'projections':checks})
    for group in ({'first_triangle':1,'triangle_count':3,'flags':1},
                  {'first_triangle':0,'triangle_count':5,'flags':1},
                  {'first_triangle':0,'triangle_count':4,'flags':4}):
        result=invoke(native.STBuildNavigation,base,encode({'settings':settings,'tiles':[{**tile,'rasterization_groups':[group]}]}),output/'invalid-groups.stn')
        assert result['state']=='UNKNOWN',result
    evidence.append({'test':'invalid_rasterization_groups_rejected','cases':3})
    # Full capacity must not silently grow the pool or publish a partial rebuild.
    small=bytearray(base.read_bytes());struct.pack_into('<i',small,48,2)
    small_base=output/'capacity2-empty.stn';small_base.write_bytes(small)
    full=output/'capacity2.stn'
    built=invoke(native.STBuildNavigation,small_base,encode({'settings':settings,'tiles':[tile]}),full)
    assert built['state']=='READY',built
    status=invoke(native.STNavigationQuery,full,encode({'mode':'status'}))
    assert status['tile_pool_full'] and status['tile_capacity']==2,status
    original=full.read_bytes();replacement=output/'capacity2-replaced.stn'
    rebuilt=invoke(native.STBuildNavigation,full,encode({'settings':settings,'tiles':[tile]}),replacement)
    assert rebuilt['state']=='READY',rebuilt
    extra={**tile,'x':1,'vertices':[[p[0]-1024,p[1],p[2]] for p in vertices]}
    unpublished=output/'capacity2-overflow.stn'
    rejected=invoke(native.STBuildNavigation,full,encode({'settings':settings,'tiles':[extra]}),unpublished)
    assert rejected['state']=='UNKNOWN' and 'tile pool exhausted' in rejected['error'],rejected
    assert not unpublished.exists() and full.read_bytes()==original
    evidence.append({'test':'full_pool_replacement_and_atomic_overflow','status':status,'overflow':rejected})
    # Replacing a full batch must not depend on tile order: one coordinate gains
    # a floor while another loses its floor, so final capacity is unchanged.
    one_floor={**tile,'vertices':vertices[:4],'triangles':triangles[:2]}
    adjacent={**one_floor,'x':1,'vertices':[[p[0]-1024,p[1],p[2]] for p in vertices[:4]]}
    two_coords=output/'capacity2-coordinates.stn'
    result=invoke(native.STBuildNavigation,small_base,encode({'settings':settings,'tiles':[one_floor,adjacent]}),two_coords)
    assert result['state']=='READY',result
    empty_adjacent={**adjacent,'vertices':[],'triangles':[]}
    batch=invoke(native.STBuildNavigation,two_coords,encode({'settings':settings,'tiles':[tile,empty_adjacent]}),output/'capacity2-batch.stn')
    assert batch['state']=='READY',batch
    reverse=invoke(native.STBuildNavigation,two_coords,encode({'settings':settings,'tiles':[empty_adjacent,tile]}),output/'capacity2-batch-reverse.stn')
    assert reverse['state']=='READY',reverse
    for path in (output/'capacity2-batch.stn',output/'capacity2-batch-reverse.stn'):
        for height in (0,300):
            query=invoke(native.STNavigationQuery,path,encode({'mode':'path','start':[200,200,height],'end':[800,800,height]}))
            assert query['state']=='READY' and query['reachable'],query
    evidence.append({'test':'full_pool_batch_capacity_is_order_independent','result':batch})
    for request in ({},{'settings':settings,'tiles':[{'x':0}]},
                    {'settings':settings,'tiles':[tile,tile]},
                    {'settings':settings,'tiles':[{**tile,'triangles':[[0,1,99999]]}]}):
        result=invoke(native.STBuildNavigation,base,encode(request),output/'invalid.stn')
        assert result['state']=='UNKNOWN',result
    # Content-addressed caches reuse a validated native mesh. Mutable filenames
    # stay uncached; changed/deleted files never return a prior successful path.
    payload=(output/'layers-2.stn').read_bytes()
    addressed=output/(hashlib.sha1(payload).hexdigest()+'.stn');addressed.write_bytes(payload)
    request=encode({'mode':'path','start':[200,200,0],'end':[800,800,0]})
    initial=invoke(native.STNavigationQuery,addressed,request)
    assert initial['reachable'] and not initial['cache_hit'],initial
    timings={}
    for label,path in (('mutable',output/'layers-2.stn'),('immutable',addressed)):
        started=time.perf_counter()
        for _ in range(32):
            result=invoke(native.STNavigationQuery,path,request)
            assert result['reachable'] and result['path']==initial['path'],result
            assert result['cache_hit']==(label=='immutable'),result
        timings[label]=time.perf_counter()-started
    # A malformed filter must fail even on a real, otherwise reachable NavMesh.
    result=invoke(native.STNavigationQuery,addressed,encode({'mode':'nearest','start':[200,200,0],'filter':[]}))
    assert result['state']=='UNKNOWN',result
    addressed.write_bytes(base.read_bytes())
    changed=invoke(native.STNavigationQuery,addressed,request);assert changed['state']=='UNKNOWN',changed
    addressed.unlink();missing=invoke(native.STNavigationQuery,addressed,request);assert missing['state']=='UNKNOWN',missing
    addressed.write_bytes(payload)
    restored=invoke(native.STNavigationQuery,addressed,request);assert restored['reachable'] and not restored['cache_hit'],restored
    evidence.append({'test':'immutable_navigation_cache_parity_invalidation','queries_per_arm':32,'seconds':timings,'changed':changed,'missing':missing,'restored':restored})
    # Two disconnected platforms: only the supplied link can connect them.
    platforms=[[x,y,0] for lo,hi in ((0,400),(624,1024)) for x,y in ((lo,0),(hi,0),(hi,1024),(lo,1024))]
    islands={**tile,'vertices':platforms,'triangles':triangles,'maximum_height':200}
    link={'start':[350,500,0],'end':[674,500,0],'radius':80,'height':40,
          'area_id':63,'flags':1,'bidirectional':True,'user_id':'0'}
    for label,updates in (('static',{}),('one_way',{'bidirectional':False}),
                          ('native_flags',{'bidirectional':False,'reversed':True,'snap_to_cheapest_area':True,'generated':True}),
                          ('runtime',{'user_id':'18446744073709551614'})):
        destination=output/('links-'+label+'.stn')
        result=invoke(native.STBuildNavigation,base,encode({'settings':settings,'link_area_order':list(range(64)),'tiles':[{**islands,'links':[{**link,**updates}]}]}),destination)
        assert result['state']=='READY',result
        result=invoke(native.STNavigationQuery,destination,encode({'mode':'path','link_area_order':list(range(64)),'start':[200,500,0],'end':[800,500,0]}))
        if label=='runtime':
            assert result['state']=='UNKNOWN' and not result['reachable'],result
            assert result['runtime_links_excluded']==['18446744073709551614'],result
        else:assert result['state']=='READY' and result['reachable']==(label!='native_flags'),result
        reverse=invoke(native.STNavigationQuery,destination,encode({'mode':'path','link_area_order':list(range(64)),'start':[800,500,0],'end':[200,500,0]}))
        assert reverse['reachable']==(label in ('static','native_flags')),reverse
        evidence.append({'test':'off_mesh_links','case':label,'forward':result,'reverse':reverse})
    # One link can snap to either of two disconnected landing areas. The native
    # cost order must change connectivity, including on a cached immutable file.
    platforms=[[x,y,0] for lo,hi in ((0,400),(624,760),(860,1024)) for x,y in ((lo,0),(hi,0),(hi,1024),(lo,1024))]
    tris=[[i,i+2,i+1] for i in (0,4,8)]+[[i,i+3,i+2] for i in (0,4,8)]
    ranked={**islands,'vertices':platforms,'triangles':tris,'links':[{**link,'end':[800,500,0],'radius':200,'bidirectional':False,'snap_to_cheapest_area':True}],
            'modifiers':[{'shape':2,'mode':0,'area_id':a,'bounds':[[lo,-10,-10],[hi,1034,20]]} for a,lo,hi in ((1,620,780),(2,850,1030))]}
    order=list(range(64));ranked_path=output/'ranked-links.stn'
    result=invoke(native.STBuildNavigation,base,encode({'settings':settings,'link_area_order':order,'tiles':[ranked]}),ranked_path)
    assert result['state']=='READY',result
    payload=ranked_path.read_bytes();ranked_hash=output/(hashlib.sha1(payload).hexdigest()+'.stn');ranked_hash.write_bytes(payload)
    query={'mode':'path','start':[200,500,0],'end':[920,500,0]}
    missing=invoke(native.STNavigationQuery,ranked_hash,encode(query));assert missing['state']=='UNKNOWN' and 'area order' in missing['error'],missing
    outcomes=[]
    for prefer_right in (False,True,False):
        rank=list(order)
        if prefer_right:rank[1],rank[2]=rank[2],rank[1]
        answer=invoke(native.STNavigationQuery,ranked_hash,encode({**query,'link_area_order':rank}))
        assert answer['state']=='READY' and answer['reachable']==prefer_right,answer
        outcomes.append(answer)
    assert outcomes[-1]['cache_hit'],outcomes
    evidence.append({'test':'native_link_cost_order_changes_cached_connectivity','outcomes':outcomes,'missing_order':missing})
    for bad in ([0]*64, list(range(63)), list(range(63))+['x']):
        answer=invoke(native.STNavigationQuery,ranked_hash,encode({**query,'link_area_order':bad}))
        assert answer['state']=='UNKNOWN' and 'Invalid' in answer['error'],answer
    result=invoke(native.STNavigationQuery,output/'missing.stn',encode({'mode':'path','start':[0,0,0],'end':[1,1,1]}))
    assert result['state']=='UNKNOWN',result
    for request in ({},{'mode':'path','start':['x',0,0]}, {'mode':'bogus','start':[0,0,0]},
                    {'mode':'nearest','start':[0,0,0],'filter':[]},
                    {'mode':'nearest','start':[0,0,0],'filter':{'costs':['x']}},
                    {'mode':'nearest','start':[0,0,0],'filter':{'include_flags':-1}}):
        result=invoke(native.STNavigationQuery,base,encode(request))
        assert result['state']=='UNKNOWN',result
    summary={'state':'PASS','editor_required':False,'native_library':str(library),'evidence':evidence}
    (output/'native-checks.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    return summary


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine',type=Path,required=True)
    parser.add_argument('--library',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    result=run(args.engine.resolve(),args.library.resolve(),args.output.resolve())
    print(encode({'state':result['state'],'checks':len(result['evidence']),'editor_required':False}))
