# AgentGuard Gateway (Workstream A - Vivek)

The trusted core of AgentGuard: an out-of-model payment firewall written in Go that validates cryptographic mandates, queries parallel AI intelligence signals, evaluates policy, enforces an idempotent state machine, moves money safely via PayPal Sandbox APIs, and records an event-sourced audit ledger in PostgreSQL.

## Architecture

```
Shopping Agent (Tharun)
         │
         ▼ POST /v1/payments/intents
┌─────────────────────────────────────────────────────────────┐
│ AgentGuard Gateway (Go)                                      │
│                                                             │
│  1. Mandate Verification (Ed25519 + Canonical JSON + Nonce) │
│  2. Parallel AI Signals Query (/scan, /judge, /risk)        │
│  3. Decision Engine (allow / step_up / block)               │
│  4. Idempotent State Machine                                │
│  5. PayPal v2 (intent=AUTHORIZE -> Capture / Void / Refund) │
│  6. Event-Sourced Ledger (PostgreSQL / In-Memory)           │
│  7. SSE Broadcaster (GET /v1/events)                        │
└─────────────────────────────────────────────────────────────┘
         ▲                                           │
         │ POST /internal/ledger/events              ▼ GET /v1/events
Atul's Webhook Service                     Rikita's AG Grid Dashboard
```

## Module Layout

- **`pkg/crypto`**: Ed25519 keypair generation, canonical JSON serialization (RFC 8785 compliant), and mandate signature verification.
- **`pkg/mandate`**: Deterministic policy verification (signature, time window, nonce store to prevent replays, cumulative and per-transaction caps, merchant allowlists, and category allowlists).
- **`pkg/statemachine`**: Formal payment lifecycle (`received -> verified -> scanning -> decided -> authorized -> captured | voided | refunded | rejected`). Illegal transitions are strictly rejected.
- **`pkg/policy`**: Decision engine combining deterministic rules and the 3 intelligence scores with configurable thresholds. Implements strict **fail-closed** behavior (missing or timed-out AI signals result in `step_up`, never `allow`).
- **`pkg/ledger`**: Append-only event store with PostgreSQL & in-memory engines. Idempotency guarantees (`idempotency_key` unique constraints), current state projections (`payments`, `mandates`), and a replay engine.
- **`pkg/paypal`**: PayPal v2 Sandbox client (OAuth token caching, `Orders` create with `intent=AUTHORIZE`, authorize, partial capture, void, refund, `PayPal-Request-Id` idempotency, and retries with backoff). Includes full mock client for offline tests.
- **`pkg/intelclient`**: Parallel caller for Atul's `/scan/content`, `/judge/intent`, and `/risk/score` with hard timeout and fail-closed handling.
- **`pkg/events`**: Server-Sent Events (SSE) broker supporting optional `payment_id` filtering.
- **`pkg/api`**: HTTP router with CORS middleware and all REST endpoints.

## Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Service health status |
| `POST` | `/v1/payments/intents` | Evaluates payment intent and executes money movement |
| `GET` | `/v1/events` | SSE stream for real-time live ledger updates |
| `GET` | `/v1/payments/{id}` | Payment projection status and full event timeline |
| `POST` | `/v1/payments/{id}/approve` | Human step-up approval (captures PayPal hold) |
| `POST` | `/v1/payments/{id}/reject` | Human step-up rejection (voids PayPal hold) |
| `GET` | `/v1/ledger/events` | Audit list of recent ledger events |
| `POST` | `/internal/ledger/events` | Internal webhook hook for Atul (`X-Internal-Token`) |

## Running Tests

Run all unit and acceptance tests:
```bash
cd gateway
go test -v ./...
```

All acceptance tests pass:
- ✅ Mandate verification: valid, expired, forged signature, tampered amount, replayed nonce, and disallowed merchant.
- ✅ State machine: legal transitions and rejection of illegal transitions (e.g. capturing a voided payment).
- ✅ Event-sourced ledger: append-only, duplicate rejection, and state preservation across `Replay()`.
- ✅ Policy engine: table-driven tests for every signal combination.
- ✅ PayPal client: idempotent calls with `PayPal-Request-Id` produce no duplicate payments.
- ✅ Internal ledger hook: token authentication and `409 Conflict` on duplicate events.

## Running the Service

```bash
cd gateway
go run ./cmd/gateway/main.go
```

By default, the gateway runs in development mode using the In-Memory ledger and Mock PayPal client on `http://localhost:8080`.
Set `GATEWAY_BIND_ADDR=127.0.0.1` to enforce listening exclusively on the loopback interface (preventing external interface exposure).
Set `TEST_PRINCIPAL_ID` and `TEST_PRINCIPAL_PUBLIC_KEY` (or `BOOTSTRAP_PRINCIPALS="id:key,..."`) to securely register principals at startup in any environment (including `ENV=production` with `GATEWAY_MODE=dryrun`).
Set `DATABASE_URL` to connect to PostgreSQL, and `PAYPAL_CLIENT_ID` / `PAYPAL_CLIENT_SECRET` with `PAYPAL_MOCK_MODE=false` to use live PayPal Sandbox.

## CLI Tools

- **Mandate Signer / Key Generator**:
  ```bash
  go run ./cmd/signer/main.go -mode generate
  go run ./cmd/signer/main.go -mode sample
  ```
- **Ledger Replay Tool**:
  ```bash
  go run ./cmd/replay/main.go -db postgres://user:pass@localhost:5432/agentguard
  ```
