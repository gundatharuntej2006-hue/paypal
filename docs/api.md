# API and audit events

[Shared contracts](../contracts/) define JSON payloads for mandates, payment intents, decisions, intelligence signals, and events. The browser stub implements the dashboard-facing behavior locally. Routes for services absent from `ritika` are the intended integration interface. <!-- VERIFY -->

## Gateway HTTP surface

| Method and route | Purpose and shape |
| --- | --- |
| `GET /health` | Readiness probe used before the live dashboard loads. Return 2xx with plain text or JSON when ready. |
| `GET /v1/mandates` | List signed mandates. |
| `POST /v1/mandates` | Store and return a [signed mandate](../contracts/mandate.json). |
| `POST /v1/mandates/:id/order` | Accept a draft without `signature` or `paypal_order_id` and return `{ "id": "order-id" }` for sandbox buyer approval. |
| `POST /v1/payments/intents` | Accept a [payment intent](../contracts/payment-intent-request.json) and return a [decision](../contracts/decision-response.json). Used by the seed script. |
| `GET /v1/payments` | List payment summaries with the decision, mandate ID, merchant, amount, currency, and timestamp. |
| `GET /v1/payments/:id/events` | Return event envelopes in stored order. Return `404` for an unknown payment. |
| `GET /v1/approvals` | List unresolved step-up payments with available item, signal, and checkout details. |
| `POST /v1/payments/:id/approve` | Submit a human approval. The optional body contains `paypal_order_id` after SDK approval. |
| `POST /v1/payments/:id/reject` | Submit a human rejection. The optional body contains `reason`. |
| `GET /v1/events` | Stream event envelopes over SSE. The optional `payment_id` query filters the stream. |
| `POST /v1/demo/run` | Accept a scenario, optional mandate ID, and idempotency key. The browser can override this URL. |
| `GET /v1/benchmark/report` | Return a [benchmark report](../contracts/benchmark-report.schema.json). |
| `POST /webhooks/paypal` | Planned sandbox webhook receiver. Verify the route, signature validation, and event mapping with the gateway owner. <!-- VERIFY --> |

A mandate binds a principal and agent to a task, currency, caps, allowlists, validity window, nonce, approved order ID, and signature. Payment intents include the signed mandate, requested items, merchant context, and an idempotency key. Money amounts are decimal strings. A decision is `allow`, `step_up`, or `block` and can include per-layer results, reasons, risk score, PayPal identifiers, and its audit event ID.

## Intelligence payloads

The shared examples contain both the request and response shapes. Confirm service route names and model configuration against Atul's implementation. <!-- VERIFY -->

| Proposed route | Contract | Expected output |
| --- | --- | --- |
| `POST /scan/content` | [Scan example](../contracts/scan-content.json) | Injection flag, confidence, and flagged snippets. |
| `POST /judge/intent` | [Judge example](../contracts/judge-intent.json) | Alignment, confidence, rationale, and external influence flag. |
| `POST /risk/score` | [Risk example](../contracts/risk-score.json) | Risk score and factors. |

## Event stream

The [event envelope](../contracts/event-envelope.json) contains `id`, `type`, `payment_id`, `mandate_id`, `ts`, and `payload`. Send JSON as the SSE `data` field and use the envelope ID as the SSE `id`. The browser reconnects with `Last-Event-ID` when IDs are supplied. The gateway must replay after that ID for gap-free reconnects. <!-- VERIFY -->

| Stage | Event types |
| --- | --- |
| Intake and authority | `intent.received`, `mandate.verified`, `mandate.failed` |
| Intelligence | `scan.completed`, `judge.completed`, `risk.scored` |
| Policy | `decision.made` |
| Human review | `human.approved`, `human.rejected` |
| PayPal | `paypal.order_created`, `paypal.authorized`, `paypal.captured`, `paypal.voided`, `paypal.refunded` |
| Webhooks and audit | `webhook.received`, `reconciliation.mismatch` |

Keep event IDs stable across replays and preserve the stored sequence in payment history responses. Emit human review events after the server records the outcome. The dashboard accepts unknown event types and exposes their raw payloads in the timeline.

See [teammate requests](requests-to-teammates.md) for CORS, idempotency, and checkout field assumptions.
