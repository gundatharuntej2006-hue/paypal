# Intelligence service contracts (v0.1.0, proposed)

These are JSON Schemas for the three endpoints the gateway calls, plus `examples.json` with real request and response pairs. They are generated from `intel/intel/models.py`. Don't edit them by hand: change the models, then run `py -m intel.export_contracts`.

| Endpoint | Request | Response |
|---|---|---|
| `POST /scan/content` | `scan_content.request.schema.json` | `scan_content.response.schema.json` |
| `POST /judge/intent` | `judge_intent.request.schema.json` | `judge_intent.response.schema.json` |
| `POST /risk/score` | `risk_score.request.schema.json` | `risk_score.response.schema.json` |

## Changes from the project brief, for the team to agree

1. **Results can be unknown.** Scan returns `verdict: clean | injection | unknown`, judge returns `aligned | misaligned | unknown`, and risk returns `status: ok | unknown` (with `score: null` when unknown). `reason` says why, for example `timeout`. **Unknown must never count as a pass.**
2. **Scan contexts can include raw HTML.** Each context can carry `html` next to `text`, which is needed to catch hidden-text tricks. Agent SDK, please send it when you have it.
3. **The judge also gets `source_contexts`**, so it can tell when the reasoning was steered by page content.
4. **Risk gets `history` and `now`.** `history` is the earlier payments under this mandate, sent by the gateway. `now` makes tests repeatable. Proposals can carry a declared `category`.
5. **All timestamps must include a timezone**, for example `2026-11-10T10:00:00Z`. A timestamp without one is rejected with 422.
6. **Every response has `version` and `latency_ms`.** Store both in the ledger so benchmark runs can be compared.
7. **Optional shared secret.** If `INTEL_API_TOKEN` is set on the service, the gateway must send the `X-Internal-Token` header.

## Ledger hook: needed from the gateway (Vivek)

The webhook receiver writes into the gateway's ledger through one internal endpoint, set as `LEDGER_URL` on this service.

```
POST <LEDGER_URL>            header: X-Internal-Token: <LEDGER_TOKEN>
{
  "type": "webhook.received" | "webhook.rejected",
  "payment_id": "pay_881" or null,
  "idempotency_key": "paypal:<PayPal event id>" or null,
  "source": "intel",
  "data": { "paypal_event_id", "paypal_event_type", "paypal_status", "amount", "currency",
            "order_id", "authorization_id", "capture_id", "refund_id", ... }
}
```

- **Reply** 2xx when the event is stored, or 409 when the `idempotency_key` already exists. Put a unique constraint on that column; it is what guarantees "exactly once".
- **Link webhooks to payments.** When creating orders, set `purchase_units[].custom_id = payment_id`. PayPal copies it onto authorizations and captures, which is how a webhook finds its payment. Refunds are linked through `capture_id`.
- **New event types to add:** `webhook.rejected`. The brief already has `webhook.received`.

## PayPal operations: internal endpoints for the gateway

Same `X-Internal-Token` as the three signals. Full description in [../../intel/README.md](../../intel/README.md).

| Endpoint | Request | Response | When the gateway calls it |
|---|---|---|---|
| `POST /recon/run` | `recon_run.request.schema.json` (body optional) | `recon_run.response.schema.json` | From a Render cron job (`py -m intel.recon`), or a dashboard button |
| `POST /payouts` | `payouts_create.request.schema.json` | `payouts_create.response.schema.json` | Only after `allow`, for a payout-type intent |
| `GET /payouts/{batch_id}` | none | `payouts_create.response.schema.json` | To refresh a payout's status |
| `POST /invoices/{id}/intent` | none | `invoice_intent.response.schema.json` | When the agent wants to pay an invoice: submit `proposal` and `source_contexts` to `/v1/payments/intents` |
| `POST /invoices/{id}/payments` | `invoice_payment.request.schema.json` | `invoice_payment.response.schema.json` | Only after `allow` and a completed capture of that invoice's intent |

Answers: 503 means PayPal or the ledger is unreachable, so retry. 404 means PayPal has no such object. 409 means PayPal or the invoice state refused it (for example, Payouts not enabled, invoice cancelled, or amount above what is due).

**Ledger event types these write** (dashboard, please show them):

- `reconciliation.mismatch`: one per payment per distinct difference, with `issues: [{field, ledger, paypal}]`. `field` is `exists`, `amount`, `currency`, `status` or `payment_id`.
- `reconciliation.completed`: one per run, with counts `checked`, `ok`, `mismatched`, `unknown` and `search_available`.
- `payout.created` and `payout.updated`, with `payout_batch_id`, `batch_status` and `item_status`.
- `invoice.paid`, with `invoice_id`, `invoice_number`, `capture_id` and `amount`.

Reconciliation reads the ledger from `GET /v1/ledger/events`, so it currently sees the last 100 events.
