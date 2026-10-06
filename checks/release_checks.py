"""Run the shipped native fixture matrix in new, isolated projects, serially.

Use the normal desktop session. Never point --output at an existing directory.
The active game editor stays untouched; native test projects disable live MCP.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import time


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(args):
    output=args.output.resolve();native=args.native_plugin.resolve()
    assert not output.exists(), 'Use a new output directory'
    assert (native/'Binaries/Win64/UnrealEditor.modules').is_file()
    assert args.fbx.is_file()
    output.mkdir(parents=True)
    report={'state':'RUNNING','native_files':{str(p.relative_to(native)):sha(p) for p in native.rglob('*') if p.is_file() and p.suffix in ('.dll','.modules','.cpp','.h','.cs','.sql')},'rows':[]}
    def record():
        (output/'qualification.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    def project(name):
        base=output/name;assert not base.exists();base.mkdir()
        (base/'SpatialTwinFixture.uproject').write_text(json.dumps({'FileVersion':3,'EngineAssociation':'5.8','Plugins':[{'Name':n,'Enabled':True} for n in ('UnrealSpatialTwin','ModelContextProtocol','AllToolsets','PythonScriptPlugin')]}))
        config=base/'Config';config.mkdir()
        (config/'DefaultGame.ini').write_text('[/Script/Engine.AssetManagerSettings]\n+PrimaryAssetTypesToScan=(PrimaryAssetType="GameFeatureData",AssetBaseClass="/Script/GameFeatures.GameFeatureData",bHasBlueprintClasses=False,bIsEditorOnly=False,Rules=(CookRule=AlwaysCook))\n')
        (config/'DefaultEngine.ini').write_text('[/Script/PythonScriptPlugin.PythonScriptPluginSettings]\nbRemoteExecution=False\n')
        (config/'DefaultEditorPerProjectUserSettings.ini').write_text('[/Script/ModelContextProtocolEngine.ModelContextProtocolSettings]\nbAutoStartServer=False\nServerPortNumber=8016\n')
        plugin=base/'Plugins/UnrealSpatialTwin';plugin.mkdir(parents=True)
        shutil.copy2(native/'UnrealSpatialTwin.uplugin',plugin)
        shutil.copytree(native/'Resources',plugin/'Resources')
        binary=plugin/'Binaries/Win64';binary.mkdir(parents=True)
        for p in (native/'Binaries/Win64').iterdir():
            if p.suffix in ('.dll','.modules'):shutil.copy2(p,binary/p.name)
        return base
    def phase(base,flags,mode='SpatialTwinTest',accept=('PASS',),extra=()):
        name=str(len(report['rows']))+'-'+(flags.replace(' ','_') or 'Storage')
        logs=output/'logs'/name;logs.mkdir(parents=True)
        before={str(p):p.stat().st_mtime_ns for p in (base/'Saved').rglob('*.json')}
        argv=[str(base/'SpatialTwinFixture.uproject'),'-run='+mode,*['-'+f for f in flags.split()],*extra,'-unattended','-nop4','-nosplash','-NullRHI','-NoSound','-ddc=InstalledNoZenLocalFallback','-DDC-ForceMemoryCache','-abslog='+str(logs/'engine.log')]
        def quote(v):return "'"+str(v).replace("'","''")+"'"
        cmd='$owned=Start-Process -FilePath '+quote(args.engine/'Binaries/Win64/UnrealEditor-Cmd.exe')+' -ArgumentList '+quote(subprocess.list2cmdline(argv))+' -WindowStyle Hidden -RedirectStandardOutput '+quote(logs/'stdout.log')+' -RedirectStandardError '+quote(logs/'stderr.log')+' -PassThru; $null=$owned.Handle; $owned.Id; $owned.WaitForExit(); $owned.ExitCode'
        started=time.perf_counter();p=subprocess.Popen(['powershell.exe','-NoProfile','-Command',cmd],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        pid=int(p.stdout.readline().strip());row={'flags':flags,'mode':mode,'project':str(base),'pid':pid,'state':'RUNNING','logs':str(logs)};report['rows'].append(row);record();print(json.dumps(row),flush=True)
        stdout,stderr=p.communicate(timeout=900) # no force kill or replay on timeout
        assert p.returncode==0,(stdout,stderr)
        code=int(stdout.strip());proofs={str(q.relative_to(base)):json.loads(q.read_text(encoding='utf-8-sig')) for q in (base/'Saved').rglob('*.json') if q.stat().st_mtime_ns!=before.get(str(q)) and (q.name.startswith('native-') or q.name.endswith('-probe.json') or q.name.endswith('-baseline.json'))}
        log=(logs/'engine.log').read_text(encoding='utf-8-sig',errors='replace')
        errors=[line for line in log.splitlines() if re.search(r'(?:Error:|Error Summary.*[1-9])',line)]
        warnings=[line for line in log.splitlines() if 'Warning:' in line]
        checked=any(q.get('state') in accept for q in proofs.values())
        if mode=='SpatialTwinNavigationTest':checked=bool(proofs) and all(q.get('passed',True) is not False for q in proofs.values())
        row.update(state='PASS' if code==0 and checked and not errors else 'FAILED',exit_code=code,seconds=time.perf_counter()-started,proofs=proofs,error_lines=errors,warning_lines=warnings)
        record();print(json.dumps({'flags':flags,'state':row['state'],'seconds':row['seconds'],'errors':len(errors),'warnings':len(warnings)}),flush=True)
        return row['state']=='PASS'
    record()
    storage=project('Storage')
    if not phase(storage,'PartitionFixture'):
        report['state']='FAILED_SETUP';record();return report
    # The broad fixture deliberately leaves an unsaved material. Resume the
    # ordinary streaming fixture first so its saved baseline is reconciled;
    # the cold single-owner test must not inherit that unrelated dirty source.
    if not phase(storage,'StreamingFixture'):
        report['state']='FAILED_SETUP';record();return report
    pristine=output/'PristineContent';shutil.copytree(storage/'Content',pristine)
    phase(storage,'ResumeFixture')
    for name in ('InstanceFixture','DataLayerFixture','AssetSwapFixture','EmptyInputFixture','AuthoredNavFixture','TransientFixture','PersistedMaterialFixture','NestedFixture'):
        base=project(name);shutil.copytree(pristine,base/'Content')
        extra=('-SpatialTwinSource='+str(args.fbx.resolve()),) if name=='AuthoredNavFixture' else ()
        if name=='TransientFixture':extra=('-TransientMap=/Game/SpatialTwinTransientPublication',)
        accepted={'AssetSwapFixture':('MEASURED',),'EmptyInputFixture':('PASS_NATIVE_PROBE',),'AuthoredNavFixture':('PASS_NATIVE_EXPORT',),'TransientFixture':('PREPARED',),'PersistedMaterialFixture':('PREPARED',)}.get(name,('PASS',))
        if not phase(base,name,accept=accepted,extra=extra):continue
        if name=='TransientFixture':phase(base,'TransientFixture TransientResume',extra=extra)
        if name=='PersistedMaterialFixture':phase(base,'PersistedMaterialFixture PersistedMaterialResume')
        if name=='NestedFixture':
            for flags in ('NestedSourceFixture','NestedSourceFixture NestedSourceVerify','NestedSourceFixture NestedSourceDelete','NestedSourceFixture NestedSourceDelete NestedSourceVerify'):
                if not phase(base,flags):break
    for flag in ('LinkFixture','ProjectedLinkFixture','FillFixture','MaskFixture','NavIdleFixture','SlopeFixture','CapacityFixture','LinkFixture LegacyRasterFixture'):
        base=project('Nav_'+flag.replace(' ','_'))
        phase(base,flag,'SpatialTwinNavigationTest',extra=('-Map=/Engine/Maps/Entry','-Position=0,0,0','-SpatialTwinRoot='+str(base/'Saved/SpatialTwin')))
    report['state']='PASS' if all(r['state']=='PASS' for r in report['rows']) else 'FAILED'
    record();return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--engine',type=Path,required=True);p.add_argument('--native-plugin',type=Path,required=True)
    p.add_argument('--fbx',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    report=run(p.parse_args());print(json.dumps({'state':report['state'],'phases':len(report['rows'])}),flush=True)
    raise SystemExit(0 if report['state']=='PASS' else 1)
