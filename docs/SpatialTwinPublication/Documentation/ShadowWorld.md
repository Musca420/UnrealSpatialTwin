# Shadow World and patches

`patch_create` stores operations and a current `base_revision` in the separate
patch database. `patch_update` replaces editable operations and invalidates old
validation. `patch_preview` constructs an overlay; canonical rows remain
read-only. Changes to canonical revision conflict with the proposal.

Supported operation names are CREATE_ACTOR, DELETE_ACTOR, MOVE_ACTOR,
ROTATE_ACTOR, SCALE_ACTOR, SET_PROPERTY, ATTACH, DETACH, CHANGE_ASSET and
SET_INSTANCE_TRANSFORM. The latter requires a persistent Instance target ID
and a complete world `transform` (position, unit quaternion, nonsingular scale).
It updates the chosen instance and aggregate bounds in Shadow, checks collisions
against siblings too, and leaves other instance poses intact. Sibling bounds
are read once per affected component per preview; sibling source records are not
dumped into the agent context. Native execution resolves the current index from
the tracked identity and compares the live pose with the simulated precondition;
stale/removed identities are refused. Package saving follows the owning Actor.
The official SpatialTwinToolset exposes this narrow identity adapter; the offline
MCP still performs no editor writes. An older native plugin refuses the whole
patch before dispatch. Native integration acceptance is recorded separately in
the production coverage ledger.
Moves propagate through components, instances and attached actors. Attachment
cycles and singular transforms are rejected. Class, property type and asset
checks use exported native reflection/cache data. A class without a spatial
prototype or a property with unknown spatial effects cannot receive a faithful
offline preview; validation rejects that uncertainty.

Validation distinguishes new/worsened blocking penetrations from preexisting
ones. AABBs only select pairs; Chaos provides narrow-phase evidence. Changed
navigation inputs trigger affected-tile rebuilds using exported agent settings,
geometry and modifiers. Missing inputs produce an invalid proposal with the
reason retained. A successful simple-plane test is not full project parity.

Statuses are DRAFT, VALIDATED, INVALID, APPLYING, APPLIED, FAILED and CONFLICTED.
Validation and execution use compare-and-set checks so concurrent operation
updates cannot silently reuse an old result. Every external write records SENT
before dispatch and ACKNOWLEDGED after the official MCP response. An uncertain
write is never automatically retried.

APPLIED requires a newer canonical revision containing expected source state.
The executor explicitly flushes the native Twin Toolset and polls the Twin.
Saving is scoped to affected packages and must not SaveAll unrelated work.
Touched packages already dirty at proposal execution cause a save conflict;
`--no-save` explicitly requests live-only changes. A failed partially executed
patch retains receipts for inspection; do not blindly retry it.

`patch_status(patch_id, recover=true)` reconciles an interrupted application
against a newer READY canonical revision without contacting Unreal, replaying
operations, saving packages or rebuilding Shadow against a different base.
The original validated operation digest must still match. Every expected effect
must be present; incomplete or changed effects leave the patch interrupted.
Application and recovery share an OS lock per world, released automatically on
process death. Legacy APPLYING records without this lease cannot be recovered
while an active old writer cannot be excluded. FAILED records remain inspectable.

Recovery returns APPLIED with `recovered=true`: this proves
the recorded spatial effects at the returned canonical revision, not a received
MCP acknowledgement or completed arbitrary Blueprint side effects. Saving remains
`saved=false` unless a durable native save completion also passes verification.
Native `patch_save_journal=1` records the exact package scope, saved revision,
disk signatures and content digests after successful scoped saving and canonical
commit, before replying. Recovery requires the original save dispatch and plan,
resolved package scope, no subsequent/unknown dirty state, matching canonical
signatures and matching current files. It then reports `saved=true` with
`native_saved_revision`, without invoking Unreal. Content comparison catches
same-size edits even when timestamps are unchanged; MD5 here detects accidental
content changes, not hostile tampering. A failed check retains `saved=false`
and its reason. No journal means no inferred save, even if a file looks correct.
Partial native saves or a crash before journal commit remain unconfirmed and
must not be blindly repeated. An offline result describes the last confirmed
editor snapshot plus the checked files. The original
error and uncertain receipts remain in the journal. Repeating recovery is a no-op.
The action CLI equivalent is `apply_patch.py --root <Twin> --patch <id>
--recover --no-save`; recovery cannot render or implicitly save other edits.

