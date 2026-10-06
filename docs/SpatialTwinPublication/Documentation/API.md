# API reference

The installed plugin advertises three focused entrypoints: `world_read`,
`shadow_plan` and `tool_describe`. The full profile has 35 advertised schemas;
wrappers delegate exact registered handlers and enforce map/revision contracts.
The [schema snapshot](https://github.com/Musca420/UnrealSpatialTwin/blob/main/docs/api-schemas.json) is from real qualified MCP responses.
Runtime source/contracts define output shapes; obtain fresh schemas on updates.

Example exact read envelope:

```json
{"queries":[{"tool":"world_search","arguments":{"kind":"Actor","limit":20,"fields":["label","class","bounds","transform"]}}],"schemas":["patch_place"]}
```

Keep returned status/project/map/revision/coverage. Page using the exact returned
cursor/next_query. Missing data is UNKNOWN, never zero or permission to write.
`shadow_plan` accepts `tool`, its exact `arguments` and the current `map_id`.
`patch_prepare` validates operations; `patch_status` records lifecycle separately
from native saving. Live application is through official Unreal MCP, outside
this offline server. See ShadowWorld for once-only application/recovery.

| Full-profile handler | Description |
|---|---|
| `tool_describe` | Exact registered schemas for 1..8 named Twin tools, including reads exposed through world_read. Request only unknown arguments; no world/editor access. All implementation and validation remain available in every profile. |
| `world_status` | Freshness, READY snapshot, coverage and counts; no editor request. |
| `world_maps` | Cached map identities and store paths. Each map keeps its own history/patches. |
| `world_query` | Jev selects and executes one supplied bounded Twin query. Fixed arguments; no Unreal control. One choice bypasses AI. |
| `world_read` | Known reads with fresh status. Bundle 1..16 {tool, arguments}, pages max20. Returns status and results:[{tool,data,next_query?}]; follow that row's next_query at the same revision. Entity pages in data have entities/cursor; bounds are [[minX,minY,minZ],[maxX,maxY,maxZ]]. Optional schemas returns tool_schemas:[{name,inputSchema,outputSchema?}], an array to find by name, never schemas[tool]. Omit schemas when known, never send an empty list; supply1..8 distinct names when missing. Source and only missing contracts together; no inventory turn. Rejects mixed Canonical/Shadow versions. No model calls; large results/schemas stay on disk, so narrow fields/pages. |
| `world_search` | Search source label/path/class. Returns {revision,entities,cursor}; each entity retains id/kind and requested fields. component_fields embeds Actor.components:{revision,entities,cursor}, with requested fields (e.g. asset_id) on each component entity. Check exact selection and both cursors locally before dependent reads in the same execution. Requires kind=Actor, limit<=20 and component_limit1..8; follow child cursor with entity_children. No recursive expansion or inferred suitability. Source units cm. StaticMesh is exact mesh kind; Asset is registry metadata. |
| `world_region` | Sphere region, summary by class or bounded entities. radius is cm. |
| `entity_get` | Compact source identity/pose/bounds. fields supports nested paths such as collision.enabled or properties.bHidden; selecting collision returns all its geometry, so request only needed leaves. Unknown leaves remain absent. |
| `entity_children` | Direct source containment; paginated. |
| `entity_relationships` | Actual source relations. No inferred NEAR/LEFT_OF relations. |
| `spatial_nearest` | Indexed nearest/k-nearest by distance to exact bounds, in cm. |
| `spatial_overlap` | Indexed broad-phase region. Component gives group poses/assets; Actor gives native spawn class. Bundle both in world_read if needed. fields supports collision.enabled, collision.simple_coverage, etc.; avoid full collision geometry for placement planning, use spatial_support and patch_prepare for exact checks. Source: class, transform, bounds, actor_id, asset_id. phase=AABB is not a penetration certificate. |
| `spatial_raycast` | Collision triangle raycast with normals and explicit coverage. No screenshots. |
| `spatial_raycast_batch` | 1..128 rays in one consistent indexed read; selected hit fields, explicit completeness. No editor requests. |
| `spatial_support` | Complex collision footprint samples in cm. Compact preserves decisions, positions, height ranges and continuous-support identity/normal/primitive. Full adds source geometry hash/tolerance and certificate scope. Sampling alone never proves continuous support or navigation. |
| `asset_get` | Cached asset facts; fields selects exact/nested source fields. Returns {revision,entity,dependency_coverage:{state,package_id,source,live_state,package_dirty}}; coverage is an object. state describes the saved Asset Registry projection; live_state is CURRENT only when indexed package state is known clean, UNKNOWN_UNSAVED when dirty, UNKNOWN for legacy/missing data. include_dependencies adds dependencies:[{id,path}], dependency_count and dependencies_complete. Require CURRENT plus dependencies_complete for the complete Asset Registry projection; unknown or >20 dependencies has dependency_query for pagination. No Unreal request or guarantee about unsaved package references. |
| `asset_stage` | Cache authored FBX and UE-coordinate geometry for Shadow. No Unreal edits or canonical writes. |
| `asset_usage` | Indexed asset users; Actor summary returns unique owner counts, source AABB union and bounded groups, without serializing Actors. Label groups use the first label_group_depth underscore-separated segments. Incomplete bounds/groups are explicit; narrow label_prefix to refine. No semantic inference or Unreal requests. |
| `geometry_get` | Geometry metadata and file reference; never serializes all triangles. |
| `world_changes` | Canonical committed changes, with before/after source state. |
| `world_diff` | Net entity difference over a revision range (not change count). |
| `navigation_status` | Exported navmesh coverage and offline backend availability. |
| `navigation_path` | Detour path within exported tiles; baseline_tile_pool_full warns of capacity. UNKNOWN is never unreachable proof. |
| `is_navigable` |  |
| `nearest_navigable_position` |  |
| `reachable` |  |
| `patch_place` | Sample support and prepare a validated Shadow placement patch in one call. candidates are world ray origins above the desired locations, cm. Uses observed asset bounds for a 3x3 footprint/bottom offset; preserves rotation/navigation defaults. For a new Blender FBX, optional manifest_file stages authored geometry using asset_id as the observed canonical spawn-profile template, then plans with the staged asset in this same revision; no separate asset_stage call/import. Optional positive size_cm sets local-axis dimensions from asset bounds, retaining scale signs. continuous_support is PROVEN only for a single actual primitive box face, actual collision triangle, or a verified closed convex collision-mesh face covering all footprint corners; otherwise UNPROVEN, finite samples alone are not proof. include_operations returns exact validated operations. Rejects unsupported candidates; UNKNOWN blocks the batch. Strict collision/navigation validation included; no Unreal edits. |
| `patch_ground` | Offline vertical grounding of explicitly selected Actors. Preserves XY, rotation, scale, assets and navigation defaults. Uses each actual world bounds envelope (including collision bounds), nine footprint samples and PROVEN continuous support; bounds are conservative, not an exact bottom-contact/physics solver. Refuses moving supports, selected ancestor/descendant pairs, unsupported geometry and tilted/unknown surfaces; no partial patch. Strict Shadow collision/navigation validation. No Unreal actions or semantic target selection. |
| `patch_prepare` | Create/update and validate Shadow in one call. Explicit current base; compact diagnostics, no Unreal edits. |
| `patch_create` | Create DRAFT; cannot write the canonical database or execute Unreal. |
| `patch_update` |  |
| `patch_validate` | Validate shadow effects, rejecting unknown essential facts. |
| `patch_preview` |  |
| `patch_continue` | Prepare/validate only an unexecuted suffix after proving interrupted effects. No Unreal calls or replay. |
| `patch_status` | Inspect, or reconcile interrupted effects as of Canonical. Recovery never replays or saves Unreal. |
