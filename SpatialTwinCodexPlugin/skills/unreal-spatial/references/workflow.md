---
name: unreal-spatial
description: Read, simulate and verify Unreal worlds using Spatial Twin; apply validated changes with official Unreal MCP. Use for placement, geometry, collision, assets, scene structure and navigation.
---

**Do not query Unreal for facts already in Spatial Twin.** Twin supplies persistent
perception and Shadow; official Unreal MCP executes; Unreal is authoritative.
Never write Canonical world.sqlite or use Computer Use, including bootstrap.
Preserve unrelated/unsaved work; no editor restart/full scan per task. Jev is
optional; deterministic queries, validation and execution need no model API.

This is the general Unreal production workflow. Target >=40% fewer full-agent
tokens and >=50% less complete-task time than an efficient no-Twin route, equal
model/results/quality/checks. Targets are not proven general savings.

Read this entrypoint once, including when provided inline. Read only the relevant
reference for an unresolved contract. Keep raw source/operations in store/load or
artifacts; emit decision facts and uncertainty. Batch known dependencies in Code
Mode when available; direct MCP works too. Never spend a model turn discovering a
known binding, copying coordinates or printing full catalogues/receipts.
Keep a handler's parameters distinct from its caller's request envelope: a
resolved wrapper taking `arguments` receives `{arguments: handlerParameters}`.
Read the advertised outer schema and the exact inner schema; never flatten them.
The first Code Mode execution must resolve the known binding **and read source**;
an inventory-only call adds a full model round trip. If a binding is absent or
ambiguous, stop there. With resolved action/result contracts, execute, check the
receipt and emit the actual returned render in the same execution. Return to the
model for selection or visual assessment, not routine decoding or field lookup.
For a stale attached reader, execution.md describes the existing fresh-process
CLI; use it without restarting Unreal/the app or rewriting configuration.

1. Use `world_read` for all exact reads, including in the ten-entrypoint workflow
   profile. It includes fresh status and enforces one revision. Retain project,
   map, root, revision, last sync, connection and coverage; never mix maps.
   Start with a summary or supplied region, then bounded fields/limit/cursor.
   Request a needed next-action schema **in this first read**, with
   `schemas:['patch_place']` for unresolved placement defaults/types. Read
   `tool_schemas` or its exact `tool_schemas_reference`. Avoid a later
   tool_describe turn for a contract you already know you will need.
   Do not request schemas already advertised by the workflow profile.
   The opt-in focused profile exposes only world_read, shadow_plan, tool_describe.
   Request the exact upcoming handler schema with source, then route Shadow via
   shadow_plan(tool,arguments,map_id=status.map). The same strict handlers run;
   no live actions or Canonical writes are exposed by that router. For Actor
   discovery needing component poses/assets, use `world_search(kind:'Actor',
   limit:20,fields:['label','class','coverage'],component_fields:['transform',
   'bounds','asset_id'],component_limit:4)` inside this read. It returns bounded
   source child pages in the same snapshot; inspect kind/coverage and page any
   child cursor with `entity_children`. No recursive expansion or hidden choice.
2. Reason over actual source. AABBs and finite samples do not certify exact
   collision or continuous support. Missing source stays UNKNOWN. Semantic
   target selection belongs to the agent; do not infer it from screenshots.
   When the task supplies an exact predicate over source fields (explicit IDs,
   dimensions, class/tag, distances or slope), express that predicate in code.
   Keep source reading, deterministic selection, strict Shadow checks,
   authorized delivery and render in one execution when their contracts are
   resolved. Do not return only source facts and start a second model turn for
   the same supplied predicate. Check errors, completeness, revision, support
   and validation in code before every dependent action. Return for reasoning when the
   selection is semantic, ambiguous or incomplete; do not add a planning turn
   solely to confirm arithmetic. When a native SDK requires fresh execution
   instructions, read those first, then batch the remaining resolved chain.
   Finish after assessing the returned image; use the already checked receipt
   for count/revision/save status instead of an extra receipt-decoding turn.
   Never substitute guessed labels or bounds-only
   collision approval for missing evidence.
   Bulk support uses `spatial_support(detail:'compact')` by default: retain its
   revision, decisions, surface identities/normals and height ranges locally.
   Request `full` only for unresolved certificate metadata. Do not combine a
   near-limit support report with unrelated pages; do not re-read geometry or
   components merely to repeat a conclusive PROVEN receipt. Strict Shadow
   collision validation and native readback still remain required.