For created Actors, recovery requires a recorded native reference or previous
canonical identity confirmation. Creation history binds an acknowledged path
to exactly one persistent ID, surviving subsequent renames. Labels, nearest
positions and matching geometry are never identity evidence. Native toolsets
advertising `patch_result_journal=1` persist the terminal official batch result
in `patches.sqlite/patch_results` before returning its MCP response. This is one
native journal call per batch, including partial failures, with no extra client
round trip. The record is immutable, matched to the validated plan and SENT
dispatch, and independent of client receipt updates. Recovery can use its creation
references even when the response is lost; it still verifies every effect through
Canonical. A failed journal write fails the application without replaying effects.
Older native toolsets retain the existing receipt behavior. A process crash
between a creation and the terminal journal write remains uncertain: this is
not an atomic transaction with Unreal authoring. Lost creation
responses with no durable reference remain unresolved and must not be retried
automatically. Recovery is explicit; Jev routing and world_read cannot invoke it.

`patch_continue(patch_id)` prepares a new, validated patch containing only the
unexecuted suffix of a terminated partial batch. It makes no Unreal calls.
The original validation keeps compact per-operation spatial effects (not repeated
geometry). A durable terminal result must identify an ordered completed prefix
and its next uncertain operation. Canonical must match the post-state of that
uncertain operation as well: it is never blindly replayed. Unknown creation
identity, incomplete effects, missing old validation evidence, a running executor
or inconsistent completion order prevent continuation.

Already created IDs/component references are rebound from native receipts and
unique history. The suffix is validated against the current revision and must
preserve the original final spatial intent. Applying it is a separate explicit
action through the same official MCP executor. Repeated preparation returns the
same child patch; parent and child retain persistent provenance. The original
patch remains interrupted until its final effects can be recovered using the
confirmed continuation chain. Saving the suffix does not implicitly save every
package touched by its parent; earlier unsaved packages still need scoped handling.
Automatic continuation of partially saved packages remains unsupported. This
does not prove arbitrary Blueprint side effects or make Unreal calls transactional.


Blender authoring stays outside Canonical World. `export_blender.py` writes an
FBX, STG1 geometry and explicit UCX convex bodies from an isolated Blender source.
`asset_stage(manifest_file, template_asset_id)` copies immutable authoring data
into `Saved/SpatialTwin/staging` and a separate patch catalog. Staged assets are
available only to Shadow/entity authoring queries, never as canonical entities.
Coordinates must explicitly be Unreal centimeters with the exported basis.

For UE5.8, a current native StaticMesh spawn profile can advertise
`native_spawn_navigation.authored_ucx_adapter=1`. Staging then derives a separate
navigation mesh from the authored convex UCX bodies, preserving each hull and
using Unreal's navigation winding. It is marked `authored_ucx_prediction_v1`:
it is a proposed input, not confirmed canonical geometry. Missing adapters retain
UNKNOWN navigation. New component rasterization defaults come from the native
profile, not assumed project settings. Unsupported per-surface slope overrides
are refused instead of silently using the agent's global slope.

The importer verifies the predicted navigation surface against the native asset
before rebinding: vertex positions, oriented plane areas (allowing hull face
retriangulation), slope and default rasterization settings must agree. The native
asset export now includes its default component navigation geometry even before
any actor uses it. Dynamic obstacles/custom surface areas still require native
owner data. A successful import alone never approves a placement.

`materialize.py` accepts a validated authoring patch, imports through the official
StaticMeshTools, synchronizes the native asset through SpatialTwinToolset, checks
bounds and vertex/convex-body parity, and creates a new patch against the current
canonical revision. Import success alone does not permit placement. Native facts
must pass validation again. An interrupted import retains its receipt; never
replay it blindly or replace an existing asset implicitly.

Explicit material bindings are checked against the Twin first. During live
materialization, a binding absent from the catalog (for example an unused Engine
material) is cached through `spatial_twin_cache_asset` once per distinct path,
then its native class and canonical identity are checked before any import.
Existing bindings need no additional editor query. The receipt records this
targeted synchronization; a successful tool response without canonical data is
insufficient. Offline validation cannot certify an uncached material.

For the isolated work-lamp probe, CREATE_ACTOR explicitly sets
`component_properties={"bCanEverAffectNavigation":false}`: this is a declared
native property applied and verified through official ObjectTools, not a claim
that a navigable obstacle has no consequences. Normal obstacles retain the native
navigation default and require valid offline navigation inputs.

Regional rebuilding includes indexed `NavigationInput` records for nonprimitive
owners. An affected tile intersecting a missing octree input is UNKNOWN, never
silently rebuilt without that source. Legacy unaudited sources require native
navigation refresh; canonical path queries still use their exported tiles.
If gap diagnostics exceed their 256-entry cap, rebuilding remains unavailable
until source coverage is restored. Unsupported links/fill-underneath remain
explicit limits even when their owner is successfully indexed.

