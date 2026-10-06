"""Explicit placement action: existing Twin validation + official execution/render.

No live action without --apply. Selection is supplied by the agent; this command
does not infer semantic targets. Failed task constraints stop before Unreal writes.
"""
import argparse
import asyncio
import inspect
import json
import math
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from spatial_twin.server import create_server,patch_summary
from spatial_twin.store import Store,encode
from apply_patch import workflow,UNREAL_MCP
from render import check_camera


def check_placement(result, *, allow_rejections=False, max_tilt_degrees=.1):
    if type(allow_rejections) is not bool:
        raise ValueError('allow_rejections must be boolean')
    if type(max_tilt_degrees) not in (int,float) or not math.isfinite(max_tilt_degrees) or not 0<=max_tilt_degrees<90:
        raise ValueError('max_tilt_degrees must be finite in [0,90)')
    validation=result.get('validation') or {}
    if result.get('status')!='VALIDATED' or validation.get('valid') is not True:
        raise ValueError('Placement is not VALIDATED; inspect its patch diagnostics')
    if validation.get('errors_count')!=0 or validation.get('new_collisions_count')!=0:
        raise ValueError('Placement has errors or new collisions')
    accepted=result.get('accepted') or []
    if not accepted or len(accepted)!=result.get('operation_count'):
        raise ValueError('Missing/inconsistent accepted placement operations')
    if result.get('rejected') and not allow_rejections:
        raise ValueError('Some selected candidates were rejected; no partial application authorized')
    threshold=math.cos(math.radians(max_tilt_degrees))
    for candidate in accepted:
        proof=candidate.get('continuous_support') or {}
        normal=proof.get('normal')
        if proof.get('state')!='PROVEN' or not isinstance(normal,list) or len(normal)!=3:
            raise ValueError('Continuous footprint support is not PROVEN')
        if any(type(v) not in (int,float) or not math.isfinite(v) for v in normal) or abs(sum(v*v for v in normal)-1)>1e-6:
            raise ValueError('Invalid support normal')
        if normal[2]+1e-12<threshold:
            raise ValueError('Support exceeds the authorized tilt')
    return result


def prepare_placement(store,arguments,**constraints):
    if not isinstance(arguments,dict):raise ValueError('Placement arguments must be an object')
    tool=create_server(store)._tool_manager.get_tool('patch_place')
    values={**arguments,'include_operations':True}
    inspect.signature(tool.fn).bind(**values)
    tool.fn_metadata.arg_model.model_validate(values,strict=True)
    result=tool.fn(**values)
    try:check_placement(result,**constraints)
    except ValueError as error:
        error.placement=result
        raise
    return result


def write_receipt(path,value):
    fd,name=tempfile.mkstemp(prefix=path.name+'.',suffix='.tmp',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as stream:
            stream.write((encode(value)+'\n').encode());stream.flush();os.fsync(stream.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)


async def run(args):
    output=args.output.resolve()
    arguments=json.loads(args.arguments.read_text(encoding='utf-8'))
    camera=json.loads(args.camera.read_text(encoding='utf-8')) if args.camera else None
    if camera is not None:check_camera(camera)
    if args.camera and not args.apply:raise ValueError('Render requires explicit --apply')
    staged=isinstance(arguments,dict) and (str(arguments.get('asset_id','')).startswith('staged:') or arguments.get('manifest_file') is not None)
    if staged and args.apply and args.no_save:raise ValueError('Staged import uses scope-save; --no-save is unsupported for staged assets')
    secondary=output.with_suffix('.materialize.json') if staged else output.with_suffix('.render.json')
    if secondary.exists() or secondary.with_suffix('.png').exists():raise FileExistsError('Related receipt/image already exists')
    with output.open('x',encoding='utf-8') as stream:stream.write('{"state":"PREPARING"}\n')
    receipt={'state':'PREPARING','arguments':str(args.arguments.resolve()),'apply_requested':args.apply}
    store=Store(args.root)
    try:
        result=prepare_placement(store,arguments,allow_rejections=args.allow_rejections,max_tilt_degrees=args.max_tilt_degrees)
        receipt.update(state='VALIDATED',patch_id=result['id'],placement=result)
        write_receipt(output,receipt) # Durable identity before any official action.
        if args.apply:
            receipt['state']='APPLYING';write_receipt(output,receipt)
            if staged:
                from materialize import workflow as materialize_workflow
                applied=await materialize_workflow(SimpleNamespace(root=args.root,patch=result['id'],url=args.url,
                    output=secondary,materials=args.materials,apply=True,camera=args.camera))
                receipt.update(state=applied['state'],result=applied)
            else:
                applied=await workflow(store,result['id'],args.url,args.timeout,not args.no_save,camera,
                                       secondary if camera is not None else None)
                receipt.update(state=applied['status'],result={**patch_summary(applied),
                    **({'render':applied['render']} if 'render' in applied else {})})
            write_receipt(output,receipt)
        return {k:v for k,v in receipt.items() if k!='placement'}
    except BaseException as error:
        if hasattr(error,'placement'):
            receipt.update(patch_id=error.placement.get('id'),placement=error.placement)
        receipt.update(state='FAILED',error=str(error));write_receipt(output,receipt);raise


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('root','arguments','output'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--apply',action='store_true');p.add_argument('--no-save',action='store_true')
    p.add_argument('--allow-rejections',action='store_true')
    p.add_argument('--max-tilt-degrees',type=float,default=.1)
    p.add_argument('--camera',type=Path);p.add_argument('--materials',type=Path)
    p.add_argument('--url',default=UNREAL_MCP);p.add_argument('--timeout',type=float,default=120)
    args=p.parse_args()
    if not math.isfinite(args.timeout) or args.timeout<=0:p.error('timeout must be positive')
    try:print(encode(asyncio.run(run(args))));return 0
    except Exception as error:print(str(error),file=sys.stderr);return 1


if __name__=='__main__':sys.exit(main())
