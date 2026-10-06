"""Prepare only the unexecuted suffix of a durably terminated official batch."""
import hashlib
import json
import time
import uuid
from . import require_current_runtime
from .store import Store,encode,check_operations
from .shadow import validate
from .verification import creation_identities,matches,observed


def prepare_continuation(store,ident):
    require_current_runtime()
    with store.application_lock():
        patch=store.patch_get(ident);validation=patch.get('validation') or {};receipts=patch.get('receipts') or []
        if patch['status'] not in ('FAILED','APPLYING'):raise ValueError('Only an interrupted patch can be continued')
        if patch['status']=='APPLYING' and not any(r.get('state')=='APPLICATION_STARTED' and r.get('lease_version')==1 for r in receipts):
            raise ValueError('Legacy APPLYING patch has no execution lease')
        digest=hashlib.sha256(encode(patch['operations']).encode()).hexdigest()
        if not validation.get('valid') or validation.get('operations_digest')!=digest or validation.get('base_revision')!=patch['base_revision']:
            raise ValueError('Continuation requires the original unchanged valid plan')
        with store.patches() as db:
            prior=db.execute('SELECT * FROM patch_continuations WHERE parent_id=?',(ident,)).fetchone()
            journal=db.execute('SELECT * FROM patch_results WHERE patch_id=?',(ident,)).fetchone()
        if prior:
            if prior['operations_digest']!=digest:raise ValueError('Continuation source changed')
            return store.patch_get(prior['child_id'])
        if (not journal or journal['operations_digest']!=digest or journal['dispatch_id']!='batch:'+ident
            or not any(r.get('state') in ('SENT','ACKNOWLEDGED') and r.get('operation_id')==journal['dispatch_id'] for r in receipts)):
            raise ValueError('Durable terminal batch result required; never replay an uncertain running batch')
        result=json.loads(journal['result']);ops=patch['operations'];ids=[op['operation_id'] for op in ops]
        completed=result.get('completed');uncertain=result.get('uncertain_operation_id')
        if result.get('complete') is not False or not isinstance(completed,list):raise ValueError('Use effect recovery for a complete batch')
        n=len(completed)
        if n>=len(ops) or [r.get('operation_id') for r in completed]!=ids[:n] or any(r.get('state')!='ACKNOWLEDGED' for r in completed) or uncertain!=ids[n]:
            raise ValueError('Batch completion order is missing or inconsistent')
        count=n+1  # Never replay the uncertain action: its post-state must be proven.
        if count==len(ops):raise ValueError('No unexecuted suffix; use effect recovery')
        effects=validation.get('operation_effects') or {}
        if set(effects)!=set(ids):raise ValueError('Original per-operation Shadow evidence is unavailable')
        prefix={}
        for op in ops[:count]:prefix.update(effects[op['operation_id']])
        freshness=store.status(False)
        if freshness.get('editor_connected') and (freshness.get('synchronizer',{}).get('pending_actors') or freshness.get('synchronizer',{}).get('error')):
            raise ValueError('Wait for canonical synchronization before continuation')
        with store.read() as db:
            revision=Store.revision(db)
            if revision<=patch['base_revision']:raise ValueError('No newer canonical revision proves the interrupted effects')
            resolved,paths=creation_identities(db,ops[:count],[{'state':'ACKNOWLEDGED','result':result}],patch['base_revision'])
            for target,expected in sorted(prefix.items(),key=lambda pair:bool(pair[1] and pair[1]['kind']!='Actor')):
                actual=observed(db,target,expected,paths,resolved)
                if actual:resolved[target]=actual['id']
                if not matches(expected,actual,resolved):raise ValueError('Interrupted operation effects are uncertain or changed; no replay: '+target)
            suffix=[]
            for op in ops[count:]:
                mapped=dict(op)
                for field in ('target','parent','component_id'):
                    if field in mapped:mapped[field]=resolved.get(mapped[field],mapped[field])
                suffix.append(mapped)
            suffix=check_operations(suffix)
        child=str(uuid.uuid4())
        with store.patches() as db:
            row=db.execute('SELECT status,operations,validation,receipts FROM patches WHERE id=?',(ident,)).fetchone()
            if tuple(row)!=(patch['status'],encode(ops),encode(validation),encode(receipts)):raise ValueError('Patch changed during continuation')
            db.execute('INSERT INTO patches(id,base_revision,status,operations,updated) VALUES(?,?,?,?,?)',(child,revision,'DRAFT',encode(suffix),time.time()))
            db.execute('INSERT INTO patch_continuations VALUES(?,?,?,?,?)',(ident,child,digest,count,revision))
        continued=validate(store,child)
        if continued['status']=='VALIDATED':
            # A changed untouched dependency must not silently change the user's
            # final intent when the suffix is simulated against a newer world.
            with store.read() as db:
                expected_suffix=continued['validation']['expected'];conflicts=[]
                if Store.revision(db)!=revision:conflicts.append('Canonical changed during continuation validation')
                for target,expected in validation['expected'].items():
                    mapped=resolved.get(target,target)
                    actual=expected_suffix[mapped] if mapped in expected_suffix else observed(db,target,expected,paths,resolved)
                    if not matches(expected,actual,resolved):conflicts.append(target)
            if conflicts:
                value=continued['validation'];value['valid']=False;value['errors'].append({'error':'Continuation differs from original final intent','targets':conflicts[:8],'count':len(conflicts)})
                with store.patches() as db:db.execute("UPDATE patches SET status='INVALID',validation=?,updated=? WHERE id=?",(encode(value),time.time(),child))
                continued=store.patch_get(child)
        return continued
