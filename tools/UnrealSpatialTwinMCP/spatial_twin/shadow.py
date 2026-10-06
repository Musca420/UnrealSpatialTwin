"""Patch overlays never mutate canonical entities or spatial indexes."""
import copy
import json
import hashlib
from pathlib import Path
import time
from .store import Store,encode,vector
from .query import Queries
from . import geometry as g
from . import require_current_runtime,native_library_path
from .verification import verification_state


IDENTITY={'position':[0.,0.,0.],'rotation':[0.,0.,0.,1.],'scale':[1.,1.,1.]}


def move_spatial_data(e,old,new):
    def mapped(point):return g.point(new,g.local(old,point))
    if e.get('collision_local_bounds'):
        e['collision_bounds']=g.transform_bounds(e['collision_local_bounds'],new)
    elif e.get('collision_bounds'):
        lo,hi=e['collision_bounds'];points=[mapped([x,y,z]) for x in (lo[0],hi[0]) for y in (lo[1],hi[1]) for z in (lo[2],hi[2])]
        e['collision_bounds']=[[min(p[i] for p in points) for i in range(3)],[max(p[i] for p in points) for i in range(3)]]
    delta=qmul(new['rotation'],[-old['rotation'][0],-old['rotation'][1],-old['rotation'][2],old['rotation'][3]])
    if e.get('collision_transform'):
        t=e['collision_transform'];t['position']=mapped(t['position']);t['rotation']=qmul(delta,t['rotation'])
        t['scale']=[t['scale'][i]*new['scale'][i]/old['scale'][i] for i in range(3)]
    for point in e.get('spline_points',[]):
        point['position']=mapped(point['position'])
        if point.get('tangent'):point['tangent']=g.sub(mapped(g.add(old['position'],point['tangent'])),new['position'])
    for link in e.get('navigation_links',[]):
        link['start']=mapped(link['start']);link['end']=mapped(link['end'])
    for modifier in e.get('navigation_modifiers',[]):
        lo,hi=modifier['bounds'];corners=[mapped([x,y,z]) for x in (lo[0],hi[0]) for y in (lo[1],hi[1]) for z in (lo[2],hi[2])]
        modifier['bounds']=[[min(p[i] for p in corners) for i in range(3)],[max(p[i] for p in corners) for i in range(3)]]
        if modifier.get('origin'):modifier['origin']=mapped(modifier['origin'])
        if modifier.get('points'):modifier['points']=[mapped(p) for p in modifier['points']]
        if modifier.get('shape') in (1,2) and abs(delta[0])+abs(delta[1])+abs(delta[2])>1e-8:
            e['navigation_preview_error']='Rotated area shape requires native modifier regeneration'
        if modifier.get('shape')==3:
            ratios=[new['scale'][i]/old['scale'][i] for i in range(3)]
            # Native convex modifiers contain a 2D footprint extruded along
            # world Z, not the original 3D brush. Tilting/shearing that prism
            # cannot reproduce native regeneration from the authored brush.
            if abs(delta[0])+abs(delta[1])>1e-8 or (abs(old['rotation'][0])+abs(old['rotation'][1])>1e-8 and max(ratios)-min(ratios)>1e-8):
                e['navigation_preview_error']='Tilted or sheared convex area requires native modifier regeneration'
        if modifier.get('radius') is not None:modifier['radius']*=max(abs(new['scale'][i]/old['scale'][i]) for i in (0,1))


def schema_type(value,kind):
    if kind in ('float','double','int','int32','int64'): return type(value) in (float,int) and (kind in ('float','double') or type(value) is int)
    if kind=='bool': return type(value) is bool
    if kind in ('str','string','name','text','object'): return isinstance(value,str) or value is None
    return False


