# Unreal Spatial Twin

The native plugin is in `Plugins/UnrealSpatialTwin`; the offline MCP is in
`tools/UnrealSpatialTwinMCP`. Unreal is authoritative. SQLite and content-addressed
geometry/navigation files are its persistent observation model. The official
Unreal MCP remains the execution layer. The Twin MCP never controls the Editor.

`SpatialTwinCore` owns transactions, schema, Chaos narrow-phase collision and
Recast/Detour offline operations. `UnrealSpatialTwin` extracts editor objects,
Asset Registry dependencies, World Partition descriptors and native navigation
inputs. Its Toolset Registry extension exposes status, scan, flush, validation
and patch dispatch through the existing official MCP server.

Canonical writes belong exclusively to the native synchronizer. Python opens
`world.sqlite` in read-only/query-only mode and uses a separate `patches.sqlite`
for proposals and action receipts. A Shadow World overlays the canonical
revision without writing it. Asset geometry is referenced by hash and shared
between actors and instances.

Publication is one SQLite transaction: entities, scene graph, spatial index,
change history and READY snapshot become visible together. WAL readers retain
their previous consistent view while a rebuild is in progress. Geometry files
are published before their database references; a failed rebuild can leave
unreferenced blobs but cannot expose half an entity update. `manifest.json` is
an informational atomic replacement; SQLite is the authoritative Twin state.

Canonical means observed editor state, including unsaved changes. A saved
revision or package receipt is separate evidence. Neither a successful scan
nor offline path query proves a played game session.

See `SPATIAL_TWIN_PRODUCTION_COVERAGE.md` for fresh test evidence and unfinished
acceptance criteria. This document describes implemented boundaries, not a
claim that every required integration scenario has passed.

Each map keeps an independent store, revision sequence and patch journal. The
first store remains in place; additional maps use `maps/<map-hash>/`. Native map
load events flush/disconnect the old synchronizer and resume an existing READY
store for the new map. Only a previously unscanned map needs an initial scan.
The atomic `active_world.json` selector lets a persistent offline MCP follow map
changes between requests. A complete request, including nested read batches, is
pinned to one store. Action clients remain pinned and check the live native target.

The portable source distribution bundles native source, offline runtime and
Codex instructions; no game module, content kit, credentials or fixed game path.
A private game and the blank SpatialTwinFixture are integration test projects.
