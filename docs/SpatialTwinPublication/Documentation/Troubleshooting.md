# Spatial Twin troubleshooting

On Windows, a project opened through a directory junction can resolve a relative
`AdditionalPluginDirectories` outside the checkout. Install or link the Twin in
the project's own `Plugins` directory; `Link-SpatialTwinPlugin.ps1` checks the
destination and preserves a different existing installation. The Win64 writer
resolves existing source files to their filesystem paths before comparing saved
signatures and maps changed files back through Unreal's content mounts. A real
configuration/module change still requires explicit reconciliation. Do not
clear the guard or rebuild merely to conceal an alias mismatch.

UNINITIALIZED means no canonical database exists yet. Run a native scan; do
not manufacture canonical records from Python. No READY snapshot means a scan
never published successfully. The previous READY database remains valid during
transactional rebuild; inspect native errors before retrying.

`editor_connected=false` is expected with Unreal closed. Ordinary read and
shadow tools remain available. Heartbeat age controls this flag independently
of canonical revision. Stale data remains historical evidence, not current
editor state. Compare map, last_sync and pending events before execution.

UNKNOWN collision or navigation coverage is not a negative hit or unreachable
proof. Check exported geometry, agent, active-tile coverage, engine build and
native DLL location. A missing navigation cache returns UNKNOWN. Native backends
need Engine/Binaries/Win64 and Windows WinSQLite (provided by the supported Windows installation).

CONFLICTED patches need an explicit proposal update against fresh canonical
state. FAILED/APPLYING patches may contain partially completed external writes.
Inspect SENT and ACKNOWLEDGED receipts and current Twin state before recovery;
never automatically replay the operation list. An acknowledgement alone does
not establish APPLIED.

Save conflicts preserve unrelated unsaved authoring work. Resolve the affected
package deliberately or choose live-only `--no-save`; do not SaveAll. Editor
restarts must preserve shared unsaved state and occur only for technical need.

Cursor expiration is intentional when revision/query parameters change. Start
a new bounded query. Geometry is shared by hash; deleting an actor must not
delete an asset blob still used by another actor.

A repeated startup scan is not the intended workflow. The startup script is
resume-only unless -SpatialTwinRebuild is explicitly supplied. Source mismatch
or inventory version mismatch returns RECONCILE_REQUIRED. Check the exact source
reason; do not rescan silently. Guarded -CheckpointSources is only a legacy
inventory migration with proven unchanged saved sources, not a way to bless
modified files. Signatures use size/mtime and have that documented limit.

If world_query is missing, reconnect the stdio MCP/session after updating code;
a running process's old tool catalog does not gain new definitions. If TypeSafe
is unavailable, use existing direct Twin queries. Configure TYPESAFE_API_KEY
locally, never in a patch, prompt, Git file or diagnostic output. No-match and
revision conflicts require fresh scope/reasoning, not an automatic API retry.

Known acceptance gaps and failed attempts are maintained in
`SPATIAL_TWIN_IMPLEMENTATION.md`. Native fixture PASS is distinct from active-map
World Partition, undo, asset reimport, full navigation parity and live MCP proof.

`Navigation tile pool full; affected region has no baseline tiles`: inspect
the selected agent in native Unreal navigation settings through official MCP.
Correct coverage/capacity deliberately and let the native navigation sync.
Do not rescan geometry, repeatedly validate the same patch, or silently enlarge
the offline pool: none establishes native path availability.

`Spatial Twin source changed since process startup` / `Native offline library
changed since loading`: restart the Twin MCP process after completing deployment,
or use a fresh `server.py --call` process. Unreal need not restart for Python-only
updates. Each MCP request and Shadow validation checks its imported source version;
native backends check the loaded file identity. Existing processes predating these
guards still need reconnection. A newly installed file cannot certify old loaded code.

`text_search_state=LEGACY_REQUIRES_UPGRADE`: install the matching schema resource
and reopen the native Twin writer once. Its schema transaction indexes persisted
metadata; no full world scan is required. Readers keep the old behavior until
commit. `no such module: fts5` or `no such tokenizer: trigram` requires a SQLite
build providing both capabilities, in the native writer and Python reader.
Do not set the version marker by hand. The upgrade and its marker commit together.
Very short searches may still inspect all text metadata; narrow kind/region when
available. Native full-world navigation rebuilding is separate from this index.