Navigation replacement removes the entire affected tile set from a private
Detour copy before adding rebuilt layers. This avoids order-dependent pool
exhaustion; failed builds publish nothing and never resize native capacity.
A full pool with no existing affected tiles fails before geometry preparation:
that region has no confirmed baseline coverage. Translation/yaw of exported
convex area prisms is supported; tilt/shear needs native modifier regeneration.

Point links now rebuild from indexed native owner data, filtered by the selected
agent. Shadow transforms endpoints; snap radius/height remain native parameters.
Default height uses native voxel climb. Detour loading defers link connections
until all tiles and the native default area order are present; immutable cache
identity includes that order. Missing order for cost-snapped links is UNKNOWN.
Runtime-controlled IDs remain excluded with explicit diagnostics. Projected
endpoints need native reprojection; segment links and unresolved definitions
remain unsupported for Shadow rebuilding, never silently dropped.

Projected point links are already processed native source endpoints. Recast tile
rebuilding reuses them when their owner transform and link array are unchanged.
The requires_projection flag requires new physics projection only when that
owner/link source changes; such changes still return UNKNOWN. A native isolated
fixture lowers a platform below an unchanged projected link: native and Shadow
agree on loss of reachability, without reprojecting endpoints onto the new floor.

Native geometry groups preserve per-object Recast fill-underneath and filled
convex flags. Groups cover the triangle stream exactly; gaps, overlap, invalid
flags and indices fail validation. Filled convex uses native temporary span
columns and per-group bounds. Tile reconstruction gathers the complete native
vertical column so moving a filled object can uncover previously hidden floors.
A native capability check prevents an old DLL silently ignoring these flags.
Fill requires exported flags, navigation bounds and a native mask audit proving
no fill-cancelling masks; unsupported masked input remains UNKNOWN.


## Recast masks and cached metadata

Box/convex masks clear RC_PROJECT_TO_BOTTOM on the tile grid; cylinder masks
do not, matching the qualified UE5.8 native implementation. Capability version
2 rejects the previous backend and invalid input types. Octree auditing enriches
metadata for indexed owners without re-exporting geometry. Instances read their
aggregate owner's flags. Missing flags are UNKNOWN and require scoped native
refresh, especially for legacy unloaded owners.

The historical Mask80 fixture restored three stripped owners and matched six
native/Shadow projections before and after a 900 cm mask move. Local validation
took 0.284 s; Canonical was unchanged. That microbenchmark is not a whole-agent
speed claim. Current regional/link/fill/mask/slope/capacity qualification is
reported in the release matrix.

Asset binding81 clears stale navigation geometry/coverage/slope metadata,
rebinds every ISM/HISM instance and recalculates aggregate render/collision
bounds with unchanged siblings. A unique component can be inferred consistently
by preview and the official MCP executor. Native Cube-to-Sphere instance
replacement and raycast parity verified; unknown navigation data still require
scoped native caching, never the old asset's triangles.


Dependent temporary references also resolve for updates, asset binding,
attachment and scoped saving in one patch. Shadow reuses each operation's
resulting transform: moving a parent and then rotating its child preserves the
resulting position. Two historical regressions were reproduced and corrected;
49 Python tests and a 26.80 s build passed at that revision. An independent
eight-operation native fixture verified dependent changes, detach and saved
cleanup in 6.115 s (harness time, not whole-agent tokens). Real lost-ACK fault
injection remained FAILED/SENT after one dispatch, while Canonical confirmed the
known effect. Recovery is explicit; failed attempts are retained locally.

## Partial package save finalization

The canonical application and file persistence remain separate. A native scoped
save records intent in `patches.sqlite.patch_save_progress` before I/O and saves
one owned package at a time. Each successful package records its path, native
file signature, content digest and editor modification counter. A terminal
failure reports compact completed/total counts; the full evidence stays on disk.
Per-file checkpoints live in `patch_save_packages`; the full scope is serialized
only at intent and terminal boundaries, not for every file. A terminal PARTIAL
snapshot contains the complete resume scope. STARTED plus checkpoints is evidence
of progress, not permission to retry an uncertain save.
The immutable `patch_saves` completion is published only after all scoped saves
and the canonical signature transaction complete.

Use `apply_patch.py --root <Twin> --patch <id> --finalize-save` explicitly for a
terminal partial save. This is an action through the official Unreal MCP, not
an offline `patch_status(recover=true)` write. The executor checks the original
validated plan, already-observed canonical effects, project identity and native
`patch_partial_save=1` capability. The native tool verifies the entire package
scope before writing: same editor session, same loaded package objects, unchanged
modification counters and unchanged files for completed packages. Modified,
transacted or marked-dirty packages invalidate continuation even if their values
are subsequently restored. It skips completed packages and never replays actor
operations. Each explicit save attempt retains a separate receipt under the
same logical `save:<patch_id>` operation; authoring uses a different batch ID.

