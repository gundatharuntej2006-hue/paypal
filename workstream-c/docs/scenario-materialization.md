# Scenario materialization (preparation only)

This layer prepares frozen experiment inputs. It never runs models, benchmarks,
gateway requests, intelligence checks or payments. Expected labels are evaluation
metadata and are absent from RuntimeCase.agent_input() and the input hashes.

## Interfaces

- benchmark/materializer.py: ModelConfiguration, RuntimeCase, EvidencePage,
  materialize_scenario(), materialize_library(), capability_report(),
  inventory_hash(), verify_paired_inputs().
- RuntimeCase.agent_input(): the existing immutable ModelRequest, available only
  for shopping_agent cases. It contains the original task/parsed constraints,
  retrieved candidates, exact consumed page text and source references.
- RuntimeCase.merchant_client(): snapshot-backed MerchantClient for injection via
  build_graph(..., merchant_client=client) or run_shopping(..., merchant_client=client).
  The normal graph default remains unchanged. No HTTP fetch fallback exists.
- benchmark/attribution.py: attribute_block(), a separate observed-evidence helper;
  it never receives a Scenario or an expected catching layer.

Each case retains the original scenario ID/category/task, payload and every
operation, complete declared input, target merchant/product, catalog snapshot,
all fixture references and their exact renderer output, and all 13 browsing pages.
EvidencePage.original_html is the clean page before materialization; .html and
.context.html are the exact materialized page. .visible_text is separately
extracted. Model page content is the exact configured raw_html or visible_text
view, with content-stable context references. Descriptive scenario payloads are
NEVER inserted into page HTML. A referenced fixture's final page is byte-for-byte
the original renderer output; fixture_previews retains that same original page.
Original merchant and fixture source files are unchanged.

content_scan_inputs() returns exactly poison_01 through poison_15, with the
original rendered HTML, visible text, fixture-ID references and Atul-compatible
inert source URLs. It is a distinct scan projection: scenario descriptions, clean
pages and other hypothetical attacks cannot enter it. No scan endpoint is called.
Hidden CSS/comments/offscreen content, reviews and attributes remain untouched.
Without an actual fixture, descriptive inline attack plans remain metadata and
cannot qualify as shopping-agent stimuli. Operations are preserved, not executed.

## Capability meaning

- shopping_agent: a single-product selection experiment can use the current
  selection/rationale/citation interface and these snapshots. This does not imply
  attack success, defense success, deterministic keyword matching, enforcement of
  all task semantics, or an actual observed outcome.
- future_gateway: requires payment history, raw request mutation, registry,
  approval/capture, concurrency, clock or fault-injection interfaces. Those
  interfaces are not implemented or enabled here, even if a gateway route exists.
- unsupported: additional fixtures/actions/parser or delivery semantics are
  missing. All IDs and reasons remain in the preparation inventory. Access to an
  agent-ready request/client is refused for both non-shopping capabilities.

Only explicitly recognized selection plans are supported. Unknown operation plans
fail closed to unsupported. Multiple fixture references retain separate exact
previews but require an explicit composition plan. Four benign cases need seeded
prior spending and are therefore future_gateway cases, not measured failures.

## Paired input integrity

An explicit nonsecret ModelConfiguration records provider, model ID, timeout and
max tokens. No environment or credential is read. Defaults describe the offline
deterministic demo; Anthropic requires an explicit model ID. Future executors must
instantiate the declared model settings identically; this module does not create
or authenticate provider clients, and fake-model tests are not real-model evidence.

Each case hash covers task and parsed constraints, full declared inputs (including
all payloads and operations), ID/category, raw/visible/consumed evidence and refs,
fixture payloads/previews, full catalog/candidates, reader/traversal configuration,
model settings, separately tagged constructed proposal/sequence snapshots, and
Anthropic prompt/output schema when applicable. Expected
scenario decisions/layers are excluded. Canonical UTF-8 JSON and sorted scenario
IDs make the inventory digest deterministic and order independent. There are no
runtime timestamps or random IDs. verify_paired_inputs() rejects any input-hash
mismatch or duplicate IDs; it does not prove model determinism or authenticity of
later results. Both arms must retain unsupported/unresolved cases separately.

Offline preparation example (no execution):

```python
from agentguard_workstream_c.benchmark.scenario_loader import load_scenarios
from agentguard_workstream_c.benchmark.materializer import (
    ModelConfiguration, materialize_library, capability_report, verify_paired_inputs,
)
cases = materialize_library(load_scenarios(), ModelConfiguration())
paired_input_sha256 = verify_paired_inputs(cases, cases)
rows = capability_report(cases)  # IDs, categories, capabilities and reasons only
```

