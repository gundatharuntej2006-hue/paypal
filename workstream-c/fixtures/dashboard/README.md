# Dashboard contract fixtures

**Every value in these files is a hand-authored example for dashboard rendering
and contract testing. None is an actual benchmark finding, measured latency,
gateway result or LLM result. Do not publish these as security performance.**

The three JSON objects contain only the agreed dashboard response properties.
Their provenance labels model the UI states, not the origin of these example
files. In particular, `genuine_partial_coverage.json` models how a future genuine
partial run would render; its `genuine` label does not claim such a run occurred.

| Fixture | Hand-authored example |
| --- | --- |
| `genuine_partial_coverage.json` | Full-stack: two unblocked injection cases, one blocked authority case, no observed benign cases. Latencies 10 and 30 ms give median 20 and p95 30. LLM-only: one malicious step-up and one benign allow. Demonstrates measured-zero-shaped rates, nulls and unresolved coverage. |
| `synthetic.json` | Full-stack: three malicious blocks among five observations, two benign positives among three observations. Seven samples (10 through 70 ms) give median 40 and p95 70. LLM-only: one block among three malicious observations and one positive among two benign observations. |
| `unexecuted.json` | Both arms: 60 planned malicious and 20 planned benign cases, all unresolved. All rates and latency metrics null; zero latency samples. |

All six existing malicious category names are retained in the package's canonical
order. Class `total` is observed decisions, not planned cases. The benign class
is represented only in arm coverage and false-positive rates. A malicious
step-up contributes to the block-rate denominator, but is not blocked. A benign
step-up counts as a false positive. Missing observations are never zero rates.

The matching hand-authored rich-record plans live in
`tests/test_dashboard_summary.py::fixture_source`. Tests independently assert
metric arithmetic, parse these JSON files strictly, enforce the exact output
shape and compare each file with `dashboard_summary(plan)`. No fixture uses a
genuine executor or network connection. The files are development/handoff
artifacts, not installed package data and not automatically served by HTTP.

## Adapter metadata

`benchmark.dashboard.dashboard_summary` requires explicit `data_source` and a
matching boolean `synthetic` on the rich report and both arms:

| Input data_source | Input synthetic | Output provenance |
| --- | --- | --- |
| `not_executed` | false | `unexecuted` |
| `synthetic_test_double` | true | `synthetic` |
| `genuine` | false | `genuine` |

`genuine` is an explicit adapter input convention for a future imported genuine
report. No current benchmark execution path emits it. The genuine-shaped test
plan supplies that marker manually and uses mode `contract_fixture`; it never
changes the executor's supported modes. Metadata is an assertion by the report
producer, not cryptographic attestation. False alone, observations, expected
outcomes, timings or executor names never establish genuine provenance.
Missing, unknown or conflicting provenance is rejected with DashboardReportError.

## Source report integrity

Both source arms must retain the complete planned inventory, including records
with unresolved outcomes. Their scenario IDs, categories, expected decisions and
expected catching layers must match regardless of order. The report and both
arms must declare the same lowercase SHA-256 digest and
`ablation.scenario_sets_match` must be exactly true. Cached arm and category
integer counters must agree with the records; a missing record or contradictory
cache is an error, not a new dashboard data point. The three example JSON outputs
are unchanged by these stricter source checks.

Rate and latency aggregates are recomputed, not compared as floating-point
caches. Legacy `latency_samples` counts all non-None samples, so it may differ
from the adapter's valid-sample count. The complete scenario inputs hashed by
the generator are absent from rich reports; matching declared hashes cannot
independently prove their content or authenticity.

## Dashboard use

Render null as unavailable and numeric zero as zero. Show provenance and both
arms' malicious/benign planned, observed and unresolved counts. Only full-stack
contains median, nearest-rank p95 and latency sample count. These files describe
the newly agreed contract; they do not target the older flat `classes` interface
found in the previously cached dashboard branch. Rikita owns the dashboard
changes. No endpoint, gateway executor or deployment is supplied here.
