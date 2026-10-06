"""Test-only open movement delivery; source discovery stays with the agent.

Reuses the existing equal-output native/Shadow verifier, never supplies target
poses or candidates to the agent. Only disposable fixture objects are writable.
"""
import argparse,json,time
from pathlib import Path
from typing import Literal
from typing_extensions import TypedDict,NotRequired
from mcp.server.fastmcp import Context
from production_benchmark_guard import server as production_server
from spatial_twin.store import check_operations
from spatial_twin.contracts import Vector3
from construction_benchmark import ACTOR,OBJECT,PROGRAMMATIC,program
import apply_patch


class MoveOperation(TypedDict):
    type:Literal['MOVE_ACTOR']
    target:str
    value:Vector3
    operation_id:NotRequired[str]


def server(spec,route,name):
    spec=Path(spec).resolve();base=Path(json.loads(spec.read_text())['project']).parent
    app=production_server(spec,route,'batch',name,defer_native=True)
    original={n:app._tool_manager.get_tool(n).fn for n in ('inspect_task','observe','complete_plan')}

    async def deliver_moves_live(operations:list[MoveOperation],base_revision:int|None,ctx:Context,patch_id:str|None=None)->dict:
        """Once-only source-derived MOVE_ACTOR plan. Native/Shadow checks,
        official execution, Canonical confirmation and capture; no map save.
        value is a three-number position [x,y,z] in cm, never an {x,y,z} object.
        With Twin requires the source revision; without Twin requires null.
        """
        check_operations(operations)
        if any(not {'type','target','value'}<=set(op) or set(op)-{'type','target','value','operation_id'} or op['type']!='MOVE_ACTOR' for op in operations):
            raise ValueError('Only exact source-selected MOVE_ACTOR target/value fields')
        s=ctx.request_context.lifespan_context
        if s['stage']!='CONNECTED':raise ValueError('Existing effects: inspect journal, never replay')
        await original['inspect_task'](ctx)
        await original['observe'](ctx) # Independent current source/support checks; not returned as an answer.
        targets=[e['id'] if route=='with' else e['path'] for e in s['targets']]
        if [op['target'] for op in operations]!=targets:raise ValueError('Incomplete/incorrect source target identities/order')
        if route=='with':
            if base_revision!=s['store'].status(False)['canonical_revision']:raise ValueError('Source revision conflict')
        elif base_revision is not None:raise ValueError('No Twin revision on native-only route')
        if patch_id:
            if route!='with' or s['store'].patch_get(patch_id)['operations']!=operations:raise ValueError('Prepared operation identities differ')
        elif any('operation_id' in op for op in operations):raise ValueError('Operation IDs require their prepared Twin patch')
        async def nav_defaults():
            body="    return [call("+repr(OBJECT)+",'get_properties',{'instance':call("+repr(ACTOR)+",'get_root_component',{'actor':{'refPath':a}}),'properties':['bCanEverAffectNavigation']}) for a in DATA]\n"
            started=time.perf_counter()
            value=await apply_patch.call_tool(s['native'],PROGRAMMATIC,'execute_tool_script',{'script':program(body,[e['path'] for e in s['targets']])})
            s['rpc'].append({'kind':'Unreal','tool':'native_navigation_defaults','seconds':time.perf_counter()-started})
            rows=(json.loads(value) if isinstance(value,str) else value)['value']
            flags=[(json.loads(row) if isinstance(row,str) else row)['bCanEverAffectNavigation'] for row in rows]
            if len(flags)!=len(targets) or not all(type(v) is bool for v in flags):raise ValueError('Incomplete navigation-default readback')
            return flags
        before=await nav_defaults()
        result_path=base/'runs'/name/'result.json'
        try:
            result=await original['complete_plan']([e['index'] for e in s['support']['accepted']],
                                                  [op['value'] for op in operations],ctx,prepared_patch_id=patch_id)
            after=await nav_defaults()
            if after!=before:raise ValueError('Native navigation defaults changed')
            receipt=json.loads(result_path.read_text());receipt['native_navigation_defaults']={'before':before,'after':after}
            receipt['rpc']=s['rpc'];result_path.write_text(json.dumps(receipt,indent=2)+'\n')
        except BaseException:
            if result_path.exists():
                receipt=json.loads(result_path.read_text());receipt['state']='FAILED_POSTCHECK';result_path.write_text(json.dumps(receipt,indent=2)+'\n')
            raise
        return result

    @app.tool()
    async def deliver_moves(operations:list[MoveOperation],base_revision:int|None,ctx:Context,patch_id:str|None=None)->dict:
        """Once-only source-derived MOVE_ACTOR plan; value is [x,y,z] in cm.
        With Twin requires source revision; native-only requires null.
        Waits for owned native readiness, then runs every native/Shadow/default
        check, official action, Canonical confirmation and capture. No map save.
        """
        from benchmark_native import connect
        s=ctx.request_context.lifespan_context
        if s['stage']!='CONNECTED':raise ValueError('Existing effects: inspect journal, never replay')
        async with connect(json.loads(spec.read_text()),route,name) as native:
            s['native']=native
            try:
                return await deliver_moves_live(operations,base_revision,ctx,patch_id)
            finally:
                s['native']=None

    # The agent cannot request the guard's observations or prepared solution.
    for tool_name in tuple(app._tool_manager._tools):
        if tool_name!='deliver_moves':app.remove_tool(tool_name)
    return app


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--spec',type=Path,required=True);p.add_argument('--route',choices=['with','without'],required=True)
    p.add_argument('--name',required=True);a=p.parse_args()
    server(a.spec,a.route,a.name).run(transport='stdio')
