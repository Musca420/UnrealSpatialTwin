"""Project-neutral launcher; never choose an arbitrary project or live MCP."""
import os
import argparse
from pathlib import Path
import subprocess
import sys


def project_path(directory,explicit=None):
    if explicit:
        project=Path(explicit).resolve()
        if project.is_file() and project.suffix.lower()=='.uproject':return project
        raise ValueError('SPATIAL_TWIN_PROJECT must name an existing .uproject')
    # Bounded workspace discovery, never a recursive disk scan or a saved user path.
    directory=Path(directory).resolve()
    for folder in (directory,*directory.parents):
        direct=list(folder.glob('*.uproject'))
        if direct:
            if len(direct)==1:return direct[0]
            raise ValueError('Multiple Unreal projects; set SPATIAL_TWIN_PROJECT explicitly')
        if folder==directory:
            nested=sorted(set(folder.glob('*/*.uproject'))|set(folder.glob('*/*/*.uproject')))
            if len(nested)==1:return nested[0]
            if len(nested)>1:raise ValueError('Multiple Unreal projects; set SPATIAL_TWIN_PROJECT explicitly')
    raise ValueError('No Unreal project in this workspace; set SPATIAL_TWIN_PROJECT')


def main():
    try:
        parser=argparse.ArgumentParser(add_help=False)
        parser.add_argument('--project')
        arguments,remaining=parser.parse_known_args()
        project=project_path(Path.cwd(),arguments.project or os.environ.get('SPATIAL_TWIN_PROJECT'))
        plugin=Path(__file__).resolve().parent
        home=Path(os.environ.get('SPATIAL_TWIN_HOME',plugin/'runtime' if (plugin/'runtime').is_dir() else plugin.parent)).resolve()
        server=home/'tools/UnrealSpatialTwinMCP/server.py'
        bundled=home/'Build/MCP/venv'/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
        python=Path(os.environ.get('SPATIAL_TWIN_PYTHON',str(bundled if bundled.is_file() else sys.executable)))
        if not server.is_file() or not python.is_file():raise ValueError('Install the Spatial Twin toolkit/dependencies; see docs/Installation.md')
        return subprocess.call([str(python),str(server),'--project',str(project),'--tool-profile','focused',*remaining])
    except ValueError as error:
        print(str(error),file=sys.stderr);return 1


if __name__=='__main__':sys.exit(main())
