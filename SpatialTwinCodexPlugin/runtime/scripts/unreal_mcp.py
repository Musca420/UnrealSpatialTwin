"""Thin CLI for the installed UE 5.8 MCP; no new server or game input injection."""
import argparse
import asyncio
import base64
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

# Keep transport errors/warnings; hundreds of HTTP 200 lines are not task evidence.
logging.getLogger('httpx').setLevel(logging.WARNING)

UNREAL_MCP = 'http://127.0.0.1:8000/mcp'
ROOT = Path(__file__).resolve().parents[1]
EDITOR = 'EditorToolset.EditorAppToolset'
PROGRAMMATIC = 'editor_toolset.toolsets.programmatic.ProgrammaticToolset'


def unpack(result):
    text = '\n'.join(c.text for c in result.content if c.type == 'text')
    if result.isError:
        raise RuntimeError(text)
    value = json.loads(text)
    if not isinstance(value, dict) or 'returnValue' not in value:
        raise RuntimeError('Missing returnValue in Unreal response')
    return value['returnValue']


def error_message(error):
    if isinstance(error, BaseExceptionGroup):
        return '; '.join(error_message(e) for e in error.exceptions)
    return str(error)


async def call_tool(session, toolset, name, arguments):
    return unpack(await session.call_tool('call_tool', {
        'toolset_name': toolset, 'tool_name': name, 'arguments': arguments}))


def capture_png(capture):
    frame = capture.get('image', capture)
    if frame.get('mimeType') != 'image/png' or not frame.get('data'):
        raise RuntimeError('Unreal returned no PNG capture')
    pixels = base64.b64decode(frame['data'], validate=True)
    if not pixels.startswith(b'\x89PNG\r\n\x1a\n'):
        raise RuntimeError('Invalid PNG capture')
    return pixels


async def capture_viewport(invoke, camera=None, annotations=None, show_ui=False):
    """Capture once through Unreal's official tool and validate its PNG envelope."""
    args={'captureTransform':camera,'annotations':annotations,'bShowUI':show_ui}
    result=await invoke(EDITOR,'CaptureViewport',args)
    capture_png(result)
    return result


def save_capture(capture, path):
    """Persist pixels and keep metadata, never print base64 in agent logs."""
    pixels=capture_png(capture)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pixels)
    return {k: v for k, v in capture.items() if k not in ('image', 'data')} | {
        'image_path': str(path.resolve()), 'bytes': len(pixels)}


async def run(args):
    async with streamablehttp_client(args.url) as (read, write, _):
        async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=args.timeout)) as session:
            await session.initialize()
            if args.mode == 'describe':
                result = await session.call_tool('describe_toolset', {'toolset_name': args.toolset})
                if result.isError:
                    raise RuntimeError(str(result.content))
                value = json.loads(next(c.text for c in result.content if c.type == 'text'))
            elif args.mode == 'call':
                arguments = json.loads(args.arguments.read_text(encoding='utf-8-sig')) if args.arguments else {}
                if not isinstance(arguments, dict):
                    raise ValueError('Tool arguments must be a JSON object')
                value = await call_tool(session, args.toolset, args.tool, arguments)
            elif args.mode == 'script':
                value = await call_tool(session, PROGRAMMATIC, 'execute_tool_script', {
                    'script': args.script.read_text(encoding='utf-8-sig')})
                value = json.loads(value) if isinstance(value, str) else value
            elif args.mode == 'capture':
                value = await capture_viewport(lambda t,n,a:call_tool(session,t,n,a),show_ui=args.show_ui)
                value = save_capture(value, args.output.with_suffix('.png'))
            else:
                script = (ROOT / 'Unreal/agent_snapshot_tools.py').read_text(encoding='utf-8')
                query = {'name': args.name, 'actor_type': {'refPath': args.actor_class} if args.actor_class else None,
                         'properties': args.properties, 'limit': args.limit}
                value = await call_tool(session, PROGRAMMATIC, 'execute_tool_script', {
                    'script': 'QUERY = ' + repr(query) + '\n' + script})
                value = json.loads(value) if isinstance(value, str) else value
                if args.capture:
                    capture = await capture_viewport(lambda t,n,a:call_tool(session,t,n,a))
                    value['capture'] = save_capture(capture, args.output.with_suffix('.png'))
                    value['capture_note'] = 'State and image sampled sequentially; not an atomic frame.'
    return {'captured_at_utc': datetime.now(timezone.utc).isoformat(), 'result': value}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default=UNREAL_MCP)
    parser.add_argument('--timeout', type=float, default=120)
    parser.add_argument('--output', type=Path, required=True)
    modes = parser.add_subparsers(dest='mode', required=True)
    describe = modes.add_parser('describe')
    describe.add_argument('toolset')
    call = modes.add_parser('call')
    call.add_argument('toolset')
    call.add_argument('tool')
    call.add_argument('--arguments', type=Path)
    script = modes.add_parser('script', help='Batch registered tools; this is not general editor Python')
    script.add_argument('script', type=Path)
    capture = modes.add_parser('capture')
    capture.add_argument('--show-ui', action='store_true')
    snapshot = modes.add_parser('snapshot')
    snapshot.add_argument('--name', default='')
    snapshot.add_argument('--class', dest='actor_class')
    snapshot.add_argument('--properties', nargs='*', default=[])
    snapshot.add_argument('--limit', type=int, default=20)
    snapshot.add_argument('--capture', action='store_true')
    args = parser.parse_args()
    if args.timeout <= 0 or (args.mode == 'snapshot' and not 1 <= args.limit <= 100):
        parser.error('Use a positive timeout and an actor limit between 1 and 100')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = asyncio.run(run(args))
    except Exception as error:
        args.output.write_text(json.dumps({'status': 'ERROR', 'error': error_message(error)}, indent=2), encoding='utf-8')
        print(f'Unreal request failed; see {args.output}', file=sys.stderr)
        return 1
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')
    print(f'Saved {args.mode}: {args.output}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
