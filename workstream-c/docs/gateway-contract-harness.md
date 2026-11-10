# Offline Gateway SDK contract harness

`tests/test_gateway_contract.py` checks SDK wire behavior against source inspected
at gateway commit `d101e4c76037a72ad88ea7d788dc98432a8139a5` on `origin/vivek`.
This is offline contract compatibility evidence, not end-to-end functionality.

## Source of the contract

The read-only inspection used `git show origin/vivek:<path>`; no teammate branch
was checked out. Relevant files at the pinned commit:

- `gateway/pkg/api/router.go:15`: `POST /v1/payments/intents`.
- `gateway/pkg/api/handlers.go:143-225`: Bearer authentication, body `agent_id`,
  `dry_run`, idempotency and source limits.
- `gateway/pkg/models/models.go:8-68`: mandate, item, source-context and intent
  JSON fields; lines 95-132: checks, PayPal details and decision response.
- `gateway/pkg/api/handlers.go:275-297,412-428,480-528,655-660`: response shapes,
  dry-run acknowledgment, step-up token and optional PayPal details.
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
- Gateway idempotency still lacks request-body binding. SDK key preservation
  tests do not establish safe duplicate recovery or gateway persistence.
- Dry-run deployment remains conditional on Vivek's security fixes. Budget
  reservation, approval/rejection races, restart recovery, synthetic-event
  isolation and report validation remain gateway concerns.
- `dry_run: true` acknowledgment is required client-side, but a mocked response
  does not prove the deployed gateway cannot contact PayPal. A future isolated
  deployment must force `GATEWAY_MODE=dryrun` and independently prove isolation.
- No benchmark executor, genuine scenario execution or payment path is added.
  The benchmark's genuine-execution rejection and SDK defaults are unchanged.

All existing uncommitted Anthropic adapter files, including the Workstream C
README and dependency declarations, are left untouched by this milestone.
