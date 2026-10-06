# Coverage and connection diagnostics

Read for UNKNOWN, stale navigation, missing geometry, map changes or old MCP processes.

For navigation, inspect the selected agent's coverage and tile capacity.
A full pool with no baseline tiles in the affected region needs a native
coverage/capacity correction; repeating offline validation cannot supply it.
`navigation_status` exposes cached `capacity_settings` and `native_actor`
identity, so diagnosis needs no live catalogue. Configured pool size and
effective `tile_capacity` are different facts. Legacy missing metadata is
unknown. Capacity changes require official editor property tools and a native
rebuild; Shadow does not simulate editor generation settings. Verify a newer
settled Twin revision, effective capacity and actual path/point coverage
before retrying the original placement. Raising a limit alone proves no path.
Authored UCX assets can predict navigation offline when the cached native
spawn profile advertises its adapter. Keep navigation enabled for obstacles;
inspect UNKNOWN rather than disabling it to pass. If the profile is missing,
refresh one suitable template asset, preferably one with few users, and stage
again. Import must confirm the predicted surface/defaults and the canonical
placement must revalidate before execution. Never label authored data native.

Missing nav tiles, unloaded descriptor-only facts and stale WP cells are explicit
limits. `CURRENT_SAVED_DESCRIPTORS` excludes unsaved partition layout. Navigation
`BUILDING`/`STALE_*` is unavailable until native completion synchronizes it.
Clean startup resumes its baseline; changed saved packages reconcile their
owners. Configuration/version mismatches and unscanned maps require explicit handling,
not an automatic full scan. Extended manuals live in `${PLUGIN_ROOT}/docs` in
the packaged plugin, or repository `docs/` in a source checkout, never under
this skill/reference directory. Read SpatialQueries.md and ShadowWorld.md for
query/simulation details; Installation.md and UnrealMCPIntegration.md for setup.
New tools require a fresh MCP connection. Never conceal missing coverage.
`collision_bounds_state=LEGACY_REQUIRES_UPGRADE` needs native cache maintenance
(Installation.md), not a full scan or a metadata edit. Runtime-controlled nav
links may need game execution; UNKNOWN never proves a route free or impossible.
The running MCP follows the active map between requests, pinning one store for
each complete request/batch. `world_maps` lists cached map/store identities;
`server.py --map /Game/MapName` pins an offline map. Never transfer a patch or
revision number between maps. Pass the returned status `root` to action clients;
they verify the live project, map, store and revision before any mutation.
If this chat still exposes an old tool catalog, use the same registered tool via
`server.py --project <uproject> --call <tool> --arguments <args.json>`; this is
the offline server's CLI, with the same validation. No editor/UI restart needed.
If a tool reports source/native-library changes since process startup, reconnect
the Twin MCP or use that fresh CLI. Never reuse the old process's validation;
updating files does not update imported Python or a loaded native library.
