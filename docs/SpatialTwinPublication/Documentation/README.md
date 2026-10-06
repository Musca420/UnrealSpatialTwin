# Documentation

Start with [repository installation and examples](https://github.com/Musca420/UnrealSpatialTwin/blob/main/README.md).

| Topic | Manual |
|---|---|
| Design and authority boundaries | [Architecture](https://github.com/Musca420/UnrealSpatialTwin/blob/main/docs/Architecture.md) |
| Canonical schema and identity | [Data model](https://github.com/Musca420/UnrealSpatialTwin/blob/main/docs/DataModel.md) |
| Indexed queries, source geometry and navigation | [Spatial queries](https://github.com/Musca420/UnrealSpatialTwin/blob/main/docs/SpatialQueries.md) |
| Planning, validation, journals and recovery | [Shadow World](https://github.com/Musca420/UnrealSpatialTwin/blob/main/docs/ShadowWorld.md) |
| Live action layer | [Official Unreal MCP](https://github.com/Musca420/UnrealSpatialTwin/blob/main/docs/UnrealMCPIntegration.md) |
| Agent workflow | [Codex integration](https://github.com/Musca420/UnrealSpatialTwin/blob/main/docs/CodexIntegration.md) |
| Setup and engine prerequisites | [Installation](https://github.com/Musca420/UnrealSpatialTwin/blob/main/docs/Installation.md) |
| Diagnosing stale/unknown/conflicted state | [Troubleshooting](https://github.com/Musca420/UnrealSpatialTwin/blob/main/docs/Troubleshooting.md) |
| Public schemas | [API reference](https://github.com/Musca420/UnrealSpatialTwin/blob/main/docs/API.md) |
| Supported target and limitations | [Coverage](https://github.com/Musca420/UnrealSpatialTwin/blob/main/docs/SPATIAL_TWIN_PRODUCTION_COVERAGE.md) |
| Functional evidence | [Qualification](https://github.com/Musca420/UnrealSpatialTwin/blob/main/docs/SPATIAL_TWIN_PACKAGE_QUALIFICATION_20261006.md) |
| Speed, costs, negative results and limits | [Performance](https://github.com/Musca420/UnrealSpatialTwin/blob/main/docs/Performance.md) |
| Runnable checks | [Tests and benchmarks](https://github.com/Musca420/UnrealSpatialTwin/blob/main/BENCHMARKS.md) |
| Contribution and disclosure | [CONTRIBUTING](https://github.com/Musca420/UnrealSpatialTwin/blob/main/CONTRIBUTING.md), [SECURITY](https://github.com/Musca420/UnrealSpatialTwin/blob/main/SECURITY.md) |

Snapshot schemas describe the qualified release. Ask `tool_describe` or
`world_read(..., schemas=[...])` for the exact current installed contract;
never guess fields from an example or mix project/map/revisions.
