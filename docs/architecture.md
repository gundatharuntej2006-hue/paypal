# Architecture

On `ritika`, the React dashboard, browser stub, shared JSON contracts, sandbox seeder, and Render Blueprint are present. The [README diagram](../README.md#architecture) includes the target services for the full system. Their wiring below is a design and integration handoff. <!-- VERIFY -->

## Browser path

- The mandate builder uses Web Crypto to generate a demo principal key, sign compact UTF-8 JSON with sorted top-level keys, and verify the Ed25519 signature before saving. The signature field is excluded from the signed bytes. The demo private key remains in local storage on that browser origin.
- Stub mode stores mandates, payments, and reviews in memory. It simulates buyer consent and publishes prerecorded payment events through the same client interface used by the dashboard.
- Live mode probes `/health` with bounded backoff, then subscribes to `/v1/events`. The ledger deduplicates by event ID and retains the newest 5,000 events. Payment Detail fetches the stored history in its supplied order and appends unique events from a payment-filtered stream.
- The approvals inbox combines pending payment snapshots with the stream. After a user action it waits for a recorded `human.approved` or `human.rejected` event before showing a confirmed review.
- The benchmark page prefers a published report file in auto mode, then the configured gateway. Its bundled sample carries a visible banner.

## Planned sandbox services

| Component | Boundary |
| --- | --- |
| Gateway | Verify mandate signatures and scope, enforce idempotency, coordinate signals, record decisions, and mediate PayPal operations. |
| Intelligence | Return injection, intent, and risk signals. The gateway owns the final policy decision. |
| Postgres | Store mandates, payment state, and audit events durably for replay. |
| PayPal Sandbox | Present buyer consent and emit transaction notifications. The gateway validates checkout state and binds a PayPal order to the mandate or payment. |
| Agent and mock merchants | Produce payment intents from a shopping task and provide controlled merchant content for scenario runs. |
| Benchmark runner | Replay measured scenarios, compare decisions with expectations, and publish a report matching the shared schema. |

<!-- VERIFY -->
Confirm gateway entry points, event persistence and replay semantics, intelligence calls, merchant catalog data, and sandbox order handling against the integrated services. Follow the [team handoff](requests-to-teammates.md) and [verification checklist](readme-verify.md).

## Deployment

The [Render Blueprint](../render.yaml) defines two web services and Postgres in Oregon, plus the static dashboard. Web services use `/health`. The dashboard serves client routes through an SPA rewrite. The seed command reuses stable idempotency keys and relies on the gateway to retain the original decision across retries. <!-- VERIFY -->

Keep credentials in server environments. The browser receives only public configuration, such as service URLs and the PayPal sandbox client ID. See [deployment setup](render-setup.md) for variable names and allowed origins.
