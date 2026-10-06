"""Test-only action-time connection; offline planning needs no live editor."""
import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
import json
import os
from pathlib import Path
import time

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def wait_ready(spec, route, name):
    receipt = os.environ.get('SPATIAL_TWIN_NATIVE_READY')
    if not receipt:
        return
    expected = Path(spec['project']).resolve().parent / 'startup' / name / 'startup.json'
    if Path(receipt).resolve() != expected:
        raise ValueError('Readiness receipt belongs to another run')
    pid = int(os.environ['SPATIAL_TWIN_NATIVE_PID'])
    deadline = time.monotonic() + 240
    while not expected.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError('Native readiness absent; no live actions attempted')
        await asyncio.sleep(.25)
    ready = json.loads(expected.read_text(encoding='utf-8'))
    if (ready.get('state') != 'READY' or ready.get('pid') != pid
            or Path(ready.get('project', '')).resolve() != Path(spec['project']).resolve()
            or ready.get('route') != route
            or ready.get('native_plugin_loaded') != (route == 'with')):
        raise ValueError('Native readiness identity/state differs')


@asynccontextmanager
async def connect(spec, route, name):
    await wait_ready(spec, route, name)
    # Enter and exit the transport in the same action task: anyio cancel scopes
    # must never migrate from a tool handler into the lifespan task.
    async with streamablehttp_client(spec['url']) as (r, w, _):
        async with ClientSession(r, w, read_timeout_seconds=timedelta(seconds=180)) as native:
            await native.initialize()
            yield native
