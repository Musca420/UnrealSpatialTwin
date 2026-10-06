# Spatial queries

Ray hits identify a triangle in the exported collision cache. Its index is not
Unreal's cooked `FaceIndex`; cooking can reorder triangles. Actor/component,
distance, impact position and normal can be compared with a native trace, but
an index mapping requires separate export metadata and is currently unavailable.

Use `world_status`, then a bounded `world_region` or `world_search`, before
requesting entity details. `detail="summary"` aggregates region contents;
`fields`, `limit` (1–500) and revision/query-bound `cursor` control entity output.
All positions and distances use Unreal centimeters: 100 meters is 10,000 units.

Entity `fields` accepts nested dictionary paths: `collision.enabled`,
`collision.simple_coverage`, `properties.bHidden`, or `after.transform` in
changes/diffs. Results preserve nested object structure. Unknown leaves are
absent, explicit null remains null. Requesting a parent includes its full value;
literal source keys take precedence over path interpretation. Arrays are returned
whole when explicitly selected; array-index paths are not supported. No source
data or validation input is changed by output projection. `asset_get(fields=...)`
supports the same projection; omitting it preserves the complete asset response.
Request coverage flags or query results rather than serializing all collision
shapes just to reason about a region. Independent regional reads can share one
`world_read` response with fresh status and revision consistency checks.

Its exact envelope is `status`, `results:[{tool,data,next_query?}]` and, when
requested, `tool_schemas:[{name,inputSchema,outputSchema?}]`. Find a contract by
name in that array; omit `schemas` when known (an empty list is invalid).
Continue each returned row's next query, not the initial row's cursor. Bounds
remain two arrays. Grounding diagnostics are under `plan.validation`, including
`valid`, `errors_count`, `new_collisions_count` and navigation; the separate
continuous support proof remains required. The prepared patch is `plan.id` (the live executor accepts it as `patch_id`), lifecycle is `plan.status`, and rejected entries are the `rejected` array. Ground support uses `minimum_normal_z` and `maximum_height_difference`; there is no separate uprightness object. Navigation contains named diagnostics with per-agent `state:READY` or an explicit `availability.state:NOT_CONFIGURED`, not an aggregate valid/state flag. The advertised output schemas and
transport preserve these exact source/validation fields and optional absence.

Canonical AABB candidates come from a three-dimensional SQLite RTree. Sphere
queries refine candidates by distance to bounds. Nearest and k-nearest expand
indexed regions until a distance bound proves the result; covering indexes
provide world extents without scanning the whole RTree on every query. Shadow
entities have their own in-memory three-dimensional RTree, merged with unchanged
canonical candidates.

`spatial_overlap` is explicitly an AABB broad-phase query. Patch collision
validation additionally uses Chaos GJK/EPA against exported analytic shapes,
convexes and candidate collision triangles. Collision response channels and
object types constrain blocking pairs. Contact does not count as penetration.

`spatial_raycast` tests native exported collision representations, with shared
mesh BVHs and analytic sphere/capsule/box intersections. Results identify actor,
component, instance, distance, position, normal and triangle or primitive index
when available. Missing collision coverage is returned explicitly; an incomplete
query is not proof of free space. A segment is a ray toward its endpoint with
maximum distance equal to its length.

`spatial_raycast_batch` accepts 1–128 rays in one consistent revision/read and
shares the cached geometry BVHs. Each ray specifies `origin`, `direction` and
`max_distance`; `fields` selects hit fields. Per-ray completeness remains
explicit. Use `complex=true` to match the installed Unreal SceneTools
`trace_world`, which traces Visibility against complex geometry. Invalid batch
arguments fail before querying. `detail="distances"` returns one distance/null
array and a single revision/completeness envelope. Uncertain rays remain in the
`incomplete` list with their indices and missing-coverage evidence; null alone
does not prove a miss. This mode accepts only the distance field. Default
`detail="hits"` preserves identity/normal/field selection. Repeated source and
geometry decoding is cached only within the same revision-pinned query.

Geometry output is bounded metadata/reference information. Request geometry
only after narrowing to relevant components. Asset usage and graph queries use
indexed relationships; queries do not load Unreal assets or need screenshots.

For support decisions use `spatial_support(candidates, offsets, max_distance)`:
one revision-pinned read samples downward complex collision at supplied footprint
offsets, then returns accepted positions/height ranges and rejected indices with
reasons. At most128candidates,512offsets and8192total samples. Missing coverage
is UNKNOWN, distinct from a confirmed missing surface; incomplete queries cannot
approve placement. Sampling is finite and does not prove continuous support or
navigation. This keeps thousands of intermediate hit records outside agent context.

