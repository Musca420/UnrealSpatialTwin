# Performance and measurement limits

## Current whole-agent portfolio

Sixteen independently verified ABBA trials cover four activities with two runs
per route, equal weights fixed before the trials, gpt-6.1-sol/high and the same
outputs/verification. Model counters include cached input; reasoning is already
part of output. Time includes startup, whole-agent work, actions/sync, requested
renders and independent verification. Restoration/cleanup have separate receipts.

| Activity | Tokens without / with, medians | Seconds without / with, medians | Token reduction | Time reduction |
|---|---:|---:|---:|---:|
| Audit | 74977 / 47780 | 88.424 / 55.183 | 36.27% | 37.59% |
| Reuse | 83526.5 / 82938 | 165.577 / 90.685 | 0.70% | 45.23% |
| Batch, 32 actors | 797421 / 118704 | 361.700 / 105.265 | 85.11% | 70.90% |
| Blender authoring/import | 99831.5 / 85124.5 | 156.366 / 102.647 | 14.73% | 34.35% |
| Sum of per-activity medians | 1055756 / 334546.5 | 772.067 / 353.779 | **68.31%** | **54.18%** |

The minimum 35% token / 45% time criteria and nominal 40% / 50% criteria are met
for the aggregate, not each activity. Actual token consumption over all 16 trials
is 2780605, which differs from the sum of medians. Batch results dominate the
aggregate: baseline trials used 1052695 / 542147 tokens and 430.610 / 292.791 s.
The first included unsupported resource/template access, unexposed BodySetup
reflection and an excessive 106798-character response. The baseline supplied
native contracts, batching/store/load and compact-output instructions; actual
agent mistakes/variance were retained rather than removed. This is not an
error-free optimum or a statistically universal speed factor.

The audit discovers asset/dependencies and 344 owners. Reuse selects actual
source/supports. Batch verifies pose/support/collision/defaults for 32 actors.
Authoring verifies Blender source, six UCX bodies, native import/materials,
placement and real renders. No new full scan occurs during Twin tasks.

## Where savings are not established

- No total development or monetary ROI: plugin implementation, qualification,
  installation, initial scans, builds, maintenance and failed trials cost work.
- Asset reuse saves only 0.70% tokens in this sample. Audit and authoring do not
  meet both minimum thresholds individually. None of these positive medians
  proves a benefit for every task or input.
- No isolated speed claim for C++ compilation, shader building, rendering or
  import alone. End-to-end improvements may come from less agent orchestration.
- No representative whole-agent comparison for Blueprint authoring, animation,
  gameplay logic, complete campaigns, artistic quality or other platforms.
- OS/DDC/Blender/model caches were not controlled. These process-cold trials
  are not strictly cold-cache or warm-cache measurements; first-scan/cache costs
  are separate. Reviewer/account billing and dollar prices were not measured.
- Optional Jev was unused. No savings from an auxiliary model are inferred.

## Earlier positive, negative and failed series

These are separate versions/protocols, never pooled into the current aggregate.
Original detailed receipts remain locally archived and SHA-referenced.

| Series | Scope | Token reduction | Time reduction | Actual task tokens | Outcome |
|---|---|---:|---:|---:|---|
| 205 | 12 ABBA trials, reuse/batch/author | 57.95% | 51.06% | 1847261 | Scoped positive; different verification time boundary, not current aggregate |
| 237 | 4 ABBA asset-audit trials | 37.77% | 25.23% | 243050 | Time criterion failed |
| 258 | 16 ABBA, four activities | 47.40% | 39.91% | 2241943 | Time criterion failed |
| 266 | 16 ABBA, four activities | 48.56% | 32.89% | 2396957 | Time criterion failed |
| 280 | 16 ABBA, four activities | 36.26% | 35.76% | 1569571 | Time criterion failed |
| 290 | 4 ABBA batch trials | 82.50% | 61.14% | 693106 | One-activity result, not full portfolio acceptance |
| 292 | Incomplete multi-activity series | Not aggregated | Not aggregated | Retained in original receipts | Incomplete |
| 398 | Incomplete 12/16 plus failed author | Not aggregated | Not aggregated | Failed author:147409 | Missing Blender backend; 153.0631 s failed author retained |
| 416 | 16 complete ABBA, four activities | 68.31% | 54.18% | 2780605 | Current bounded portfolio |

The initial October 3 scripted pipeline took 14.281 s with Twin vs 4.200 s
without: 3.400x slower overall despite faster perception. This was not a pair
of complete billed Codex agents and had different collision-check coverage.
Later scripted Build37 measurements were 4.134/4.399 s for a new asset with
108 probes, 5.164/21.500 s with 2172 probes, and 3.199/19.670 s for reuse with
2172 probes. They show density-dependent pipeline effects, not whole-agent ROI.

Historical failed/source-only trial costs 201/203/204 were 76400/46766/205973
tokens. Earlier negative 192-194/202/227/229, failed223, pre-agent256 and
incomplete292 are retained in the original archive. Native scan252 took
18.3599707 s, build378 took 147.88 s, fixture preparation404 took 61.406443 s;
these are preparation costs rather than discounts from a task's delivery time.

Development parent-counter snapshots include 9119644 tokens at239,14990211
at261,17723815 for the village phase and13707649 for qualification/packaging503.
These are bounded snapshots with different cutoffs, not a complete account bill;
do not add overlapping or reset series or subtract them from successful-task
medians. The latest snapshot excludes subsequent final packaging/publication work.
See `../benchmarks/development-costs.json` for exact boundaries.

## Reproduction and provenance

[BENCHMARKS](../BENCHMARKS.md) documents the runnable native/Python/MCP and
whole-agent reproduction workflow. `../benchmarks/protocol*.json`, `trials.json`
and `results.json` preserve the frozen plans and measurements. Public locator
redaction does not alter counters or timings; original receipt hashes and
the documentation source hashes preserve provenance. Qualification phases,
unit checks and parity comparisons are not counted as whole-agent trials.
