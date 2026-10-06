# Apply, import and render

Read before live application or new-asset staging. For authored UCX navigation,
read the native spawn-profile and parity requirements in [coverage.md](coverage.md).

Resolve the project from the workspace or `SPATIAL_TWIN_PROJECT`; never infer a
game, map, class or asset name from examples. Multiple projects require explicit
selection. The packaged toolkit is `${PLUGIN_ROOT}/runtime`, or
`SPATIAL_TWIN_HOME`; scripts are under `tools/UnrealSpatialTwinMCP`. Use the
configured `SPATIAL_TWIN_PYTHON` environment with its installed dependencies.
Use the existing complete workflows through one official MCP session.

If a missing Twin fact requires the official UE5.8 SceneTools fallback,
`trace_world(start,end)` returns a float distance from start in cm, or None
for no hit. It returns neither a point nor a hit object. For a vertical downward
ray, hit_z = start.z - distance. Preserve unknown/no-hit results; finite rays
alone do not prove continuous support. Programmatic scripts use Python
True/False and serialize tool arguments with json.dumps. Read the actual
Programmatic execution instructions before using it; a changed engine/toolset
requires checking its own contract.

For grounding, source-select Actor IDs and call `patch_ground(entity_ids,
base_revision,max_distance,include_operations:true)`. Require VALIDATED,
accepted_count equal to the selection, no rejected/unknown/new collisions and
continuous_support.state PROVEN with the authorized minimum_normal_z.
This conservative world-bounds envelope is not a physics settle or exact bottom
contact solver. Selected attachment ancestor/descendant pairs and moving floors
are refused. Full support certificates use detail:'full'; compact is default.
Apply this exact patch ID through existing official execution; retain its
operation_id values. Never regenerate operations or claim the planning patch
APPLIED from a different patch's receipt. Confirm Canonical, saving and render.

If the attached reader reports SourceGuard and the client cannot reload it, use
the existing public fresh-process CLI without asking for an app restart or
changing TOML: `server.py --project <actual.uproject> --call world_read
--arguments <bounded-request.json>`. Optional --root overrides the Twin location;
--map pins a cached map. It invokes the same registered offline tool with exact
argument validation and SourceGuard, never controls Unreal or writes Canonical.
Other offline entrypoints work too. Keep the bounded JSON output locally and
emit decision facts. This recovers access to fresh data; the desktop's old MCP
connection remains separate until it can reload. Never replay a native write.

When surface selection and task constraints are resolved, `place.py --root <Twin>
--arguments <plan.json> --output <job.json>` runs the exact registered patch_place
contract offline and retains a validated Shadow patch. Arguments are patch_place
inputs derived from current source. Add `--apply --camera <camera.json>` only for
authorized delivery: task checks, official application, Canonical confirmation,
scope-save and render run in one command. Existing assets use apply_patch.workflow;
staged Blender assets use materialize.workflow and optional --materials bindings.
No new MCP server or actor-edit implementation is involved.

Default task checks require every selected candidate, PROVEN continuous footprint
support and at most 0.1 degrees of tilt. Set --max-tilt-degrees from the actual task.
--allow-rejections explicitly permits omitting rejected candidates, never placing
them. Missing support, invalid/colliding Shadow or excessive tilt stops before
Unreal writes. --no-save is transient work with existing assets; staged imports
require scope-save. Unrepresented requirements still need a separate decision.

Compact stdout has state, patch_id and result. Existing-asset success has
state="APPLIED", result.canonical_verified and result.render.image; authored-asset
success has state="APPLIED", result.canonical_verified and result.image with
result.render_state="VERIFIED". View that image in the same orchestration step.
For a staged asset, patch_id identifies the original authoring Shadow. Verify
patch_status using result.canonical_patch_id: native import rebinds the asset
and creates that separately validated live patch. Keep both identities; do not
mark the original authoring operations as canonically APPLIED.
The job keeps exact operations/checks and a patch ID before any write. Existing
job output refuses rerun; uncertain effects require recovery, never another spawn.
Without --apply it does not import, modify, save or render Unreal.

The Codex launcher advertises the compact tool profile: `patch_prepare` combines
draft creation/update and validation, avoiding three repeated operation schemas.
Legacy `patch_create`, `patch_update` and `patch_validate` calls remain compatible;
`--tool-profile full` restores their catalogue entries. No capability or validation
is removed. JSON transport preserves the same source data without pretty printing.

- Existing canonical assets: `apply_patch.py --root <Twin> --patch <validated-id>
  --camera <camera.json> --output <render.json>` applies, scope-saves, confirms and
  captures. Omit camera/output if no image is needed. `--no-save` is explicit
  transient work; do not save unrelated dirty packages.
- New Blender assets: direct bpy/export -> manifest, UE-coordinate mesh and convex
  UCX -> `asset_stage` -> `patch_prepare` -> `materialize.py --root <Twin> --patch
  <authoring-id> --output <receipt.json> --materials <bindings.json> --apply
  --camera <camera.json>`. It imports, verifies native vertex/bounds/UCX parity,
  rebinds/revalidates, applies, scope-saves, confirms and renders. Existing slot
  bindings avoid unnecessary material creation. Import only changed sources.
  For source-selected surface placement, pass that manifest as `manifest_file`
  to `patch_place`/`place.py`, with the observed canonical StaticMesh template ID
  in `asset_id`. It composes the same immutable staging and Shadow validation in
  one call; skip the separate `asset_stage` round trip. Template selection remains
  yours, not a guessed default. The resulting operations reference the authored
  staged asset. `--apply` still runs native import/parity, revalidation, Canonical
  confirmation and scope-save; `--no-save` is refused for this authored route.
- Keep receipts/images on disk. Put visual observations in the final reply; do not
  create duplicate observation documents unless requested. Return outcome, revision, saving status, relevant
  conflicts and paths. No bulk source catalogs, vertex arrays or RPC chatter.
  Global integrity audits are explicit background jobs, not per-edit prerequisites.

For the existing-asset action CLI, parse its compact JSON stdout once. Successful
application has `status="APPLIED"`, `canonical_verified=true`, `canonical_revision`
and `saved`. With a requested capture, `render.state="VERIFIED"`, `render.image`
and `render.canonical_revision` identify the real image and its confirmed world.
Use `render.image` with view_image in the same orchestration step; the full receipt
is retained at `render.receipt`. These fields eliminate a second read just to
discover result keys. Any absent confirmation, conflict or error requires stopping;
never infer success from an exit code alone or replay a write. This contract is
for apply_patch.py; do not assume unrelated tools return the same fields.


For an existing ISM/HISM element use `SET_INSTANCE_TRANSFORM` with its persistent
Instance ID and complete world transform. Never move its whole Actor to correct
one element, and never use a stored array index as identity. Shadow checks the
other instances in that Actor too. The native adapter resolves the current index
and refuses a changed live pose; saving is scoped to the owning Actor package.
An old editor adapter must be updated before this operation can execute.