The shared query forces the three-dimensional RTree to run before entity kind
filters (`CROSS JOIN`, then primary rowid lookup). SQLite otherwise selected the
kind index for Component/Instance and scanned hundreds of thousands of entities
per ray. EXPLAIN regression checks cover the actual filtered query, not just the
presence of the spatial index.

Navigation uses exported Detour tiles and matching native code while the Editor
is closed. Agent-specific path queries report coverage and UNKNOWN when inputs
or backend are unavailable. READY denotes available exported data; it does not
imply complete streaming-world navigation coverage. A cache with zero tiles is
UNKNOWN and cannot validate navigation changes. qualification-project currently uses
invoker-only dynamic navigation: a saved editor scan without runtime invokers
does not acquire the missing runtime tiles. Paths report `requested_start`,
`requested_end`, `projected_start`, `projected_end` and the end projection
distance in centimeters. Reachability refers to the projected NavMesh endpoints;
a nearby projected endpoint is not proof that the exact requested point is
walkable. Invalid cache layout or unsupported cache version returns UNKNOWN.

`world_query` is a bounded TypeSafe dispatcher over these existing tools. It
chooses and executes an exact supplied call, with revision checks before and
after execution. It does not replace indexed queries or native collision math.
Use it when selecting the next query benefits from semantic interpretation;
known queries need no inference. See CodexIntegration.md for limits and outcomes.

`world_read` batches 1..16 known reads using the same bounded argument checks,
includes fresh status, and rejects mixed revisions/snapshots. It does not call
Jev or validate/mutate patches; use `patch_prepare` explicitly. Large output is
stored as an artifact rather than silently truncated. Read the relevant section
or narrow the query; omitted data is not evidence of absence.

Support queries prefetch collision candidates once per footprint, within the
same canonical read/Shadow overlay. Every ray still tests its own bounds and
exact geometry and preserves UNKNOWN. At1024 examined candidates, prefetch falls
back to individual indexed rays to bound memory for large sparse footprints.
No candidate cache crosses revision/overlay boundaries.

`world_maps` lists the cached worlds without loading Unreal. A running MCP follows
the native active-map selector between requests; a request and its nested batch
stay pinned to one store. `--map /Game/MapName` pins a cached map explicitly.
Map revisions and patches are local to their store, not interchangeable counters.

Navigation no longer assumes a game-specific agent name. Omitting `agent` selects
the sole exported agent; multiple agents require an explicit choice. `BUILDING`
or `STALE_*` rejects old tiles. A navigation system without any Recast data is
`NOT_CONFIGURED`, not stale navigation. `CURRENT_SAVED_DESCRIPTORS` on WP layout
means unsaved actor changes are not certified by that saved descriptor layout.

Native offline queries retain up to two content-addressed NavMeshes (up to64MiB
and65536tile slots each). Mutable filenames and larger files remain queryable
without retention. Cold loads verify the filename digest; file changes/deletion
invalidate cached reads. This reduces repeated disk parsing, not Recast build
cost or whole-agent reasoning time. Shadow rebuilds publish immutable digest
files atomically, preserving earlier validated meshes if a later build fails.

Native static point links and one-way direction are supported by queries.
Runtime-controlled link IDs are excluded: a confirmed alternate route can still
be returned; failure to find one while such links are encountered is UNKNOWN,
not a proof of impossibility. Search budget exhaustion is likewise UNKNOWN.
This does not certify gameplay traversal. Shadow reconstruction of arbitrary
link owners, segment links and their Blueprint callbacks remains unsupported.

`collision_bounds_state=LEGACY_REQUIRES_UPGRADE` identifies old broad-phase
indexes. Rays/overlaps explicitly report incomplete coverage; a missing hit
cannot certify free space and patch validation is refused. Native cache upgrade
repairs extents from existing collision shapes/geometry, with a provenance tag
distinct from source render bounds. See Installation.md for the maintenance path.

Navigation status reports `tile_capacity` and `tile_pool_full` per agent.
It also returns cached `capacity_settings` (`fixed_tile_pool`, `tile_pool_size`,
`tile_number_hard_limit`) and `native_actor` (`id`, `path`, `package`). These are
source settings and identity, separate from the effective Detour capacity.
Older snapshots return null for missing metadata until a native navigation
refresh; no full scan or live query is required just to read status. Use the
identity with official editor tools when a native configuration repair is
needed, wait for synchronization and verify actual coverage. The Shadow
validator does not simulate changes to these generation settings.
READY applies to exported active tiles, not all world positions. A full pool
is a capacity limit, not proof that every region is navigable.
# Text search cost

