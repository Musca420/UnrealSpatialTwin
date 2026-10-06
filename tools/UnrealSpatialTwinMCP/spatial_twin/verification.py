"""Canonical effect verification and recovery; no editor connection or writes."""
import hashlib
import json
import time
from pathlib import Path
from .store import Store,encode
from . import require_current_runtime


def verification_state(entity):
    """Only fields used by effect matching/identity; no geometry or spline dumps."""
    if entity is None:return None
    fields=('id','kind','class','transform','instance_transform','asset_id','affects_navigation','attached_to','actor_id','label','properties')
    result={key:entity[key] for key in fields if key in entity}
    if entity.get('collision'):result['collision']={'enabled':entity['collision'].get('enabled')}
    return result


def matches(expected,actual,resolved=None):
    if expected is None:return actual is None
    if actual is None or actual.get('class')!=expected.get('class') or actual['kind']!=expected['kind']:return False
    for key in ('position','scale'):
        if any(abs(a-b)>.01 for a,b in zip(expected['transform'][key],actual['transform'][key])):return False
    qa,qb=expected['transform']['rotation'],actual['transform']['rotation']
    if 1-abs(sum(a*b for a,b in zip(qa,qb)))>1e-6:return False
    if 'instance_transform' in expected:
        ea,eb=expected['instance_transform'],actual.get('instance_transform')
        if not eb:return False
        if any(abs(a-b)>.01 for key in ('position','scale') for a,b in zip(ea[key],eb[key])):return False
        if 1-abs(sum(a*b for a,b in zip(ea['rotation'],eb['rotation'])))>1e-6:return False
    if 'asset_id' in expected and actual.get('asset_id')!=expected['asset_id']:return False
    if 'affects_navigation' in expected and actual.get('affects_navigation')!=expected['affects_navigation']:return False
    if 'attached_to' in expected:
        parent=expected['attached_to'];parent=(resolved or {}).get(parent,parent)
        if actual.get('attached_to')!=parent:return False
    if expected.get('collision') and actual.get('collision',{}).get('enabled')!=expected['collision'].get('enabled'):return False
    return all(actual.get('properties',{}).get(k)==v for k,v in expected.get('properties',{}).items())


def observed(db,target,expected,paths,resolved):
    existing=db.execute('SELECT source FROM entities WHERE id=?',(resolved.get(target,target),)).fetchone()
    if existing:return json.loads(existing[0])
    if expected and expected['kind']!='Actor' and expected.get('actor_id') in paths and expected['actor_id'] not in resolved:
        return None
    if expected and expected['kind']!='Actor' and expected.get('actor_id') in resolved:
        rows=db.execute("SELECT source FROM entities WHERE actor_id=? AND class=? AND label=?",
                        (resolved[expected['actor_id']],expected['class'],expected.get('label'))).fetchall()
        if not rows:
            rows=db.execute("SELECT source FROM entities WHERE actor_id=? AND class=? AND asset_id=?",
                            (resolved[expected['actor_id']],expected['class'],expected.get('asset_id'))).fetchall()
        if len(rows)!=1:return None
        return json.loads(rows[0][0])
    if target in paths and target not in resolved:
        row=db.execute("SELECT source FROM entities WHERE path=? AND kind='Actor'",(paths[target],)).fetchone()
    else:row=db.execute('SELECT source FROM entities WHERE id=?',(resolved.get(target,target),)).fetchone()
    return json.loads(row[0]) if row else None


def creation_identities(db,operations,evidence,base_revision):
    """Resolve recorded creation paths through unique canonical history, never labels."""
    created={op['target']:op['operation_id'] for op in operations if op['type']=='CREATE_ACTOR'}
    resolved={};paths={}
    for receipt in evidence:
        if receipt.get('state')=='CANONICAL_VERIFIED':resolved.update(receipt.get('actor_ids',{}))
        if receipt.get('state')!='ACKNOWLEDGED':continue
        result=receipt.get('result');result=json.loads(result) if isinstance(result,str) else result
        if not isinstance(result,dict):continue
        rows=result.get('created',[])
        if result.get('refPath'):
            rows=[{'target':t,'operation_id':op,'actor':result} for t,op in created.items() if op==receipt.get('operation_id')]
        for row in rows:
            if row.get('target') not in created or row.get('operation_id')!=created[row['target']]:raise ValueError('Creation receipt does not match the operation plan')
            path=row.get('actor',{}).get('refPath')
            if not isinstance(path,str) or not path:raise ValueError('Creation receipt has no native identity reference')
            if row['target'] in paths and paths[row['target']]!=path:raise ValueError('Conflicting creation references')
            paths[row['target']]=path
    candidates={p:set() for p in paths.values()}
    if candidates:
        for row in db.execute("SELECT entity_id,json_extract(after_json,'$.path') FROM changes WHERE revision>? AND type='CREATE' AND json_extract(after_json,'$.kind')='Actor' AND json_extract(after_json,'$.path') IN (SELECT value FROM json_each(?))",
                              (base_revision,encode(list(candidates)))):candidates[row[1]].add(row[0])
    for target in created:
        if target in resolved:continue
        ids=candidates.get(paths.get(target),set())
        if len(ids)!=1:raise ValueError('Created Actor identity is unknown or ambiguous; no automatic retry: '+target)
        resolved[target]=next(iter(ids))
    return resolved,paths


