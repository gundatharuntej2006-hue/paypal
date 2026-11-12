# Gateway benchmark report contract change request

## Ownership and inspected sources

`git ls-files gateway contracts dashboard` returns no files on `tharun`.
The endpoint is not owned by this working branch. No extracted cached Gateway
snapshot was modified. Inspection used cached `origin/vivek` commit
`d4471a2d2b00d3fa840c406b68c8915faee0b8dc`,
`gateway/pkg/api/handlers.go` (`handleGetBenchmarkReport`,
`validateBenchmarkReport`) and `gateway/pkg/api/router.go`, plus cached
`origin/ritika` commit `207dac2ae19f31358e8bae9c1a3351b792a3c3c2`,
`dashboard/src/benchmark/report.ts` and `contracts/benchmark-report.schema.json`.
These are source inspections, not endpoint or validator executions.

## Workstream C producer change

`run_benchmark` now assigns one UUID4 after input/executor validation and before
arm execution, and captures `generated_at` once at report completion as an ISO
8601 UTC timestamp ending in Z. These identify an actual infrastructure run,
including validation-only runs; they do not imply genuine security observations.
Both values are retained in the returned report and forwarded unchanged by
`dashboard_summary`. JSON serialization never regenerates them. Separate calls
create separate run IDs. Existing data-source/provenance labels and metrics are
unchanged. Static fixtures are unchanged and intentionally omit metadata.
Historical imported reports may omit metadata; the adapter does not invent it.

Runtime metadata removes the immediate missing-field blocker for newly generated
summaries only. It does not repair Gateway validation or publication safety.
Rich research reports must not be published directly as dashboard summaries.

## Requested Gateway-owner changes and focused acceptance tests

1. **Optional metadata:** accept absent or null `run_id`/`generated_at`, matching
   dashboard parsing. When present and non-null require nonblank strings. Amend
   the shared schema to explicitly permit null, since its current optional string
   properties disagree with the parser about explicit null.
   Tests: all three unchanged C dashboard fixtures accepted; absent/null metadata
   accepted; blank, whitespace-only, numeric, array and object values rejected.
2. **Timestamps:** validate supplied `generated_at` as a real timezone-qualified
   ISO/RFC3339 date-time, including calendar validity, rather than any string.
   Align dashboard date validation and shared schema on this precise subset;
   JavaScript Date.parse currently accepts a broader set than RFC3339.
   Tests: UTC Z and explicit-offset timestamps accepted; invalid dates, arbitrary
   strings and timezone-free timestamps rejected. Preserve the supplied value.
3. **Version:** when supplied require numeric value exactly 1, never integer
   truncation; align dashboard validation too. The schema currently makes the
   version optional, so keep omission supported unless jointly versioned.
   Tests: 1 accepted; 1.5, 0, 2, true, null and string "1" rejected.
4. **Structure:** validate classes/by_class aliases, row objects, names,
   nonnegative integer counts, blocked <= total, nullable rates in [0,1], both
   ablation arms and nested malicious/benign coverage (or the agreed top-level
   coverage alternative). Enforce planned = observed + unresolved for complete
   counts; preserve unavailable nulls and measured zeros. Reject conflicting
   aliases and contradictory counters. Align dashboard/schema checks as needed.
   Tests: unchanged fixtures accepted; malformed rows, nonobject ablation,
   missing arms/cohorts, negative/fractional counts, blocked > total, out-of-range
   rates and inconsistent coverage rejected; null and zero retained distinctly.
5. **Bounded input:** replace unbounded os.ReadFile with a bounded read of at most
   5 MiB + 1 bytes from an opened file; reject excess before JSON decoding. A
   preliminary stat alone cannot protect against a file growing during reading.
   Tests: valid input at the limit accepted, limit + 1 rejected, growing input
   cannot trigger unbounded allocation, oversized input never reaches decoder.
6. **Approved publication projection:** validate and serialize an explicit
   summary-field allowlist at every nesting level, never return original bytes.
   Approved scope: report_version, provenance, optional run metadata, class
   summary rows, arm rates/coverage/latencies, and explicitly reviewed optional
   dashboard summary fields. Detailed scenarios should require a separate review;
   never copy arbitrary research records, prompts, HTML, mandates or headers.
   Tests: unknown top-level and nested canary fields are rejected or omitted per
   the chosen documented policy; approved values survive; no raw input fallback;
   invalid/missing reports retain the existing 404 behavior without execution.

The current handler also requires nonempty class arrays whereas the shared
schema/parser permit empty arrays. Agree explicitly on inventory completeness
rather than silently imposing a different Gateway contract. Provenance labels
remain genuine/synthetic/unexecuted; validation cannot prove their truth.

Run these as in-process pure-validator/handler tests with temporary files and
recorders, without HTTP clients, provider calls, payments or benchmark execution.
No Gateway-owner implementation or tests were performed in this task.
