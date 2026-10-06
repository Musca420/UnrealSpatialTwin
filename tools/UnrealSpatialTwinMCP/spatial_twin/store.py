"""Revision-pinned read-only canonical database and transactional patch storage."""
import base64
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import time
import uuid
import os
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar

SCHEMA_VERSION = 1


class Connection(sqlite3.Connection):
    """A transaction context owns its connection, including exceptional exits."""
    def __exit__(self, *arguments):
        try:
            return super().__exit__(*arguments)
        finally:
            self.close()


def encode(value):
    return json.dumps(value, separators=(',', ':'), sort_keys=True, allow_nan=False)


def vector(value, size=3):
    if not isinstance(value, (list, tuple)) or len(value) != size or any(
            isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in value):
        raise ValueError(f'Expected {size} finite numbers')
    return list(value)


def bounds(value):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError('Expected [minimum, maximum] bounds')
    lo, hi = map(vector, value)
    if any(a > b for a, b in zip(lo, hi)):
        raise ValueError('Inverted bounds')
    return [lo, hi]


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self._snapshot_reader = ContextVar('spatial_twin_snapshot_reader', default=None)

    def read(self):
        reader = self._snapshot_reader.get()
        if reader is not None:
            return nullcontext(reader)
        db = sqlite3.connect((self.root / 'world.sqlite').as_uri() + '?mode=ro', uri=True, timeout=5, factory=Connection)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA query_only=ON')
            db.execute('BEGIN')
            meta = dict(db.execute('SELECT key,value FROM metadata'))
            ready = db.execute("SELECT id FROM snapshots WHERE state='READY' ORDER BY revision DESC LIMIT 1").fetchone()
        except BaseException:
            db.close()
            raise
        if int(meta.get('schema_version', 0)) != SCHEMA_VERSION:
            db.close()
            raise ValueError('Unsupported canonical schema; migrate with Unreal before querying')
        if not ready:
            db.close()
            raise ValueError('No READY snapshot: run SpatialTwinScan or spatial_twin_rebuild')
        return db

    @contextmanager
    def read_snapshot(self):
        """Reuse one READY transaction for a synchronous batch; each worker has its own context."""
        with self.read() as db:
            token = self._snapshot_reader.set(db)
            try:
                yield db
            finally:
                self._snapshot_reader.reset(token)

    def patches(self):
        self.root.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.root / 'patches.sqlite', timeout=5, factory=Connection)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('''CREATE TABLE IF NOT EXISTS patches(id TEXT PRIMARY KEY,base_revision INTEGER NOT NULL,
            status TEXT NOT NULL,operations TEXT NOT NULL,validation TEXT,receipts TEXT NOT NULL DEFAULT '[]',updated REAL NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS patch_results(patch_id TEXT PRIMARY KEY,
            operations_digest TEXT NOT NULL,dispatch_id TEXT NOT NULL,result TEXT NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS patch_saves(patch_id TEXT PRIMARY KEY,
            operations_digest TEXT NOT NULL,dispatch_id TEXT NOT NULL,result TEXT NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS patch_save_progress(patch_id TEXT PRIMARY KEY,
            operations_digest TEXT NOT NULL,result TEXT NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS patch_save_packages(patch_id TEXT NOT NULL,
            package TEXT NOT NULL,result TEXT NOT NULL,PRIMARY KEY(patch_id,package))''')
        db.execute('''CREATE TABLE IF NOT EXISTS patch_continuations(parent_id TEXT PRIMARY KEY,
            child_id TEXT UNIQUE NOT NULL,operations_digest TEXT NOT NULL,prefix_count INTEGER NOT NULL,revision INTEGER NOT NULL)''')
        return db

    @contextmanager
    def application_lock(self):
        """Serialize application/recovery per world; OS releases the lock on crash."""
        self.root.mkdir(parents=True,exist_ok=True)
        # One editor mutation stream per world; readers and other worlds stay free.
        with (self.root/'patch-application.lock').open('a+b') as handle:
            if handle.tell()==0:handle.write(b'\0');handle.flush()
            handle.seek(0)
            if os.name=='nt':
                import msvcrt
                acquire=lambda:msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
                release=lambda:msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
            else:
                import fcntl
                acquire=lambda:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
                release=lambda:fcntl.flock(handle,fcntl.LOCK_UN)
            try:acquire()
            except OSError as error:raise ValueError('Another patch application or recovery is active for this world') from error
            try:yield
            finally:handle.seek(0);release()

    def status(self,include_counts=True):
        if not (self.root / 'world.sqlite').exists():
            return {'state': 'UNINITIALIZED', 'editor_connected': False, 'canonical_revision': 0}
        try:reader=self.read()
        except ValueError as error:
            if not str(error).startswith('No READY snapshot'):raise
            return {'state':'NO_READY_SNAPSHOT','editor_connected':False,'canonical_revision':None,'reason':str(error)}
        with reader as db:
            meta = dict(db.execute('SELECT key,value FROM metadata'))
            snap = dict(db.execute("SELECT * FROM snapshots WHERE state='READY' ORDER BY revision DESC,timestamp DESC LIMIT 1").fetchone())
            counts = dict(db.execute('SELECT kind,count(*) FROM entities GROUP BY kind')) if include_counts else None
        # Heartbeat is deliberately separate from canonical revision changes.
        try:
            heartbeat = json.loads((self.root / 'heartbeat.json').read_text())
            age = time.time() - float(heartbeat['epoch'])
            connected = math.isfinite(age) and 0 <= age < 10
        except (OSError, ValueError, KeyError, TypeError):
            heartbeat, connected = {}, False
        return {'state': 'READY', 'editor_connected': connected,'root':str(self.root),
                'canonical_revision': int(meta['world_revision']), 'last_sync': meta.get('last_sync'),
                'snapshot_id': snap['id'], 'snapshot_timestamp': snap['timestamp'], 'map': snap['map'],
                'engine_version': meta.get('engine_version'), 'project_id': meta.get('project_id'),
                'saved_revision': meta.get('saved_revision'), 'coverage': meta.get('coverage'),
                'source_mode':meta.get('source_mode'),
                'last_failed_sync':meta.get('last_failed_sync'),
                'partition_cell_state':meta.get('partition_cell_state'),
                'navigation_state':meta.get('navigation_state'),
                'text_search_state':'CURRENT' if meta.get('text_search_version')=='1' else 'LEGACY_REQUIRES_UPGRADE',
                'asset_relationship_state':'INDEXED_REFERENCED' if meta.get('referenced_asset_dependencies_version')=='1' else 'LEGACY_REQUIRES_UPGRADE',
                'asset_dependency_scope_state':'CURRENT' if meta.get('asset_dependency_scope_version')=='1' else 'LEGACY_REQUIRES_UPGRADE',
                'collision_bounds_state':'CURRENT' if meta.get('collision_bounds_version')=='2' else 'LEGACY_REQUIRES_UPGRADE',
                'nested_container_state':'CURRENT' if meta.get('nested_container_export_version')=='2' else 'LEGACY_UNVERIFIED',
                **({'counts':counts} if include_counts else {}), 'synchronizer': heartbeat}

    @staticmethod
    def revision(db):
        return int(db.execute("SELECT value FROM metadata WHERE key='world_revision'").fetchone()[0])

    @staticmethod
    def entity(db, entity_id):
        row = db.execute('SELECT source FROM entities WHERE id=?', (entity_id,)).fetchone()
        if not row:
            raise ValueError('Entity not found: ' + entity_id)
        return json.loads(row[0])

    @staticmethod
    def search_sql(db,text,kind=None):
        if not isinstance(text,str):raise ValueError('Search text must be a string')
        current=db.execute("SELECT 1 FROM metadata WHERE key='text_search_version' AND value='1'").fetchone()
        args=[]
        if text and current:
            # ponytail: short strings scan the covering metadata index; add a
            # smaller n-gram index only if measured demand warrants its storage.
            source='entities AS s INDEXED BY entity_search'
            if len(text)>=3 and '\x00' not in text:
                # detail=none saves positions. Sample literal 3-character tokens
                # to find a superset; LIKE below preserves exact old semantics.
                last=len(text)-3
                tokens=sorted({text[i:i+3] for i in (0,last//4,last//2,3*last//4,last)})
                match=' AND '.join('"'+t.replace('"','""')+'"' for t in tokens)
                # Broad terms should stream the ordered metadata page rather
                # than sorting hundreds of thousands of FTS matches.
                candidates=db.execute('SELECT rowid FROM entity_text WHERE entity_text MATCH ? LIMIT 257',(match,)).fetchall()
                if len(candidates)<=256:
                    source='entity_text CROSS JOIN entities AS s ON s.rowid=entity_text.rowid'
                    args.append(match)
            sql='SELECT s.id FROM '+source+' WHERE '+('entity_text MATCH ? AND ' if args else '')
        else:
            sql='SELECT s.id FROM entities AS s WHERE '
        if text:
            escaped=text.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
            sql+='(s.label LIKE ? ESCAPE CHAR(92) OR s.path LIKE ? ESCAPE CHAR(92) OR s.class LIKE ? ESCAPE CHAR(92))'
            args.extend(['%'+escaped+'%']*3)
        else:sql+='(s.label IS NOT NULL OR s.path IS NOT NULL OR s.class IS NOT NULL)'
        if kind:sql+=' AND s.kind=?';args.append(kind)
        return sql+' ORDER BY s.id',args

    @staticmethod
    def page(db, sql, arguments, query, limit=50, cursor=None, fields=None, detail='entities',entity_ids=False,source_overrides=False):
        if fields is not None and (not isinstance(fields,list) or any(not isinstance(f,str) for f in fields)):
            raise ValueError('fields must be field names')
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError('limit must be 1..500')
        revision = Store.revision(db)
        signature = hashlib.sha256(encode(query).encode()).hexdigest()
        offset = 0
        if cursor:
            try:
                token = json.loads(base64.urlsafe_b64decode(cursor))
                if token['revision'] != revision or token['query'] != signature:
                    raise ValueError('Cursor expired or belongs to another query')
                offset = int(token['offset'])
                if offset < 0:
                    raise ValueError('Invalid cursor offset')
            except (KeyError, ValueError, TypeError) as error:
                raise ValueError('Invalid or expired cursor') from error
        sql+=' LIMIT ? OFFSET ?'
        if source_overrides:
            sql='SELECT coalesce(selected.source,e.source) AS source FROM ('+sql+') AS selected LEFT JOIN entities AS e ON e.id=selected.id ORDER BY selected.id'
        elif entity_ids:
            sql='SELECT e.source FROM ('+sql+') AS selected CROSS JOIN entities AS e ON e.id=selected.id ORDER BY selected.id'
        rows = db.execute(sql, [*arguments, limit + 1, offset]).fetchall()
        selected = [json.loads(r['source']) for r in rows[:limit]]
        if detail == 'summary':
            raise ValueError('Summary must be aggregated before pagination')
        selected = [select_fields(e,fields) for e in selected]
        following = None
        if len(rows) > limit:
            following = base64.urlsafe_b64encode(encode({'revision': revision, 'query': signature, 'offset': offset + limit}).encode()).decode()
        return {'revision': revision, 'entities': selected, 'cursor': following, 'returned': len(selected)}

    def changes(self, since, until=None, limit=50, cursor=None, fields=None):
        fields = ['revision','entity_id','before','after'] if fields is None else fields
        if not isinstance(fields, list) or any(not isinstance(f, str) for f in fields):
            raise ValueError('fields must be field names')
        columns = "'id',entity_id||':'||revision,'kind',type,'revision',revision,'entity_id',entity_id"
        for name, column in (('before','before_json'), ('after','after_json')):
            if name in fields or any(f.startswith(name+'.') for f in fields): columns += f",'{name}',json({column})"
        with self.read() as db:
            end = self.revision(db) if until is None else until
            if not isinstance(since, int) or not isinstance(end, int) or not 0 <= since <= end <= self.revision(db):
                raise ValueError('Invalid revision range')
            # Cursor carries the immutable range as well as current revision.
            query = {'changes': [since,end], 'fields':fields}
            return self.page(db, 'SELECT json_object('+columns+') AS source FROM changes WHERE revision>? AND revision<=? ORDER BY revision,entity_id',
                             [since,end],query,limit,cursor,fields)

    def patch_get(self, patch_id):
        with self.patches() as db:
            row = db.execute('SELECT * FROM patches WHERE id=?', (patch_id,)).fetchone()
            continuation=db.execute('SELECT parent_id,prefix_count,revision FROM patch_continuations WHERE child_id=?',(patch_id,)).fetchone()
            continued=db.execute('SELECT child_id FROM patch_continuations WHERE parent_id=?',(patch_id,)).fetchone()
        if not row:
            raise ValueError('Patch not found')
        value = dict(row)
        for key in ('operations','validation','receipts'):
            value[key] = json.loads(value[key]) if value[key] else None
        if continuation:value['continuation']=dict(continuation)
        if continued:value['continued_by']=continued['child_id']
        return value

    def patch_create(self, operations, base_revision=None):
        with self.read() as db:
            rev = self.revision(db)
        base = rev if base_revision is None else base_revision
        if type(base) is not int or base != rev:
            raise ValueError('Patch base must be current canonical revision')
        operations = check_operations(operations)
        ident = str(uuid.uuid4())
        with self.patches() as db:
            db.execute('INSERT INTO patches(id,base_revision,status,operations,updated) VALUES(?,?,?,?,?)',
                       (ident,base,'DRAFT',encode(operations),time.time()))
        return self.patch_get(ident)

    def patch_update(self, patch_id, operations):
        operations = check_operations(operations)
        with self.patches() as db:
            result = db.execute("UPDATE patches SET operations=?,status='DRAFT',validation=NULL,updated=? WHERE id=? AND status IN ('DRAFT','VALIDATED','INVALID','CONFLICTED')",
                                (encode(operations),time.time(),patch_id))
            if result.rowcount != 1:
                raise ValueError('Patch absent or no longer editable')
        return self.patch_get(patch_id)


def select_fields(entity,fields=None):
    """Project source dictionaries without serializing unrequested nested geometry.

    Unknown leaves are absent, explicit null stays null, and a requested parent
    includes its complete value. Literal source keys take precedence over paths.
    """
    if fields is not None and (not isinstance(fields,list) or any(not isinstance(f,str) for f in fields)):
        raise ValueError('fields must be field names')
    names=fields if fields is not None else ['label','class','parent_id','actor_id','asset_id','bounds','transform','path','coverage']
    mask={}
    for name in ['id','kind',*names]:
        parts=[name] if name in entity else name.split('.')
        branch=mask
        for part in parts[:-1]:
            if branch.get(part) is True:break
            branch=branch.setdefault(part,{})
        else:branch[parts[-1]]=True
    def project(value,selection):
        result={}
        for key,child in selection.items():
            if key not in value:continue
            if child is True:result[key]=value[key]
            elif isinstance(value[key],dict):
                nested=project(value[key],child)
                if nested:result[key]=nested
        return result
    return project(entity,mask)


OPERATIONS = {'CREATE_ACTOR','DELETE_ACTOR','MOVE_ACTOR','ROTATE_ACTOR','SCALE_ACTOR','SET_PROPERTY','ATTACH','DETACH','CHANGE_ASSET','SET_INSTANCE_TRANSFORM'}


def check_operations(operations):
    if not isinstance(operations, list) or not 1 <= len(operations) <= 10000:
        raise ValueError('Expected 1..10000 operations')
    result = []
    for index, op in enumerate(operations):
        if not isinstance(op, dict) or op.get('type') not in OPERATIONS:
            raise ValueError('Unknown patch operation; use type with one of: '+', '.join(sorted(OPERATIONS)))
        op = dict(op)
        if not isinstance(op.get('target'), str) or not op['target']:
            raise ValueError('Every operation needs a target ID (temporary ID for create)')
        op.setdefault('operation_id', str(index))
        if not isinstance(op['operation_id'],str) or not op['operation_id']:
            raise ValueError('operation_id must be a nonempty string')
        if op['type']=='CREATE_ACTOR':
            if not isinstance(op.get('class'),str) or not op['class']:
                raise ValueError('CREATE_ACTOR requires an exported class')
            properties=op.get('component_properties',{})
            if not isinstance(properties,dict) or set(properties)-{'bCanEverAffectNavigation'} or any(type(v) is not bool for v in properties.values()):
                raise ValueError('Only explicit boolean bCanEverAffectNavigation is supported for mesh creation')
            if 'transform' in op:
                t=op['transform']
                if not isinstance(t,dict):raise ValueError('Invalid transform')
                vector(t.get('position'));scale=vector(t.get('scale'));q=vector(t.get('rotation'),4)
                if any(abs(v)<1e-20 for v in scale) or abs(sum(v*v for v in q)-1)>1e-8:raise ValueError('Singular scale or nonunit rotation')
        if op['type']=='SET_INSTANCE_TRANSFORM':
            t=op.get('transform')
            if not isinstance(t,dict):raise ValueError('SET_INSTANCE_TRANSFORM requires a world transform')
            vector(t.get('position'));scale=vector(t.get('scale'));q=vector(t.get('rotation'),4)
            if any(abs(v)<1e-20 for v in scale) or abs(sum(v*v for v in q)-1)>1e-8:raise ValueError('Singular scale or nonunit rotation')
        if op['type'] in ('MOVE_ACTOR','SCALE_ACTOR'):
            vector(op.get('value'))
        if op['type'] == 'ROTATE_ACTOR':
            q = vector(op.get('value'),4)
            if abs(sum(x*x for x in q)-1) > 1e-8:
                raise ValueError('Rotation must be a unit quaternion [x,y,z,w]')
        if op['type'] == 'SET_PROPERTY' and not isinstance(op.get('property'), str):
            raise ValueError('SET_PROPERTY needs property and value')
        result.append(op)
    if len({op['operation_id'] for op in result}) != len(result):
        raise ValueError('Duplicate operation_id')
    encode(result)
    return result
