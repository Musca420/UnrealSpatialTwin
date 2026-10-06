"""Immutable authoring assets for Shadow queries, outside the canonical DB."""
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import struct
import time
from .store import encode
from . import geometry as g


def digest_file(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def ucx_navigation(meshes):
    """UE5.8 default UCX export prediction; native import parity remains required."""
    vertices=[];triangles=[]
    for mesh in meshes:
        start=len(vertices);vertices.extend(mesh.vertices)
        center=[sum(v[i] for v in mesh.vertices)/len(mesh.vertices) for i in range(3)]
        for tri in mesh.triangles:
            a,b,c=(mesh.vertices[i] for i in tri)
            # Recast's native convex exporter reverses the outward hull faces.
            inward=g.dot(g.cross(g.sub(b,a),g.sub(c,a)),g.sub(center,a))>0
            triangles.append([start+i for i in (tri if inward else reversed(tri))])
    return (b'STG1'+struct.pack('<III',1,len(vertices),len(triangles))+
            b''.join(struct.pack('<3d',*v) for v in vertices)+b''.join(struct.pack('<3I',*t) for t in triangles))


def stage(store, manifest_file, template_asset_id):
    path = Path(manifest_file).resolve()
    if path.stat().st_size > 1 << 20:
        raise ValueError('Authoring manifest exceeds 1 MiB')
    manifest = json.loads(path.read_text(encoding='utf-8'))
    if manifest.get('schema_version') != 1 or manifest.get('units') != 'cm' or manifest.get('coordinates') != 'Unreal_LH_Zup':
        raise ValueError('Expected schema 1, cm and Unreal_LH_Zup coordinates')
    target = manifest.get('target_path', '')
    import re
    if not re.fullmatch(r'/Game/(?:[A-Za-z0-9_]+/)+[A-Za-z0-9_]+\.[A-Za-z0-9_]+', target) or target.rsplit('/', 1)[1].split('.')[0] != target.split('.')[-1]:
        raise ValueError('Expected a unique /Game/package.Asset target')
    def source(name):
        p = (path.parent / name).resolve()
        if not p.is_relative_to(path.parent) or not p.is_file() or p.stat().st_size > 512 << 20:
            raise ValueError('Authoring file missing, outside manifest folder or exceeds 512 MiB')
        return p
    fbx = source(manifest['source_file'])
    if fbx.suffix.lower() != '.fbx':
        raise ValueError('This authoring adapter imports FBX static meshes')
    files = [source(manifest['geometry_file']), *[source(p) for p in manifest['collision_files']]]
    if not 2 <= len(files) <= 257:
        raise ValueError('Export render geometry and 1..256 convex collision bodies')
    meshes = [g.load_mesh(str(p)) for p in files]
    if any(not mesh.root for mesh in meshes):
        raise ValueError('Empty geometry')
    for mesh in meshes[1:]:
        edges = {}
        for tri in mesh.triangles:
            a, b, c = (mesh.vertices[i] for i in tri)
            if g.length(g.cross(g.sub(b, a), g.sub(c, a))) < 1e-12:
                raise ValueError('Degenerate collision face')
            for i, j in zip(tri, (*tri[1:], tri[0])):
                key = tuple(sorted((i, j))); edges[key] = edges.get(key, 0) + 1
        if any(n != 2 for n in edges.values()):
            raise ValueError('Collision body must be a closed convex mesh')
        center = [sum(p[i] for p in mesh.vertices) / len(mesh.vertices) for i in range(3)]
        # Blender stores mesh coordinates as float32. A fixed 1e-7 cm plane
        # threshold rejects quantized convex faces (measured 3.1e-6 cm drift).
        # Bound tolerance to float32 roundoff at the stored local coordinates
        # (small UCX bodies can be far from the shared asset pivot), with a
        # 0.001 cm ceiling; native hull/vertex parity is still mandatory.
        span=max(max(p[i] for p in mesh.vertices)-min(p[i] for p in mesh.vertices) for i in range(3))
        magnitude=max(span,max(abs(v) for p in mesh.vertices for v in p))
        tolerance=max(1e-7,min(.001,magnitude*2e-7))
        for tri in mesh.triangles:
            a, b, c = (mesh.vertices[i] for i in tri)
            n = g.cross(g.sub(b, a), g.sub(c, a))
            if g.dot(n, g.sub(center, a)) > 0: n = g.mul(n, -1)
            if any(g.dot(n, g.sub(p, a)) > tolerance * g.length(n) for p in mesh.vertices):
                raise ValueError('Collision body is not convex')
    with store.read() as db:
        from .store import Store
        template = Store.entity(db, template_asset_id)
        if template['kind'] != 'StaticMesh' or not template.get('native_spawn_collision'):
            raise ValueError('Select a canonically exported StaticMesh spawn profile')
        revision = Store.revision(db)
    hashes = [hashlib.sha1(p.read_bytes()).hexdigest() for p in files]
    navigation_profile=template.get('native_spawn_navigation',{})
    navigation_blob=ucx_navigation(meshes[1:]) if navigation_profile.get('authored_ucx_adapter')==1 else None
    fingerprint = hashlib.sha256(encode({'manifest': manifest, 'geometry': hashes, 'fbx': digest_file(fbx),
                                          'navigation_adapter':navigation_profile,'stage_version':2,
                                          'spawn': template['native_spawn_collision'], 'navigation': template['native_spawn_affects_navigation']}).encode()).hexdigest()
    ident = 'staged:' + fingerprint
    folder = store.root / 'staging' / fingerprint
    folder.mkdir(parents=True, exist_ok=True)
    # Publish complete immutable files before their catalog transaction.
    refs = []
    for p, digest in zip(files, hashes):
        dst = folder / (digest + '.stg')
        if not dst.exists(): shutil.copyfile(p, dst.with_suffix('.tmp')); dst.with_suffix('.tmp').replace(dst)
        if hashlib.sha1(dst.read_bytes()).hexdigest() != digest: raise ValueError('Staged geometry integrity mismatch')
        refs.append({'hash': digest, 'path': str(dst.relative_to(store.root)), 'metadata': {'format': 'STG1', 'provenance': 'AUTHORED'}})
    cached_fbx = folder / fbx.name
    if not cached_fbx.exists(): shutil.copyfile(fbx, cached_fbx.with_suffix('.tmp')); cached_fbx.with_suffix('.tmp').replace(cached_fbx)
    if digest_file(cached_fbx) != digest_file(fbx): raise ValueError('Staged FBX integrity mismatch')
    boxes = [mesh.root[0] for mesh in meshes]
    if any(b[0][i]<boxes[0][0][i]-1e-7 or b[1][i]>boxes[0][1][i]+1e-7 for b in boxes[1:] for i in range(3)):
        raise ValueError('Collision outside render bounds requires a wider native collision spatial index')
    asset = {'id': ident, 'kind': 'StaticMesh', 'path': target, 'package': target.split('.')[0],
             'label': target.split('.')[-1], 'class': '/Script/Engine.StaticMesh', 'provenance': 'AUTHORED',
             'local_bounds': boxes[0], 'geometry_hash': hashes[0], 'collision_simple': hashes[1:],
             'collision_complex': [hashes[0]], 'collision_shapes': [], 'trace_mode': 'UseSimpleAndComplex',
             'collision_bounds': [[min(b[0][i] for b in boxes) for i in range(3)], [max(b[1][i] for b in boxes) for i in range(3)]],
             'native_spawn_collision': template['native_spawn_collision'],
             'native_spawn_affects_navigation': template['native_spawn_affects_navigation'],
             'source_file': str(cached_fbx), 'source_sha256': digest_file(cached_fbx),
             'geometry': refs, 'template_asset_id': template_asset_id, 'staged_at_revision': revision,
             'import_verification': 'REQUIRED', 'manifest': manifest}
    asset['native_spawn_navigation']=navigation_profile
    if navigation_blob is not None:
        digest=hashlib.sha1(navigation_blob).hexdigest();dst=folder/(digest+'.stg')
        if not dst.exists():dst.with_suffix('.tmp').write_bytes(navigation_blob);dst.with_suffix('.tmp').replace(dst)
        if hashlib.sha1(dst.read_bytes()).hexdigest()!=digest:raise ValueError('Staged navigation integrity mismatch')
        refs.append({'hash':digest,'path':str(dst.relative_to(store.root)),'metadata':{'format':'STG1','provenance':'DERIVED_AUTHORED_UCX','adapter_version':1}})
        asset.update(navigation_geometry_hash=digest,navigation_input_coverage='authored_ucx_prediction_v1')
        for key in ('navigation_slope_behavior','navigation_slope_angle'):
            if key in navigation_profile:asset[key]=navigation_profile[key]
    with store.patches() as db:
        db.execute('CREATE TABLE IF NOT EXISTS staged_assets(id TEXT PRIMARY KEY,source TEXT NOT NULL,created REAL NOT NULL)')
        db.execute('INSERT OR IGNORE INTO staged_assets VALUES(?,?,?)', (ident, encode(asset), time.time()))
    return asset


def get(store, ident):
    path = store.root / 'patches.sqlite'
    if not path.exists(): raise ValueError('No staged asset catalog')
    db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
    try:
        try: row = db.execute('SELECT source FROM staged_assets WHERE id=?', (ident,)).fetchone()
        except sqlite3.OperationalError: row = None
    finally: db.close()
    if not row: raise ValueError('Staged asset not found: ' + ident)
    return json.loads(row[0])


def geometry(store, digest):
    path = store.root / 'patches.sqlite'
    if not path.exists(): return None
    db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
    try:
        for row in db.execute("SELECT j.value FROM staged_assets a,json_each(a.source,'$.geometry') j WHERE json_extract(j.value,'$.hash')=? LIMIT 1", (digest,)):
            return json.loads(row[0])
    except sqlite3.OperationalError: return None
    finally: db.close()
