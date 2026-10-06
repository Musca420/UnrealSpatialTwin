# Data handling

Spatial Twin reads the selected local Unreal project and writes its derived
database/cache under that project's `Saved/SpatialTwin` directory. Shadow
plans and receipts are stored locally. The plugin's stdio MCP does not expose
a public network server. Live actions use the separately enabled official
Unreal MCP; configure that service for your local environment.

The deterministic workflow does not call an auxiliary model API. If you
explicitly enable Jev, the selected excerpts and questions are sent to
`api.typesafe.ai` using your locally configured key. Credentials are not
included in the distribution. Codex and other tools handle submitted context
under their own service policies; this document does not promise that an
agent conversation remains entirely on the local computer.

Delete derived data only after preserving needed patches/receipts and while
the canonical writer is closed. Deleting cache requires a subsequent scan;
it does not delete the project's source assets.
