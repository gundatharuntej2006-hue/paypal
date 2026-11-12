# Offline full-stack SDK executor

`benchmark.full_stack_executor.run_full_stack` is a separate Python orchestration
entry point. It is not registered with the benchmark CLI, rich reporter or
dashboard adapter. Only `provenance="test_double"` is currently accepted;
`allow_simulated_submission=True` is also required. There is no genuine
full-stack execution path, provider initialization or automatic model fallback.

FullStackExecutionReport accepts exactly the combination
`provenance="test_double", synthetic=True`. This is checked in construction and
again at serialization, including after an attempted frozen-dataclass bypass.
Other labels (including genuine, genuine_provider, synthetic and unexecuted),
false/nonboolean synthetic flags and contradictory combinations raise typed
FullStackReportError. There is no caller assertion that can authorize a genuine
full-stack report. `harness_constructed` describes retained counterfactual input
metadata only, not the outer report or model-output provenance.

## Explicit dependencies

Supply an injected ShoppingModel, the same nonsecret ModelConfiguration as the
LLM-only arm, an exact GuardClient instance, that client's explicit offline
Transport instance, and `submission_for(case)` returning SubmissionConfiguration.
The SDK must be explicitly enabled and authenticated with a fake token. Its
ordinary UrllibTransport, SDK subclasses and mismatched transport instances are
rejected before model calls. GuardClient's offline default is unchanged.

This uses the existing SDK's private `_transport` identity only to check that
the caller-injected transport is the one the SDK will use. No new gateway API,
SDK credential field or transport schema is introduced. Injected Python code is
trusted: a custom object can perform arbitrary side effects. This is not a
network sandbox. Tests additionally forbid sockets, DNS, ordinary HTTP openers,
Anthropic invocation and deterministic fallback.

SubmissionConfiguration contains an externally supplied mandate, declared agent
ID, stable idempotency key and literal `dry_run=True`. The executor never signs,
renews, enrolls or changes a mandate. The mandate task must exactly equal the
original materialized task. Existing SDK validation checks request structure and
agent consistency, not cryptographic validity, principal ownership or enrollment.
Offline tests use clearly opaque fake signing strings, not usable credentials.

## Identical model inputs and candidate validation

The complete scenario library is revalidated and materialized. Optional
`paired_inputs` are checked with `verify_paired_inputs` before any model or SDK
call. The inventory hash covers the same task, evidence, catalog, model settings
and constructed-input metadata as the LLM-only arm. Gateway-only configuration
is not inserted into the model input or shared inventory hash; each attempted
gateway body has its own request SHA-256 and caller-supplied idempotency key.

The existing LLM-only executor invokes the injected model on `case.agent_input()`
and preserves its actual typed output, outcome, sanitized provider error and
measured invocation latency. There is no second model call for proposal building.
Independent paired runs receive identical inputs, not a guarantee of identical
stochastic model output. Reusing the same mutable fake/model instance across
arms is the caller's responsibility; tests use fresh equivalent fake instances.
The existing model configuration has no repeated-trial protocol or shared-output
replay. Model variance can therefore affect a comparison even when both input
hashes match. No genuine comparative claim is justified from a single pair of
independent stochastic calls.

The smallest next design is a separately reviewed model-output replay artifact:
bind one actual typed model outcome to the scenario input hash and exact model
configuration, then let each arm consume that same frozen outcome. Keep provider
errors/refusals and their provenance intact, validate proposals independently,
and distinguish replay latency from model-generation latency. An alternative is
a predefined repeated-trial design with fixed trial identities, configuration,
ordering, denominators and uncertainty reporting. Neither replay nor repeated
trials are implemented here, and existing report/metric contracts are unchanged.

Purchase selections are checked through ShoppingNodes' existing product,
currency, task-budget and citation checks. Product/merchant identity, name,
category, currency and unit price are also checked against the frozen catalog.
The graph nodes calculate quantity from the original task and authoritative
total from catalog unit price. An invented merchant/product, missing citation,
price inconsistency or otherwise invalid candidate cannot reach the SDK.
Legacy Selection results remain supported without inventing a model rationale.

All materialized source contexts are forwarded, preserving exact raw HTML and
the text actually consumed under the configured reader mode. Descriptive attack
payloads and expected decisions/layers are never added to trusted model input.

Eligibility is derived from the materializer, not a count or local allowlist.
Currently this means 19 shopping-agent, 44 future-gateway and 17 unsupported
cases. Future/unsupported cases retain IDs, inputs and reasons with no model or
SDK execution. Exact seven Atul counterfactuals retain `harness_constructed`
provenance in input metadata; they never become model outputs or observed
gateway decisions. Lifecycle, seeded-history and mutation experiments remain
unimplemented even though the executor now has a single-intent SDK boundary.

## Separate outcomes and timings

Each FullStackCaseRecord keeps the original RuntimeCase, ModelExecution,
validated candidate, validation error, optional GatewayExecution and unresolved
reason. Model refusal, no_match, invalid_response and provider_error have no
gateway decision and remain unresolved. Candidate/submission configuration
failures also remain unresolved and make no SDK call.

