"""Import a validated authoring patch through official MCP, then revalidate native facts."""
import argparse
import asyncio
from contextlib import nullcontext
from datetime import timedelta
import itertools
import json
import math
from pathlib import Path
import sys
import time
from spatial_twin.store import Store,encode
from spatial_twin.shadow import validate
from spatial_twin.staging import get,digest_file
from spatial_twin.query import Queries
from spatial_twin import geometry as g
from apply_patch import require_target,TWIN

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'scripts'))
from unreal_mcp import ClientSession,streamablehttp_client,call_tool,UNREAL_MCP,error_message


def import_script(authored,bindings):
    """Import, assign existing materials and save once through official tools."""
    package,name=authored['path'].split('.')
    data={'path':authored['path'],'package':package,'folder':package.rsplit('/',1)[0],
          'name':name,'source':authored['source_file'],'materials':bindings}
    return 'import json\nimport time\nDATA='+repr(data)+'''
def call(t,n,a):
    return execute_tool(t+'.'+n,json.dumps(a))['returnValue']
def run():
    mesh='editor_toolset.toolsets.static_mesh.StaticMeshTools'
    assets='editor_toolset.toolsets.asset.AssetTools'
    imported=[]
    try:
        started=time.perf_counter()
        imported=call(mesh,'import_file',{'folder_path':DATA['folder'],'asset_name':DATA['name'],'source_file':DATA['source'],'import_materials':False,'import_textures':False,'combine_meshes':True})
        if imported!=[{'refPath':DATA['path']}]:raise ValueError('Unexpected imported assets')
        meshref={'refPath':DATA['path']}
        slots=call(mesh,'get_material_slots',{'mesh':meshref})
        if set(slots)!=set(DATA['materials']):raise ValueError('Material slot mismatch')
        confirmed={}
        for slot,path in DATA['materials'].items():
            if not call(mesh,'set_material',{'mesh':meshref,'slot_name':slot,'material':{'refPath':path}}):raise ValueError('Material assignment failed')
            value=call(mesh,'get_material',{'mesh':meshref,'slot_name':slot})
            if value!={'refPath':path}:raise ValueError('Material readback mismatch')
            confirmed[slot]=value
        if not call(assets,'save_assets',{'asset_paths':[DATA['package']]}):raise ValueError('Scoped asset save failed')
        return {'complete':True,'assets':imported,'materials':confirmed,'seconds':time.perf_counter()-started}
    except Exception as error:
        return {'complete':False,'assets':imported,'error':str(error)}
'''


def same_points(a,b,tolerance=.05):
    """Compare split/welded native vertex clouds using spatial buckets, not O(n²)."""
    def covered(source,target):
        grid={}
        for p in target:grid.setdefault(tuple(math.floor(v/tolerance) for v in p),[]).append(p)
        for p in source:
            key=tuple(math.floor(v/tolerance) for v in p)
            if not any(sum((p[i]-q[i])**2 for i in range(3))<=tolerance*tolerance
                       for offset in itertools.product((-1,0,1),repeat=3)
                       for q in grid.get(tuple(key[i]+offset[i] for i in range(3)),[])):return False
        return True
    return covered(a,b) and covered(b,a)


def same_surface(a,b,tolerance=.05):
    """Compare oriented planar area, allowing native hull retriangulation."""
    if not same_points(a.vertices,b.vertices,tolerance):return False
    def planes(mesh):
        result=[]
        for tri in mesh.triangles:
            p,q,r=(mesh.vertices[i] for i in tri);cross=g.cross(g.sub(q,p),g.sub(r,p));length=g.length(cross)
            if length<1e-12:continue
            normal=g.mul(cross,1/length);distance=g.dot(normal,p)
            row=next((v for v in result if g.dot(normal,v[0])>1-1e-8 and abs(distance-v[1])<=tolerance),None)
            if row is None:result.append([normal,distance,length*.5])
            else:row[2]+=length*.5
        return result
    expected=planes(a);actual=planes(b)
    if len(expected)!=len(actual):return False
    for normal,distance,area in expected:
        match=next((i for i,v in enumerate(actual) if g.dot(normal,v[0])>1-1e-8 and abs(distance-v[1])<=tolerance and abs(area-v[2])<=max(.0001,area*1e-4)),None)
        if match is None:return False
        actual.pop(match)
    return True