def saved_completion(db,patch,receipts,journal,resolved,revision):
    """Confirm the native save receipt and still-matching package signatures."""
    if not journal:return False,'No durable native save completion'
    if (journal['operations_digest']!=patch['validation']['operations_digest'] or journal['dispatch_id']!='save:'+patch['id']
        or not any(r.get('operation_id')==journal['dispatch_id'] and r.get('state') in ('SENT','ACKNOWLEDGED') for r in receipts)):
        raise ValueError('Native save journal does not match the recorded dispatch')
    result=json.loads(journal['result'])
    saved_revision=result.get('canonical_revision')
    if result.get('state')!='SAVED' or not isinstance(saved_revision,int) or not patch['base_revision']<saved_revision<=revision:
        raise ValueError('Invalid native save revision')
    packages=result.get('packages')
    if not isinstance(packages,list) or not packages:raise ValueError('Native save has no package scope')
    scope=set()
    for op in patch['operations']:
        ident=resolved.get(op['target'],op['target'])
        row=db.execute("SELECT source FROM (SELECT source,revision FROM entities WHERE id=? UNION ALL SELECT before_json AS source,revision FROM changes WHERE entity_id=? AND type='DELETE') ORDER BY revision DESC LIMIT 1",(ident,ident)).fetchone()
        entity=json.loads(row[0]) if row else {}
        if entity.get('kind')=='Instance':
            owner=db.execute('SELECT source FROM entities WHERE id=?',(entity.get('actor_id'),)).fetchone()
            entity=json.loads(owner[0]) if owner else {}
        package=entity.get('package')
        if not package:return False,'Saved package scope no longer resolves'
        scope.add(package)
    if len(packages)!=len(scope) or {p.get('package') for p in packages}!=scope:
        raise ValueError('Native save package scope differs from the patch')
    dirty=db.execute("SELECT 1 FROM entities WHERE kind='Actor' AND json_extract(source,'$.package') IN (SELECT value FROM json_each(?)) AND COALESCE(json_extract(source,'$.package_dirty'),1)!=0 LIMIT 1",(encode(sorted(scope)),)).fetchone()
    if dirty:return False,'Saved package has subsequent or unknown dirty state'
    for package in packages:
        path=package.get('path');signature=package.get('signature')
        if not isinstance(path,str) or not Path(path).is_absolute() or (signature is not None and not isinstance(signature,str)):
            raise ValueError('Invalid native saved package signature')
        row=db.execute('SELECT signature FROM package_sources WHERE path=?',(path,)).fetchone()
        if (row[0] if row else None)!=signature:return False,'Canonical saved package signature changed'
        try:
            stat=Path(path).stat()
            # UE5.8 Win64 deliberately truncates FILETIME to whole seconds.
            disk=f'{stat.st_size}|{621355968000000000+stat.st_mtime_ns//1000000000*10000000}'
            with Path(path).open('rb') as stream:content=hashlib.file_digest(stream,'md5').hexdigest()
        except FileNotFoundError:disk=None;content=None
        except OSError:return False,'Saved package file is unavailable'
        if disk!=signature:return False,'Saved package file changed since native confirmation'
        if content!=package.get('content_md5'):return False,'Saved package content changed since native confirmation'
    return True,saved_revision


