---
name: unreal-spatial
description: Read, simulate and verify Unreal worlds using Spatial Twin; apply validated changes with official Unreal MCP. Use for placement, geometry, collision, assets, scene structure and navigation.
---

**Read Spatial Twin first.** It supplies persistent source facts and offline
Shadow simulation. Unreal is authoritative; official Unreal MCP performs live
actions, authoring, compilation, PIE/tests and rendering. Never write Canonical,
use Computer Use, restart the editor or full-scan at each task. Preserve unrelated
and unsaved work. Jev is optional; no model API is needed for exact checks.

When Unreal is closed and a READY persisted Twin exists, start bounded source
reads, Blender authoring and Shadow planning while authorized native preparation
runs independently. First scans and unavailable source must finish before reads.
Do not wait for live MCP during offline preparation. Before applying/importing,
require the actual project's editor readiness, current map/plugin and source
revision/preflight; conflict or unknown coverage stops writes. Never rebase a
stale plan implicitly. Retain both startup and final verified-delivery timing;
measure overlapping wall time, never subtract an estimated startup cost.

Keep the whole resolved dependency chain in one Code Mode execution: read fresh
status/source, page at one revision, evaluate the task's exact predicate, prepare
strict Shadow, check it, execute previously authorized actions, confirm Canonical
and emit the returned real image. Keep raw data/operations in store/load;
return only unresolved decisions or the checked receipt/image. Do not end a
call solely to print source or perform arithmetic in another model turn.
Semantic or ambiguous selection still requires agent reasoning. Missing schema,
incomplete coverage, UNKNOWN, conflict or failed checks stop dependent actions.
Read mandatory native SDK instructions before using that SDK.
Use exact reflected property names from the current class contract. A getter or
collision query is not necessarily a reflected property; never append guessed
names to a verified `get_properties` request. Reuse verified source and the
executor's independent readback; inspect only a missing relevant contract.
The first execution must resolve a unique known binding and read actual source;
never print ALL_TOOLS, even filtered entries, or stop at an inventory-only call.
Write that first execution through to the checked result when the task predicate
and contracts are known. Dependent reads are sequential awaits inside that
execution, not new model turns. Reuse source held at the checked revision; do not
repeat discovery, print a conclusive preview or request another confirmation of
an already authorized, fully validated action.

For known import/apply/sync/render compositions, use the exec pragma
`// @exec: {"yield_time_ms": 60000}`. If it still yields a running cell, wait
with `yield_time_ms:60000`; do not use one-second execution yields or polls.
Return immediately on completion/failure; keep each wait at most 60 seconds.
These waits preserve all checks and avoid model turns spent polling.

Use `world_read` for bounded exact reads and fresh status. Preserve project/map,
revision, connection, freshness and coverage; never mix snapshots. Request only
needed fields/limits/cursors and include any missing next-action schema in this
first read. In the focused profile, `shadow_plan(tool,arguments,map_id=status.map)`
delegates the exact registered handler. Do not rediscover known bindings/schemas.
Respect both the outer wrapper and inner contract: an `arguments` wrapper
receives `{arguments: handlerParameters}`, never flattened parameters.

The exact Twin read payload is `{queries:[{tool:'world_search',arguments:{
kind:'Actor',limit:20,fields:['label','class','bounds','transform'],
component_fields:['actor_id','bounds','transform','asset_id']}}],schemas:['patch_place']}`.
`queries` is required; never send `{world_search:{...}}`. Add a text filter only
from observed or supplied names. Keep source in code; follow each `next_query`
until complete and select by the actual task predicate before planning.
Decode MCP structuredContent or its JSON text once. `world_read.results[i].data`
holds the exact read result. Entity pages have `{revision,entities,cursor}`;
an expanded Actor has `components:{revision,entities,cursor}`. Requested child
`asset_id` is on each component entity. Check uniqueness and both cursors locally,
then use that source ID for dependent reads in the same execution; no model
decision is needed merely to decode these known fields.
Requested contracts are `tool_schemas:[{name,inputSchema,outputSchema}]`: find
by name, never `schemas[tool]`. Omit `schemas` for known contracts; `[]` is invalid.
Continue each current result row's `next_query`, not the initial batch's cursor.
Bounds are `[[minX,minY,minZ],[maxX,maxY,maxZ]]`, never `{min,max}`.
Grounding checks are in `plan.validation`: require `valid`, zero `errors_count`
and `new_collisions_count`, and evaluate its `navigation` diagnostics; support
remains `plan.continuous_support`. Do not invent top-level error/collision arrays.
The prepared patch is `plan.id`, its lifecycle is `plan.status`; submit that ID
as the executor's `patch_id`. Require `accepted_count` to match the selection
and `rejected.length === 0`, not `rejected_count`. For grounding, compare
`continuous_support.minimum_normal_z` to `cos(max_tilt_degrees)` and
`maximum_height_difference` to the requested limit; no `uprightness` object.
Navigation is a dictionary of named diagnostics: configured agents require
`state:'READY'`; `availability.state:'NOT_CONFIGURED'` explicitly reports absent
navigation for this map. There is no aggregate `navigation.valid` or `state`.

Select from real source, then use `patch_prepare`, `patch_place`, or `patch_ground`.
AABBs and finite samples do not prove exact collisions or continuous support.
Require VALIDATED, no errors/new blocking collisions, applicable navigation
checks and PROVEN support when needed. Keep exact operation IDs; never rebase,
regenerate a confirmed plan, strip defaults or replay uncertain writes.

Use existing official execution workflows. Success needs the expected source
state in a newer Canonical revision; MCP OK is insufficient. Confirm saving
separately and preserve unrelated dirty packages. View the returned real render
for appearance/clipping; spatial identities/positions come from Twin. Live,
saved, cold-reloaded and played state are separate proofs.

Read only the reference needed for an unresolved contract:
- [queries.md](references/queries.md): exact queries and bounded fields.
- [execution.md](references/execution.md): apply/import/render and stale-reader CLI.
- [coverage.md](references/coverage.md): missing geometry/navigation/authoring parity.
- [recovery.md](references/recovery.md): partial writes and recovery; never replay.
- [workflow.md](references/workflow.md): detailed procedures and read example.

Finish with outcome/count, revision/save status, actual image assessment and
unresolved limits. Measure native whole-agent tokens and complete-task delivery
time against an efficient equivalent baseline; include failures/setup/cache costs.
Nominal targets: -40% tokens/-50% time. User tolerance: +/-5 percentage points,
minimum -35%/-45%; criteria, not universal proven savings.
