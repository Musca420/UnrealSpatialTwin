# Detailed query and Shadow contracts

Read only for an unresolved query/planning contract; do not reread the entrypoint.

## Read and reason

`world_read.results[i].next_query`, when present, is the exact next `{tool,arguments}`
call with the same selection and cursor; pass it back without rediscovering a
schema. Stale revision cursors fail. `schemas` includes registered `outputSchema`
alongside input contracts. Source `bounds` is `[[minX,minY,minZ],[maxX,maxY,maxZ]]`.
`patch_ground.status` is the patch state; compact success has `accepted_count`
and `continuous_support.state`. BLOCKED creates no patch and has no `id`.

- Once this entrypoint has been read, including when supplied inline, do not
  reread it. Open only the reference relevant to an unresolved question.
- For documented queries, resolve the unique tool binding and call it in the
  same code-mode execution. Do not spend a model turn printing a catalogue or
  rediscovering known schemas. This helper decodes either MCP response format:

  ```javascript
  const call = async (name, args = {}) => {
    const matches = ALL_TOOLS.filter(t => t.name.endsWith('__' + name));
    if (matches.length !== 1) throw Error('Resolve missing/ambiguous Twin binding: ' + name);
    const r = await tools[matches[0].name](args);
    if (r.isError) throw Error(JSON.stringify(r.content));
    return r.structuredContent ?? JSON.parse(r.content.find(c => c.type === 'text').text);
  };
  ```

  The installed plugin defaults to focused (three entrypoints); workflow exposes ten. Call all exact reads through `world_read` in every profile;
  they remain available even without individual bindings. The helper above
  invokes entrypoints, not unadvertised individual read tools; `tool_describe(names)` returns any unknown exact
  schema without loading the whole catalogue. `--tool-profile compact` or `full`
  restores individual tool discovery. Use known arguments below or request only
  the missing schema. Batch the required facts, store the decoded
  result locally, and emit the relevant source fields and uncertainty. Do not
  serialize ALL_TOOLS, complete wrappers, geometry arrays or every receipt.
- Use `world_read(queries)` for known independent reads: it includes fresh status
  and rejects mixed revisions. For one fact use status plus the single query.
  If a subsequent tool's schema is unknown, pass `schemas:['exact_tool_name']`
  in this same call; read `tool_schemas`. Large requested schemas use
  `tool_schemas_reference`. Do not request known schemas or a full catalogue.
  For component-based placement, request Actor class/coverage and Component
  transform/bounds/actor_id/asset_id. Actor poses duplicate those observations
  when not used; include them for Actor movement, attachment or other needs.
  Keep project/map/root, revision, last sync, connection and coverage. Never move
  patches or revision numbers between maps. Counts are opt-in.
  `--tool-profile focused` advertises only `world_read`, `shadow_plan`
  and `tool_describe`. Read status through world_read; request an unknown patch
  handler schema in that same source call. Invoke it through
  `shadow_plan(tool:'patch_place',arguments:exactArgs,map_id:status.map)` (or
  patch_prepare/patch_ground/patch_preview/patch_status/patch_continue/asset_stage).
  This reuses each existing strict handler and refuses a changed map. It has
  no live actions or Canonical writes. `--tool-profile workflow` restores ten entrypoints; the smaller
  catalogue is not proof of lower whole-agent tokens.
- Keep deterministic follow-up reads and support calculations in the same
  code-mode call when their inputs follow directly from the first results.
  Retain data with store/load; emit the facts needed for the next decision.
  Do not ask the model again merely to copy IDs, calculate coordinates, page an
  already bounded selection or decode JSON. Stop on errors, UNKNOWN or conflicts;
  dependent writes still require a validated plan and canonical confirmation.
- Start with region summary, then bounded `fields`, `limit`, `cursor`:
  world -> region -> actor -> component -> geometry. Component region queries avoid
  one children call per Actor. Units are cm; rotations are [x,y,z,w] quaternions.
  Use persistent IDs and cached asset geometry; do not dump meshes into context.
