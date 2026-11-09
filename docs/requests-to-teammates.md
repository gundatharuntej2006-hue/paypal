# Dashboard integration requests

The dashboard shell defaults to the in-memory stub. Set `VITE_USE_STUB=false` and point `VITE_GATEWAY_URL` at the gateway to try the live client. The following HTTP surface is proposed where the shared contracts do not already define it.

## Gateway (Vivek)

- `GET /v1/mandates` returns an array of mandates and `POST /v1/mandates` accepts and returns a mandate.
- `GET /v1/payments` and `GET /v1/approvals` return arrays of payment summaries. Each summary extends the shared decision with `mandate_id`, `merchant_id`, `amount`, `currency`, and `ts`.
- `GET /v1/payments/:id/events` returns an array of event envelopes.
- `POST /v1/approvals/:id/approve` and `POST /v1/approvals/:id/reject` use the payment ID and return a successful HTTP status.
- `GET /v1/events` streams event envelopes as default SSE messages. Support the optional `payment_id` query filter and allow the dashboard's origin for browser requests.

## Benchmark (Tharun and Vivek)

Expose the report via `GET /v1/benchmark` or agree on an alternate serving location. The current client expects `classes` entries with `name`, `blocked`, and `total`, plus `false_positive_rate`, `median_latency_ms`, `full_stack_block_rate`, and `llm_only_block_rate`. Rates are fractions from 0 to 1.
