import asyncio,json,os,sqlite3,sys,time,hashlib
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
import argparse
p=argparse.ArgumentParser(description='Real packaged MCP schema/query/Shadow and rejection checks; no Unreal actions')
p.add_argument('--plugin',type=Path,required=True);p.add_argument('--project',type=Path,required=True);p.add_argument('--root',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--navigation-root',type=Path)
a=p.parse_args();plugin=a.plugin.resolve();project=a.project.resolve();base=project.parent;root=a.root.resolve();output=a.output.resolve();assert not output.exists();output.parent.mkdir(parents=True,exist_ok=True)
with sqlite3.connect((root/'world.sqlite').as_uri()+'?mode=ro',uri=True) as db:
 actor=json.loads(db.execute("SELECT source FROM entities WHERE kind='Actor' AND label='GenericPartitionCube'").fetchone()[0]);mesh=json.loads(db.execute("SELECT source FROM entities WHERE id='asset:/Engine/BasicShapes/Cube.Cube'").fetchone()[0]);revision=int(db.execute("SELECT value FROM metadata WHERE key='world_revision'").fetchone()[0]);map_id=db.execute("SELECT map FROM snapshots WHERE state='READY' ORDER BY revision DESC LIMIT 1").fetchone()[0]
report={'state':'RUNNING','plugin':str(plugin),'root':str(root),'revision':revision,'rows':[]}
def record():output.write_text(json.dumps(report,indent=2)+'\n')
async def main():
 params=StdioServerParameters(command=sys.executable,args=[str(plugin/'server.py'),'--project',str(project),'--root',str(root),'--map',map_id,'--tool-profile','full'],cwd=str(base),env={**os.environ,'SPATIAL_TWIN_PYTHON':sys.executable})
 async with stdio_client(params) as (read,write):
  async with ClientSession(read,write) as s:
   await s.initialize();names=[t.name for t in (await s.list_tools()).tools];report['advertised_tools']=names;record()
   async def call(name,args,negative=False):
    start=time.perf_counter();result=await s.call_tool(name,args);text='\n'.join(x.text for x in result.content if x.type=='text');data=result.structuredContent or (json.loads(text) if not result.isError else {'error':text})
    row={'tool':name,'arguments':args,'expected':'REJECTED' if negative else 'SUCCESS','is_error':bool(result.isError),'seconds':time.perf_counter()-start,'bytes':len(text.encode()),'data':data};report['rows'].append(row);record()
    assert bool(result.isError)==negative,row
    return data
   schemas=[]
   for i in range(0,len(names),8):
    data=await call('tool_describe',{'names':names[i:i+8]});schemas.extend(t['name'] for t in data['tools'])
   assert sorted(schemas)==sorted(names)
   report['all_advertised_schemas_returned']=True
   p=actor['transform']['position'];above=[p[0],p[1],p[2]+1000]
   cases=[('world_status',{'include_counts':False}),('world_maps',{}),('world_search',{'text':'GenericPartitionCube','kind':'Actor','limit':1,'fields':['label','transform','bounds']}),('world_region',{'center':p,'radius':500,'detail':'summary'}),('entity_get',{'entity_id':actor['id'],'fields':['label','transform','bounds']}),('entity_children',{'entity_id':actor['id'],'limit':2,'fields':['asset_id','transform']}),('entity_relationships',{'entity_id':actor['id'],'limit':5}),('spatial_nearest',{'position':p,'k':1,'kind':'Actor'}),('spatial_overlap',{'aabb':[[p[0]-100,p[1]-100,p[2]-100],[p[0]+100,p[1]+100,p[2]+100]],'limit':3}),('spatial_raycast',{'origin':above,'direction':[0,0,-1],'max_distance':1200}),('spatial_raycast_batch',{'rays':[{'origin':above,'direction':[0,0,-1],'max_distance':1200}],'detail':'hits'}),('spatial_support',{'candidates':[[p[0],p[1],p[2]+100]],'offsets':[[0,0]],'max_distance':200}),('asset_get',{'asset_id':mesh['id'],'include_dependencies':True}),('asset_usage',{'asset_id':mesh['id'],'detail':'summary','kind':'Actor'}),('geometry_get',{'geometry_hash':mesh['geometry_hash']}),('world_changes',{'since_revision':0,'limit':5}),('world_diff',{'revision_a':0,'revision_b':revision,'limit':5}),('navigation_status',{})]
   for name,args in cases:await call(name,args)
   guided=await call('world_query',{'request':'Read current status','queries':[{'tool':'world_status','arguments':{'include_counts':False}}]})
   assert guided['state']=='EXECUTED' and guided['decision']['provider']=='deterministic' and guided['model_usage']=={'input_tokens':0,'output_tokens':0}
   await call('world_read',{'queries':[{'tool':name,'arguments':args} for name,args in cases[:8]],'schemas':['patch_prepare','patch_ground','patch_place','asset_stage']})
   op={'type':'CREATE_ACTOR','target':'temp:package489','class':'/Script/Engine.StaticMeshActor','asset':mesh['id'],'label':'Package489_ShadowOnly','transform':{'position':[10000,10000,1000],'rotation':[0,0,0,1],'scale':[.2,.2,.2]},'component_properties':{'bCanEverAffectNavigation':False}}
   draft=await call('patch_create',{'operations':[op],'base_revision':revision});patch=draft['id']
   await call('patch_update',{'patch_id':patch,'operations':[op]});checked=await call('patch_validate',{'patch_id':patch});assert checked['status']=='VALIDATED',checked
   await call('patch_preview',{'patch_id':patch,'detail':'operations','limit':1});await call('patch_status',{'patch_id':patch});await call('patch_prepare',{'patch_id':patch,'operations':[op],'base_revision':revision})
   for name,args in [('spatial_raycast',{'origin':[0,0,0],'direction':[0,0,0],'max_distance':100}),('world_search',{'limit':1000}),('world_read',{'queries':[{'tool':'patch_create','arguments':{'operations':[op]}}]}),('shadow_plan',{'tool':'patch_prepare','arguments':{'operations':[op],'base_revision':revision},'map_id':'/WrongMap'}),('patch_prepare',{'operations':[op],'base_revision':revision-1}),('patch_continue',{'patch_id':patch}),('asset_stage',{'manifest_file':str(root/'absent-manifest.json'),'template_asset_id':mesh['id']})]:await call(name,args,True)
   final=await call('world_status',{'include_counts':False});assert final['canonical_revision']==revision and not final['editor_connected'],final
   await call('patch_ground',{'entity_ids':[actor['id']],'base_revision':revision-1,'max_distance':100},True)
   await call('patch_place',{'candidates':[above],'asset_id':mesh['id'],'actor_class':'/Script/Engine.StaticMeshActor','base_revision':revision-1,'max_distance':1200,'label_prefix':'Package_Stale_'},True)
   report.update(state='PASS_PACKAGED_FULL_MCP_READ_SHADOW_AND_REJECTIONS',canonical_unchanged=True,qualified_queries=sorted({x['tool'] for x in report['rows']}),calls=len(report['rows']),optional_jev_api='NOT_INVOKED_NO_REQUIRED_DEPENDENCY',navigation_paths='Separate native-navigation stores/parity qualification',live_execution='Separate fresh official-MCP apply/save/render cycle')
   record();print(json.dumps({k:v for k,v in report.items() if k not in ['rows','advertised_tools']}))
async def nav_calls():
 if not a.navigation_root:return
 report['state']='RUNNING_NAVIGATION';record()
 navroot=a.navigation_root.resolve();native=json.loads((navroot/'native-navigation-test.json').read_text());sample=native['native_project_point'][0];assert sample['found'] and sample['reachable']
 navproject=navroot.parents[1]/project.name;assert navproject.is_file()
 params=StdioServerParameters(command=sys.executable,args=[str(plugin/'server.py'),'--project',str(navproject),'--root',str(navroot),'--tool-profile','full'],cwd=str(navproject.parent),env={**os.environ,'SPATIAL_TWIN_PYTHON':sys.executable})
 async with stdio_client(params) as (read,write):
  async with ClientSession(read,write) as s:
   await s.initialize()
   start,end,agent=sample['position'],sample['projected_end'],sample['agent']
   for name,args in [('navigation_path',{'start':start,'end':end,'agent':agent}),('reachable',{'start':start,'end':end,'agent':agent}),('is_navigable',{'position':start,'agent':agent}),('nearest_navigable_position',{'position':start,'agent':agent})]:
    begin=time.perf_counter();result=await s.call_tool(name,args);assert not result.isError,result
    data=result.structuredContent or json.loads(result.content[0].text);assert data['state']=='READY',data
    if name in ('navigation_path','reachable'):assert data['reachable']
    report['rows'].append({'tool':name,'arguments':args,'expected':'SUCCESS','is_error':False,'data':data,'seconds':time.perf_counter()-begin,'scope':'independent native navigation fixture'})
 report.update(state='PASS_PACKAGED_FULL_MCP_READ_SHADOW_AND_REJECTIONS',calls=len(report['rows']),navigation_paths='PASS_REAL_PACKAGED_MCP_NATIVE_SCANNED_NAVIGATION',qualified_queries=sorted({x['tool'] for x in report['rows']}));record()
asyncio.run(main());asyncio.run(nav_calls())
print(json.dumps({'state':report['state'],'calls':report['calls'],'schemas':len(report['advertised_tools']),'navigation':report['navigation_paths']}))