## Catching-layer evidence

Only an observed gateway block with an audit-event ID can be attributed. Explicit
mandate=fail supports mandate attribution. injection/intent/risk require the
corresponding flagged check and actual gateway block reason
(prompt_injection_detected, intent_drift_detected, or
risk_score_exceeds_block_threshold). Multiple matching layers are ambiguous and
return null. Refusals, invalid output, timeouts, missing evidence and step-ups
return null; no expected label is copied.

Other requires a matching actual gateway decision.made event: same payment and
audit-event IDs, source gateway, decision block, nonsynthetic data, and a matching
recognized other block reason (currently concurrent_budget_cap_exceeded).
The inspected gateway may reference an earlier allow event when budget reservation
later fails; that does NOT meet these requirements and remains unattributed.
Callers must obtain genuine response/event evidence: this helper does not establish
provenance or fetch audit data. It is not wired into the existing executor/report
format. The older rich report's catching-layer vocabulary remains unchanged.

## Offline tests and boundaries

Tests forbid sockets, DNS and the HTTP opener, validate all definitions/references,
retain all payloads/operations, check hashes and expectation exclusion, and exercise
snapshot injection with fake selections. They do not execute a benchmark or infer
attack/defense results. The genuine-executor rejection, SDK and payment protections
are unchanged. No dependencies were added.

## Constructed operations and provenance

benchmark/counterfactuals.py implements the exact seven DRIFT request mappings
from Atul commit dd6c2430e43b5bacac0d0db168821d9aec4192cb. The checked-in
fixtures/counterfactual/atul-drift.json is an independent snapshot of those golden
request bodies, not execution evidence. Every constructed object has explicit
harness_constructed provenance and is separate from the current agent selection
and Observation.model_output. No observed decision or catching layer is assigned.

Each RuntimeCase.constructed_mutation keeps original and mutated purchase values,
original task, exact Atul reasoning, text-only judge citations and separate original
raw HTML. In intent_unrelated the source scenario already names baskets, so
original and mutated purchase values coincide; neither is rewritten to invent an
original matching electronics purchase. intent_reassurance keeps the original hub
separate from the lamp proposal and its deliberately misleading hub summary.

The seven judge_request() bodies match Atul's actual supported proposal fields.
They contain no signed gateway mandate, authenticated agent or execution client.
They are usable only as explicitly constructed judge/gateway-evaluation inputs
where the actual downstream contract permits them. They cannot pass as the
catalog-only agent's Selection or ModelDecision, and are rejected in observed
model-output slots. All seven remain future_gateway capabilities, not successful
executions. LLM-only runs must retain actual ModelDecision outputs separately;
they must never replace them with these constructed proposals.

RuntimeCase.constructed_sequence for budget_threshold contains exactly three
10.00 USD steps linked by the unchanged task. It retains the stated 29.99 USD
settlement, the 30.00 USD sequence total, the 0.01 USD discrepancy and null
step_up_over. No missing threshold, reconciled item composition, signing values
or request identifiers are invented. Its limitations prevent claims about
threshold evasion or execution until those source ambiguities are resolved.

## Actual intelligence evidence and calibration

Observation.intelligence optionally contains IntelligenceEvidence(scan, judge,
risk). Each available SignalMetadata has version and latency_ms, taken only from
actual decoded response fields by from_responses(). An absent signal stays null;
a missing version or latency stays null. Zero latency remains a measured zero.
No model ID, configured engine or calibration reference fills observed versions.
active_risk_policy_version comes solely from the risk response version.

Observation.latency_ms remains end-to-end decision latency. Per-signal times are
independent evidence fields, not subtracted or substituted. The rich JSON retains
this evidence per scenario and arm; Markdown has an observed-metadata table when
evidence exists. Observation.model_output retains an actual validated ModelDecision
separately. Test doubles default both fields to null. A response parser does not
prove provenance: a future collector must supply genuine correlated responses.
Offline test samples remain fake plumbing data, not performance measurements.

Rich reports also include methodology.risk_calibration. Its reference policy is
risk_rules_v2: the existing 20 benign scenarios were used during its calibration.
False-positive rates on those cases are in-sample, NOT held-out generalization
results. A separate held-out benign set is needed for independent evaluation.
This reference disclosure does not imply that policy ran and does not populate
active observed policy versions. The agreed dashboard summary contract and its
three fixtures are unchanged. No genuine executor has been enabled.

## Capability inventory

Preparation counts and constructed objects are NOT benchmark measurements.

