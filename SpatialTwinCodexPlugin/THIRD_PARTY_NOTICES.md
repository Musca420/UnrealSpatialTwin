# External dependencies

This source package does not bundle Unreal Engine binaries, Unreal headers,
third-party libraries, game content or API credentials.

- Unreal Engine, official MCP toolsets, Chaos, Recast/Detour integration and
  engine APIs: supplied by the user's Unreal installation; governed by Epic's
  [Unreal Engine EULA](https://www.unrealengine.com/eula/unreal).
- WinSQLite: supplied by Windows; not redistributed here.
- Python: supplied by the user's environment under the Python license.
- `mcp` Python SDK and its dependencies: installed separately with pip; their
  own licenses remain applicable. The tested versions are recorded in the
  qualification environment, not copied into this archive.
- Blender and bpy/FBX authoring tools: supplied by the user's Blender
  installation; not redistributed here.
- TypeSafe/Jev: optional external service; no model or API credential included.

No license for original plugin files changes the license of these dependencies
or establishes a permitted public distribution channel for engine technology.