def verify_import(store,authored,canonical_id):
    with store.read() as db:
        q=Queries(store,db);native=q.entity(canonical_id)
        if native['kind']!='StaticMesh':raise ValueError('Imported asset has no native geometry export')
        if any(abs(a-b)>.05 for aa,bb in zip(authored['local_bounds'],native['local_bounds']) for a,b in zip(aa,bb)):
            raise ValueError('Native FBX bounds differ from authored coordinates; fix the export transform')
        am=g.load_mesh(q.geometry(authored['geometry_hash'])['path']);nm=g.load_mesh(q.geometry(native['geometry_hash'])['path'])
        if not same_points(am.vertices,nm.vertices):raise ValueError('Native mesh vertices differ from authored geometry')
        if native.get('trace_mode')!='UseSimpleAndComplex' or native.get('collision_shapes'):
            raise ValueError('Native collision mode/primitive conversion needs a matching authoring representation')
        candidates=[g.load_mesh(q.geometry(h)['path']) for h in native.get('collision_simple',[])]
        if len(candidates)!=len(authored['collision_simple']):raise ValueError('Native UCX body count differs')
        for digest in authored['collision_simple']:
            mesh=g.load_mesh(q.geometry(digest)['path'])
            index=next((i for i,c in enumerate(candidates) if same_points(mesh.vertices,c.vertices)),None)
            if index is None:raise ValueError('Native convex collision differs from authored body')
            candidates.pop(index)
        navigation='NOT_PREDICTED'
        if authored.get('navigation_input_coverage')=='authored_ucx_prediction_v1':
            if not native.get('navigation_geometry_hash') or native.get('native_spawn_navigation')!=authored.get('native_spawn_navigation'):
                raise ValueError('Native navigation source/defaults differ from authored prediction')
            if any(native.get(k)!=authored.get(k) for k in ('navigation_slope_behavior','navigation_slope_angle')):
                raise ValueError('Native navigation slope differs from authored prediction')
            predicted=g.load_mesh(q.geometry(authored['navigation_geometry_hash'])['path'])
            actual=g.load_mesh(q.geometry(native['navigation_geometry_hash'])['path'])
            if not same_surface(predicted,actual):raise ValueError('Native navigation surface differs from authored UCX prediction')
            navigation='NATIVE_SURFACE_VERIFIED'
        return {'canonical_asset_id':canonical_id,'native_revision':Store.revision(db),'mesh_vertex_parity_cm':.05,'navigation_prediction':navigation,
                'convex_bodies':len(authored['collision_simple']),'bounds':native['local_bounds']}


async def ensure_materials(store,bindings,session,receipt,save):
    """Resolve only absent bindings during live import, then trust canonical readback.

    The normal catalog includes project/plugin assets and referenced engine assets.
    An unused engine material may legitimately be absent after a first scan.
    """
    for path in sorted(set(bindings.values())):
        with store.read() as db:
            present=db.execute('SELECT 1 FROM entities WHERE id=?',('asset:'+path,)).fetchone() is not None
        if not present:
            item={'path':path,'state':'SENT'}
            receipt.setdefault('material_cache',[]).append(item);save()
            status=await call_tool(session,TWIN,'spatial_twin_cache_asset',{'object_path':path})
            status=json.loads(status) if isinstance(status,str) else status
            require_target(store,status)
            if status.get('error') or not status.get('ready'):raise ValueError('Material synchronization failed: '+str(status))
        with store.read() as db:
            material=Store.entity(db,'asset:'+path)
            if material.get('class','').rsplit('.',1)[-1] not in ('Material','MaterialInstance','MaterialInstanceConstant','MaterialInstanceDynamic'):
                raise ValueError('Binding is not a canonical material: '+path)
        if not present:item['state']='CANONICAL_VERIFIED';save()


