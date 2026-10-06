# Twin data model, schema 1

Collision source coverage is explicit per query representation:
`collision.simple_coverage` / `complex_coverage` and corresponding asset fields
`collision_simple_coverage` / `collision_complex_coverage` are `AVAILABLE`,
`EMPTY`, or `UNKNOWN`. `EMPTY` requires successful native physics creation and
zero native elements, or an observed brush component with no BodySetup at all.
Unsupported or incompletely exported bodies are `UNKNOWN`.
Legacy arrays without coverage retain their previous conservative behavior;
an empty array alone never proves free space. Trace-mode routing chooses which
coverage applies. Asset rebinding replaces, rather than inherits, this evidence.

`navigation_input_coverage=native_empty_geometry` records an exact native octree
owner with no collision/voxel geometry after lazy gathering has completed.
Pending data is not empty. Area modifiers and links remain separate inputs and
are still processed. This component evidence is not an asset-wide promise that
new components will have identical navigation behavior. A scoped native asset
cache refresh updates affected owners; no full-world rebuild is necessary.

Each entity has a stable string `id`, `kind`, JSON source record and database
revision. Indexed columns cover parent, owner actor, asset, object path, class
and double-precision AABB. World, Level, Actor, Component, Instance, StaticMesh,
Material, Asset, DataLayer and NavRegion records have native producers. Some
Unreal types are represented by their Actor/Component record and native class,
including Landscape, SplineMesh, volumes and primitive shapes.

`NavigationInput` stores native octree data owned by an Actor or a nonprimitive
component. Its ID is the source entity ID plus `:navigation`; `source_entity_id`
and `HAS_NAVIGATION_INPUT` retain ownership. Native bounds, transform, area
modifiers and link-presence flags are spatially indexed. Actor moves and deletes
also affect these records; an input is not a collidable/renderable component.
Legacy startup enriches only the loaded nonprimitive navigation owners once.
`NavRegion.input_coverage` audits registered octree owners against exported data;
bounded `input_gaps` preserve missing regions, including custom sub-elements.

Actor identity combines map identity and `ActorInstanceGuid`. World Partition
descriptors use the container's actor GUID mapping. Component identity combines
actor identity with a deterministic hash of component path, name and class.
Actor source `transient` records the native object/class flag. Such session objects
are not serialized with the map; a recreated object can have the same path and a
new GUID. Its old ID is retired, not silently rebound. Resume reconciles these
objects separately from unchanged package signatures and exports only their
current owners. Deletion removes owned components, navigation inputs and graph
edges in the same canonical transaction. An observed transient object retires
legacy predecessors at the same path/class even when their old source lacks the
flag. Descriptor-backed identities are excluded from this path-based retirement.
Legacy objects missing both native evidence and the source flag are not guessed
to be transient. Offline data remains the last confirmed snapshot, with freshness
reported separately; it cannot predict future engine-generated identities.
No memory addresses are persisted. Instance GUIDs are stored independently of
index and tracked through Unreal's instance relocation notifications, including
the tested undo/redo path. If a package changed while the synchronizer was closed,
unique exact local-instance transforms preserve surviving identities. Moved,
new, duplicate or legacy instances without that evidence receive new identities;
`instance_identity` reports this boundary instead of silently reusing indices.

Transforms preserve Unreal centimeters, doubles, quaternion `[x,y,z,w]`, scale,
world and relative transforms. AABBs use REAL columns; SQLite RTree bounds are
conservative single precision and every candidate is refined against source
doubles. `bounds` remains native visual bounds; `collision_bounds` and
`collision_local_bounds` retain physical extents. Indexed extents conservatively
include both, so collision outside a rendered mesh remains discoverable. This
preserves Large World Coordinates without relying on RTree
precision for the final decision.

`relationships(source,target,kind)` contains real ownership, attachment,
asset, package and dependency edges. Proximity is queried, not stored. Source
bounds and transforms remain separate from derived distance/intersection output.

DataLayer records retain direct `visible` / `loaded_in_editor` flags and native
`effective_visible` / `effective_loaded_in_editor`, which include parent state.
`ATTACHED_TO` records the layer hierarchy; `IN_DATA_LAYER` records real Actor
membership. Dedicated editor layer callbacks track same-frame batch changes,
including removals whose generic object-modification notification was coalesced.
Layer deletion/reset reindexes affected graph members, not every world Actor.
Changing editor visibility or loading does not mean the Actor stopped existing.

`changes` retains before/after source JSON. `snapshots` has BUILDING, READY and
INVALID states. `metadata` identifies schema, project, engine, canonical
revision and synchronization time. Readers reject absent READY snapshots or
unsupported schemas. Schema upgrades must occur in the native writer.

