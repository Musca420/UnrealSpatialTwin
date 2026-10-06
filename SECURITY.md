# Security policy

## Supported release

The qualified target is source release 0.1.0 on Unreal 5.8.2 / Windows x64,
Python 3.12 and Codex with Agent Plugins. Other combinations require testing.
Security scanning is a dated snapshot, not a guarantee of absence of defects.

## Report privately

Use [GitHub private vulnerability reporting](https://github.com/Musca420/UnrealSpatialTwin/security/advisories/new).
Do not post secrets, authentication files, proprietary assets, private database
contents or unfiltered logs in public issues. Describe affected versions,
reproduction, prerequisites and impact. A minimal synthetic example is preferred.

## Trust boundary

The offline MCP uses stdio and exposes no public listener. Live actions connect
to the separately configured official Unreal MCP, with a loopback default.
Unreal and Blender execution, Python modules and native DLLs are trusted local
code, not a sandbox. Install only trusted source and native builds. Do not expose
editor/Blender automation services to an untrusted network. Configure their
access controls using their own documentation; Spatial Twin does not secure
a third-party server automatically.

Canonical is read-only to Python; patches use a separate journal. Contracts
enforce bounded inputs, finite geometry, revision/map checks and scoped staging
paths. Uncertain native effects are refused or UNKNOWN; never replay them.
These protections are not a certification of arbitrary Blueprint side effects.

The deterministic route has no auxiliary-model dependency. Optional Jev sends
selected excerpts/questions to `api.typesafe.ai` only when explicitly invoked
with a local key. Codex and third-party tools have their own data policies.
See [PRIVACY](SpatialTwinCodexPlugin/PRIVACY.md).

## Publication checks

The publication process scans exact source files, all reachable Git history
and the release ZIP with Gitleaks, checks for private home paths and locally
configured credential values without printing them, audits pinned dependencies,
verifies manifests and runs installation/MCP/regression checks. Source contains
no `.env`, auth/config files, native binaries or game assets. Git identity uses
the public Musca420 handle and its GitHub noreply address.

Pre-publication dependency review found two PyJWT 2.14.0 advisories and updates
the tested snapshot to 2.15.0:
[payload parsing](https://github.com/jpadilla/pyjwt/security/advisories/GHSA-42vr-xj54-vc7v),
[OKP key consistency](https://github.com/jpadilla/pyjwt/security/advisories/GHSA-x33g-cr3x-6449).
See `benchmarks/publication-audit.json` for dated final results. Dependency
advisories may change after release. The GitHub workflow uses immutable action
commits and read-only repository permissions.

Gitleaks retains its default rules. The only allowlist requires both the generated
`manifest.json` path and a 64-character lowercase hexadecimal digest; each such
inventory entry is verified against its file. API-named documentation hashes
otherwise trigger the generic-key heuristic. No credential-like source content
or entire directory is allowlisted.