def mesh_component(q,actor_id,component_id=None):
    ids={r[0] for r in q.db.execute('SELECT id FROM entities WHERE parent_id=?',(actor_id,))}
    ids.update(e['id'] for e in q.overlay.values() if e and e.get('parent_id')==actor_id)
    candidates=[q.entity(i) for i in ids if i not in q.overlay or q.overlay[i] is not None]
    candidates=[e for e in candidates if e.get('asset_id') and (not component_id or e['id']==component_id)]
    if len(candidates)!=1:raise ValueError('Choose one static mesh component_id for this binding')
    return candidates[0]


def aggregate_bounds(entity,components):
    for field,local_field in (('bounds','local_bounds'),('collision_bounds','collision_local_bounds')):
        boxes=[c[field] for c in components if c.get(field)]
        if not boxes:
            entity.pop(field,None);entity.pop(local_field,None);continue
        lo=[min(b[0][i] for b in boxes) for i in range(3)];hi=[max(b[1][i] for b in boxes) for i in range(3)]
        entity[field]=[lo,hi]
        points=[g.local(entity['transform'],[x,y,z]) for x in (lo[0],hi[0]) for y in (lo[1],hi[1]) for z in (lo[2],hi[2])]
        entity[local_field]=[[min(p[i] for p in points) for i in range(3)],[max(p[i] for p in points) for i in range(3)]]


