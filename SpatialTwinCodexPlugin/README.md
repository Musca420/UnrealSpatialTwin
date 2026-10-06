# Unreal Spatial Twin

Read persistent Unreal world data, prepare spatial changes offline, validate
them in Shadow, and apply through Unreal's official MCP. Unreal remains the
authority for execution, compilation, saving and rendering.

Qualified target: Unreal Engine **5.8.2 / Windows x64**, Python **3.11+**
(tested 3.12), Codex with Agent Plugins support. Blender authoring was tested
with 5.2.1 LTS. Other engine builds and platforms need separate qualification.

## Install

1. Copy the accompanying `UnrealSpatialTwin` folder into your project's
   `Plugins` directory and build with your licensed Unreal toolchain.
2. Enable Unreal's official ModelContextProtocol toolsets. Follow
   [Installation](docs/Installation.md) for the AllToolsets prerequisite.
3. Create a Python environment and install
   `runtime/tools/UnrealSpatialTwinMCP/requirements.txt`.
4. Install this folder as a local Codex plugin using a repository marketplace.
   Set `SPATIAL_TWIN_PROJECT` to your `.uproject` and `SPATIAL_TWIN_PYTHON` to
   your Python executable when workspace discovery is ambiguous.
5. Launch Unreal with `SPATIAL_TWIN_HOME` pointing to this folder's `runtime`
   for native patch dispatch. Perform the initial explicit scan once. Inspect
   `world_read` status before subsequent work; reuse the cached snapshot.

The source archive contains a marketplace example. Its `source.path` is
relative to the extracted archive root. Use Codex's documented local plugin
installation flow; no manual rewrite of an existing configuration is needed.

## Use

Ask Codex to use the `unreal-spatial` skill. The focused MCP advertises
`world_read`, `shadow_plan` and `tool_describe`; the complete query/operation
schemas remain available through these entrypoints. Known deterministic
queries and validation work without a model API. Jev is optional.

Unsupported or stale geometry, missing navigation, ambiguous identities and
uncertain write outcomes remain explicit. Never edit Canonical, force a PASS,
replay an uncertain mutation or infer a complete game playthrough from a test.

## Verification and measurements

Read [Coverage](docs/SPATIAL_TWIN_PRODUCTION_COVERAGE.md) and
[Package qualification](docs/SPATIAL_TWIN_PACKAGE_QUALIFICATION_20261006.md).
The four-activity, 16-trial portfolio measured 68.3121% fewer native whole-agent
tokens and 54.1777% less verified-delivery time. These are measured portfolio
results, with variable baseline and uncontrolled caches, not universal gains.
Earlier failures and costs are retained in the benchmark reports.

## Publication channel

This plugin uses a local stdio MCP and a locally installed Unreal editor.
It is prepared for downloadable source/repository distribution. OpenAI's
public directory uses a remotely reachable, verified MCP service; the local
package does not claim that integration or directory approval.

Public reference: [OpenAI packaging](https://developers.openai.com/plugins/build/plugins).
For a reproducible bug report, see [Support](SUPPORT.md).