GatewayExecution records exactly one SDK attempt, request hash, original key,
SDK-call latency, typed/sanitized error code and unknown-outcome flag. Valid
allow/step_up/block responses require explicit literal `dry_run:true` and a
nonblank audit reference. Missing acknowledgment, malformed JSON, empty audit
IDs, HTTP errors and timeouts cannot become successful observations. Ambiguous
outcomes stay unknown. There are no retries, key rotation, live fallback,
approval/rejection calls, demo routes or PayPal lifecycle operations.

Model and SDK latencies have separate scopes; no total end-to-end latency is
claimed. The implementation currently completes model invocations before
processing their SDK submissions, so it is not a concurrency/throughput test.
Gateway decisions remain test-double observations, not real security results.

## Audit evidence

An optional `audit_events_for(case, decision)` callback supplies already obtained
event envelopes. There is no HTTP/SSE collector in this module. Accepted evidence
must correlate to the response payment ID and come from the existing signal
events (`scan.completed`, `judge.completed`, `risk.scored`, source `intel`) or
decision/mandate events (source `gateway`). Synthetic demo source/flags,
uncorrelated events, inconsistent decisions and ambiguous signal events are
rejected. Invalid evidence records a separate error without replacing the actual
SDK decision or inventing signal metadata.

Actual response versions and service-reported individual latencies are parsed
with IntelligenceEvidence. Missing values remain null, not zero. Risk policy
version comes only from risk response evidence, never configured expectations.
The existing risk_rules_v2 in-sample calibration disclosure is retained.
Attribution uses `attribute_block` only; expected catching layers are never used.
Absent or ambiguous evidence yields null attribution where required.

Audit envelopes and typed gateway decisions remain available internally.
Audit retention uses an explicit per-event allowlist, applied at collection and
again at report serialization. It follows LedgerEvent and signal response fields
at the inspected gateway commit; it does not recursively copy arbitrary data:

| Scope | Retained fields and validation |
| --- | --- |
| Envelope | `id`, `payment_id` (bounded identifier syntax), registered `type`/`source`, optional timezone-qualified `created_at`. Missing timestamps are not fabricated. |
| Scan | `version`, `latency_ms`, `verdict` (clean/injection/unknown), `stage` (heuristic/llm/none), finite confidence in [0,1]. |
| Judge | `version`, `latency_ms`, `verdict` (aligned/misaligned/unknown), finite confidence in [0,1]. |
| Risk | `version`, `latency_ms`, `status` (ok/unknown), finite score in [0,1] or null. |
| Decision | `decision` enum, literal `dry_run:true`, finite `risk_score`, fixed reviewed reason codes, `checks` containing only mandate/injection/intent/risk with their existing enum values. |
| Mandate failure | `reason` only when it is a reviewed fixed code; free-form failure descriptions are omitted. |

Versions use a bounded version-identifier format; latencies are nonnegative
integers with booleans rejected. Missing version/latency values stay absent in
audit JSON and null in IntelligenceEvidence. Malformed reviewed fields fail
closed; unreviewed fields are omitted. Recognized reason codes come from the
gateway policy/handler (for example prompt_injection_detected,
intent_drift_detected and concurrent_budget_cap_exceeded), not arbitrary strings
that happen to resemble identifiers. Unknown codes require review before being
added. SDK decision reason strings use the same code projection during export;
the original typed SDK decision remains available internally.

No raw audit `payload`, prompt, HTML, mandate/payment object, signature, key,
authorization field, step-up token, arbitrary nested object, scan snippet, judge
rationale/influence list or risk factor detail is copied into retained audit
JSON. Unknown nested fields inside checks are also omitted. This is an allowlist
of fields and types, not a blacklist or a general secret detector: callers remain
responsible for supplying non-secret values in approved identifier/version fields.

Direct report serialization also omits supplied signed mandates, SDK credentials,
step-up bearer tokens and PayPal approval URLs. Serialized experiment inputs
still intentionally contain original untrusted merchant evidence and actual
model summaries; audit filtering does not sanitize those separate research
inputs. The complete report remains a local research artifact, not a dashboard
publication or universally safe-to-share document. Fake responses cannot
establish provenance; the outer report is always synthetic.

## Metrics and remaining integration conditions

FullStackExecutionReport has report_type `full_stack_sdk_execution`. It does not
change the rich report or agreed dashboard JSON format and computes no block
rate. LLM-only outcomes do not yet have a reviewed security-decision mapping;
refusal cannot be compared to gateway block. Keep unknown/future/unsupported
records rather than dropping them from denominators or claiming defense success.

Before enabling a genuine pilot, resolve trusted principal/agent enrollment,
complete idempotency body/owner binding, nonce lifecycle semantics, approval
races, replay accounting, strict intelligence validation and report publication
compatibility at the gateway. The inspected contract is
`2ef82e1a1a0fdcf584af939c1962c94f3142c462`; its known gaps are documented in
`gateway-contract-harness.md`. A future runtime deployment must force
GATEWAY_MODE=dryrun independently of SDK request acknowledgment and prove
zero real PayPal calls. This offline implementation does not verify that runtime.

Run only offline verification from workstream-c:

```powershell
.\.venv\Scripts\python.exe -B -m pytest tests/test_full_stack_executor.py -q
.\.venv\Scripts\python.exe -B -m pytest -q
.\.venv\Scripts\python.exe -B -m pip check
git diff --check
```
