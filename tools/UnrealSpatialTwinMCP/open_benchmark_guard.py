"""Test-only delivery guard for open perception/placement benchmarks.

It supplies no candidate positions. Agents use the actual product Twin or the
official Unreal MCP to discover/plan. Only delivery and identical native checks
are guarded here. All edits belong to the disposable SpatialTwinBench project.
"""
import argparse
from contextlib import asynccontextmanager
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import sys
import time

from mcp.server.fastmcp import Context, FastMCP
from construction_benchmark import (ClientSession, streamablehttp_client, program,
    PROGRAMMATIC, ACTOR, SCENE, OBJECT, EDITOR, MESH, ASSET)
from apply_patch import call_tool, execute, create_script, xform
from spatial_twin.store import Store, check_operations
from spatial_twin.contracts import PatchOperation
from unreal_mcp import save_capture,capture_viewport
from mcp import StdioServerParameters
from mcp.client.stdio import stdio_client


def server(spec_path, route, name, scenario='reuse'):
    spec_path = Path(spec_path).resolve()
    spec = json.loads(spec_path.read_text())
    base = Path(spec['project']).resolve().parent
    if (base != spec_path.parent or Path(spec['project']).stem != 'SpatialTwinBench'
            or route not in ('with', 'without') or not name.isalnum() or len(name) > 24
            or spec['map'] != '/Game/Benchmark/Base' or scenario not in ('reuse','author')):
        raise ValueError('Owned disposable project and unique run name required')
    folder = base / 'runs' / name
    journal = folder / 'journal.json'
    target=f'/Game/BenchRuns/SM_ST_Rail_{name}.SM_ST_Rail_{name}' if scenario=='author' else '/Engine/BasicShapes/Cube.Cube'
    local_bounds=[[-76,-9,0],[76,9,100]] if scenario=='author' else [[-50,-50,-50],[50,50,50]]

    def record(state):
        journal.write_text(json.dumps(state, indent=2) + '\n')

    @asynccontextmanager
    async def lifespan(app):
        if journal.exists():
            raise ValueError('Existing journal: inspect instead of replaying')
        state = dict(stage='CONNECTED', route=route, scenario=scenario,
                     name=name, created=[], rpc=[])
        record(state)
        yield {'native': None, 'state': state}

    app = FastMCP('open-production-delivery', lifespan=lifespan)

    @app.tool()
    async def author_asset(ctx:Context)->dict:
        """Author the specified editable Blender rail and six UCX bodies in the isolated technical scene. Returns actual source files/bounds, never world candidates. Both routes use the same authoring procedure."""
        state=ctx.request_context.lifespan_context['state']
        if scenario!='author' or state['stage']!='CONNECTED' or state.get('source_hashes'):raise ValueError('Not a fresh authoring task')
        from construction_benchmark import data
        from agent_benchmark_guard import source_hashes
        root=Path(__file__).resolve().parents[2]
        if any(folder.glob('SM_ST_Rail_'+name+'.*')):raise ValueError('Owned source already exists; do not overwrite')
        command=root/'Build/MCP/venv/Scripts/mcp-for-blender.exe'
        code="import importlib.util\ns=importlib.util.spec_from_file_location('rail_builder',"+repr(str(root/'art/SpatialTwinBenchmark/build_recovered_rail.py'))+")\nm=importlib.util.module_from_spec(s);s.loader.exec_module(m)\nprint(m.build("+repr('SM_ST_Rail_'+name)+','+repr(str(folder))+','+repr(target)+'))'
        async with stdio_client(StdioServerParameters(command=str(command),args=[],env={**os.environ,'DISABLE_TELEMETRY':'true'})) as (r,w):
            async with ClientSession(r,w,read_timeout_seconds=timedelta(seconds=120)) as blender:
                await blender.initialize()
                authored=data(await blender.call_tool('execute_blender_code',{'code':code,'user_prompt':'Author the user-authorized technical rail in the isolated benchmark scene.'}))
                if 'Error' in str(authored):raise ValueError('Blender authoring failed')
        state['source_hashes']=source_hashes(folder,name,target);record(state)
        from spatial_twin import geometry
        manifest=json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
        observed_bounds=geometry.load_mesh(str(folder/manifest['geometry_file'])).root[0]
        if any(abs(a-b)>.001 for aa,bb in zip(observed_bounds,local_bounds) for a,b in zip(aa,bb)):raise ValueError('Builder produced a different design')
        return dict(state='AUTHORED',manifest_file=str(folder/'manifest.json'),source=str(folder/('SM_ST_Rail_'+name+'.blend')),asset_path=target,local_bounds_cm=observed_bounds,convex_bodies=len(manifest['collision_files']))

    async def deliver_live(operations: list[PatchOperation], patch_id: str, ctx: Context) -> dict:
        """Deliver this agent's plan once; native check, official apply, readback,
        real render. With Twin requires its independently VALIDATED patch ID;
        without Twin uses empty ID. Keep navigation defaults. No map save.
        Returns state, actor_count, render_path, receipt, canonical_confirmed,
        map_saved. VERIFIED requires all native checks; view render_path within
        this same Code Mode execution. Do not reopen its receipt for known keys.
        """
        live = ctx.request_context.lifespan_context
        native, state = live['native'], live['state']
        if state['stage'] != 'CONNECTED':
            raise ValueError('Delivery already attempted; inspect journal')
        state['stage'] = 'CHECKING'
        record(state)
        operations = check_operations(operations)
        store = Store(base / 'Saved/SpatialTwin') if route == 'with' else None
        if scenario=='author':
            from agent_benchmark_guard import source_hashes
            if not state.get('source_hashes') or source_hashes(folder,name,target)!=state['source_hashes']:raise ValueError('Missing or changed authored source')
            if store:
                from spatial_twin.staging import get
                for op in operations:
                    staged=get(store,op.get('asset',''))
                    if staged['path']!=target or staged['source_sha256']!=hashlib.sha256((folder/('SM_ST_Rail_'+name+'.fbx')).read_bytes()).hexdigest():raise ValueError('Different staged authoring source')
        prefix = f'BenchRun_{name}_'
        for i, op in enumerate(operations):
            if (op['type'] != 'CREATE_ACTOR' or op['class'] != '/Script/Engine.StaticMeshActor'
                    or (scenario=='reuse' and op['asset'] != 'asset:'+target)
                    or (scenario=='author' and not store and op['asset']!='asset:'+target)
                    or not op['label'].startswith(prefix)
                    or op['transform']['rotation'] != [0, 0, 0, 1]
                    or any(abs(v-1)>1e-6 for v in op['transform']['scale'])
                    or op.get('component_properties') or op.get('properties')):
                raise ValueError('Proposal outside this task or changed navigation defaults')
        if len({op['target'] for op in operations}) != len(operations):
            raise ValueError('Duplicate target identity')
        if len({op['label'] for op in operations}) != len(operations):
            raise ValueError('Duplicate actor label')

        async def invoke(toolset, tool, args):
            started = time.perf_counter()
            value = await call_tool(native, toolset, tool, args)
            state['rpc'].append(dict(tool=tool, seconds=time.perf_counter() - started))
            return value

        async def batch(body, values):
            value = await invoke(PROGRAMMATIC, 'execute_tool_script',
                                 {'script': program(body, values)})
            return (json.loads(value) if isinstance(value, str) else value)['value']

        # Independent verification from actual native source, never from an
        # answer table or the submitted positions. Same checks on both routes.
        refs = await invoke(SCENE, 'find_actors', dict(name='Bench_'+scenario+'_Support_',
                                                    tag='', collision_channels=[]))
        rows = await batch("    return [{'label':call(" + repr(ACTOR) + ", 'get_label',{'actor':a}),'pose':call(" + repr(ACTOR) + ", 'get_actor_transform',{'actor':a}),'mesh':call(" + repr(OBJECT) + ", 'get_properties',{'instance':call(" + repr(ACTOR) + ", 'get_root_component',{'actor':a}),'properties':['StaticMesh']})} for a in DATA]\n", refs)
        if len(rows) != 12:
            raise ValueError('Changed benchmark source')
        expected = []
        for row in sorted(rows, key=lambda r: r['label']):
            pose = row['pose']
            if json.loads(row['mesh'])['StaticMesh']['refPath'] != '/Engine/BasicShapes/Cube.Cube':
                raise ValueError('Changed support asset')
            if any(abs(v) > .001 for v in pose['rotation'].values()):
                continue
            if pose['scale']['x'] * 100 < local_bounds[1][0]-local_bounds[0][0] or pose['scale']['y'] * 100 < local_bounds[1][1]-local_bounds[0][1]:
                continue
            expected.append([pose['location']['x'], pose['location']['y'],
                             pose['location']['z'] + 50 * pose['scale']['z'] - local_bounds[0][2] + .2])
        poses = [op['transform']['position'] for op in operations]
        if poses != sorted(poses):
            raise ValueError('Use ascending XYZ order for deterministic identities')
        if len(poses) != len(expected) or any(abs(a-b) > .001 for p,q in zip(sorted(poses), sorted(expected)) for a,b in zip(p,q)):
            raise ValueError('Incomplete or incorrect placement')
        if await invoke(SCENE, 'find_actors', dict(name=prefix, tag='', collision_channels=[])):
            raise ValueError('Owned labels already exist')
        bounds = [dict(min=dict(zip(('x','y','z'), [p[i]+local_bounds[0][i]+.1 for i in range(3)])),
                       max=dict(zip(('x','y','z'), [p[i]+local_bounds[1][i]-.1 for i in range(3)])), isValid=True) for p in poses]
        overlap_body = "    return [call(" + repr(SCENE) + ", 'find_actors',{'name':'','tag':'','bounds':b,'collision_channels':['ObjectTypeQuery1','ObjectTypeQuery2']}) for b in DATA]\n"
        if any(await batch(overlap_body, bounds)):
            raise ValueError('Native proposed collision volume occupied')
        # Actual physics supports all nine footprint probes. Quality check,
        # not information supplied to the agent or a precomputed solution.
        xs=(local_bounds[0][0],0,local_bounds[1][0]);ys=(local_bounds[0][1],0,local_bounds[1][1])
        probe_body = "    return [[call(" + repr(SCENE) + ", 'trace_world',{'start':{'x':p[0]+x,'y':p[1]+y,'z':p[2]+100},'end':{'x':p[0]+x,'y':p[1]+y,'z':p[2]-150}}) for x in "+repr(xs)+" for y in "+repr(ys)+"] for p in DATA]\n"
        support = await batch(probe_body, poses)
        if any(d is None or abs(d - (100.2-local_bounds[0][2])) > .01 for distances in support for d in distances):
            raise ValueError('Native footprint support differs')
        if store:
            patch = store.patch_get(patch_id)
            if patch['status'] != 'VALIDATED' or patch['operations'] != operations:
                raise ValueError('Patch not validated or submitted plan differs')
        elif patch_id:
            raise ValueError('Without-Twin route must not read/use a Twin patch')
        state.update(stage='APPLYING', operations=operations, patch_id=patch_id,
                     positions_cm=poses)
        record(state)
        if scenario=='author':
            import materialize
            materials={key:'/Engine/BasicShapes/BasicShapeMaterial.BasicShapeMaterial' for key in ('RecoveredMetal','SafetyAmber')}
            if store:
                imported=await materialize.materialize(store,patch_id,spec['url'],folder/'import.json',materials,native)
                state['authoring_patch_id']=patch_id;patch_id=imported['canonical_patch_id'];state['patch_id']=patch_id
                operations=store.patch_get(patch_id)['operations'];record(state)
            else:
                imported=await invoke(PROGRAMMATIC,'execute_tool_script',{'script':materialize.import_script(dict(path=target,source_file=str(folder/('SM_ST_Rail_'+name+'.fbx'))),materials)})
                imported=json.loads(imported) if isinstance(imported,str) else imported
                state['import']=imported;record(state)
                if not imported.get('complete'):raise ValueError('Import incomplete; inspect without replay')
            from author_benchmark_parity import verify_native_asset
            state['native_authoring_parity']=await verify_native_asset(native,base,folder,name,target);record(state)
        if store:
            applied = await execute(store, patch_id, spec['url'], 180, False, native)
            if applied['status'] != 'APPLIED':
                raise ValueError('Not canonically confirmed')
            ids = next(x['actor_ids'] for x in reversed(applied['receipts']) if x['state'] == 'CANONICAL_VERIFIED')
            with store.read() as db:
                created = [dict(refPath=Store.entity(db, ids[op['target']])['path']) for op in operations]
        else:
            entries = [dict(target=op['target'], operation_id=str(i), tool='add_to_scene_from_asset',
                            arguments=dict(asset_path=target, name=op['label'],
                                           xform=xform(op['transform']), parent=None, snap_to_ground=False), properties={})
                       for i, op in enumerate(operations)]
            value = await invoke(PROGRAMMATIC, 'execute_tool_script', {'script': create_script(entries)})
            value = json.loads(value) if isinstance(value, str) else value
            state['creation_ack'] = value
            record(state)
            if not value.get('complete'):
                raise ValueError('Partial creation: inspect before retry')
            created = [v['actor'] for v in value['created']]
        state['created'] = created
        record(state)
        native_rows = await batch("    return [{'label':call(" + repr(ACTOR) + ", 'get_label',{'actor':a}),'transform':call(" + repr(ACTOR) + ", 'get_actor_transform',{'actor':a}),'properties':call(" + repr(OBJECT) + ", 'get_properties',{'instance':call(" + repr(ACTOR) + ", 'get_root_component',{'actor':a}),'properties':['StaticMesh','bCanEverAffectNavigation']})} for a in DATA]\n", created)
        if len(native_rows) != len(poses):
            raise ValueError('Incomplete native readback')
        for op, row in zip(operations, native_rows):
            props = json.loads(row['properties'])
            if (row['label'] != op['label'] or props['StaticMesh']['refPath'] != target
                    or props['bCanEverAffectNavigation'] is not True
                    or any(abs(row['transform']['location'][k] - v) > .001 for k,v in zip(('x','y','z'), op['transform']['position']))
                    or any(abs(row['transform']['scale'][k] - 1) > .001 for k in ('x','y','z'))
                    or any(abs(v) > .001 for v in row['transform']['rotation'].values())):
                raise ValueError('Native pose/asset/defaults differ')
        overlaps = await batch(overlap_body, bounds)
        if any(any(v['refPath'] != own['refPath'] for v in hits) for hits, own in zip(overlaps, created)):
            raise ValueError('Native final collision')
        capture = save_capture(await capture_viewport(invoke,spec['cameras'][scenario]),folder/'render.png')
        indexes = [next(i for i in range(12) if abs(p[0] - i*210) < .001) for p in poses]
        result = dict(state='VERIFIED', route=route, scenario=scenario, positions_cm=poses,
                      candidate_indexes=indexes, rejected=[], actor_refs=created,
                      expected_labels=[op['label'] for op in operations],
                      asset_path=target, patch_id=patch_id,authoring_patch_id=state.get('authoring_patch_id'),
                      native_authoring_parity=state.get('native_authoring_parity'),source_hashes=state.get('source_hashes'),
                      native_rows=native_rows, native_overlaps=overlaps,
                      capture=capture,
                      native_support=support, render_path=capture['image_path'], rpc=state['rpc'], saved_map=False)
        (folder/'result.json').write_text(json.dumps(result, indent=2)+'\n')
        state['stage'] = 'VERIFIED'
        record(state)
        return dict(state='VERIFIED', actor_count=len(created), render_path=capture['image_path'],
                    receipt=str(folder/'result.json'), canonical_confirmed=bool(store), map_saved=False)

    @app.tool()
    async def deliver(operations: list[PatchOperation], patch_id: str, ctx: Context) -> dict:
        """Once-only reviewed delivery. Returns state, actor_count, render_path,
        receipt, canonical_confirmed, map_saved; view render_path after VERIFIED.
        Offline preparation is allowed; action waits for owned native readiness.
        """
        from benchmark_native import connect
        live = ctx.request_context.lifespan_context
        if live['state']['stage'] != 'CONNECTED':
            raise ValueError('Delivery already attempted; inspect journal')
        async with connect(spec, route, name) as native:
            if await call_tool(native, SCENE, 'get_current_level', {}) != spec['map']:
                raise ValueError('Wrong live map')
            if await call_tool(native, EDITOR, 'IsPIERunning', {}):
                raise ValueError('Fixture PIE must be stopped')
            catalog = await native.call_tool('list_toolsets', {})
            present = 'UnrealSpatialTwin.SpatialTwinToolset' in '\n'.join(
                c.text for c in catalog.content if c.type == 'text')
            if catalog.isError or present != (route == 'with'):
                raise ValueError('Wrong native plugin state')
            if scenario == 'author' and await call_tool(native, ASSET, 'exists', {'path': target}):
                raise ValueError('Owned target already exists; do not overwrite')
            live['native'] = native
            try:
                return await deliver_live(operations, patch_id, ctx)
            finally:
                live['native'] = None

    @app.tool()
    async def place_and_deliver(arguments:dict,ctx:Context)->dict:
        """Twin route only: the agent supplies source-derived patch_place inputs.
        Request envelope is {arguments: <actual patch_place parameters>}.
        Never put candidates, asset_id or base_revision at the request root.
        Uses the product placement task checks, then the identical native delivery
        checks/render. No candidate table or selection is supplied. Does not replay.
        Returns the same exact delivery keys: state, actor_count, render_path,
        receipt, canonical_confirmed, map_saved. View render_path after VERIFIED.
        """
        if route!='with':raise ValueError('This arm must use official perception and deliver its own operations')
        from place import prepare_placement
        result=prepare_placement(Store(base/'Saved/SpatialTwin'),arguments)
        return await deliver(result['operations'],result['id'],ctx)

    return app


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--spec', required=True, type=Path)
    p.add_argument('--route', required=True, choices=['with','without'])
    p.add_argument('--name', required=True)
    p.add_argument('--scenario',default='reuse',choices=['reuse','author'])
    a = p.parse_args()
    server(a.spec, a.route, a.name,a.scenario).run(transport='stdio')
