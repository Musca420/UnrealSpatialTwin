# Installation

The product has no dependency on a particular game, map, asset kit or checkout.
The current native target is **Unreal 5.8 / Win64**, tested on 5.8.2. Other engine
versions/platforms require a native compatibility adapter and fresh tests.

Create a portable source distribution:

```powershell
python tools/UnrealSpatialTwinMCP/package_plugin.py --output <new-output-directory>
```

Copy its `UnrealSpatialTwin` folder into `<YourProject>/Plugins`, enable it, and
compile with the project's Unreal C++ toolchain. For a Blueprint-only project,
the engine provides `RunUAT.bat BuildPlugin -Plugin=<absolute-uplugin>
-Package=<new-package-directory> -TargetPlatforms=Win64` to build an installable
plugin; verify its result against your engine. Enable the official MCP toolsets.
ToolsetRegistry is the native MCP dependency; Twin never replaces it.

For a shared development checkout on Windows, link the source into the project's
own Plugins directory with `scripts/Link-SpatialTwinPlugin.ps1 -Project
<absolute-uproject> -PluginSource <absolute-UnrealSpatialTwin-folder>`. This is
idempotent and refuses to replace a different existing installation. A relative
`AdditionalPluginDirectories` path can resolve differently when opening the
project through a directory junction; a project-local plugin remains discoverable
through either path. The linked source must remain on disk. For distribution,
use the independent copy described above.

When enabling Unreal's complete `AllToolsets` bundle in a new project, its
GameFeatures dependency also needs the engine's `GameFeatureData` Asset Manager
rule in `Config/DefaultGame.ini`:

```ini
[/Script/Engine.AssetManagerSettings]
+PrimaryAssetTypesToScan=(PrimaryAssetType="GameFeatureData",AssetBaseClass="/Script/GameFeatures.GameFeatureData",bHasBlueprintClasses=False,bIsEditorOnly=False,Rules=(CookRule=AlwaysCook))
```

This is an engine bundle prerequisite, not a game content dependency. A commandlet
can finish its own test with result 0 while engine initialization reports errors;
check both the test receipt and the process exit/engine error summary.

Install the dependencies from
`SpatialTwinCodexPlugin/runtime/tools/UnrealSpatialTwinMCP/requirements.txt` in
Python 3.11+ and set `SPATIAL_TWIN_PYTHON` to that executable. Install
`SpatialTwinCodexPlugin` in Codex and open the game workspace. Its launcher finds
a single `.uproject` in the workspace/parents or two bounded child levels;
ambiguous workspaces require `SPATIAL_TWIN_PROJECT=<absolute-uproject>`.
Set `SPATIAL_TWIN_HOME=<absolute-SpatialTwinCodexPlugin/runtime>` in the environment
used to launch Unreal for native patch dispatch. Jev is optional; deterministic
reads/checks work without its API key.

The initial explicit scan uses the current editor map through the official
SpatialTwinToolset, or `UnrealEditor-Cmd <YourProject.uproject> -run=SpatialTwinScan
-Map=/Game/YourMap`. `-Map` is required: there is no default game map. Data defaults
to `<YourProject>/Saved/SpatialTwin`. Use `world_status` before any rebuild.
The launcher accepts the server's `--status` and `--call/--arguments` options.

Saved package changes reconcile incrementally on resume, including previously
unsaved package owners after a crash. Configuration/module/engine changes still
require explicit handling; no silent full scan. Each additional map gets its
own `maps/<stable-map-hash>/` store. Returning to a cached map resumes it; an
unscanned map requires its first explicit scan. `active_world.json` publishes
the selection atomically. The MCP follows it between requests and keeps one map
pinned during each request. `--map /Game/YourMap` selects a cached offline map;
`world_maps` exposes store paths. Action clients stay pinned and check their
actual native target before mutations. Never copy patches between stores.

Older stores report `collision_bounds_state=LEGACY_REQUIRES_UPGRADE`. The native
writer upgrades cached collision extents transactionally on resume, deriving
bounds from exported geometry without loading Actors. This is a one-time cache
pass; subsequent resumes skip it. A standalone maintenance command is available:

```text
UnrealEditor-Cmd <YourProject.uproject> -run=SpatialTwinValidate
  -SpatialTwinRoot=<exact-map-store> -UpgradeCollisionBounds
  -ValidationOutput=<receipt.json> -unattended -nullrhi -nosound
```

Pass the actual store returned by world_status (including maps/<hash> when
applicable). No map argument or Actor scan is required. The writer lease refuses
maintenance while another editor/scanner owns that canonical store. A failure
rolls back; fix the reported missing/corrupt cache data and retry explicitly.
Legacy collision queries remain incomplete and patch validation refuses them
until the upgrade succeeds. Do not mark the data current by editing metadata.

The native database uses Windows WinSQLite with real file locking/WAL.
Unreal SQLiteCore's virtual filesystem lacks the shared-memory semantics
needed for this multiprocess Canonical store. Build with your qualified UE
toolchain; no game checkout is required.