- For a supplied AABB use `spatial_overlap(aabb=[min,max])`, with Actor and
  Component reads together. Do not replace the box with an enclosing sphere:
  that fetches unrelated entities and creates extra pages. Use `limit=20` for
  these bounded batches; group any required follow-up pages in one execution.
  These exact arguments are known; resolve and call `world_read` immediately,
  rather than making an execution that only prints bindings or these schemas:

  ```javascript
  const source = await call('world_read', {queries: ['Actor','Component'].map(kind => ({
    tool:'spatial_overlap', arguments:{aabb:region,kind,limit:20,
      fields:kind==='Actor' ? ['class','coverage'] :
        ['transform','bounds','actor_id','asset_id','coverage']}
  }))});
  if (source.state !== 'READY' || !source.results) throw Error(JSON.stringify(source));
  store('source',source); // region comes from the user/task; never invent it.
  ```
- `entity_get` defaults to identity/pose/bounds. Request additional source fields
  explicitly; nested paths such as `collision.enabled` and `properties.bHidden`
  avoid large arrays. `asset_get(fields=...)` also supports nested paths. Unknown
  fields stay absent. Source keys are `class`, `transform`, `bounds`, `asset_id`.
  Kinds are exact: `StaticMesh`/`Material`; `Asset` is registry metadata, not an
  umbrella kind. No Class kind: reuse an observed Actor class, not a Component.
- Prefer `spatial_support` for placement footprints. Finite samples do not prove
  continuous support. `spatial_raycast_batch` accepts 1..128 rays, `detail="hits"`
  or `"distances"`; hit fields include `actor_id`, `component_id`, `instance_id`,
  not `entity_id`. Use each tool's advertised schema rather than invented aliases.
- Known queries are deterministic. Jev is optional; Codex can resolve ambiguous
  requests itself. Use `world_query(request, queries)` with 1..16 concrete bounded
  choices only when Jev selection is useful. No model API is required for ordinary
  reads, geometry, Shadow, validation, synchronization or native execution.
  NONE/NEEDS_REASONING, CONFLICTED or UNAVAILABLE require reasoning, not paid
  retries. Cached selection still reads fresh data; Jev never certifies geometry.

## Simulate, execute, verify

For repeated placement on surfaces, use `patch_place`: supply candidate ray
origins above the surfaces, observed mesh/class IDs, current base revision and
the requested size or scale/rotation. It calculates the mesh footprint/bottom
offset, samples support, rejects missing/uneven candidates and runs the same
strict Shadow collision/navigation validation. Inspect accepted/rejected and
VALIDATED; UNKNOWN blocks the group. This replaces separate asset geometry,
support, coordinate-copying and draft-validation calls. Set `include_operations`
when those operations are needed for the authorized execution step: keep the
result locally and pass its exact operations, rather than spending a model turn
on another preview. `continuous_support.state="PROVEN"` certifies an observed
single primitive box face, actual collision triangle, or verified closed convex collision-mesh face covering
the whole footprint rectangle; use its normal
and height range for the task's flatness requirement. `UNPROVEN` remains sampling
only. The tool makes no Unreal edits and does not choose semantic targets.
It already checks the observed asset dimensions, actual collision and navigation.
Do not repeat `asset_get`, geometry metadata, resource discovery or component
collision dumps to certify facts conclusively supplied by this result. Query
only an unresolved requirement; `geometry_get` describes a binary cache, it is
not a rendered image or a dump of vertices. Never turn UNPROVEN into approval.
The following input contract is known. Values come from the source and requested
task; no schema request is needed merely to copy these arguments. Do not describe
`patch_prepare` when this placement result already contains validated operations:

```javascript
const placement = await call('patch_place', {
  candidates, asset_id:meshId, actor_class:actorClass,
  base_revision:source.status.canonical_revision, label_prefix:labelPrefix,
  max_distance:rayDistance,
  size_cm:requestedSize, clearance:requestedClearance,
  max_height_difference:allowedHeightDifference, include_operations:true
});
store('placement',placement); // inspect status/support/unknown before live delivery.
```

Validated operations contain `operation_id`: a unique nonempty patch/journal
identifier added by the store, not an Unreal property or navigation override.
Preserve it unchanged during execution/recovery. For CREATE without requested
property overrides, the known keys are `type`, `target`, `class`, `asset`,
`label`, `transform`, `operation_id`. Other operation types have their own
declared fields. Reject actual unexpected fields, invalid values, UNKNOWN or
failed validation; do not reject this declared identifier or strip it.

