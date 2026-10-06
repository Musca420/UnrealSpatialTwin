# Codex integration

`SpatialTwinCodexPlugin` contains `plugin.json`, `mcp.json`, a stdio launcher and
the `unreal-spatial` skill. Install from the repository marketplace using README. The package does not
modify project or global Codex configuration automatically.

The portable launcher resolves a single workspace project or explicit
`SPATIAL_TWIN_PROJECT`, rejects ambiguity and starts the same offline server.
The distribution bundles its Python toolkit under `runtime`; an explicit
`SPATIAL_TWIN_HOME` can select another installation. No game path is embedded
in the MCP manifest. It inherits stdio, keeping MCP
transport separate from diagnostics. A fresh Codex session may be needed to
discover newly configured tools.

When configuring an explicit project for a Codex stdio MCP, put
`SPATIAL_TWIN_PROJECT`, and any explicit Python/home overrides, in that server's
`env` configuration. Do not assume variables set only in the parent shell are
forwarded to the MCP child. Workspace discovery needs no override when a single
project is visible. Never print credentials while diagnosing startup failures.

For spatial work, follow this sequence:

1. Inspect freshness, map, engine, coverage and revision with `world_status`.
2. Select the relevant region and narrow summary → actor → component → geometry.
3. Reason from structural source data; keep unknowns explicit.
4. Create a Shadow World patch and validate collision/navigation consequences.
5. Use the official Unreal MCP for live execution.
6. Wait for canonical synchronization and independently query expected state.

Do not query Unreal for information already present in the Twin. Screenshots
remain appropriate for lighting, composition, VFX, material appearance and final
visual inspection. Positions, identity, bounds, collision, scene structure and
distance are structural queries.

Example: lamps every 15 meters along a road use spline points, existing lamp
and obstacle queries, candidate transforms, shadow collision checks and one
validated action plan. Use 1,500 Unreal units for spacing. Creation still needs
a cached native class/asset prototype; never guess missing geometry.


For a newly authored Blender asset: export -> `asset_stage` -> `patch_prepare`
(and additional Shadow queries when needed) -> `materialize.py --materials
bindings.json --apply --camera camera.json`. The client revalidates native facts,
executes, confirms and renders without intermediate agent turns. The render
client requests official CaptureViewport with an explicit camera transform and
writes PNG plus source-state/revision metadata. It does not use desktop automation.
The offline MCP never gains editor-control or rendering tools.

Use summary first, bounded pages and requested fields; do not serialize raw
geometry or base64 pixels into agent text. `benchmark.py` records query latency,
response characters and UTF-8 bytes. These measurements do not establish billable
model tokens or monetary savings. New asset-stage tools require restarting the
stdio MCP process/session after installing updated code.

`world_query(request, queries, decision_timeout_seconds=5)` implements bounded
TypeSafe function calling over the existing Twin tools. Codex supplies 1..16
concrete calls with exact arguments and a short purpose. Jev chooses one typed
option or NONE; code validates and actually executes that call. It can select
reads and Shadow validation, never live Unreal operations. A single known call
bypasses inference. Coordinates, geometry, revision checks and dispatch remain
code; the typed choice is a derived decision, not native world evidence.

Pages default to10 and are capped at20, nearest k at10. Choices are limited to
12KB and request text to2,000 characters. Large results stay on disk. Changed
canonical revision yields CONFLICTED; NONE/low confidence yields NEEDS_REASONING;
service failure yields UNAVAILABLE without automatic retry. The current0.6
confidence threshold is a local routing policy, not a universal accuracy claim.
Cache reuse requires identical request, choices, model and revision; only the
decision is cached, the actual query runs again against current data. Use direct
Twin calls when Codex already knows which query it needs.

