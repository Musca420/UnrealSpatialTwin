# Unreal MCP integration

Spatial Twin MCP = persistent world intelligence. Unreal MCP = live Unreal
execution. They are separate connections with different responsibilities.

The installed engine is Unreal 5.8.2. Its official ModelContextProtocol plugin
and Toolset Registry are used directly. `USpatialTwinToolset` registers after
engine initialization under `UnrealSpatialTwin.SpatialTwinToolset` and exposes
`spatial_twin_status`, `spatial_twin_sync`, `spatial_twin_rebuild`,
`spatial_twin_validate`, `spatial_twin_apply_patch` and the scoped package
finalizer `spatial_twin_save_patch`. `spatial_twin_cache_asset(object_path)` exports
one newly imported or unused asset and refreshes its existing dependents; import
itself remains an official StaticMeshTools operation. No additional live MCP server exists in
this plugin.

`apply_patch.py` reuses `scripts/unreal_mcp.py` and official SceneTools,
ActorTools and ObjectTools for actor creation/removal, transforms, attachments
and properties. The offline MCP has no spawn, editor command or Python
execution tools. Patch dispatch launches the explicit action CLI; it does not
perform direct SQLite canonical mutations.

Default official endpoint is `http://127.0.0.1:8000/mcp`; the action CLI accepts
`--url` for an isolated test editor. Native patch dispatch reads the official
server's configured port and URL path, including fixture port8001.
Blueprint authoring, materials, compilation,
PIE, Slate and automation continue to use existing Unreal toolsets.

Commandlets `SpatialTwinScan -Map=/Game/YourMap` and
`SpatialTwinValidate` provide UI-free native scan and integrity verification.
`SpatialTwinValidate -CheckpointSources` is a guarded source-inventory migration
for a proven clean saved baseline, not a scan or an editor action server.
The isolated `SpatialTwinTest` refuses to run on the game project: it requires
project name `SpatialTwinFixture` and writes a disposable native fixture.

The current native target requires UE 5.8 Toolset Registry. Compatibility with
engines predating the official MCP is not yet verified or supported; do not
disable dependency checks or install a competing live server as a workaround.

Homogeneous CREATE patches batch existing Scene/Actor/Object tools through the
official ProgrammaticToolset. Partial creation references are journaled before
property changes; incomplete batches fail without automatic replay. Canonical
state confirmation and scoped saving remain mandatory. Import/material/readback/
asset saving similarly batch the official tools, followed by one native cache
and authored/native parity check.

The action clients accept a caller-owned official MCP session and never close
it. `materialize.py --materials bindings.json --apply --camera camera.json`
uses one session for import, native confirmation, patch execution, scoped save
and real CaptureViewport. Detailed receipts stay on disk; stdout returns only
state/patch/image references. CLI failure retains partial state and the patch
receipts. Rendering uses fresh native synchronization plus canonical before/after
checks, avoiding stale heartbeat pending flags; it remains a sequential frame
pair, not an atomic capture.

Incremental relationship history now journals changed edges rather than copying
every child of a level. Native fixture verifies one new edge among1024siblings
and a delete/reinsert no-op without a spurious canonical revision. Whole-database
integrity validation runs asynchronously in the existing commandlet.
