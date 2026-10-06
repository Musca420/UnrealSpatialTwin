"""Create a standalone public source repository and ZIP; never overwrite outputs.

Runtime/native sources are unchanged. Historical game-specific benchmark entry
points are excluded; their shared helper functions are copied verbatim by AST.
"""
import argparse,ast,hashlib,json,re,shutil,zipfile
from pathlib import Path
from package_plugin import package,ROOT

TOOLS=ROOT/'tools/UnrealSpatialTwinMCP'

def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def public_text(text, root=ROOT):
    # JSON receipts may contain escaped JSON strings; redact every escape depth.
    variants={str(root),root.as_posix()}
    for _ in range(4):
        variants.update(v.replace('\\','\\\\') for v in tuple(variants))
    for value in sorted(variants,key=len,reverse=True):
        text=re.sub(re.escape(value),lambda _: '<qualification-checkout>',text,flags=re.I)
    text=re.sub(r'(?i)[A-Z]:[\\/]+Users[\\/]+[^\\/<>\s"\']+', '<local-user-home>',text)
    if list(root.glob('*.uproject')) or list(root.glob('Unreal/*/*.uproject')):
        text=text.replace(root.name,'qualification-project')
    return re.sub(r'(?<=/Game/Maps/)[^"\\\s,;]+','qualification-map',text)

def function(filename,name):
    text=(TOOLS/filename).read_text(encoding='utf-8')
    node=next(n for n in ast.parse(text).body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name==name)
    return ast.get_source_segment(text,node)+'\n'