After native completion, the client waits for the local synchronization heartbeat
and verifies the journal, canonical effects and package files. It does not resend
the save while waiting. A late completed journal can confirm persistence after
an earlier recovery reported APPLIED with saved=false. Repeated finalization of
an already-confirmed completion performs no editor operation.

Interrupted STARTED/uncertain outcomes and partial saves from another editor
session are deliberately refused: persistent intent alone does not prove that
the old unsaved contents still exist. Completed native receipts remain verifiable
offline across sessions. Standard Unreal modification/transaction/dirty events
are required; arbitrary uninstrumented memory writes by third-party C++ code
are outside this guarantee. Unknown creation effects and arbitrary Blueprint
side effects remain separate recovery limits.

# Authored collision precision

Convex staging accepts plane deviations bounded by float32 serialization
roundoff: `max(1e-7, min(0.001, body_span * 2e-7))` centimetres. Blender stores
mesh vertices as float32; the former fixed1e-7cm threshold rejected a verified
convex source after a3.1e-6cm export deviation. A regression distinguishes this
roundoff from a0.1cm concavity, which remains rejected. The tolerance does not
replace closed-topology checks, native hull/vertex parity or placement collision
validation. No collision-free claim follows from asset staging alone.
# Per-surface slope and engine parity

Source body slope overrides remain in the Twin. Offline Recast reconstruction
accepts nondefault overrides only when the native export declares a verified
engine policy. UE 5.8.2 changelist 56702186 uses the global agent slope in its
Recast generator: its call to `ModifyWalkableFloorZ` discards the returned value.
An independent native fixture with all four override modes, at 30 and 60 degrees,
verified the same projections after rebuilding all 36 coordinates offline.
This describes Recast navigation, not CharacterMovement walking constraints.
Other engine builds and legacy exports without the policy remain UNKNOWN for
nondefault overrides; refresh native navigation metadata before simulating them.


## Repeated placement without model round trips

`patch_place(candidates, asset_id, actor_class, base_revision, max_distance,
label_prefix, rotation, scale, max_height_difference, clearance, size_cm)` is an
offline planning operation. Candidates are world ray origins above chosen
locations, in centimetres; the agent still discovers and chooses those locations.
The tool reads observed asset local bounds in one READY snapshot, transforms the
bounds with the requested quaternion/scale and probes a 3x3 conservative XY
footprint. It accounts for a nonzero mesh pivot and negative scale. `size_cm`
optionally specifies positive local-axis dimensions from the observed bounds;
it replaces scale magnitudes and preserves their signs.

Unsupported or uneven candidates are rejected with their indexes. Missing or
unreadable collision geometry makes the batch BLOCKED, with no patch created.
A changed base revision conflicts. Accepted candidates become a normal Shadow
patch, through the same strict collision, class/asset and navigation validation
as `patch_prepare`. It preserves navigation defaults and performs no Unreal
operation or canonical write. Labels append the original candidate index to the
prefix; use normal patch editing if another naming convention is required.

Finite samples do not certify continuous surface support, and the conservative
bounding footprint can reject a shape that would fit more narrowly. Examine
support/collision diagnostics for the required quality. A validated proposal
still needs official Unreal MCP application, synchronization, native confirmation
and visual render inspection where appropriate. Staged Blender meshes use the
same manifest/native parity requirements as other Shadow proposals.

Geometry caching keys include file identity/size/mtime; content-addressed STG
files additionally require their declared SHA1. Deleted, replaced or corrupt
geometry never keeps supplying an old cached hit. Ray results flag unknown
components and incomplete coverage instead of inventing an empty scene.


150 adds source-backed continuous surface evidence: PROVEN only when all
footprint corners and samples share a single actual box face, or a single face
of a small closed convex collision mesh. Mesh topology welds exact source
coordinates (including UV seams), requires two faces per edge and a connected
surface, and checks every vertex against every outward face plane. Proof is
capped at256triangles/512vertices and uses a bounded local numerical tolerance.
Open, concave, disconnected, nonmanifold or larger meshes remain UNPROVEN.
Multiple supporting components/primitives or different faces remain UNPROVEN.
It certifies the surface under the whole conservative footprint rectangle;
it does not certify full bottom contact, navigability or the requested slope.
Use the returned normal and height range for those requirements. Primitive
ray origins inside collision are rejected. `include_operations=true` returns
the exact validated patch operations for subsequent authorized live execution;
it does not apply them or weaken revision/Shadow/native verification.