async def materialize(store,patch_id,url,output,material_bindings=None,session=None):
    output=Path(output).resolve();output.parent.mkdir(parents=True,exist_ok=True)
    started=time.perf_counter();patch=validate(store,patch_id)
    if patch['status']!='VALIDATED':raise ValueError('Authoring placement must validate before import')
    staged=sorted({op['asset'] for op in patch['operations'] if op.get('asset','').startswith('staged:')})
    if not staged:raise ValueError('Patch contains no authored assets')
    receipt={'state':'IMPORTING','authoring_patch_id':patch_id,'imports':[],
             'timings':{'authoring_validation_seconds':time.perf_counter()-started}}
    def save():output.write_text(json.dumps(receipt,indent=2),encoding='utf-8')
    save();resolved={}
    try:
        if material_bindings is not None:
            if not isinstance(material_bindings,dict) or not 1<=len(material_bindings)<=64 or any(not isinstance(k,str) or not isinstance(v,str) for k,v in material_bindings.items()):raise ValueError('Supply1..64named material bindings')
        borrowed=session is not None
        async with (nullcontext((None,None,None)) if borrowed else streamablehttp_client(url)) as (read,write,_):
            async with (nullcontext(session) if borrowed else ClientSession(read,write,read_timeout_seconds=timedelta(seconds=120))) as session:
                if not borrowed:await session.initialize()
                require_target(store,await call_tool(session,TWIN,'spatial_twin_sync',{}))
                # Fail before importing if the native observation extension is unavailable.
                described=await session.call_tool('describe_toolset',{'toolset_name':'UnrealSpatialTwin.SpatialTwinToolset'})
                if described.isError:raise ValueError('Native Spatial Twin toolset description failed')
                schema=json.loads(''.join(c.text for c in described.content if c.type=='text'))
                if not any(t.get('name')=='UnrealSpatialTwin.SpatialTwinToolset.spatial_twin_cache_asset' for t in schema.get('tools',[])):
                    raise ValueError('Load the updated native Spatial Twin toolset before import')
                await ensure_materials(store,material_bindings or {},session,receipt,save)
                for ident in staged:
                    authored=get(store,ident)
                    if digest_file(authored['source_file'])!=authored['source_sha256']:raise ValueError('Staged FBX changed')
                    package,name=authored['path'].split('.');folder=package.rsplit('/',1)[0]
                    if await call_tool(session,'editor_toolset.toolsets.asset.AssetTools','exists',{'path':authored['path']}):
                        raise ValueError('Import target already exists; inspect prior receipts instead of creating a duplicate')
                    r={'staged_asset_id':ident,'target':authored['path'],'state':'SENT'};receipt['imports'].append(r);save()
                    phase=time.perf_counter()
                    if material_bindings is None:
                        result=await call_tool(session,'editor_toolset.toolsets.static_mesh.StaticMeshTools','import_file',
                                              {'folder_path':folder,'asset_name':name,'source_file':authored['source_file'],
                                               'import_materials':False,'import_textures':False,'combine_meshes':True})
                    else:
                        batch=await call_tool(session,'editor_toolset.toolsets.programmatic.ProgrammaticToolset','execute_tool_script',{'script':import_script(authored,material_bindings)})
                        batch=json.loads(batch) if isinstance(batch,str) else batch
                        r['native_bundle']=batch;save()
                        if not batch.get('complete'):raise ValueError('Import/material/save batch incomplete; inspect partial asset receipts: '+str(batch.get('error')))
                        result=batch['assets']
                    if not isinstance(result,list) or [e.get('refPath') for e in result]!=[authored['path']]:
                        raise ValueError('Import returned unexpected assets: '+str(result))
                    r.update(state='IMPORTED',result=result,import_seconds=time.perf_counter()-phase);save()
                    phase=time.perf_counter()
                    if material_bindings is None and not await call_tool(session,'editor_toolset.toolsets.asset.AssetTools','save_assets',{'asset_paths':[package]}):
                        raise ValueError('Imported asset save failed')
                    if material_bindings is None:r['save_seconds']=time.perf_counter()-phase
                    else:r['save_in_bundle']=True
                    phase=time.perf_counter()
                    status=await call_tool(session,'UnrealSpatialTwin.SpatialTwinToolset','spatial_twin_cache_asset',{'object_path':authored['path']})
                    r['synchronization_seconds']=time.perf_counter()-phase
                    status=json.loads(status) if isinstance(status,str) else status
                    if status.get('error') or not status.get('ready'):raise ValueError('Native asset synchronization failed: '+str(status))
                    phase=time.perf_counter();canonical='asset:'+authored['path'];r['parity']=verify_import(store,authored,canonical)
                    r['parity_seconds']=time.perf_counter()-phase
                    r['state']='CANONICAL_VERIFIED';resolved[ident]=canonical;save()
        operations=[{**op,**({'asset':resolved[op['asset']]} if op.get('asset') in resolved else {})} for op in patch['operations']]
        phase=time.perf_counter();live=store.patch_create(operations);live=validate(store,live['id'])
        receipt['timings'].update(native_validation_seconds=time.perf_counter()-phase,total_seconds=time.perf_counter()-started)
        receipt.update(state='VALIDATED' if live['status']=='VALIDATED' else 'INVALID',canonical_patch_id=live['id'],
                       native_revision=live['base_revision'],validation_errors=live['validation'].get('errors',[]));save()
        if live['status']!='VALIDATED':raise ValueError('Imported native facts invalidate the proposed placement; inspect receipt')
        return receipt
    except BaseException as error:
        receipt.update(state='FAILED',error=error_message(error));save();raise


