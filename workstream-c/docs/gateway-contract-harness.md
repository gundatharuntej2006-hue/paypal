# Offline Gateway SDK contract harness

`tests/test_gateway_contract.py` checks SDK wire behavior against source inspected
at gateway commit `2ef82e1a1a0fdcf584af939c1962c94f3142c462` on `origin/vivek`.
This is offline contract compatibility evidence, not end-to-end functionality.

## Source of the contract

The read-only inspection used `git show origin/vivek:<path>`; no teammate branch
was checked out. Relevant files at the pinned commit:

- `gateway/pkg/api/router.go:15`: `POST /v1/payments/intents`.
- `gateway/pkg/api/handlers.go:146-242`: Bearer authentication, body `agent_id`,
  `dry_run`, idempotency and source limits.
- `gateway/pkg/models/models.go:8-68`: mandate, item, source-context and intent
  JSON fields; lines 98-125: checks, PayPal details and decision response.
- `gateway/pkg/api/handlers.go:245-345,500-619,759-767`: response shapes,
  dry-run acknowledgment, step-up token and optional PayPal details.
- `gateway/pkg/api/handlers.go:285-324`: hash, cache namespace, lookup and nonce.
- `gateway/pkg/mandate/verifier.go:266-373`: signature, identity and spending
  verification before cache lookup. This includes mutable spending state.
- `gateway/pkg/api/handlers.go:1765-1843`: report file lookup and validation.
- `gateway/pkg/policy/engine.go:49-162`: allow, block and unknown-signal step-up.
- `contracts/test_vectors/mandate_signing_vector.json`: public signing-vector
  payload, public key and signature only. No private key is copied or used.

The checked-in public payment schemas omit some implemented extensions. This
harness follows the actual Go structs/handler for `agent_id` and `dry_run`.
It is not a new shared contract or a replacement for Go runtime validation.

## What is checked

The harness reuses `FakeTransport`, `FakeOpener`, and supporting helpers from the
existing SDK tests. Request tests check exact route, Bearer headers, identity,
unchanged signed-mandate values, Decimal money strings, UUID spelling, item and
merchant fields, source refs, exact consumed text, raw HTML and `dry_run: true`.
No `X-Internal-Token` is sent. The proposal-boundary test confirms that internal
product IDs and citation lists are not invented as gateway request properties.

One test exercises the real urllib POST construction against an injected
in-memory fake opener, without performing HTTP. All other submissions use the
existing recording fake transport. Every test forbids socket connection attempts,
DNS resolution, and the standard HTTP opener's network entry point.

Hand-authored Go-shaped responses cover allow, step-up and mandate-failure block;
they are examples, not observed gateway results. Tests require literal
`dry_run: true` for every decision and reject missing/malformed acknowledgments,
missing/invalid fields, malformed JSON, redirects and oversized responses.
Authorization errors, conflicts and transport failures remain typed errors.
Timeouts preserve unknown-outcome semantics and the original key; no automatic
retry, key rotation or live fallback is allowed. Network submission stays off by
default and requires explicit authentication and caller opt-in.

Run from `workstream-c/`, using the existing environment:

```powershell
.\.venv\Scripts\python.exe -B -m pytest tests/test_gateway_contract.py -v
.\.venv\Scripts\python.exe -B -m pytest -q
.\.venv\Scripts\python.exe -B -m pip check
git diff --check
```

No Anthropic installation, provider key, gateway token, network server or payment
credential is needed. Token strings are test doubles. The public mandate vector
has historical timestamps; it is not a fresh, enrolled or runtime-valid mandate.
The harness does not sign, verify, renew or change that mandate.

## Remaining integration limitations

- Gateway runtime, Go decoding/encoding and cryptographic verification are not
  executed. The examples are pinned source interpretations, not Go-generated
  fixtures or a live schema-drift detector. Reinspect and update the harness when
  the gateway contract changes.
- Trusted test-agent enrollment and token-to-principal/agent ownership remain
  unresolved on the gateway. Local SDK identity consistency is not ownership.
- Gateway idempotency has partial payload binding, not complete body binding;
  the precise coverage and limitations are documented below. SDK key preservation
  tests do not establish safe duplicate recovery or gateway persistence.
- Dry-run deployment remains conditional on Vivek's security fixes. Budget
  reservation, approval/rejection races, restart recovery, synthetic-event
  isolation and report validation remain gateway concerns.
- `dry_run: true` acknowledgment is required client-side, but a mocked response
  does not prove the deployed gateway cannot contact PayPal. A future isolated
  deployment must force `GATEWAY_MODE=dryrun` and independently prove isolation.
- No benchmark executor, genuine scenario execution or payment path is added.
  The benchmark's genuine-execution rejection and SDK defaults are unchanged.

The Anthropic adapter, benchmark executor, scenario materializer, dashboard
adapter/fixtures and dependency declarations are unchanged by this update.

## Exact idempotency binding at the pinned commit

The Go handler constructs the following eight inputs, joins their string forms
with `|`, then hashes the UTF-8 representation with SHA-256:

1. `mandate.mandate_id`
2. `agent_id`
3. `merchant_id`
4. `amount`
5. `currency`
6. `category`
7. `agent_reasoning`
8. Go `json.Marshal(req.Items)` (name, qty, unit_price, optional category).

Its cache key is `live:<mandate_id>:<agent_id>:<trimmed_idempotency_key>` or
`dryrun:<mandate_id>:<agent_id>:<trimmed_idempotency_key>`. The authenticated
token/principal is not included. A changed agent or mandate ID changes the
namespace, rather than necessarily producing a conflict in the old namespace.