`spatial_twin_cache_asset` is an explicit refresh: it marks the asset dirty and
invalidates its users. Repeating it on a widely used unchanged mesh can reload
thousands of actors. Read the exported asset first; use refresh only for missing
geometry/collision export or after authoring/import. This is distinct from a
full scan. Build88 live evidence retains the65.108s/3367users failed warm-up.

`STALE_NATIVE_REBUILD_PENDING` with no pending actor edits can mean native
navigation work is still queued or locked. The synchronizer polls native
readiness once per second only while a navigation refresh is pending, then
exports when the octree, dirty areas, bounds, registration and build queues
are idle. Changes outside navigation bounds can finish without a tile event.
The poll does not rescan actors or rebuild navigation. Native status exposes
`navigation_refresh_pending`, `navigation_building`, `navigation_dirty_areas`,
`navigation_locked` and `navigation_pending_bounds`. An editor build lock
keeps data stale conservatively; do not force the metadata to AVAILABLE or
clear native dirty queues. AVAILABLE describes exported tiles, not global
coverage or sufficient tile-pool capacity.

When Unreal runs in the background, its CPU throttle can delay navigation even
with no actor edits pending. Native `background_tick_requested` reports the
Twin's scoped request through `ShouldDisableCPUThrottlingDelegates`: pending
sync/scan work, or unlocked native Recast work, including a build that starts
after an actor/property update was already synchronized. The idle observer
checks native readiness once per second and invalidates the cache when such
work appears. Recast actor edits also request a navigation export.
It releases the request when idle or in error, and does not bypass navigation
locks. The callback changes no saved or in-memory user preference. Other Unreal
subsystems can independently request CPU time. Do not disable global throttling,
rescan the world, or treat the request flag as proof of navigation completion.

Regression: on the isolated `SpatialTwinFixture` project, run
`-run=SpatialTwinNavigationTest -NavIdleFixture -Map=/Game/Unused
-Position=0,0,0 -SpatialTwinRoot=<new-empty-directory>` with the usual
headless options. It tests blocked freshness, no revision churn, idle recovery
without tile events and disabling a component's navigation contribution.
It also queues native work without any Twin actor event and verifies CPU demand,
cache invalidation while blocked, event-free recovery and release at idle.
The commandlet creates an unsaved test world and never saves a game map.

The same readiness gate also applies to full scans and direct native exports.
A scan during a native build or build lock publishes `BUILDING`, preserving
the previous navigation cache when one exists. Readers reject that cache for
pathfinding until an idle incremental refresh completes. No second full scan
is needed. The regression above includes an initially locked scan and a locked
rescan, cache preservation, and automatic recovery after unlocking.

World Partition unload regression: in the isolated `SpatialTwinFixture`
project, first create its saved partition map with `SpatialTwinTest
-PartitionFixture`. A separate process can then run `-run=SpatialTwinTest
-StreamingFixture -SpatialTwinRoot=<the-partition-store>`. The fixture must
have its saved `GenericPartitionCube`; do not run it on a game project. This
test resumes the existing cache, loads/releases/reloads a real World Partition
reference, compares cached components/geometry/relationships, moves and restores
the actor through normal property events, and saves only its test package.
`native-streaming-test.json` is written only on success. No full scan is requested.
Unreal's descriptor `IsLoaded()` can still find an unregistered UObject before
garbage collection: the test checks level membership and component registration
to prove unload. This covers external-actor residency; nested level instances,
data-layer transitions and arbitrary streaming layouts require their own tests.

DataLayer regression: the same saved isolated partition fixture supports
`-run=SpatialTwinTest -DataLayerFixture -SpatialTwinRoot=<new-empty-store>`.
It scans once, then verifies creation, hierarchy, membership, inherited editor
visibility/loading, rename Undo/Redo and deletion using the automatic ticker.
`native-data-layer-test.json` is written only on success. No explicit sync or
forced dirty notifications follow the baseline. The test creates its own
transaction buffer when the commandlet lacks one and never saves layer edits.
Bind DataLayerEditor callbacks after an editor world context exists; forcing
that subsystem's early initialization can assert during engine startup.

Failed incremental synchronization keeps its pending work and error. Automatic
ticks stop retrying that same failure, so editor/MCP requests can continue.
Inspect `spatial_twin_status`, resolve the reported source/reference problem,
then explicitly call `spatial_twin_sync`. A successful transaction clears the
error and resumes automatic updates. Never treat a ready snapshot with an
error or pending synchronization as a fully synchronized editor.