`world_search` preserves literal substring matching across label, path and class,
including `%`, `_`, quotes and Unicode. A trigram index finds selective candidates;
common and very short terms stream a covering metadata index in ID order. Pagination
runs before loading entity source JSON. Neither route enumerates Unreal objects.
The reader reports index availability in `world_status.text_search_state`; old
stores retain their previous query path until the native transactional migration.
Search responses remain bounded by `limit` and `fields`. Query microbenchmarks do
not measure whole-agent tokens or the latency of complete authoring tasks.
# Derived contact geometry

`spatial_twin.conform.support_prisms(bearing_triangles, support_triangles)`
constructs convex support bodies between explicitly selected horizontal bearing
faces and the piecewise planar upper envelope of supplied surfaces. Inputs are
Unreal centimetres relative to a nearby origin. This is an offline Python helper
for authoring recipes; no new MCP action or inferred scene relationship.

The caller selects actual exported source triangles and preserves their IDs and
revision. Polygon partitioning retains openings between bearing faces. Unknown
surface coverage and subdivision/body budgets refuse instead of bridging missing
data. Default contact clearance is0.02cm; depths below0.1cm are omitted to avoid
native convex-welding slivers. These tolerances are explicit, not a claim of
continuous physical support. The helper neither subtracts other obstacles nor
certifies structural engineering or gameplay.

Author the resulting meshes in Blender, export through the existing FBX/UCX
adapter, stage them, and require Shadow collision/navigation validation plus
native import parity before placement. Building footprints must come from real
faces: sampling a rectangular AABB can miss narrow walls or classify a doorway
as an unsupported floor. HLOD proxies and their original actors are distinct
sources and must not be counted as two authored buildings.
# Regional navigation geometry

Navigation query results include `baseline_tile_pool_full` from the canonical
agent's exported tile count/capacity, without another native query or a tile
inventory dump. `null` means legacy capacity metadata is unavailable. In Shadow
this still describes the **canonical baseline**, not the rebuilt tile count.
Full capacity is a warning, not proof that any particular route is missing.
A found path remains valid within the exported graph; a negative result says
nothing about unexported areas or a different future navigation build. `UNKNOWN`
must not be treated as unreachable. Geometry input coverage, streaming coverage
and tile capacity are separate limits.

At full baseline capacity, Shadow reconstruction refuses any requested tile
coordinate with no baseline layers, including batches that also contain covered
coordinates. Replacing only covered coordinates remains allowed; a native
rebuild that cannot fit new layers still fails. The tool never silently increases
the project's navigation pool or changes the agent. Adjust the native generation
configuration through official Unreal MCP only in the intended authoring scope,
then synchronize and verify coverage before retrying.

Offline navigation reconstruction selects triangles through each cached mesh's
BVH before transforming vertices. The conservative local selection preserves
triangle order/winding, supports LWC and negative scale, and includes Recast's
rounded cell border. Filled-volume rasterization retains complete meshes so
that clipping cannot change the fill extent. Canonical geometry is unchanged.

The paired regional test on 4 October 2026 rebuilt six cells for each of two
agents. Full-geometry and indexed output hashes matched in every trial. Warm
median times were 7.659 to 2.919 seconds (Human, 2.62x) and 7.130 to 3.076 seconds
(Scoride, 2.32x); three measured trials per arm, alternating order after warmup.
This measures offline navigation work, not a whole-agent task or token savings.


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
footprint corners and samples share a single actual box face, a single actual
collision triangle, or a single face of a small closed convex collision mesh.
A triangle is convex even on an open terrain mesh: hits at all four rectangle
corners on that same source triangle prove the complete rectangle lies on it.
Different triangles do not gain this certificate merely by matching samples.
Ray normals use inverse transpose, preserving source orientation under reflected
and nonuniform scale. For the closed-mesh certificate, topology welds exact source
coordinates (including UV seams), requires two faces per edge and a connected
surface, and checks every vertex against every outward face plane. Proof is
capped at256triangles/512vertices and uses a bounded local numerical tolerance.
Open, concave, disconnected, nonmanifold or larger meshes cannot receive the
closed-mesh certificate; their single triangle can still prove its own surface.
Multiple supporting components/primitives or different faces remain UNPROVEN.
It certifies the surface under the whole conservative footprint rectangle;
it does not certify full bottom contact, navigability or the requested slope.
Use the returned normal and height range for those requirements. Primitive
ray origins inside collision are rejected. `include_operations=true` returns
the exact validated patch operations for subsequent authorized live execution;
it does not apply them or weaken revision/Shadow/native verification.


