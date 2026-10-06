"""Build a source distribution independent of any game repository or user path."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

ROOT=Path(__file__).resolve().parents[2]


def package(destination, publication=False):
    destination=Path(destination).resolve()
    if destination.exists():raise ValueError('Use a new output directory; existing files are never replaced')
    destination.mkdir(parents=True)
    native=destination/'UnrealSpatialTwin'
    source=ROOT/'Plugins/UnrealSpatialTwin'
    native.mkdir();shutil.copy2(source/'UnrealSpatialTwin.uplugin',native/'UnrealSpatialTwin.uplugin')
    for name in ('Source','Resources'):shutil.copytree(source/name,native/name)
    agent=destination/'SpatialTwinCodexPlugin'
    shutil.copytree(ROOT/'SpatialTwinCodexPlugin',agent,ignore=shutil.ignore_patterns('__pycache__','*.pyc','runtime',*(['docs'] if publication else [])))
    docs=agent/'docs';docs.mkdir(exist_ok=True)
    documentation=ROOT/'docs/SpatialTwinPublication/Documentation' if publication else ROOT/'docs'
    for name in ('Architecture','DataModel','SpatialQueries','ShadowWorld','UnrealMCPIntegration','CodexIntegration','Installation','Troubleshooting','SPATIAL_TWIN_PRODUCTION_COVERAGE','SPATIAL_TWIN_PACKAGE_QUALIFICATION_20261006'):
        shutil.copy2(documentation/(name+'.md'),docs/(name+'.md'))
    for report in documentation.glob('SPATIAL_TWIN_BENCHMARK_*.md'):
        shutil.copy2(report,docs/report.name)
    if publication:
        for entry in documentation.iterdir():
            if entry.is_file() and not (docs/entry.name).exists():shutil.copy2(entry,docs/entry.name)
    history=ROOT/'docs/SpatialTwinHistory'
    if history.is_dir() and not publication:
        target=docs/'SpatialTwinHistory';target.mkdir(exist_ok=True)
        for entry in history.glob('PRODUCTION_COVERAGE_*.md'):shutil.copy2(entry,target/entry.name)
    runtime=agent/'runtime';tools=runtime/'tools/UnrealSpatialTwinMCP';tools.mkdir(parents=True)
    shutil.copytree(ROOT/'tools/UnrealSpatialTwinMCP/spatial_twin',tools/'spatial_twin',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    for name in ('server.py','requirements.txt','apply_patch.py','materialize.py','place.py','render.py','export_blender.py'):
        shutil.copy2(ROOT/'tools/UnrealSpatialTwinMCP'/name,tools/name)
    scripts=runtime/'scripts';scripts.mkdir()
    for name in ('unreal_mcp.py','jev_review.py','Link-SpatialTwinPlugin.ps1'):shutil.copy2(ROOT/'scripts'/name,scripts/name)
    (runtime/'Unreal').mkdir()
    shutil.copy2(ROOT/'Unreal/agent_snapshot_tools.py',runtime/'Unreal/agent_snapshot_tools.py')
    checks=destination/'checks';checks.mkdir()
    for name in ('release_checks.py','native_checks.py'):
        shutil.copy2(ROOT/'tools/UnrealSpatialTwinMCP'/name,checks/name)
    marketplace=destination/'.agents/plugins';marketplace.mkdir(parents=True)
    (marketplace/'marketplace.json').write_text(json.dumps({'name':'unreal-spatial-twin-source','interface':{'displayName':'Unreal Spatial Twin source'},'plugins':[{'name':'unreal-spatial-twin','source':{'source':'local','path':'./SpatialTwinCodexPlugin'},'policy':{'installation':'AVAILABLE','authentication':'ON_INSTALL'},'category':'Developer Tools'}]},indent=2)+'\n',encoding='utf-8')
    (destination/'INSTALL.txt').write_text('''Unreal Spatial Twin — source distribution
Supported native target: Unreal Engine5.8 / Win64. Other targets need a tested native adapter.

1. Copy UnrealSpatialTwin into <YourProject>/Plugins and enable it; compile with
   your Unreal toolchain. Enable the official ModelContextProtocol toolsets.
2. Install Python3.11+ dependencies from
   SpatialTwinCodexPlugin/runtime/tools/UnrealSpatialTwinMCP/requirements.txt
   into your chosen environment. Set SPATIAL_TWIN_PYTHON to that Python executable.
3. Install SpatialTwinCodexPlugin in Codex. Open your game workspace, or set
   SPATIAL_TWIN_PROJECT to its .uproject. Ambiguous projects require explicit choice.
4. For native spatial_twin_apply_patch dispatch, launch Unreal with
   SPATIAL_TWIN_HOME pointing to SpatialTwinCodexPlugin/runtime and the same
   SPATIAL_TWIN_PYTHON. The explicit action CLI works without these native settings.
5. Run the initial scan once through the official SpatialTwinToolset. Headless:
   UnrealEditor-Cmd <YourProject.uproject> -run=SpatialTwinScan -Map=/Game/YourMap
   Data defaults to <YourProject>/Saved/SpatialTwin. Further edits synchronize
   incrementally. Query world_status before acting; respect missing/stale coverage.

No game content, API credentials, native Unreal binaries, or second live MCP
server are bundled. Jev is optional and requires your own TYPESAFE_API_KEY;
known queries, geometry and deterministic checks do not require a model API.
''',encoding='utf-8')
    files={p.relative_to(destination).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(destination.rglob('*')) if p.is_file()}
    (destination/'manifest.json').write_text(json.dumps({'format':1,'native_target':'UE5.8 Win64','files':files},indent=2)+'\n',encoding='utf-8')
    return {'path':str(destination),'files':len(files),'game_content_included':False}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',required=True,type=Path)
    print(json.dumps(package(parser.parse_args().output)))
