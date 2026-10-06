"""Immutable authored-source guard shared by the independent benchmarks."""
import json,hashlib
def source_hashes(folder,name,target):
    """Only the immutable files authored for this run may reach native import."""
    manifest=json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
    stem='SM_ST_Rail_'+name
    expected={'source_file':stem+'.fbx','geometry_file':stem+'.stg','collision_files':[f'UCX_{stem}_{i:02d}.stg' for i in range(6)]}
    if manifest.get('target_path')!=target or any(manifest.get(k)!=v for k,v in expected.items()):raise ValueError('Source outside authorized scope')
    files=['manifest.json',stem+'.blend',expected['source_file'],expected['geometry_file'],*expected['collision_files']]
    hashes={}
    for filename in files:
        path=(folder/filename).resolve()
        if not path.is_relative_to(folder.resolve()):raise ValueError('Source path escaped scope')
        hashes[filename]=hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes
