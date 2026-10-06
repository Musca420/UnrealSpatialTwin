"""Official MCP constants/batching helpers; no game-specific runner."""
import json
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
import apply_patch
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/"scripts"))
from unreal_mcp import EDITOR,PROGRAMMATIC
SCENE=apply_patch.SCENE;ACTOR=apply_patch.ACTOR;OBJECT=apply_patch.OBJECT
ASSET="editor_toolset.toolsets.asset.AssetTools"
MESH="editor_toolset.toolsets.static_mesh.StaticMeshTools"
def data(result):
    if result.isError:raise RuntimeError(str(result.content))
    return result.structuredContent or json.loads('\n'.join(c.text for c in result.content if c.type=='text'))
def program(body,values):
    body='\n'.join("    return {'value': "+line.removeprefix('    return ')+'}' if line.startswith('    return ') else line for line in body.splitlines())+'\n'
    return 'import json\nDATA='+repr(values)+'\n' + "def call(t,n,a):\n    return execute_tool(t+'.'+n,json.dumps(a))['returnValue']\n" + 'def run():\n'+body