If source selection and task constraints are already resolved, perform the
known placement call, local task checks, authorized execution, canonical
confirmation and requested image retrieval in one Code Mode execution.
Return to the model when a new decision, conflict or failed check requires it;
do not add turns just to print operations or copy them into the executor.
For authorized resolved placement, prefer the complete place.py workflow in
execution.md: it enforces task constraints and reuses the official executor/render.
This avoids stopping after patch_place just to copy operations into another call.
Offline planning uses the same command without --apply. It does not cover
unresolved requirements; validation/coverage/recovery conditions below still apply.

1. Use `patch_prepare(operations, base_revision)` to create and validate Shadow
   together; optional patch_id updates a draft. CREATE uses `type="CREATE_ACTOR"`,
   new temporary `target`, observed `class`, `asset` ID, `label`, and `transform`
   with position/scale triples and unit quaternion. Specify the final pose in
   CREATE. Dependent operations may reference earlier CREATE targets. For one
   ISM/HISM element use `SET_INSTANCE_TRANSFORM` with persistent Instance ID and
   complete world transform; never move its entire Actor or use an array index.
2. Inspect VALIDATED, conflicts and UNKNOWN. Query Shadow only for unresolved
   questions; don't repeat a complete preview after a conclusive receipt.
   AABBs are not exact collision proof. Never disable navigation to pass, guess
   missing geometry, bypass validation or blindly rebase. Changes to revision,
   operations or validator invalidate validation. For missing/stale coverage,
   read [coverage.md](coverage.md) before continuing dependent work.
3. Before live execution or Blender asset staging, read
   [execution.md](execution.md). Reuse its action workflows for
   import/native parity, official MCP application, scope-save, synchronization
   and automatic render. Offline-only planning ends at the validated patch.
4. Success requires a newer Canonical revision and the expected source state,
   not MCP OK. Saving needs its own confirmed receipt. On failed/uncertain writes
   stop that batch; never replay a spawn. Read [recovery.md](recovery.md)
   before recovery/continuation. Preserve partial receipts and unrelated work.
5. Inspect the returned real render with view_image for appearance and clipping.
   Positions, identity, distance and collision come from Twin. Live confirmation,
   saved state, cold reload and played traversal are distinct proofs.

Asset-impact reads should use asset_usage(kind="Actor", detail="summary") when the decision needs counts/groups/ingombri rather than Actor identities. label_prefix filters actual source labels; group_by supports class, level, or label_prefix with label_group_depth1..8 underscore-separated segments. total deduplicates component/instance owners. bounds is the source Actor AABB union; bounds_complete must be true before treating it as complete. groups are limited; groups_complete=false requires narrowing the prefix or reading relevant entities, never guessing the omitted groups. No spatial scan of the whole world or asset loading occurs.
asset_get(include_dependencies=true) returns entity, revision, dependencies:[{id,path}], dependency_count, dependencies_complete, and dependency_coverage:{state,package_id,source,live_state,package_dirty}. Check dependency_coverage.state === 'CURRENT', not the object against a string. source='asset_registry_disk' and state refer to the saved native Asset Registry projection. For current live package references, require live_state='CURRENT'; UNKNOWN_UNSAVED means edits exist and that disk list is insufficient, UNKNOWN means legacy/missing proof. Do not force-save packages merely to make the flag pass. Supported live USES_ASSET relations (e.g. mesh materials) remain separate source facts. Native save synchronization refreshes only the saved files; dirty-state events update package scope, not world geometry. Missing registry data, missing target paths or more than20 dependencies are explicitly incomplete; use dependency_query and normal graph pagination for deeper analysis. Combine the required asset metadata and owner summary in world_read at the same revision. If the asset ID comes from an earlier read, decode that source and issue the dependent read within the same Code Mode execution; return only the checked aggregate or unresolved uncertainty. Do not enumerate every owner merely to compute a count/union. The shared dispatcher distinguishes relation direction strings from ray direction vectors and still rejects invalid enums/nonfinite rays.
