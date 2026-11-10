# Dashboard integration requests

The dashboard shell defaults to the in-memory stub. Set `VITE_USE_STUB=false` and point `VITE_GATEWAY_URL` at the gateway to try the live client. The following HTTP surface is proposed where the shared contracts do not already define it.

## Gateway (Vivek)

- `GET /v1/mandates` returns an array of mandates and `POST /v1/mandates` accepts and returns a mandate.
- `GET /v1/payments` and `GET /v1/approvals` return arrays of payment summaries. Each summary extends the shared decision with `mandate_id`, `merchant_id`, `amount`, `currency`, and `ts`.
- `GET /v1/payments/:id/events` returns an array of event envelopes.
- `POST /v1/payments/:id/approve` accepts an optional JSON body with `paypal_order_id`. `POST /v1/payments/:id/reject` accepts an optional JSON body with `reason`. Both return a successful HTTP status and emit the corresponding human decision event for that payment.
- `GET /v1/events` streams event envelopes as default SSE messages. Support the optional `payment_id` query filter and allow the dashboard's origin for browser requests.

## Benchmark (Tharun and Vivek)

Use [the proposed benchmark report schema](../contracts/benchmark-report.schema.json) for one complete run and confirm the field names with the dashboard workstream. Put a measured run in dashboard/public/benchmark-report.json, or serve it from GET /v1/benchmark/report. The dashboard checks the static file first in auto mode and uses the configured gateway if a published file is missing. Set VITE_BENCHMARK_SOURCE to static or gateway to choose a single source, and VITE_BENCHMARK_REPORT_URL to override the static URL.

Report rates as fractions from 0 to 1 and latency in milliseconds. Use attack classes only in by_class, include every attack and benign scenario exactly once in scenarios, and use null for an unassigned catching layer. Use error as actual when the runner cannot obtain a decision. Mark illustrative runs with sample set to true and omit that flag for measured results.

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

## Hosted demo and CORS (Vivek and Tharun)

- In the gateway, set CORS_ALLOWED_ORIGINS to the exact public dashboard origin, normally https://agentguard-dashboard.onrender.com. If Render assigns a suffixed hostname or a custom domain is added, use the exact origin shown for that deployment, with no path or trailing slash. For local development, allow http://localhost:5173 and http://127.0.0.1:5173 only in the development allowlist. Apply the same allowlist to a separate browser-facing demo trigger. Handle OPTIONS and allow GET, POST, Content-Type, and Last-Event-ID for the HTTP and event-stream routes, including GET /health. Use explicit origins for the live deployment.
- With no agent trigger in this branch, the proposed browser endpoint is POST /v1/demo/run. Set VITE_DEMO_RUN_URL to the exact URL if it is served by a different sandbox service. Accept JSON with scenario (happy_path, poisoned_page, or budget_splitting), optional mandate_id, and idempotency_key. Default to the seeded demo mandate if no ID is supplied. Return 2xx after accepting the run and publish its actual payment events to the gateway SSE stream. Enable this trigger only in sandbox, validate the selected mandate and allowed scenario, and limit public trigger usage on the server.
- For the manual seed, preserve idempotency for POST /v1/mandates and POST /v1/payments/intents across process restarts. Repeated payment requests using the same idempotency_key must return the original decision without new payment effects or ledger entries. The script also sends the same value as Idempotency-Key.
- To make first-time private-browser creation work in live mode, provide authenticated sandbox enrollment for the browser principal key, or a preapproved sandbox signing flow. The stub generates a local demo key and can be tested immediately.