async def workflow(args):
    store=Store(args.root);bindings=json.loads(args.materials.read_text(encoding='utf-8')) if args.materials else None
    if not args.apply:return await materialize(store,args.patch,args.url,args.output,bindings)
    from apply_patch import execute
    from render import render,check_camera
    camera=json.loads(args.camera.read_text(encoding='utf-8')) if args.camera else None
    if camera is not None:check_camera(camera)
    async with streamablehttp_client(args.url) as (read,write,_):
        async with ClientSession(read,write,read_timeout_seconds=timedelta(seconds=120)) as session:
            await session.initialize()
            result=await materialize(store,args.patch,args.url,args.output,bindings,session)
            try:
                applied=await execute(store,result['canonical_patch_id'],args.url,120,True,session)
                result.update(state=applied['status'],canonical_revision=applied['receipts'][-1]['canonical_revision'],
                              saved=applied['receipts'][-1]['saved'],canonical_verified=applied['status']=='APPLIED')
                if args.camera:
                    capture=await render(store,applied['id'],camera,Path(args.output).with_suffix('.render.json'),args.url,session)
                    result['image']=capture['capture']['image_path'];result['render_state']=capture['state']
            except BaseException as error:
                result.update(state='FAILED',application_error=error_message(error));raise
            finally:Path(args.output).write_text(json.dumps(result,indent=2),encoding='utf-8')
            return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--patch',required=True);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--url',default=UNREAL_MCP)
    parser.add_argument('--materials',type=Path,help='Existing canonical material paths keyed by authored slot')
    parser.add_argument('--apply',action='store_true',help='Execute, confirm and scope-save through the same official MCP session')
    parser.add_argument('--camera',type=Path,help='With --apply, capture the real Unreal result')
    args=parser.parse_args()
    if args.camera and not args.apply:parser.error('--camera requires --apply')
    result=asyncio.run(workflow(args))
    print(encode({k:result[k] for k in ('state','canonical_patch_id','canonical_revision','saved','canonical_verified','image','render_state') if k in result}))