def preview(store,db,patch,operation_transforms=None,operation_effects=None):
    require_current_runtime()
    if patch['base_revision']!=Store.revision(db): raise ValueError('CONFLICTED: canonical revision changed')
    overlay={}; errors=[]; q=Queries(store,db,overlay); touched=set(); instance_groups={}
    def get(ident):
        touched.add(ident)
        if ident not in overlay: overlay[ident]=copy.deepcopy(Store.entity(db,ident))
        if overlay[ident] is None: raise ValueError('Entity deleted earlier in patch')
        return overlay[ident]
    def children(ident):
        return [r[0] for r in db.execute('SELECT id FROM entities WHERE parent_id=?',(ident,))]+[e['id'] for e in overlay.values() if e and e.get('parent_id')==ident]
    def instance_bounds(component):
        if component not in instance_groups:
            # One bounded owner query per preview, not full source decoding and
            # thousands of individual SELECTs for every edited instance.
            instance_groups[component]=[{key:json.loads(value) if value is not None else None for key,value in zip(('bounds','collision_bounds'),row[1:])} | {'id':row[0]}
                for row in db.execute("SELECT id,json_extract(source,'$.bounds'),json_extract(source,'$.collision_bounds') FROM entities WHERE parent_id=? AND kind='Instance'",(component,))]
        return [overlay.get(e['id'],e) for e in instance_groups[component] if overlay.get(e['id'],e) is not None]
    def move_contents(ident,old,new):
        attached=[r[0] for r in db.execute("SELECT source FROM relationships WHERE target=? AND kind='ATTACHED_TO'",(ident,))]
        attached.extend(e['id'] for e in overlay.values() if e and e.get('attached_to')==ident)
        for child in set(children(ident)+attached):
            e=get(child)
            if e['kind']=='Actor' and e.get('attached_to')!=ident: continue
            if not e.get('transform'): continue
            previous=copy.deepcopy(e['transform']); t=e['transform']
            t['position']=g.point(new,g.local(old,t['position']))
            # Preserve full child orientation/scale by quaternion composition.
            delta=qmul(new['rotation'],[-old['rotation'][0],-old['rotation'][1],-old['rotation'][2],old['rotation'][3]])
            t['rotation']=qmul(delta,t['rotation'])
            t['scale']=[t['scale'][i]*new['scale'][i]/old['scale'][i] for i in range(3)]
            move_spatial_data(e,previous,t)
            if e.get('local_bounds'): e['bounds']=g.transform_bounds(e['local_bounds'],t)
            elif e.get('bounds'):
                e['local_bounds']=g.transform_bounds(e['bounds'],{'position':g.local(old,[0,0,0]),'rotation':[-old['rotation'][0],-old['rotation'][1],-old['rotation'][2],old['rotation'][3]],'scale':[1/v for v in old['scale']]})
                e['bounds']=g.transform_bounds(e['local_bounds'],new)
            move_contents(child,previous,t)
    for op in patch['operations']:
        touched.clear()
        try:
            kind=op['type']; ident=op['target']
            if kind=='CREATE_ACTOR':
                if ident in overlay or db.execute('SELECT 1 FROM entities WHERE id=?',(ident,)).fetchone(): raise ValueError('Create ID already exists')
                row=db.execute('SELECT source FROM class_schemas WHERE class=?',(op.get('class'),)).fetchone()
                if not row: raise ValueError('Class not exported')
                s=json.loads(row[0]); prototype=s.get('prototype')
                asset=q.entity(op['asset']) if op.get('asset') else None
                if not prototype and op['class']=='/Script/Engine.StaticMeshActor' and asset and asset['kind']=='StaticMesh':
                    prototype={'local_bounds':asset['local_bounds'],'transform':copy.deepcopy(IDENTITY),'coverage':'derived_asset_template'}
                if not prototype: raise ValueError('Class needs a native prototype export before spatial simulation')
                e=copy.deepcopy(prototype); e.update(id=ident,kind='Actor',class_=op['class'],label=op.get('label',ident))
                e['class']=e.pop('class_'); e['transform']=copy.deepcopy(op.get('transform',IDENTITY))
                overlay[ident]=e
                touched.add(ident)
                if e.get('local_bounds'): e['bounds']=g.transform_bounds(e['local_bounds'],e['transform'])
                if asset and asset['kind']=='StaticMesh':
                    c={'id':ident+':component:StaticMeshComponent','kind':'Component','parent_id':ident,'actor_id':ident,'asset_id':asset['id'],
                       'class':'/Script/Engine.StaticMeshComponent','label':'StaticMeshComponent','transform':copy.deepcopy(e['transform']),
                       'local_bounds':copy.deepcopy(asset['local_bounds']),'bounds':copy.deepcopy(e['bounds']),
                       'affects_navigation':asset.get('native_spawn_affects_navigation',True),
                       'collision':copy.deepcopy(asset.get('native_spawn_collision',{}))}
                    if not c['collision']:raise ValueError('Native spawn collision profile not exported')
                    c['affects_navigation']=op.get('component_properties',{}).get('bCanEverAffectNavigation',c['affects_navigation'])
                    bind_asset(c,asset)
                    for key in ('navigation_fill_underneath','navigation_filled_convex'):
                        if key in asset.get('native_spawn_navigation',{}):c[key]=asset['native_spawn_navigation'][key]
                    overlay[c['id']]=c;touched.add(c['id'])
            elif kind=='SET_INSTANCE_TRANSFORM':
                e=get(ident)
                if e['kind']!='Instance':raise ValueError('Instance operation requires a persistent Instance target')
                c=get(e['component_id']);a=get(e['actor_id'])
                if not c.get('collision',{}).get('aggregate_only'):raise ValueError('Instance parent is not an exported instanced mesh')
                old=copy.deepcopy(e['transform']);new=copy.deepcopy(op['transform']);e['transform']=new
                e['bounds']=g.transform_bounds(e['local_bounds'],new);move_spatial_data(e,old,new)
                parent=c['transform'];pq=parent['rotation']
                e['instance_transform']={'position':g.local(parent,new['position']),
                    'rotation':qmul([-pq[0],-pq[1],-pq[2],pq[3]],new['rotation']),
                    'scale':[new['scale'][i]/parent['scale'][i] for i in range(3)]}
                instances=instance_bounds(c['id'])
                if len(instances)!=c.get('instance_count'):raise ValueError('Instanced mesh coverage incomplete')
                aggregate_bounds(c,instances)
                aggregate_bounds(a,[q.entity(child) for child in set(children(a['id'])) if q.entity(child)['kind']=='Component'])
                if operation_transforms is not None:operation_transforms[op['operation_id']]={'before':old,'after':copy.deepcopy(new)}
            else:
                e=get(ident)
                if e['kind']!='Actor': raise ValueError('Actor operation requires Actor target')
                if kind=='DELETE_ACTOR':
                    pending=[ident]
                    while pending:
                        target=pending.pop(); pending.extend(children(target)); overlay[target]=None;touched.add(target)
                elif kind in ('MOVE_ACTOR','ROTATE_ACTOR','SCALE_ACTOR'):
                    old=copy.deepcopy(e['transform']); key={'MOVE_ACTOR':'position','ROTATE_ACTOR':'rotation','SCALE_ACTOR':'scale'}[kind]
                    e['transform'][key]=op['value']
                    if any(abs(x)<1e-20 for x in e['transform']['scale']): raise ValueError('Singular scale')
                    if e.get('local_bounds'): e['bounds']=g.transform_bounds(e['local_bounds'],e['transform'])
                    move_spatial_data(e,old,e['transform'])
                    if not e.get('attached_to'):e['relative_transform']=copy.deepcopy(e['transform'])
                    move_contents(ident,old,e['transform'])
                elif kind in ('ATTACH','DETACH'):
                    parent=op.get('parent') if kind=='ATTACH' else None
                    cursor=parent; visited={ident}
                    while cursor:
                        if cursor in visited: raise ValueError('Attachment cycle')
                        visited.add(cursor); cursor=q.entity(cursor).get('attached_to')
                    e['attached_to']=parent
                elif kind=='CHANGE_ASSET':
                    asset=q.entity(op.get('asset',''))
                    if asset['kind']!='StaticMesh':raise ValueError('Binding requires a StaticMesh asset')
                    c=get(mesh_component(q,ident,op.get('component_id'))['id']);bind_asset(c,asset)
                    if c.get('collision',{}).get('aggregate_only'):
                        instances=[get(child) for child in set(children(c['id'])) if q.entity(child)['kind']=='Instance']
                        if len(instances)!=c.get('instance_count'):raise ValueError('Instanced mesh coverage incomplete')
                        for instance in instances:bind_asset(instance,asset)
                        aggregate_bounds(c,instances)
                    aggregate_bounds(e,[q.entity(child) for child in set(children(ident)) if q.entity(child)['kind']=='Component'])
                elif kind=='SET_PROPERTY':
                    row=db.execute('SELECT source FROM class_schemas WHERE class=?',(e['class'],)).fetchone()
                    s=json.loads(row[0]) if row else {}
                    prop=s.get('properties',{}).get(op['property'])
                    if not prop or not prop.get('editable',True) or not schema_type(op.get('value'),prop['type']): raise ValueError('Missing/noneditable property or incompatible type')
                    effect=prop.get('spatial_effect','unknown')
                    if effect=='unknown': raise ValueError('Property requires native preview of spatial effects')
                    e.setdefault('properties',{})[op['property']]=op['value']
                    if effect=='collision_enabled':
                        for child in set(children(ident)):
                            c=get(child)
                            if c.get('collision'):c['collision']['enabled']=op['value']
            if operation_transforms is not None and kind in ('MOVE_ACTOR','ROTATE_ACTOR','SCALE_ACTOR'):
                operation_transforms[op['operation_id']]=copy.deepcopy(e['transform'])
            if operation_effects is not None:
                operation_effects[op['operation_id']]={i:copy.deepcopy(verification_state(overlay[i])) for i in touched}
        except (ValueError,KeyError,ZeroDivisionError) as error:
            errors.append({'operation_id':op['operation_id'],'error':str(error)})
    return overlay,errors