The projection omits `merchant_name`, the entire `source_contexts` array (ref,
kind, URL, consumed text and original HTML), and all signed-mandate fields other
than mandate ID, including principal, task, nonce, caps, validity window, public
key and signature. These omissions do not imply an altered/invalid signature
passes verification: the verifier runs before lookup. However, two otherwise
valid requests can change omitted evidence without a hash mismatch. The
delimiter representation is not a canonical encoding of the complete request.

For a cached entry, a nonempty stored hash mismatch returns HTTP 409 plain text:
`idempotency key conflict: request payload differs from previous request with this key`.
Matching hashes, or legacy empty hashes, return the cached decision with
`X-Cache-Lookup: HIT`. Cache-read errors fall through; decision-save errors are
discarded. Mutable spending checks before lookup can also prevent recovery of
an earlier decision. These are gateway limitations, not SDK retry guarantees.

The added tests submit two explicit caller requests through recording fake
transport, checking exact changed bodies and unchanged idempotency spelling.
`pinned_hash_projection` documents the inspected eight inputs only; it is not a
Go serializer/hash implementation, cache simulator or automatic remote-source
drift detector. Fixed fake responses do not prove that Go accepts the changes.
Mutated signed-mandate examples are deliberately not re-signed or presented as
runtime-valid authorizations. A mismatched agent is independently rejected by
the SDK before transport, with a valid-request control guarding the test.

## Incomplete block responses are protocol errors

Normal mandate-verification failure (`handlers.go:245-277`) returns HTTP 200,
`decision: block`, `risk_score: 1`, mandate `fail`, other checks `unknown`,
`dry_run: true` for these requests, and an audit ID from `mandate.failed` if
event persistence succeeds. A complete response is a valid block decision.

The early agent-mismatch branch (`196-218`) and nonce-replay branch (`324-345`)
emit a failure event but do not assign its ID to `DecisionResponse`. Since
`audit_event_id` lacks `omitempty`, Go serializes it as an empty string. The
normal invalid-mandate path also produces an empty audit ID if emission fails.
The exact nonce reason is
`mandate nonce already consumed: replay attack detected`.

Missing, null, empty or whitespace-only audit IDs are not complete successful
SDK decisions, even when the response claims `block`. The SDK raises typed
`InvalidResponseError`, keeps `outcome_unknown=True` and the caller's original
idempotency key, and performs no retry or live fallback. This update adds a
narrow nonblank audit-reference validation: previously the SDK rejected missing
or nonstring IDs but erroneously accepted empty strings. No audit ID is invented
and no gateway failure is promoted to an observed benchmark defense.

HTTP non-200 responses remain `GatewayHTTPError`, with HTTP 409 specifically
`IdempotencyConflictError`; protocol/JSON failures remain `InvalidResponseError`.
Transport timeouts remain `GatewayTimeoutError`, not proof of non-execution.

## Benchmark report endpoint incompatibility

This is a static source/fixture audit; no report server or Go validator is run.
All three original dashboard fixtures retain their exact contents, including
nullable metrics and coverage. Even `genuine_partial_coverage.json` is
hand-authored illustrative contract data, not genuine measured evidence.

| Property | Gateway behavior at the pinned commit | Compatibility consequence |
| --- | --- | --- |
| `provenance` | Required string: `genuine`, `synthetic`, `unexecuted` | All three fixtures use supported labels; the handler does not verify their truth. |
| `run_id`, `generated_at` | Both required nonblank strings; timestamp format not validated | All three agreed fixtures omit these optional dashboard fields and are rejected unchanged. Explicit null is also rejected. |
| `classes` / `by_class` | Requires a nonempty array, preferring a typed `classes` array | Fixtures provide six `by_class` rows. Entries, category inventory and counters are not validated. |
| `ablation` | Any non-null value passes the presence check | Arm metrics, provenance agreement and coverage are not validated. |
| `report_version` | Optional; null/string rejected; numeric value converted to integer and compared to 1 | Fixture version 1 passes; fractional values such as 1.5 also pass incorrectly. |
| Metrics / coverage | Not inspected | Nulls and measured zeros pass through; invalid or contradictory values also pass through. |
| Unknown fields | Accepted and returned verbatim | No sensitive-field filtering; an accepted file could expose prompts, source HTML, mandates or tokens. Never publish such data. |
| Size | Rejects over `5 * 1024 * 1024` bytes after `os.ReadFile` | Acceptance limit exists but file allocation is not bounded before reading. |
| Source | Four fixed relative paths to `contracts/test_vectors/benchmark-report.json` | No configurable operator artifact path; the checked-in candidate is synthetic. |
| Missing/invalid file | Tries remaining paths, ultimately HTTP 404 | No generated fallback metrics and no benchmark/payment execution side effect. |

Rikita's parser accepts absent/null `run_id` and `generated_at`; our agreed
fixtures need not be rewritten to satisfy this stricter handler. Align the
gateway with the agreed schema, use bounded reads, and validate/project a
sanitized report before publication. Static fixture tests preserve this known
incompatibility explicitly; they do not assert endpoint compatibility.

## Execution boundaries

Every test in this harness uses a fake transport or in-memory opener; an autouse
guard counts and rejects socket connections, DNS and ordinary opener calls.
No genuine benchmark, gateway runtime, principal enrollment, signature checking,
provider/intelligence request or PayPal operation occurs. Forced deployment
`GATEWAY_MODE=dryrun` remains necessary for any future isolated runtime test.
Approval races, durable nonce/idempotency recovery, replay accounting and strict
intelligence schema validation remain unresolved gateway integration conditions.
