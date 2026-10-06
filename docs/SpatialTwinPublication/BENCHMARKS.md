# Reproduce the qualification and benchmarks

Run commands from the repository root in the environment installed by README.
Use fresh output paths; the tools preserve failed trials and refuse overwrite.
Native launches must run in your ordinary desktop session, with permission for
Unreal to use its normal SDK/cache paths. Never force-kill or replay a failed
write to make a test pass. Fixtures own their newly created projects only.

## Python and actual packaged MCP

```powershell
& .\Build\MCP\venv\Scripts\python.exe -m unittest discover -s tools/UnrealSpatialTwinMCP -v
```

All 134 existing tests are included, covering transactions/leases, history,
queries, completeness, collision bounds, navigation, Shadow, staging,
placement/grounding, stale revisions, input contracts, runtime discovery,
safe installation, benchmark prompts and independent render receipts.

After producing the native matrix below, use its saved storage fixture:

```powershell
$fixtureRoot = (Resolve-Path Build/Qualification/Storage).Path
$storeRoot = (Get-ChildItem "$fixtureRoot\Saved\SpatialTwin\maps" -Directory | Select-Object -First 1).FullName
& .\Build\MCP\venv\Scripts\python.exe tools/UnrealSpatialTwinMCP/package_mcp_checks.py `
  --plugin SpatialTwinCodexPlugin --project "$fixtureRoot\SpatialTwinFixture.uproject" `
  --root "$storeRoot" --output Build/packaged-mcp.json
```

This really launches stdio MCP, requests all 35 public schemas in batches of
eight, queries the native-scanned store, prepares/validates Shadow and checks
seven expected rejections. It checks Canonical remains unchanged and Unreal
is disconnected. Jev is not called.

The expanded checker also verifies deterministic `world_query` without a model
API and two further stale placement/grounding rejections. To exercise its four
positive navigation MCP calls, add `--navigation-root
Build/Qualification/Nav_LinkFixture/Saved/SpatialTwin`. The release ran45calls
with all35schemas and nine expected rejections on the extracted plugin.

## Native fixtures and Chaos/Recast

Build the plugin with README's `BuildPlugin` command first. Supply an FBX
authored by `export_blender.py`, with real closed UCX bodies; use the independent
author benchmark to produce its six-UCX rail. No proprietary sample FBX is
included. Export units, axes and manifest are checked by the exporter.

The exact six-body source recipe is included at
`art/SpatialTwinBenchmark/build_recovered_rail.py`. In an isolated Blender
scene named `SpatialTwinBenchmarkWorkspace`, import that module and call
`build('SM_ST_Rail_Publication', '<repository>/Build/SpatialTwinBench/Authored',
'/Game/SpatialTwinTests/SM_ST_Rail_Publication.SM_ST_Rail_Publication')`.
It writes editable `.blend`, FBX and STG1/UCX manifests inside that fresh owned
folder only. Keep its sources for the native import/parity tests.

```powershell
& .\Build\MCP\venv\Scripts\python.exe tools/UnrealSpatialTwinMCP/release_checks.py `
  --engine "$engineRoot" --native-plugin Build/PackagedPlugin `
  --fbx 'C:\YourAuthoredSource\Mesh.fbx' --output Build/Qualification
$env:PYTHONPATH = "$releaseRoot\tools\UnrealSpatialTwinMCP"
& .\Build\MCP\venv\Scripts\python.exe tools/UnrealSpatialTwinMCP/native_checks.py `
  --engine "$engineRoot" --library "$releaseRoot\Build\PackagedPlugin\Binaries\Win64\UnrealEditor-SpatialTwinCore.dll" `
  --output Build/ChaosRecast
```

The matrix serializes 25 phases: storage/World Partition, streaming, cold
resume, ISM/HISM, Data Layer, mesh swap, empty input, authored UCX/navigation,
transient/persisted-material cold recovery, nested depth2 move/delete/cold,
point/projected/legacy links, fill/mask, navigation idle, slope and capacity.
Both native receipts and actual process exit/engine errors are examined.

Compare the saved native fixtures with Shadow using
`native_asset_parity.py`, `native_link_parity.py`, `native_fill_parity.py`,
`verify_navigation_slope.py` (`--root <fixture>/Saved/SpatialTwin --output
<fresh-file.json>`). Link runs cover `Nav_LinkFixture`,
`Nav_ProjectedLinkFixture`, `Nav_LinkFixture_LegacyRasterFixture`; fill covers
`Nav_FillFixture`, `Nav_MaskFixture`; asset/slope use their matching fixture.
The original release ran all seven comparisons successfully.

## Whole-agent portfolio

`benchmarks/protocol.json`, `trials.json`, `results.json` retain the original
fixed protocol, 16 independent receipts and medians. Local checkout locators
are redacted; hashes retain original provenance. `trials.json` contains native
counters/timings, not private conversation or reasoning text. The raw internal
qualification files remain in the original test checkout.

The independent runner is `cold_agent_benchmark.py`; it uses `open_*` task
runners, real official MCP, guarded once-only delivery and
`verify_production_benchmark.py`. It retains setup, launch, native completed-turn
counters, requested renders and independent verification. Generic helper bodies
were copied verbatim from the measured source; historical game-specific runners
were excluded. Deployment substitutions are recorded in `manifest.json`.

Create a new project with `prepare_benchmark.py --output <new-directory>
--plugin <built-plugin>`. Launch it with `benchmark_fixture.py` through
`-ExecutePythonScript`, setting `SPATIAL_TWIN_BENCH_SPEC` to its generated spec.
That script creates and saves the same 344-source map and scans it once. Close
only after the saved READY receipt and a native clean-state check. Copy the
prepared fixture into separate With/Without directories, set the native plugin
enabled state appropriately in each `.uproject`, and update each `spec.json`
project path. Disable/read no Twin on the Without route. These are preparation
costs, reported separately from the task comparison.

Use the original protocol's four scenario plans as a template: change runtime,
fixture spec and executable paths to your installation; create fresh unique run
names, keep `agent_workflow=open`, focused tools, model/effort, ABBA order,
equal weights, native schemas, output criteria and timing/cache policy fixed
before running. Native schemas must be freshly checked against your own engine;
the original versioned contracts are in the distributed protocol. Set
`SPATIAL_TWIN_ENGINE` to your engine directory when its location differs.

```powershell
& .\Build\MCP\venv\Scripts\python.exe tools/UnrealSpatialTwinMCP/cold_agent_benchmark.py `
  --plan Build/your-fixed-scenario-plan.json --index 0 --cli 'C:\YourCodex\codex.exe' `
  --close-script tools/UnrealSpatialTwinMCP/cold_benchmark_control.py
```

Repeat two With and two Without trials per scenario in ABBA order. Blender's
`mcp-for-blender.exe` is expected in `Build/MCP/venv/Scripts` for authoring; start
its real Blender addon/backend and verify scene connectivity before charging
agent trials. Installing stdio alone is insufficient. Use an isolated Blender
workspace and preserve existing user preferences.

The runner starts real Codex sessions and consumes account tokens. Record CLI
and native/runtime source hashes before trials. Compare sum of per-activity
medians at fixed equal weights; retain failures and restore all originals. Do
not erase OS/DDC/model caches or label these measurements strictly cold/warm.
The release used process-cold trials with existing uncontrolled caches. No
automatic new-model substitution or guaranteed savings is supported.

Historical failed/incomplete series and their costs remain in the benchmark
reports. The functional matrix is broader than the four performance activities;
it does not supply a speed percentage for every supported function.
