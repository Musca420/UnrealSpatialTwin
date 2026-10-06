# Unreal Spatial Twin — Codex plugin for Unreal Engine

[![Python checks](https://github.com/Musca420/UnrealSpatialTwin/actions/workflows/python.yml/badge.svg)](https://github.com/Musca420/UnrealSpatialTwin/actions/workflows/python.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://github.com/Musca420/UnrealSpatialTwin/blob/main/LICENSE)

Persistent spatial data and offline authoring for Codex and Unreal. Read the
world once, prepare changes in Shadow, validate collision/navigation, apply
through the official Unreal MCP, synchronize, save the affected packages and
retrieve a real Unreal render.

Qualified release: **0.1.0**, **UE 5.8.2-56702186 / Windows x64**, Python 3.12,
Codex 0.160.1 with plugins. Python 3.11+ is the intended minimum; only 3.12 was
used for release qualification. Blender authoring was tested with 5.2.1 LTS.
Other platforms/engine builds need separate native qualification.

This is an independent open-source Codex Agent Plugin and MCP toolkit. It is
not an official OpenAI or Epic Games product and does not claim directory approval.

## What it does

| Capability | Workflow |
|---|---|
| Persistent world model | Explicit initial scan, per-map SQLite stores, incremental synchronization, identities and change history. |
| Scene and asset inspection | Bounded actor/component/instance pages, Asset Registry dependencies, indexed spatial queries. |
| Collision and support | Cached source geometry, raycasts, overlaps and grounding; incomplete evidence remains UNKNOWN. |
| Offline navigation | Recast/Detour path queries and Shadow tile reconstruction for qualified native inputs. |
| Offline authoring | Prepare placement, grounding and actor/component/asset patches; stage closed-UCX Blender geometry. |
| Verified changes | Official Unreal MCP applies authorized changes; receipts, canonical readback, scoped saving and real renders verify the result. |

The local MCP uses stdio. It does not replace Unreal's official MCP or run a
public HTTP service. Unreal remains authoritative for applying, compiling,
playing, saving and rendering. See [architecture](https://github.com/Musca420/UnrealSpatialTwin/blob/main/docs/Architecture.md),
[API reference](https://github.com/Musca420/UnrealSpatialTwin/blob/main/docs/API.md), [privacy](https://github.com/Musca420/UnrealSpatialTwin/blob/main/SpatialTwinCodexPlugin/PRIVACY.md) and
[security](https://github.com/Musca420/UnrealSpatialTwin/blob/main/SECURITY.md).

## Example requests in Codex

After installation, open your Unreal project workspace and ask in English:

> Use the unreal-spatial skill. Inspect this map's cached actors, asset usage
> and navigation coverage. Report stale or unknown data before proposing changes.

> Find an existing static mesh suitable for this area, plan its placement in
> Shadow, check support and collision, then apply the authorized change through
> official Unreal MCP. Verify synchronization, save only affected packages and
> return a real Unreal render.

Exact names, assets and targets must come from your own project. Unsupported
geometry, stale revisions and ambiguous identities stop dependent writes.

## Get the source

Download the ZIP from [Releases](https://github.com/Musca420/UnrealSpatialTwin/releases),
or clone the repository:

```powershell
git clone https://github.com/Musca420/UnrealSpatialTwin.git
cd UnrealSpatialTwin
```

Release downloads include a SHA256 checksum and an exact qualification receipt.
Then follow both installation sections below. Native sources require your own
licensed Unreal toolchain; this is not a precompiled binary download.

## Install in Unreal

You need your own licensed Unreal installation and its supported C++ toolchain.
This source distribution contains no Unreal binaries, headers or game assets.
From the extracted repository in PowerShell:

```powershell
$releaseRoot = (Get-Location).Path
$engineRoot = 'C:\Program Files\Epic Games\UE_5.8\Engine'
& "$engineRoot\Build\BatchFiles\RunUAT.bat" BuildPlugin `
  "-Plugin=$releaseRoot\Plugins\UnrealSpatialTwin\UnrealSpatialTwin.uplugin" `
  "-Package=$releaseRoot\Build\PackagedPlugin" -TargetPlatforms=Win64
```

Copy `Build/PackagedPlugin` to `<YourProject>/Plugins/UnrealSpatialTwin` and
enable it along with the official `ModelContextProtocol` and `AllToolsets`
plugins. For `AllToolsets`, add the `GameFeatureData` Asset Manager rule from
[Installation](https://github.com/Musca420/UnrealSpatialTwin/blob/main/docs/Installation.md) to the project's `Config/DefaultGame.ini`.
Preserve existing configuration entries. Compile/restart only as needed.

## Install in Codex

Create a dedicated Python environment inside this checkout. Keep the extracted
source directory available; the local marketplace points to its plugin folder.

```powershell
py -3.12 -m venv Build/MCP/venv
$releaseRoot = (Get-Location).Path
& .\Build\MCP\venv\Scripts\python.exe -m pip install -r .\tools\UnrealSpatialTwinMCP\requirements-tested.txt
$env:SPATIAL_TWIN_PYTHON = "$releaseRoot\Build\MCP\venv\Scripts\python.exe"
$env:SPATIAL_TWIN_HOME = "$releaseRoot\SpatialTwinCodexPlugin\runtime"
$env:SPATIAL_TWIN_PROJECT = 'C:\YourGame\YourGame.uproject'
codex plugin marketplace add "$releaseRoot"
codex plugin add unreal-spatial-twin@unreal-spatial-twin-source
```

Launch Codex and Unreal from an environment containing these variables, or set
them in your own application launch configuration. `SPATIAL_TWIN_HOME` is for
native patch dispatch; the explicit action CLI also works independently. The
plugin manifest starts `python`, so Python must be on PATH; its launcher uses
`SPATIAL_TWIN_PYTHON` for the selected environment.

These are explicit installation commands for the person installing the plugin;
the package does not rewrite an existing Codex configuration. See the
[official Codex commands](https://learn.chatgpt.com/docs/developer-commands#codex-plugin)
and [plugin format](https://developers.openai.com/plugins/build/plugins).

## First use

Open your game workspace and ask Codex to use the `unreal-spatial` skill.
Run the initial explicit scan through `SpatialTwinToolset.spatial_twin_rebuild`
in the official Unreal MCP, or the documented `SpatialTwinScan` commandlet
with an explicit map. Inspect status before rebuilding an existing store.
Data defaults to `<YourProject>/Saved/SpatialTwin`.

The focused MCP exposes `world_read`, `shadow_plan`, `tool_describe`. Through
these it supports indexed scene/asset/dependency queries, geometry and support,
navigation, staged Blender meshes, placement/grounding and Shadow patches.
Use official Unreal MCP for live actions. Verify Canonical synchronization,
scope saving and the render. Never replay an uncertain mutation.

Known deterministic queries and checks require no model API. Jev is optional
and needs your own `TYPESAFE_API_KEY`; privacy details are in
[PRIVACY](https://github.com/Musca420/UnrealSpatialTwin/blob/main/SpatialTwinCodexPlugin/PRIVACY.md). Blender authoring additionally
needs Blender and the separately installed `mcp-for-blender` backend. It is
optional for reading existing Unreal worlds; see [benchmarks](https://github.com/Musca420/UnrealSpatialTwin/blob/main/BENCHMARKS.md).

For the tested Blender route, install `mcp-for-blender==2.0.0` in the same
environment and install/enable its matching Blender addon using the upstream
[manual setup](https://github.com/ahujasid/mcp-for-blender#manual-setup-in-case-the-automatic-setup-doesnt-work).
Start its loopback backend on port9876 in an isolated technical scene. Disable
optional telemetry/online asset services for the benchmark. The source recipe
and direct bpy/exporter also work without an MCP backend.

## Qualification

The original release matrix includes 134 Python tests, 25 isolated native phases,
40 Chaos/Recast check records, seven native/offline parity comparisons, real
packaged stdio MCP calls, normal live save/render/recovery and a scoped game
movement trial. They certify the documented scope, not every game/platform.
See [the full report](https://github.com/Musca420/UnrealSpatialTwin/blob/main/docs/SPATIAL_TWIN_PACKAGE_QUALIFICATION_20261006.md).

The 16 complete ABBA trials across four activities measured **68.3121% fewer
native whole-agent tokens** and **54.1777% less verified-delivery time**.
Baseline variability, failures, preparation and uncontrolled caches are
reported. There is no universal speed or whole-game quality claim.

| Measured activity | Fewer whole-agent tokens | Less verified-delivery time |
|---|---:|---:|
| Scene/asset audit | 36.27% | 37.59% |
| Asset reuse and placement | 0.70% | 45.23% |
| Grounding/placement of 32 actors | 85.11% | 70.90% |
| Blender authoring, import and placement | 14.73% | 34.35% |
| Aggregate of per-activity medians, equal weights | **68.31%** | **54.18%** |

These are task measurements, not a reduction in the entire cost of developing
this plugin or a game. No monetary ROI, account bill, installation/build/initial
scan payback or maintenance saving has been established. Asset reuse saves
almost no tokens in this sample. No isolated speedup is claimed for C++
compilation, rendering, shader building or import alone. Blueprint authoring,
animation, gameplay implementation, complete campaigns and other platforms
have no representative whole-agent speed benchmark here. Baseline variation
is substantial, especially in the batch task; OS/DDC/model caches are uncontrolled.
Earlier slower and failed trials remain in [Performance](https://github.com/Musca420/UnrealSpatialTwin/blob/main/docs/Performance.md).
The security-reviewed publication adds a nested-receipt redaction regression
to the original suite; see the publication receipt for its final test result.

Run and inspect the included [tests and benchmarks](https://github.com/Musca420/UnrealSpatialTwin/blob/main/BENCHMARKS.md). The
`manifest.json` inventory records every distributed file's SHA256. MIT covers
original sources; dependencies keep their own licenses.

## Public repository

This tree is suitable for a standalone public Git repository. It contains no
game checkout, credentials, private conversations or Unreal binaries. The
marketplace works from a local checkout or a public Git source after publishing.
Publishing the tree does not confer OpenAI directory approval.

## Contribute and get help

Start with the [documentation index](https://github.com/Musca420/UnrealSpatialTwin/blob/main/docs/README.md) and
[contribution guide](https://github.com/Musca420/UnrealSpatialTwin/blob/main/CONTRIBUTING.md). Source, tests, benchmark runners and
versioned measurement receipts are included. Changes to native compatibility
need native verification; Python CI alone is insufficient.

Use [issues](https://github.com/Musca420/UnrealSpatialTwin/issues) for reproducible
bugs and feature proposals, and [pull requests](https://github.com/Musca420/UnrealSpatialTwin/pulls)
for contributions. Send vulnerabilities through
[private reporting](https://github.com/Musca420/UnrealSpatialTwin/security/advisories/new),
never a public issue containing credentials, proprietary projects or raw logs.

Report issues using the repository's issue tracker once published, including
versions and filtered receipts as described in
[SUPPORT](https://github.com/Musca420/UnrealSpatialTwin/blob/main/SpatialTwinCodexPlugin/SUPPORT.md).