3. Simulate with `patch_prepare(operations,base_revision)`, or `patch_place` for
   selected surface candidates. For vertical grounding of selected Actors use
   `patch_ground`: it reads current poses/bounds, proves each support, preserves
   other source fields and prepares Shadow without model coordinate copying.
   Select IDs from source first; no semantic selection is delegated to this tool.
   Inspect VALIDATED, accepted/rejected, collision,
   navigation and task constraints. Continuous support requires PROVEN; UNPROVEN
   is sampling only. Respect normals/height ranges. Do not guess geometry,
   disable navigation, strip operation_id, blindly rebase or weaken checks.
   Changed revision/operations/validator invalidate validation. Inspect Shadow
   only for an unresolved question, not to duplicate a conclusive receipt.
4. Before live actions or Blender staging, read [execution.md](execution.md)
   unless its verified procedure is already supplied. Resolved placement uses
   actual `place.py`: offline by default; explicit --apply composes task checks,
   official execution, Canonical confirmation, scope-save and requested render.
   Existing assets use apply_patch; staged Blender assets use materialize with
   native geometry/UCX parity and revalidation. No second live MCP server.
   For authored surface placement, supply `manifest_file` and the observed
   canonical template `asset_id` to patch_place/place.py. Staging is composed;
   no separate asset_stage call is needed, and all native checks remain.
5. Success requires a newer Canonical revision and expected source state, not MCP
   OK. Confirm save separately; preserve unrelated dirty packages. Failed or
   uncertain writes stop the batch: never replay a spawn. Read
   [recovery.md](recovery.md) before recovery.
6. View the returned real render with view_image for appearance/clipping. Pose,
   identity, distance and collision come from Twin. Live, saved, cold reload and
   played traversal are separate proofs. Report outcome/count, revision, save
   status, image and unresolved limits; do not repeat JSON/source tables.

Known bounded AABB read (cm; quaternions [x,y,z,w]):

```javascript
const matches=ALL_TOOLS.filter(t=>t.name.endsWith('__world_read'));
if(matches.length!==1) throw Error('Resolve missing/ambiguous Twin binding');
const r=await tools[matches[0].name]({queries:['Actor','Component'].map(kind=>({
  tool:'spatial_overlap',arguments:{aabb:region,kind,limit:20,
    fields:kind==='Actor'?['class','coverage']:
      ['transform','bounds','actor_id','asset_id','coverage']}
})),schemas:['patch_place']}); // Include schema when this is the upcoming operation.
if(r.isError) throw Error(JSON.stringify(r.content));
const source=r.structuredContent??JSON.parse(r.content.find(c=>c.type==='text').text);
if(source.state!=='READY'||!source.results) throw Error(JSON.stringify(source));
store('source',source); // region comes from the task, never invented.
```

Actor class/coverage plus Component pose/bounds/asset avoid duplicate Actor poses;
include Actor transform for movement/attachments. Page only a bounded selection.
Use exact supplied schema, not guessed aliases. For other fields, ray/support,
asset, instance or patch contracts read [queries.md](queries.md).
For missing/stale/unsupported coverage, navigation or authored collision read
[coverage.md](coverage.md) before dependent work. Blueprint, materials,
animation, gameplay, compile/PIE/tests use official toolsets; arbitrary Blueprint
effects require native tests and synchronized spatial readback.

Measure equal complete tasks with native input+output/time, retain failures and
setup, distinguish initial import/scan from reuse. Cached input counts; reasoning
is already output. Bytes/calls are not tokens/money. Reviewer/account costs may
be UNKNOWN. No global Codex settings changes or universal coverage/speed claims.
