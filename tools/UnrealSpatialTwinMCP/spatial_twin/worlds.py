"""Map discovery; every MCP request pins one canonical store and patch journal."""
from contextlib import contextmanager
from contextvars import ContextVar
import json
from pathlib import Path
import sqlite3
from .store import Store


def select_world(base, map_id=None):
    base=Path(base).resolve()
    if map_id is not None:
        for record in worlds(base):
            if record['map']==map_id:return Path(record['root'])
        raise ValueError('No cached READY world for map '+map_id)
    active=base/'active_world.json'
    if not active.exists():return base
    record=json.loads(active.read_text(encoding='utf-8'))
    root=(base/record['directory']).resolve()
    if not root.is_relative_to(base):raise ValueError('Active world directory escapes Twin root')
    if not (root/'world.sqlite').is_file():raise ValueError('Active world database is missing')
    return root


def worlds(base):
    base=Path(base).resolve();result=[]
    for root in [base,*sorted((base/'maps').glob('*'))]:
        root=root.resolve()
        if not root.is_relative_to(base) or not (root/'world.sqlite').is_file():continue
        try:
            status=Store(root).status(include_counts=False)
            if status['state']=='READY':result.append({key:status[key] for key in
                ('map','root','canonical_revision','last_sync','editor_connected','project_id')})
        except (sqlite3.Error,ValueError):continue
    return result


class ProjectStore:
    """Only the MCP dispatcher follows map changes; action clients stay pinned."""
    def __init__(self,base):
        self.base_root=Path(base).resolve()
        self.current=ContextVar('spatial_twin_request_store',default=None)

    @contextmanager
    def bind(self):
        if self.current.get() is not None:
            yield
            return
        token=self.current.set(Store(select_world(self.base_root)))
        try:yield
        finally:self.current.reset(token)

    def __getattr__(self,name):
        store=self.current.get()
        if store is None:raise RuntimeError('A project query must bind its world before reading')
        return getattr(store,name)