This follows the official [function calling cookbook](https://docs.typesafe.ai/cookbooks/function_calling)
and [HTTP API](https://docs.typesafe.ai/api). The configured project wrapper pins
jev-1.13.0 and loads TYPESAFE_API_KEY locally. No second AI provider or runtime-game
dependency is required. A fresh stdio MCP connection listed and executed
world_query in the normal qualification-project project; this chat's initial tool catalog
does not automatically gain newly added tools. Reconnect the MCP/session for
native tool discovery. See Performance.md for measured API usage and latency.

For normal development, ask world_status(include_counts=False) for freshness and
use spatial_support for bounded placement decisions rather than thousands of hit
objects. Preserve UNKNOWN and reject incomplete coverage. Use persistent asset IDs
from entity/asset queries: Unreal object paths are different identifiers, and unknown
IDs fail explicitly. Reuse the confirmed cached asset whenever authoring is unchanged.

materialize.py --materials bindings.json --apply --camera camera.json performs
authorized import, exact parity, canonical-asset patch validation, official action,
scoped saving and real render through one MCP session. Keep detailed local receipts;
read only state/revision/patch/image paths into context. No GUI automation is needed.
Jev routing summary choices omit unused page/field arguments; exact geometric work
and a known next query remain code/direct calls. Measured normal-task and TypeSafe
results, equal-output checks and remaining gaps are in Performance.md.

For known independent reads, `world_read(queries)` returns status and 1..16
bounded query results in one call, without Jev inference. It rejects mixed
revisions/snapshots. Results larger than12KB are referenced on disk; narrow the
fields/pages before making decisions. `world_status` counts are now opt-in;
`entity_get` defaults to compact identity/pose/bounds, with detailed source fields
explicitly requested. Asset metadata remains available through `asset_get`.

`patch_prepare(operations, base_revision, patch_id=None)` creates/updates and
validates Shadow in one call. It returns bounded actionable collision pairs and
counts. There are no canonical writes. For cached assets, `apply_patch.py --root
<Twin> --patch <id> --camera <camera.json> --output <render.json>` now performs
application, scoped save, canonical confirmation and real capture in one session.
The camera is validated before any world write. Homogeneous creation and batches
of move/rotate/scale/property/delete operations use official ProgrammaticToolset;
partial acknowledgements and the uncertain operation are retained on failure.

No extra verification dump is required when the executor already supplied the
needed canonical proof. Read detailed receipts only for new questions, conflicts
or missing evidence. Follow SPATIAL_TWIN_OPTIMIZATION_20261003.md for measured
scope and whole-agent counters, distinct from query/RPC microbenchmarks.

If an existing Codex chat retains an older MCP catalog, invoke the same registered
offline tool with `server.py --project <uproject> --call world_read --arguments
<args.json>`. Arguments use the MCP schema and the same implementation; no editor
restart or GUI bootstrap is needed. Fresh stdio connections expose the new tools.
Render CLI responses and persisted receipts now share revision/map/patch/saved
confirmation fields, so an agent does not have to discover the receipt schema.

For a Codex CLI MCP entry that is required for the task, set `required = true`
and a bounded `startup_timeout_sec` (40 seconds in the verified 0.160.0 trials).
This makes startup wait for successful MCP initialization. Optional MCP servers
can miss the initial tool catalogue: the default optional startup grace is only
one second. A connection that becomes ready later is not evidence that its
binding existed in the first model call. A required connection failure must stop
the task rather than invite guesses or repeated discovery.

These are Codex CLI configuration fields, not verified Agent Plugin `mcp.json`
fields. Do not insert them into the plugin manifest or change global user settings
implicitly. A plugin host must finish connecting before the first task; if an
existing transport is closed, use a fresh connection or the documented CLI path.
Sources: [MCP configuration](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)
and [configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference).

After connection, use already verified bindings and batch known serial dependencies
inside one code-mode call. For example, obtain status and the bounded relevant
region, then return the evidence needed for a placement decision. Keep those
structured results in local code-mode storage; construct patch arguments from
them instead of asking the model to transcribe coordinates. After the model makes
the decision, validation, authorized execution, canonical confirmation and image
loading can share a call when the executor already implements the required guards.
Stop on errors, retain partial receipts and never replay an uncertain write.

Batching reduces model round trips in both native and Twin workflows. Benchmark
both with the same orchestration quality; do not attribute all batching savings
to the Twin. The three-scenario whole-agent benchmark retains the earlier,
unfavorable token results as well as the subsequent optimized comparison.
# Client execution and measurement

Judge orchestration by the actual tool calls, not a feature flag alone. In the
tested Codex CLI0.160.0, both values of `features.code_mode.enabled` produced
`functions.exec` calls. The attempted off/on experiment therefore did not
establish two different execution modes. The flag remains experimental client
configuration; the plugin does not change global settings or require it.
See the [official configuration reference](https://developers.openai.com/codex/config-reference).

When Code Mode is available, keep large results locally and emit selected facts;
check MCP `isError` before decoding text as JSON. Use the advertised enum for
each query instead of copying modes/field names from another tool. Direct MCP
and `world_read` remain supported. Measure actual complete tasks after changes;
neither enabling a flag nor fewer backend calls proves a token or time saving.


## Instructions on demand

The skill entrypoint keeps the everyday read/Shadow/confirm workflow and its
quality constraints. Linked execution, recovery and coverage references are
loaded only when the task needs those operations. They remain part of the
packaged plugin. Extended manuals are under the plugin root docs directory,
not under skills/unreal-spatial. No invocation policy or global setting changes.

Batch and direct MCP calls normalize arguments through the same input model.
A pagination cursor can move between these entry points when the query, fields
and revision are unchanged. Keep all query parameters; a cursor is not reusable
for a different scope or revision. spatial_support offsets are [x,y] in cm; its
candidate ray origins are [x,y,z]. The public schema exposes both dimensions.


The Codex launcher defaults to `--tool-profile workflow`: nine high-level
entrypoints, with all registered exact reads available through `world_read`.
Request `tool_describe(names)` only for unknown schemas (1..8 exact names);
arguments still undergo their original strict validation. `--tool-profile full`
or `compact` restores individual tool discovery. No capability or canonical
write permission is added or removed. Full plans/receipts stay in patches and
artifacts; final delivery reports only confirmed state and unresolved limits.


For resolved surface placement, place.py composes the registered offline
patch_place tool with the existing official apply/materialize workflows. It
requires explicit --apply for live operations and durable patch identity before
any write. This is an action client, not another MCP server. Its task constraints
and result contract are in the skill execution reference. A combined command
is not proof of whole-agent savings; measure complete tasks.


Offline preparation (288): with a READY persisted snapshot, source queries,
Blender authoring and Shadow planning do not require a live editor. Authorized
editor preparation can run concurrently. A first scan or missing coverage must
finish before dependent reads. Import/apply still requires the actual project,
map, plugin, current revision and live preflight; conflicts never silently rebase.
Measure elapsed wall time from request through the independent verified result,
with startup/first-scan/cache and failed attempts reported separately.
The public test runner cold_agent_benchmark accepts agent_workflow="open" and
an explicit overlap_native_preparation flag in a frozen protocol; it overlaps
only persisted-ready Twin preparation, enters native transports within the action
task and checks the exact owned startup receipt before connecting. These are
benchmark controls, not a second live Unreal MCP or a general editor launcher.
Tests: test_offline_preparation.py; native qualification gate290 remains scoped
until the complete representative portfolio is measured. Never extrapolate a
successful batch trial to general game production.


Reproducible owned cleanup (296): future open protocols may pass
`--close-script tools/UnrealSpatialTwinMCP/cold_benchmark_control.py` to the
public cold runner. It only closes a restored disposable SpatialTwinBench run
whose exact startup project/PID and independent readback agree. It rejects an
existing close receipt, other projects, dirty unrelated packages and changed
baseline Actors; original loopback remote settings are restored. The command
accepts no arbitrary scripts. Scope regression passed; actual native wrapper
qualification remains pending until the serialized portfolio292 finishes.