def bind_asset(component,asset):
    component['asset_id']=asset['id']
    component['geometry_hash']=asset.get('geometry_hash')
    component['local_bounds']=copy.deepcopy(asset['local_bounds'])
    component['bounds']=g.transform_bounds(component['local_bounds'],component['transform'])
    component.pop('collision_local_bounds',None);component.pop('collision_bounds',None)
    if asset.get('collision_local_bounds'):
        component['collision_local_bounds']=copy.deepcopy(asset['collision_local_bounds'])
        component['collision_bounds']=g.transform_bounds(asset['collision_local_bounds'],component['transform'])
    for key in ('navigation_geometry_hash','navigation_input_coverage','navigation_slope_angle','navigation_slope_behavior'):
        component.pop(key,None)
        if key in asset:component[key]=copy.deepcopy(asset[key])
    component.setdefault('collision',{}).update(simple=copy.deepcopy(asset.get('collision_simple',[])),complex=copy.deepcopy(asset.get('collision_complex',[])),
                                               shapes=copy.deepcopy(asset.get('collision_shapes',[])),trace_mode=asset.get('trace_mode','UseSimpleAndComplex'))
    for kind in ('simple','complex'):
        component['collision'].pop(kind+'_coverage',None)
        if 'collision_'+kind+'_coverage' in asset:component['collision'][kind+'_coverage']=asset['collision_'+kind+'_coverage']


