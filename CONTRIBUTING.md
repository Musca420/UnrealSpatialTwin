# Contributing

Unreal Spatial Twin is an independent, MIT-licensed project. Bug fixes,
documentation, reproducible benchmark work and tested compatibility adapters
are welcome. Discuss a substantial change in an issue before implementing it.

## Development setup

Follow [README](README.md) for the licensed Unreal toolchain, Python environment
and Codex installation. [BENCHMARKS](BENCHMARKS.md) gives runnable checks and
fresh independent native fixtures. Never run destructive tests against a user's
working game or reuse output folders belonging to another test.

| Area | Source |
|---|---|
| Native extraction, events and official Toolset Registry extension | `Plugins/UnrealSpatialTwin/Source/UnrealSpatialTwin` |
| SQLite, Chaos and Recast/Detour core | `Plugins/UnrealSpatialTwin/Source/SpatialTwinCore` |
| Offline model, queries, contracts and Shadow | `tools/UnrealSpatialTwinMCP/spatial_twin` |
| Official-MCP action clients and verification | `tools/UnrealSpatialTwinMCP`, `scripts/unreal_mcp.py` |
| Installed Codex skill and bundled runtime | `SpatialTwinCodexPlugin` |
| Public packaging inputs | `docs/SpatialTwinPublication` |

The standalone tree keeps root tooling and a bundled runtime. Edit root runtime
sources and the skill first; regenerate a **new** distribution using
`python tools/UnrealSpatialTwinMCP/public_repository.py --output Build/NewRelease`.
Do not hand-edit one runtime copy and leave the other stale. The packager refuses
existing output folders. `manifest.json` records exact distributed bytes.

## Pull requests

Use a branch and submit a PR describing the concrete problem, resulting behavior,
reproduction, validation and remaining limits. Keep independent work separate.
Add a regression for a real failure; do not add tests that merely repeat the code.
Run:

```powershell
& .\Build\MCP\venv\Scripts\python.exe -m pip check
& .\Build\MCP\venv\Scripts\python.exe -m unittest discover -s tools/UnrealSpatialTwinMCP -v
```

For native, geometry, identity, navigation, application or persistence changes,
build with the qualified engine and run the relevant native phases and parity
checks from BENCHMARKS. Preserve failed attempts. Attach filtered receipts,
engine/runtime hashes and before/after renders when appearance is affected.
Do not submit engine binaries, game assets, private conversations or credentials.

## Invariants

Unreal is authoritative; Python never writes Canonical. Missing data stays
UNKNOWN. Respect map/project/revision identity, preserved unsaved work, scoped
saving and once-only journals. Never silently rebase or retry uncertain mutations.
Jev remains optional. Never disable correctness checks to improve a benchmark.

Performance comparisons require frozen inputs/model/effort/protocol/weights,
equal results and independent verification. Include whole-agent native counters,
verified-delivery time, cache policy, preparation and failures. Report a bounded
result rather than extrapolating a microbenchmark to all game production.

Use the GitHub issue templates for bugs/proposals. Be respectful and specific
when reviewing contributions. For security findings, follow [SECURITY](SECURITY.md).
