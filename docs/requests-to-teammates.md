# Dashboard integration requests

The dashboard shell defaults to the in-memory stub. Set `VITE_USE_STUB=false` and point `VITE_GATEWAY_URL` at the gateway to try the live client. The following HTTP surface is proposed where the shared contracts do not already define it.

## Gateway (Vivek)

- `GET /v1/mandates` returns an array of mandates and `POST /v1/mandates` accepts and returns a mandate.
- `GET /v1/payments` and `GET /v1/approvals` return arrays of payment summaries. Each summary extends the shared decision with `mandate_id`, `merchant_id`, `amount`, `currency`, and `ts`.
- `GET /v1/payments/:id/events` returns an array of event envelopes.
- `POST /v1/payments/:id/approve` accepts an optional JSON body with `paypal_order_id`. `POST /v1/payments/:id/reject` accepts an optional JSON body with `reason`. Both return a successful HTTP status and emit the corresponding human decision event for that payment.
- `GET /v1/events` streams event envelopes as default SSE messages. Support the optional `payment_id` query filter and allow the dashboard's origin for browser requests.

## Benchmark (Tharun and Vivek)

Expose the report via `GET /v1/benchmark` or agree on an alternate serving location. The current client expects `classes` entries with `name`, `blocked`, and `total`, plus `false_positive_rate`, `median_latency_ms`, `full_stack_block_rate`, and `llm_only_block_rate`. Rates are fractions from 0 to 1.

## Mandate signing and PayPal consent (Vivek)

- Confirm canonical JSON before enabling live saves. The dashboard strips only the top-level signature, sorts mandate field names lexicographically, preserves array order, uses compact JavaScript JSON string escaping, and signs the UTF-8 bytes. Verify matching escaping for HTML characters, Unicode line separators, and other non-ASCII text in Go. The dashboard sends padded standard base64 for the 64-byte Ed25519 signature.
- For the demo, bind the displayed principal ID to its displayed public key in the gateway's trusted key configuration. The public key is padded standard base64 of the 32 raw Ed25519 bytes. The browser keeps the demo private key as base64 PKCS#8 in localStorage. Treat it as a demo key accessible to scripts on the dashboard origin. If you expose an authenticated POST /v1/principals/keys route, confirm its contract and enrollment rules before connecting the dashboard.
- The proposed POST /v1/mandates/:id/order accepts the draft mandate without a PayPal order ID or signature, creates a PayPal Orders v2 order with AUTHORIZE intent for the draft currency and total cap, and returns a JSON object with the order ID under id. Bind the order to the exact draft and principal on the gateway. On signed mandate save, verify the signature and order binding, confirm buyer approval server-side, and authorize funds through PayPal before activating the mandate. Make order creation and saving idempotent for the same mandate ID to support retries.

## Ledger replay (Vivek)

Send each event envelope in an SSE data frame with an id field equal to the envelope id. Honor the browser's Last-Event-ID header on reconnect. The ledger deduplicates replays by envelope ID and keeps the newest 5,000 events. To populate the initial live payment summaries, stream recent payment chains on first connection. Include merchant_id, amount, and currency on intent.received, the shared checks and paypal objects on decision.made, and signal rationale or factors in the corresponding check events.

## Step-up checkout (Vivek)

- Return only unresolved step-up payments from GET /v1/approvals. Use the decision timestamp in ts, and include items, checks, reasons, risk_score, injection_rationale and intent_rationale where available. Include payment item data on intent.received so new inbox cards can also be built from the stream.
- For external PayPal checkout, provide an HTTPS approval_url on paypal.com. The gateway owns the buyer return or webhook confirmation, verifies PayPal's result, and emits human.approved only after verification. For SDK checkout, supply approval_order_id and approval_intent (authorize or capture, default authorize). The dashboard can also use paypal.order_id for a pending checkout. Validate the returned order against the payment before recording the review. Omit checkout fields when a plain approval POST is appropriate.
- Treat both review actions as idempotent for the payment and reject conflicting decisions. Include a supplied rejection reason in human.rejected so it appears in the inbox and ledger.