def qmul(a,b):
    return [a[3]*b[0]+a[0]*b[3]+a[1]*b[2]-a[2]*b[1],a[3]*b[1]-a[0]*b[2]+a[1]*b[3]+a[2]*b[0],
            a[3]*b[2]+a[0]*b[1]-a[1]*b[0]+a[2]*b[3],a[3]*b[3]-sum(a[i]*b[i] for i in range(3))]


def validate(store,patch_id):
    version=require_current_runtime()
    patch=store.patch_get(patch_id)
    if patch['status'] not in ('DRAFT','INVALID','VALIDATED','CONFLICTED'): raise ValueError('Patch is no longer validatable')
    with store.read() as db:
        fingerprint=hashlib.sha256(version.encode())
        native=db.execute("SELECT value FROM metadata WHERE key='native_library'").fetchone()
        if native:
            engine=db.execute("SELECT value FROM metadata WHERE key='engine_directory'").fetchone()
            library=native_library_path(engine[0] if engine else '',native[0])
            with library.open('rb') as stream:fingerprint.update(hashlib.file_digest(stream,'sha256').digest())
        version=fingerprint.hexdigest();operations_digest=hashlib.sha256(encode(patch['operations']).encode()).hexdigest()
        previous=patch.get('validation') or {}
        if (patch['status']=='VALIDATED' and previous.get('valid') and
            patch['base_revision']==Store.revision(db)==previous.get('base_revision') and
            previous.get('validator_fingerprint')==version and previous.get('operations_digest')==operations_digest):
            return patch
        try:
            operation_transforms={};operation_effects={}
            overlay,errors=preview(store,db,patch,operation_transforms,operation_effects)
        except ValueError as error:
            result={'valid':False,'errors':[str(error)],'base_revision':patch['base_revision']}; status='CONFLICTED'
        else:
            from .collision import penetration,blocks
            q=Queries(store,db,overlay); canonical=Queries(store,db); broad=set();collisions=[];preexisting=[]
            if not q.collision_index_ready:errors.append({'error':'Legacy collision index requires native cache upgrade before validation'})
            owners=db.execute("SELECT value FROM metadata WHERE key='navigation_owners_version'").fetchone()
            if (not owners or owners[0] not in ('1','2')) and db.execute("SELECT 1 FROM entities WHERE kind='NavRegion' LIMIT 1").fetchone():
                errors.append({'error':'Legacy navigation owner coverage requires native cache upgrade before validation'})
            for op in patch['operations']:
                if op['type']=='CREATE_ACTOR':continue
                try:target=Store.entity(db,op['target'])
                except ValueError:continue # Preview retains the missing-target error.
                if target.get('coverage')=='descriptor_only':
                    errors.append({'error':'Descriptor-only Actor lacks native component coverage','target':op['target']})
            instance_targets={op['target'] for op in patch['operations'] if op['type']=='SET_INSTANCE_TRANSFORM'}
            for e in overlay.values():
                if not e or e['kind'] not in ('Component','Instance') or not e.get('collision',{}).get('enabled'): continue
                if e.get('collision',{}).get('aggregate_only'):continue
                for other in q.region(g.spatial_bounds(e)):
                    if other['kind'] not in ('Component','Instance') or other['id']==e['id'] or other.get('collision',{}).get('aggregate_only'):continue
                    if other.get('actor_id')==e.get('actor_id') and not ({e['id'],other['id']} & instance_targets):continue
                    if not other.get('collision',{}).get('enabled'): continue
                    if not blocks(e,other):continue
                    pair=tuple(sorted([e['id'],other['id']]))
                    if pair in broad:continue
                    broad.add(pair);after=penetration(q,e,other)
                    if after['state']!='READY':errors.append({'error':'Collision coverage unknown','pair':pair,'detail':after});continue
                    if not after['overlap']:continue
                    try:before=penetration(canonical,canonical.entity(pair[0]),canonical.entity(pair[1]))
                    except ValueError:before={'state':'READY','penetration':0,'overlap':False}
                    evidence={'pair':pair,'after':after,'before':before}
                    if before['state']=='READY' and before['overlap'] and after['penetration']<=before['penetration']+.01:preexisting.append(evidence)
                    else:collisions.append(evidence)
            if collisions:errors.append({'error':'New blocking penetrations','collisions':collisions})
            from .navigation import input_changed
            nav_changes=[];navigation={}
            for ident,e in overlay.items():
                try:before=Store.entity(db,ident)
                except ValueError:before=None
                if input_changed(before,e):nav_changes.append(ident)
            if nav_changes:
                from .navigation import Navigation
                agents=[json.loads(row[0])['agent'] for row in db.execute("SELECT source FROM entities WHERE kind='NavRegion'")]
                state=db.execute("SELECT value FROM metadata WHERE key='navigation_state'").fetchone()
                if not agents and (not state or state[0]!='NOT_CONFIGURED'):errors.append({'error':'Navigation consequences UNKNOWN: no native navigation export','entities':nav_changes})
                if not agents and state and state[0]=='NOT_CONFIGURED':navigation['availability']={'state':'NOT_CONFIGURED','reason':'Native scan confirms no Recast navigation data in this map'}
                for agent in agents:
                    try:navigation[agent]=Navigation(store,agent).simulate(db,patch_id,overlay)
                    except (ValueError,KeyError) as error:navigation[agent]={'state':'UNKNOWN','reason':str(error)}
                    if navigation[agent].get('state')!='READY':errors.append({'error':'Navigation rebuild not confirmed','agent':agent,'detail':navigation[agent]})
            result={'valid':not errors,'errors':errors,'base_revision':patch['base_revision'],'changed_entities':len(overlay),'aabb_candidates':sorted(broad),'navigation_changed':nav_changes,'new_collisions':collisions,'preexisting_collisions':preexisting,
                    'expected':{ident:e for ident,e in overlay.items() if e is None or e['kind'] in ('Actor','Component','Instance')},'navigation':navigation,'operation_transforms':operation_transforms,'operation_effects':operation_effects}
            status='VALIDATED' if result['valid'] else 'INVALID'
        result.update(validator_fingerprint=version,operations_digest=operations_digest)
    with store.patches() as db:
        updated=db.execute("UPDATE patches SET status=?,validation=?,updated=? WHERE id=? AND operations=? AND status IN ('DRAFT','INVALID','VALIDATED','CONFLICTED')",
                           (status,encode(result),time.time(),patch_id,encode(patch['operations'])))
        if updated.rowcount!=1: raise ValueError('Patch changed concurrently')
    return store.patch_get(patch_id)