The Codex launcher defaults to `--tool-profile workflow`: nine high-level
entrypoints, with all registered exact reads available through `world_read`.
Request `tool_describe(names)` only for unknown schemas (1..8 exact names);
arguments still undergo their original strict validation. `--tool-profile full`
or `compact` restores individual tool discovery. No capability or canonical
write permission is added or removed. Full plans/receipts stay in patches and
artifacts; final delivery reports only confirmed state and unresolved limits.


## Bounded Shadow pages and schema batches (160)

Shadow region/overlap pages select indexed IDs and sparse overrides first, then
fetch canonical source only for the requested page. Moved/deleted entities are
excluded from canonical candidates; created entities come from the overlay.
The existing revision/query/patch cursor contract is preserved.

world_read accepts optional schemas: an array of 1..8 exact tool names needed
for subsequent calls. Source reads and tool_schemas share one response. Unknown
or repeated names fail. Metadata exceeding the combined12KB payload budget
is kept verbatim at tool_schemas_reference; source paging limits remain intact.
Known schemas should not be requested again. No live Unreal action occurs.

## Grounding selected Actors (168)

patch_ground is offline authoring: explicitly selected persistent Actor IDs,
current base_revision and max_distance in cm. It derives nine footprint samples
from the actual world-bounds envelope, including available collision bounds;
ray origins start below that envelope. Every accepted surface must have PROVEN
continuous coverage and meet max_height_difference/max_tilt_degrees. Moving
support owners, supports attached to selected Actors, and selected attachment
ancestor/descendant pairs are refused. Any rejection blocks the entire plan.
Only MOVE_ACTOR operations are produced; XY, rotation, scale, assets and navigation
defaults are preserved. Strict Shadow collision/navigation checks still apply.
Compact returns counts, minimum_normal_z, maximum_height_difference and patch
diagnostics; detail='full' includes individual support identities and positions.
include_operations returns exact validated operation IDs for authorized execution.
This proves a conservative rectangular envelope over a surface, not exact bottom
contact, semantic suitability, navigation or physical settling. Unreal remains
authoritative for effects/readback; no live actions occur in this tool.


Actor source discovery: world_search(kind="Actor",limit=20,component_fields=["transform","bounds","asset_id"],component_limit=4) embeds bounded direct-child pages in the same database read snapshot. Each page preserves revision/cursor and is identical to entity_children. Inspect kind/coverage; missing fields remain unknown. Child expansion supports at most20Actors and8children per Actor, never recursive instances or geometry dumps.

## Optional focused MCP profile

`--tool-profile focused` exposes three entrypoints: `world_read`, `shadow_plan`
and `tool_describe`. Existing full/compact/workflow catalogues are preserved.
Exact read and patch schemas remain available through `world_read(schemas=...)`;
request only a missing contract alongside the source. `shadow_plan` delegates
only to named existing offline patch/staging handlers, validates their arguments
and requires `map_id` equal to the selected world's map. It never executes Unreal
or writes Canonical. Catalogue190 is2102bytes versus10432bytes for workflow;
this79.85%byte reduction is **not** a token or complete-task benchmark result.

asset_get includes dependency_coverage with the actual package ID and CURRENT/UNKNOWN state. Query outgoing DEPENDS_ON on that observed package ID: complete=true requires CURRENT source evidence. An empty relationship list alone never proves zero dependencies. world_status.asset_relationship_state=INDEXED_REFERENCED indicates that referenced-asset migration ran, not universal dependency completeness. The workflow profile routes these handlers through world_read; keep its bounded pagination loop and local reduction inside Code Mode.

Asset-impact reads should use asset_usage(kind="Actor", detail="summary") when the decision needs counts/groups/ingombri rather than Actor identities. label_prefix filters actual source labels; group_by supports class, level, or label_prefix with label_group_depth1..8 underscore-separated segments. total deduplicates component/instance owners. bounds is the source Actor AABB union; bounds_complete must be true before treating it as complete. groups are limited; groups_complete=false requires narrowing the prefix or reading relevant entities, never guessing the omitted groups. No spatial scan of the whole world or asset loading occurs.
asset_get(include_dependencies=true) returns at most20 direct package references with actual IDs/paths, dependency_count and dependencies_complete. CURRENT refers to the native Asset Registry dependency projection, not proof of arbitrary unsaved package contents. Missing registry data, missing target paths or more than20 dependencies are explicitly incomplete; use dependency_query and normal graph pagination for deeper analysis. Prefer one world_read to combine the required asset metadata and owner summary at the same revision. Do not enumerate every owner merely to compute a count/union. The shared dispatcher distinguishes relation direction strings from ray direction vectors and still rejects invalid enums/nonfinite rays.