def build(destination):
    package(destination, publication=True)
    destination=Path(destination).resolve()
    (destination/'Plugins').mkdir()
    assert (destination/'UnrealSpatialTwin').resolve().is_relative_to(destination) and (destination/'Plugins/UnrealSpatialTwin').resolve().is_relative_to(destination)
    shutil.move(str(destination/'UnrealSpatialTwin'),str(destination/'Plugins/UnrealSpatialTwin'))
    shutil.copytree(destination/'SpatialTwinCodexPlugin/runtime/tools',destination/'tools')
    shutil.copy2(TOOLS/'requirements-tested.txt',destination/'tools/UnrealSpatialTwinMCP/requirements-tested.txt')
    shutil.copytree(destination/'SpatialTwinCodexPlugin/runtime/scripts',destination/'scripts')
    shutil.copytree(destination/'SpatialTwinCodexPlugin/runtime/Unreal',destination/'Unreal')
    shutil.copy2(ROOT/'Unreal/start_spatial_twin.py',destination/'Unreal/start_spatial_twin.py')
    author=destination/'art/SpatialTwinBenchmark';author.mkdir(parents=True)
    shutil.copy2(ROOT/'art/SpatialTwinBenchmark/build_recovered_rail.py',author/'build_recovered_rail.py')
    shutil.copytree(destination/'SpatialTwinCodexPlugin/docs',destination/'docs')
    target=destination/'tools/UnrealSpatialTwinMCP'
    seeds=[p.name for p in TOOLS.glob('test_*.py')]+['public_repository.py','package_plugin.py','release_checks.py','native_checks.py','prepare_benchmark.py','benchmark_fixture.py','cold_agent_benchmark.py','cold_benchmark_control.py','cold_benchmark_startup.py','native_link_parity.py','native_fill_parity.py','native_asset_parity.py','native_link_checks.py','verify_navigation_slope.py','navigation_parity.py','package_mcp_checks.py']
    legacy={'construction_benchmark','agent_benchmark','agent_benchmark_guard'}
    copied=set()
    while seeds:
        name=seeds.pop()
        if name in copied or Path(name).stem in legacy:continue
        source=TOOLS/name
        if not source.is_file():raise FileNotFoundError(source)
        copied.add(name);shutil.copy2(source,target/name)
        for node in ast.walk(ast.parse(source.read_text(encoding='utf-8'))):
            modules=([node.module] if isinstance(node,ast.ImportFrom) and node.module else [n.name for n in node.names] if isinstance(node,ast.Import) else [])
            for module in modules:
                local=module.split('.')[0]+'.py'
                if (TOOLS/local).is_file() and local not in copied:seeds.append(local)
    helpers={
      'agent_benchmark.py':'"""Native whole-agent usage accounting; copied verbatim from the qualified runner."""\n'+function('agent_benchmark.py','usage'),
      'agent_benchmark_guard.py':'"""Immutable authored-source guard shared by the independent benchmarks."""\nimport json,hashlib\n'+function('agent_benchmark_guard.py','source_hashes'),
      'construction_benchmark.py':'"""Official MCP constants/batching helpers; no game-specific runner."""\nimport json\nfrom mcp import ClientSession\nfrom mcp.client.streamable_http import streamablehttp_client\nimport apply_patch\nfrom scripts.unreal_mcp import EDITOR,PROGRAMMATIC\nSCENE=apply_patch.SCENE;ACTOR=apply_patch.ACTOR;OBJECT=apply_patch.OBJECT\nASSET="editor_toolset.toolsets.asset.AssetTools"\nMESH="editor_toolset.toolsets.static_mesh.StaticMeshTools"\n'+function('construction_benchmark.py','data')+function('construction_benchmark.py','program')}
    # Match the existing script import convention without requiring a package.
    helpers['construction_benchmark.py']=helpers['construction_benchmark.py'].replace('from scripts.unreal_mcp import EDITOR,PROGRAMMATIC','import sys\nfrom pathlib import Path\nsys.path.insert(0,str(Path(__file__).resolve().parents[2]/"scripts"))\nfrom unreal_mcp import EDITOR,PROGRAMMATIC')
    projections={}
    for name,text in helpers.items():
        (target/name).write_text(text,encoding='utf-8');projections[name]={'original_sha256':digest(TOOLS/name),'public_sha256':digest(target/name),'reason':'Exclude historical game-specific execution; retain exact shared helpers'}
    # Engine location is the sole deployment substitution in benchmark code.
    p=target/'cold_agent_benchmark.py';text=p.read_text().replace("EDITOR=Path('C:/Program Files/Epic Games/UE_5.8/Engine/Binaries/Win64/UnrealEditor.exe')","EDITOR=Path(os.environ.get('SPATIAL_TWIN_ENGINE','C:/Program Files/Epic Games/UE_5.8/Engine'))/'Binaries/Win64/UnrealEditor.exe'")
    p.write_text(text,encoding='utf-8')
    projections[p.name]={'original_sha256':digest(TOOLS/p.name),'public_sha256':digest(p),'reason':'Allow SPATIAL_TWIN_ENGINE deployment override; qualified default retained'}
    p=target/'author_benchmark_parity.py';text=p.read_text().replace("sys.path.insert(0,'C:/Program Files/Epic Games/UE_5.8/Engine/Plugins/Experimental/PythonScriptPlugin/Content/Python')","from cold_agent_benchmark import EDITOR\n    sys.path.insert(0,str(EDITOR.parents[2]/'Plugins/Experimental/PythonScriptPlugin/Content/Python'))")
    p.write_text(text,encoding='utf-8')
    projections[p.name]={'original_sha256':digest(TOOLS/p.name),'public_sha256':digest(p),'reason':'Use selected benchmark engine for observation adapter, no measurement/verification change'}
    for name in ('PUBLIC_README.md','LICENSE','BENCHMARKS.md','CONTRIBUTING.md','SECURITY.md','CHANGELOG.md'):
        shutil.copy2(ROOT/'docs/SpatialTwinPublication'/name,destination/('README.md' if name=='PUBLIC_README.md' else name))
    for name in ('protocol.json','trials.json','results.json','qualification.json','development-costs.json','protocol-audit.json','protocol-reuse.json','protocol-batch.json','protocol-author.json','publication-audit.json'):
        dest=destination/'benchmarks'/name;dest.parent.mkdir(exist_ok=True)
        shutil.copy2(ROOT/'docs/SpatialTwinPublication'/name,dest)
    shutil.copytree(ROOT/'docs/SpatialTwinPublication',destination/'docs/SpatialTwinPublication')
    # Build-input documents are also browsable on GitHub; their links target
    # the rendered root manuals rather than an input-folder-relative path.
    public_url='https://github.com/Musca420/UnrealSpatialTwin/blob/main/'
    for p in (destination/'docs/SpatialTwinPublication').rglob('*.md'):
        base=destination/'docs' if 'Documentation' in p.parts else destination
        def link(match):
            target=match[1]
            if target.startswith(('https:','http:','#')):return match[0]
            path=(base/target.split('#')[0]).resolve()
            if not path.is_relative_to(destination) or not path.exists():return match[0]
            anchor='#'+target.split('#',1)[1] if '#' in target else ''
            return ']('+public_url+path.relative_to(destination).as_posix()+anchor+')'
        p.write_text(re.sub(r'\]\(([^\s)]+)\)',link,p.read_text(encoding='utf-8')),encoding='utf-8')
    for p in (destination/'SpatialTwinCodexPlugin/docs').glob('*.md'):
        text=p.read_text(encoding='utf-8')
        for name in ('BENCHMARKS.md','CONTRIBUTING.md','SECURITY.md','README.md'):
            text=text.replace('](../'+name+')',']('+public_url+name+')')
        p.write_text(text,encoding='utf-8')
    workflow=destination/'.github/workflows';workflow.mkdir(parents=True)
    shutil.copy2(ROOT/'docs/SpatialTwinPublication/ci.yml',workflow/'python.yml')
    shutil.copy2(ROOT/'docs/SpatialTwinPublication/gitleaks.toml',destination/'.gitleaks.toml')
    shutil.copytree(ROOT/'docs/SpatialTwinPublication/ISSUE_TEMPLATE',destination/'.github/ISSUE_TEMPLATE')
    (destination/'INSTALL.txt').write_text('See README.md for the standalone public repository installation. Native source: Plugins/UnrealSpatialTwin. Codex plugin: SpatialTwinCodexPlugin. Full qualification: BENCHMARKS.md.\n',encoding='utf-8')
    (destination/'.gitignore').write_text('Build/\nBinaries/\nIntermediate/\nSaved/\nDerivedDataCache/\n__pycache__/\n*.pyc\n.venv/\n.env\n.env.*\n.codex/\n.aws/\n.ssh/\nauth.json\ncredentials*\n*.pem\n*.p12\n*.pfx\n*.key\n*.log\n*.sqlite*\n',encoding='utf-8')
    (destination/'.gitattributes').write_text('# Preserve the released SHA256 inventory across platforms and Git clones.\n* -text\n',encoding='utf-8')
    # Public documentation copies replace local locators, never measurements.
    for p in [*destination.rglob('*.md'),*destination.rglob('*.txt'),*destination.rglob('*.json')]:
        if p.is_relative_to(destination/'SpatialTwinCodexPlugin/runtime') or p.is_relative_to(destination/'SpatialTwinCodexPlugin/skills'):continue
        text=p.read_text(encoding='utf-8');clean=public_text(text)
        if clean!=text:p.write_text(clean,encoding='utf-8')
    files={p.relative_to(destination).as_posix():digest(p) for p in sorted(destination.rglob('*')) if p.is_file() and p.name!='manifest.json'}
    manifest={'format':1,'native_target':'UE5.8.2-56702186 Win64','files':files,'benchmark_helper_projections':projections,'game_content_included':False,'native_binaries_included':False}
    (destination/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    archive=destination.parent/(destination.name+'.zip')
    if archive.exists():raise ValueError('Existing ZIP is never replaced')
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for p in sorted(destination.rglob('*')):
            if p.is_file():z.write(p,p.relative_to(destination).as_posix())
    return {'repository':str(destination),'zip':str(archive),'sha256':digest(archive),'files':len(files)+1,'bytes':archive.stat().st_size}

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',required=True,type=Path)
    print(json.dumps(build(p.parse_args().output)))