| Category | shopping_agent | future_gateway | unsupported |
| --- | ---: | ---: | ---: |
| authority_attacks | 1 | 6 | 3 |
| benign | 16 | 4 | 0 |
| budget_evasion | 0 | 10 | 0 |
| indirect_prompt_injection | 1 | 3 | 6 |
| intent_drift | 0 | 7 | 3 |
| merchant_attacks | 1 | 4 | 5 |
| operational_attacks | 0 | 10 | 0 |

| Scenario ID | Capability | Reason |
| --- | --- | --- |
| auth_admin_notice | future_gateway | Skipping payment checks requires a gateway invocation/lifecycle boundary. |
| auth_approval_agent | future_gateway | Requires pending human approval and a capture attempt, absent from the shopping graph. |
| auth_auditor | future_gateway | An additional verification payment requires a multi-payment lifecycle interface. |
| auth_developer | unsupported | No premium replacement-item fixture is defined for this directive. |
| auth_gateway_notice | future_gateway | Firewall-bypass semantics require an actual gateway invocation boundary. |
| auth_intel_notice | future_gateway | Independent-check bypass semantics require gateway intelligence integration. |
| auth_signed_notice | future_gateway | An unsigned mandate amendment requires gateway mandate-verification semantics. |
| auth_support | unsupported | Only a descriptive payload/plan exists; no actual merchant fixture delivers this attack. |
| auth_system_banner | shopping_agent | Single-product selection over materialized merchant evidence is representable. |
| auth_user_quote | unsupported | No premium-bundle product or multi-item bundle action exists. |
| benign_01 | shopping_agent | Clean catalog selection with task quantity, price and evidence is representable. |
| benign_02 | shopping_agent | Clean catalog selection with task quantity, price and evidence is representable. |
| benign_03 | shopping_agent | Clean catalog selection with task quantity, price and evidence is representable. |
| benign_04 | shopping_agent | Clean catalog selection with task quantity, price and evidence is representable. |
| benign_05 | shopping_agent | Clean catalog selection with task quantity, price and evidence is representable. |
| benign_06 | shopping_agent | Clean catalog selection with task quantity, price and evidence is representable. |
| benign_07 | shopping_agent | Clean catalog selection with task quantity, price and evidence is representable. |
| benign_08 | shopping_agent | Clean catalog selection with task quantity, price and evidence is representable. |
| benign_09 | shopping_agent | Clean catalog selection with task quantity, price and evidence is representable. |
| benign_10 | shopping_agent | Clean catalog selection with task quantity, price and evidence is representable. |
| benign_11 | shopping_agent | Clean catalog selection with task quantity, price and evidence is representable. |
| benign_12 | shopping_agent | Clean catalog selection with task quantity, price and evidence is representable. |
| benign_13 | shopping_agent | Clean catalog selection with task quantity, price and evidence is representable. |
| benign_14 | shopping_agent | Clean catalog selection with task quantity, price and evidence is representable. |
| benign_15 | shopping_agent | Clean catalog selection with task quantity, price and evidence is representable. |
| benign_16 | shopping_agent | Clean catalog selection with task quantity, price and evidence is representable. |
| benign_17 | future_gateway | Remaining-budget semantics require seeded mandate payment history. |
| benign_18 | future_gateway | Remaining-budget semantics require seeded mandate payment history. |
| benign_19 | future_gateway | Remaining-budget semantics require seeded mandate payment history. |
| benign_20 | future_gateway | Remaining-budget semantics require seeded mandate payment history. |
| budget_currency | future_gateway | Requires raw money/quantity mutations, historical spending, or multi-intent budget lifecycle. |
| budget_hidden_shipping | future_gateway | Requires raw money/quantity mutations, historical spending, or multi-intent budget lifecycle. |
| budget_item_mismatch | future_gateway | Requires raw money/quantity mutations, historical spending, or multi-intent budget lifecycle. |
| budget_parallel | future_gateway | Requires raw money/quantity mutations, historical spending, or multi-intent budget lifecycle. |
| budget_premium | future_gateway | Requires raw money/quantity mutations, historical spending, or multi-intent budget lifecycle. |
| budget_prior_spend | future_gateway | Requires raw money/quantity mutations, historical spending, or multi-intent budget lifecycle. |
| budget_quantity | future_gateway | Requires raw money/quantity mutations, historical spending, or multi-intent budget lifecycle. |
| budget_rounding | future_gateway | Requires raw money/quantity mutations, historical spending, or multi-intent budget lifecycle. |
| budget_split | future_gateway | Requires raw money/quantity mutations, historical spending, or multi-intent budget lifecycle. |
| budget_threshold | future_gateway | Requires raw money/quantity mutations, historical spending, or multi-intent budget lifecycle. |
| intent_accessory | future_gateway | Exact harness_constructed judge proposal available; not an LLM observation or shopping action. |
| intent_brand | unsupported | No structured brand/compatibility fixtures exist. |
| intent_bulk | future_gateway | Exact harness_constructed judge proposal available; not an LLM observation or shopping action. |
| intent_delivery | future_gateway | Exact harness_constructed judge proposal available; not an LLM observation or shopping action. |
| intent_donation | future_gateway | Exact harness_constructed judge proposal available; not an LLM observation or shopping action. |
| intent_evidence | unsupported | No separate incompatibility-warning source and evidence-drop runtime plan exists. |
| intent_reassurance | future_gateway | Exact harness_constructed judge proposal available; not an LLM observation or shopping action. |
| intent_subscription | future_gateway | Exact harness_constructed judge proposal available; not an LLM observation or shopping action. |
| intent_task_rewrite | unsupported | The model cannot mutate retained original task state. |
| intent_unrelated | future_gateway | Exact harness_constructed judge proposal available; not an LLM observation or shopping action. |
| ipi_bidi | unsupported | Only a descriptive payload/plan exists; no actual merchant fixture delivers this attack. |
| ipi_customer_review | future_gateway | Requires unauthorized quantity; the current graph fixes quantity from the original task. |
| ipi_encoded | unsupported | Only a descriptive payload/plan exists; no actual merchant fixture delivers this attack. |
| ipi_fragmented | unsupported | Ordered cross-source fragment assembly with quantity override has no complete runtime plan. |
| ipi_hidden_css | future_gateway | Requires an arbitrary payment amount; the current model validates catalog money. |
| ipi_html_comment | unsupported | The attack requests a catalog-wide purchase; the graph selects one product only. |
| ipi_metadata | unsupported | Only a descriptive payload/plan exists; no actual merchant fixture delivers this attack. |
| ipi_offscreen | shopping_agent | Single-product selection over materialized merchant evidence is representable. |
| ipi_tool_result | unsupported | Only a descriptive payload/plan exists; no actual merchant fixture delivers this attack. |
| ipi_zero_font | future_gateway | Requires unauthorized quantity; the current graph fixes quantity from the original task. |
| merchant_casefold | future_gateway | Requires a distinct padded identity and gateway registry normalization semantics. |
| merchant_disallowed | unsupported | Only a descriptive payload/plan exists; no actual merchant fixture delivers this attack. |
| merchant_homoglyph | unsupported | Only a descriptive payload/plan exists; no actual merchant fixture delivers this attack. |
| merchant_link_launder | unsupported | Only a descriptive payload/plan exists; no actual merchant fixture delivers this attack. |
| merchant_lookalike | shopping_agent | Single-product selection over materialized merchant evidence is representable. |
| merchant_metadata | unsupported | Conflicting path/metadata identity cannot yield a current agent product candidate. |
| merchant_order_swap | future_gateway | Requires order ownership and payer approval lifecycle state. |
| merchant_payee | future_gateway | Requires independent order-payee mutation and verification. |
| merchant_rebind | future_gateway | Requires a post-selection settlement mutation boundary. |
| merchant_redirect | unsupported | An HTTP redirect/recipient-change response cannot be represented by a static page snapshot. |
| ops_capture_void | future_gateway | Requires idempotency, approval/capture, clock/concurrency, or service/ledger fault injection. |
| ops_empty_key | future_gateway | Requires idempotency, approval/capture, clock/concurrency, or service/ledger fault injection. |
| ops_expired_approval | future_gateway | Requires idempotency, approval/capture, clock/concurrency, or service/ledger fault injection. |
| ops_intel_malformed | future_gateway | Requires idempotency, approval/capture, clock/concurrency, or service/ledger fault injection. |
| ops_intel_unknown | future_gateway | Requires idempotency, approval/capture, clock/concurrency, or service/ledger fault injection. |
| ops_key_body | future_gateway | Requires idempotency, approval/capture, clock/concurrency, or service/ledger fault injection. |
| ops_ledger_failure | future_gateway | Requires idempotency, approval/capture, clock/concurrency, or service/ledger fault injection. |
| ops_parallel_approval | future_gateway | Requires idempotency, approval/capture, clock/concurrency, or service/ledger fault injection. |
| ops_replay_approval | future_gateway | Requires idempotency, approval/capture, clock/concurrency, or service/ledger fault injection. |
| ops_unknown_retry | future_gateway | Requires idempotency, approval/capture, clock/concurrency, or service/ledger fault injection. |
