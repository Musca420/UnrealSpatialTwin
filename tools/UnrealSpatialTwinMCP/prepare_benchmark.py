"""Create a new isolated benchmark project; never overwrite an existing folder."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

def prepare(folder,plugin):
    folder=Path(folder).resolve();plugin=Path(plugin).resolve()
    if folder.exists():raise ValueError('Use a new benchmark directory')
    folder.mkdir(parents=True);target=folder/'Plugins/UnrealSpatialTwin';target.mkdir(parents=True)
    for name in ('Binaries','Resources'):shutil.copytree(plugin/name,target/name)
    shutil.copy2(plugin/'UnrealSpatialTwin.uplugin',target/'UnrealSpatialTwin.uplugin')
    project=folder/'SpatialTwinBench.uproject'
    project.write_text(json.dumps(dict(FileVersion=3,EngineAssociation='5.8',Plugins=[dict(Name=n,Enabled=True) for n in ('UnrealSpatialTwin','ModelContextProtocol','AllToolsets','PythonScriptPlugin','EditorScriptingUtilities')]),indent=2))
    config=folder/'Config';config.mkdir()
    (config/'DefaultEditorPerProjectUserSettings.ini').write_text('[/Script/ModelContextProtocolEngine.ModelContextProtocolSettings]\nServerPortNumber=8013\nbAutoStartServer=True\n')
    (config/'DefaultGame.ini').write_text('[/Script/Engine.AssetManagerSettings]\n+PrimaryAssetTypesToScan=(PrimaryAssetType="GameFeatureData",AssetBaseClass="/Script/GameFeatures.GameFeatureData",bHasBlueprintClasses=False,bIsEditorOnly=False,Rules=(CookRule=AlwaysCook))\n')
    spec=dict(fixture_format=1,project=str(project),map='/Game/Benchmark/Base',url='http://127.0.0.1:8013/mcp',
              template_asset='/Engine/BasicShapes/Cube.Cube',
              scope='Technical spatial authoring; no game lore/content and no whole-game navigation or gameplay claim',
              scenarios={
                  'batch':dict(kind='move',label_prefix='Bench_Move_',candidates=[[i%8*260,i//8*260,200] for i in range(32)],offsets=[[x,y] for x in (-45,0,45) for y in (-45,0,45)],height_offset=50,scale=[1,1,1],bounds=[[-50,-50,-50],[50,50,50]]),
                  'reuse':dict(kind='create',candidates=[[i*210,-1700,300] for i in range(12)],offsets=[[x,y] for x in range(-45,46,5) for y in (-15,0,15)],height_offset=50,scale=[1,1,1],bounds=[[-50,-50,-50],[50,50,50]]),
                  'author':dict(kind='author',candidates=[[i*210,1700,300] for i in range(12)],offsets=[[x,y] for left in (-76,60) for x in range(left,left+17,2) for y in range(-9,10,2)],height_offset=0,scale=[1,1,1],bounds=[[-76,-9,0],[76,9,100]])},
              cameras={name:dict(location=dict(x=1100,y=y-2400,z=1800),rotation=dict(pitch=-35,yaw=90,roll=0),scale=dict(x=1,y=1,z=1)) for name,y in [('batch',400),('reuse',-1700),('author',1700)]},
              plugin_sha256=hashlib.sha256((target/'Binaries/Win64/UnrealEditor-UnrealSpatialTwin.dll').read_bytes()).hexdigest())
    # Reused blocks have a 100x100 footprint; query the whole footprint equally.
    spec['scenarios']['reuse']['offsets']=[[x,y] for x in range(-45,46,5) for y in range(-45,46,5)]
    (folder/'spec.json').write_text(json.dumps(spec,indent=2)+'\n')
    return dict(project=str(project),spec=str(folder/'spec.json'),plugin_sha256=spec['plugin_sha256'])

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);p.add_argument('--plugin',type=Path,required=True);a=p.parse_args()
    print(json.dumps(prepare(a.output,a.plugin)))