def recover(store,ident):
    """Confirm recorded effects as of a READY revision. Never retry or save Unreal."""
    require_current_runtime()
    with store.application_lock():
        patch=store.patch_get(ident)
        if patch['status']=='APPLIED':
            if any(r.get('state')=='CANONICAL_VERIFIED' and r.get('saved') for r in patch.get('receipts',[])):return patch
            with store.patches() as db:
                if not db.execute('SELECT 1 FROM patch_saves WHERE patch_id=?',(ident,)).fetchone():return patch
        receipts=patch.get('receipts') or [];validation=patch.get('validation') or {}
        if patch['status'] not in ('FAILED','APPLYING','APPLIED'):raise ValueError('Only an interrupted application can be recovered')
        if patch['status']=='APPLYING' and not any(r.get('state')=='APPLICATION_STARTED' and r.get('lease_version')==1 for r in receipts):
            raise ValueError('Legacy APPLYING patch has no execution lease; active writer cannot be excluded')
        if (not validation.get('valid') or not validation.get('expected') or validation.get('base_revision')!=patch['base_revision']
            or validation.get('operations_digest')!=hashlib.sha256(encode(patch['operations']).encode()).hexdigest()):
            raise ValueError('Recovery requires the original valid, unchanged operation plan')
        if not any(r.get('state') in ('SENT','ACKNOWLEDGED') for r in receipts):raise ValueError('No recorded dispatch to recover')
        freshness=store.status(False)
        if freshness.get('editor_connected') and (freshness.get('synchronizer',{}).get('pending_actors') or freshness.get('synchronizer',{}).get('error')):
            raise ValueError('Wait for canonical synchronization before recovery')
        # The native writer uses a separate row: a late transport error must not
        # overwrite its result with the client's older SENT receipt list.
        with store.patches() as db:
            journal=db.execute('SELECT * FROM patch_results WHERE patch_id=?',(ident,)).fetchone()
            save_journal=db.execute('SELECT * FROM patch_saves WHERE patch_id=?',(ident,)).fetchone()
        evidence=list(receipts)
        if journal:
            if (journal['operations_digest']!=validation['operations_digest'] or journal['dispatch_id']!='batch:'+ident
                or not any(r.get('operation_id')==journal['dispatch_id'] and r.get('state') in ('SENT','ACKNOWLEDGED') for r in receipts)):
                raise ValueError('Native result journal does not match the recorded dispatch')
            evidence.append({'state':'ACKNOWLEDGED','result':json.loads(journal['result'])})
        parent=patch;seen={ident}
        while parent.get('continued_by'):
            child_id=parent['continued_by']
            if child_id in seen or len(seen)>=100:raise ValueError('Invalid or excessive continuation chain')
            seen.add(child_id);child=store.patch_get(child_id)
            if child['status']!='APPLIED':raise ValueError('Continuation has not been canonically confirmed')
            child_validation=child.get('validation') or {}
            if child_validation.get('operations_digest')!=hashlib.sha256(encode(child['operations']).encode()).hexdigest():raise ValueError('Continuation plan changed')
            with store.patches() as db:
                link=db.execute('SELECT operations_digest FROM patch_continuations WHERE parent_id=? AND child_id=?',(parent['id'],child_id)).fetchone()
                child_journal=db.execute('SELECT * FROM patch_results WHERE patch_id=?',(child_id,)).fetchone()
            if not link or link[0]!=hashlib.sha256(encode(parent['operations']).encode()).hexdigest():raise ValueError('Continuation source changed')
            if child_journal:
                if child_journal['operations_digest']!=child_validation['operations_digest'] or child_journal['dispatch_id']!='batch:'+child_id:
                    raise ValueError('Continuation native result differs from its plan')
                evidence.append({'state':'ACKNOWLEDGED','result':json.loads(child_journal['result'])})
            else:
                evidence.extend(r for r in child.get('receipts',[]) if r.get('state')=='ACKNOWLEDGED')
            parent=child
        with store.read() as db:
            revision=Store.revision(db)
            if revision<=patch['base_revision']:raise ValueError('No newer canonical revision confirms the effects')
            resolved,paths=creation_identities(db,patch['operations'],evidence,patch['base_revision'])
            mismatches=[]
            for target,expected in validation['expected'].items():
                actual=observed(db,target,expected,paths,resolved)
                if actual:resolved[target]=actual['id']
                if not matches(expected,actual,resolved):mismatches.append(target)
            if mismatches:raise ValueError('Expected canonical effects are incomplete or changed: '+encode({'count':len(mismatches),'targets':mismatches[:8]}))
            saved,save_evidence=saved_completion(db,patch,receipts,save_journal,resolved,revision)
            receipt={'state':'CANONICAL_VERIFIED','canonical_revision':revision,'actor_ids':resolved,'saved':saved,
                     'recovered':True,'editor_connected':freshness.get('editor_connected',False),'observed_at':time.time()}
            if saved:receipt['native_saved_revision']=save_evidence
            elif save_journal:receipt['save_unconfirmed_reason']=save_evidence
            if journal:receipt['native_result_recorded']=True
        current_validation=dict(validation)
        if 'application_error' in current_validation:receipt['prior_application_error']=current_validation.pop('application_error')
        with store.patches() as db:
            changed=db.execute("UPDATE patches SET status='APPLIED',receipts=?,validation=?,updated=? WHERE id=? AND status=? AND operations=? AND validation=? AND receipts=?",
                               (encode([*receipts,receipt]),encode(current_validation),time.time(),ident,patch['status'],encode(patch['operations']),encode(validation),encode(receipts)))
            if changed.rowcount!=1:raise ValueError('Patch changed during recovery')
        return store.patch_get(ident)