`package_sources(path,signature)` records saved project packages, configuration
and the game module baseline. Signatures use size and modification time. An
unchanged READY baseline can reconnect without geometry export; changed,
added or deleted packages automatically reconcile their owners and affected asset
users. Registry file scans are restricted to changed packages. Configuration and
module changes require explicit handling. This is not a
content hash or evidence that runtime navigation stayed unchanged. Confirmed
scoped saves update only the touched package signatures.

`source_inventory_version=2` covers all project uasset/umap/config files plus the
game module. The normal qualification-project proof contains 47,487 signatures. Legacy
inventories affected by the recursive-file API's clear-by-default behavior are
refused at resume; the guarded closed-editor checkpoint can repair a proven
unchanged baseline. Size/mtime can miss same-sized backdated changes. Matching
engine version is required; this is not a hash inventory of every engine or
plugin DLL, nor complete runtime navigation freshness tracking.

Authored assets are immutable entries in `patches.sqlite` with blobs under
`staging/`. They have `staged:` IDs and AUTHORED provenance. They never appear as
canonical entities until Unreal imports and the native synchronizer exports them.

STG1 blobs contain magic, version, uint32 vertex/triangle counts, double XYZ
vertices and uint32 triangle indices. Hashes deduplicate render, collision and
navigation geometry. STN1 container version2 stores native Detour parameters
(including agent dimensions and each resolution's BV quantization) and tile bytes;
these require the matching engine build and native Twin module. Do not treat
STN1 as a portable cross-version Detour interchange format. Version1 caches
omit required native parameters and are refused; regenerate their navigation
through Unreal. Readers validate complete tile layout and polygon/detail/BV
indices before passing a tile to native Detour. Experimental segment links
require native regeneration and return UNKNOWN offline.

Asset ID and Unreal object path are distinct: for example
asset:/Game/SpatialTwinBench/SM_ST_Rail_Development.SM_ST_Rail_Development is the
persistent ID; /Game/SpatialTwinBench/SM_ST_Rail_Development.SM_ST_Rail_Development
is the object path. Use returned IDs for asset_usage. An unknown ID is an error,
not proof of zero usages. A known cached asset may legitimately have no usages.

Point navigation links are native source records in `navigation_links`: world
endpoints, supported agent indices, area class, uint64 ID as a string, direction,
reversed/generated/cheapest-area flags, snap radius and optional snap height.
Endpoints in a processed modifier may already reflect Unreal projection;
`requires_projection` prevents treating them as unprocessed authored points.
NavRegion stores the native default `link_area_order`, agent index and link flag.
Navigation owner version2 enriches known link owners incrementally, including
previously indexed unloaded owners, without a full world rescan.

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


Rasterization80: navigation_modifiers[].mask_fill_underneath stores native
FCompositeNavModifier mask state. NavRegion.rasterization_mask_version=1
requires octree audit; native capability STNavigationRasterizationVersion=2
supports masks. Cached owner fill/convex/mask flags are refreshed from the
live octree within the same canonical transaction; geometry stays unchanged.
ISM/HISM instances inherit rasterization flags from their aggregate owner.
Missing flags remain unknown and cannot silently default during tile rebuild.
# Text search index

`text_search_version=1` identifies the derived FTS5 trigram index over label,
object path and class. SQLite triggers maintain it in the same transaction as
entity insert/rename/delete; transform-only changes do not retokenize text.
Opening an older store upgrades the schema and backfills the index atomically
from persisted entity metadata, without loading Unreal objects or changing the
world revision. A failed upgrade rolls back to the previous readable snapshot.
The native writer performs this migration; the MCP reader remains read-only.

FTS postings omit positions. Candidate searches use literal three-character
tokens followed by the original LIKE filter, preserving punctuation and case
semantics. One- and two-character searches use a covering metadata index. Entity
source JSON is fetched only after the matching IDs have been paginated. A bounded
257-candidate probe routes common terms to the ordered metadata index, avoiding
a full sort of a very large FTS match set. Legacy readers still work; new readers
report `text_search_state=LEGACY_REQUIRES_UPGRADE` until the native upgrade runs.

Every stored patch operation has a unique nonempty `operation_id`. The store
adds it when omitted. It identifies journal and recovery effects; it is not an
Unreal property override. Preserve it when passing validated operations to the
official action executor. Use the operation-specific declared fields when
checking overrides; valid metadata must not trigger a false rejection.

Referenced assets carry IN_PACKAGE edges even outside /Game, including Engine and mounted content. Native versioned migration restores missing cached references once, without exporting all Actors. Package dependencies_state is CURRENT only when Asset Registry supplies its direct dependency set; missing coverage is UNKNOWN, including runtime objects. Indexing a dependency target preserves its existing richer package record. Mesh material replacement removes stale USES_ASSET edges transactionally.
