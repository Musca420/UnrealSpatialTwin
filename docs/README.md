# Documentation

Start with [repository installation and examples](../README.md).

| Topic | Manual |
|---|---|
| Design and authority boundaries | [Architecture](Architecture.md) |
| Canonical schema and identity | [Data model](DataModel.md) |
| Indexed queries, source geometry and navigation | [Spatial queries](SpatialQueries.md) |
| Planning, validation, journals and recovery | [Shadow World](ShadowWorld.md) |
| Live action layer | [Official Unreal MCP](UnrealMCPIntegration.md) |
| Agent workflow | [Codex integration](CodexIntegration.md) |
| Setup and engine prerequisites | [Installation](Installation.md) |
| Diagnosing stale/unknown/conflicted state | [Troubleshooting](Troubleshooting.md) |
| Public schemas | [API reference](API.md) |
| Supported target and limitations | [Coverage](SPATIAL_TWIN_PRODUCTION_COVERAGE.md) |
| Functional evidence | [Qualification](SPATIAL_TWIN_PACKAGE_QUALIFICATION_20261006.md) |
| Speed, costs, negative results and limits | [Performance](Performance.md) |
| Runnable checks | [Tests and benchmarks](../BENCHMARKS.md) |
| Contribution and disclosure | [CONTRIBUTING](../CONTRIBUTING.md), [SECURITY](../SECURITY.md) |

Snapshot schemas describe the qualified release. Ask `tool_describe` or
`world_read(..., schemas=[...])` for the exact current installed contract;
never guess fields from an example or mix project/map/revisions.
