# Dashboard integration requests

The dashboard shell defaults to the in-memory stub. Set `VITE_USE_STUB=false` and point `VITE_GATEWAY_URL` at the gateway to try the live client. The following HTTP surface is proposed where the shared contracts do not already define it.

## Gateway (Vivek)

- `GET /v1/mandates` returns an array of mandates and `POST /v1/mandates` accepts and returns a mandate.
- `GET /v1/payments` and `GET /v1/approvals` return arrays of payment summaries. Each summary extends the shared decision with `mandate_id`, `merchant_id`, `amount`, `currency`, and `ts`.
- `GET /v1/payments/:id/events` returns an array of event envelopes.
- `POST /v1/payments/:id/approve` accepts an optional JSON body with `paypal_order_id`. `POST /v1/payments/:id/reject` accepts an optional JSON body with `reason`. Both return a successful HTTP status and emit the corresponding human decision event for that payment.
- `GET /v1/events` streams event envelopes as default SSE messages. Support the optional `payment_id` query filter and allow the dashboard's origin for browser requests.

## Benchmark (Tharun and Vivek)

Publish the first adapter report at dashboard/public/benchmark-report.json before building. The Render demo selects static loading. The dashboard supports the [summary schema](../contracts/benchmark-report.schema.json) with classes[].name, provenance, coverage per arm and cohort, and nested ablation. It also accepts by_class as an alias for classes.

Class totals count observed malicious decisions. Blocks exclude step-up, and benign false positives include block and step-up. Preserve null for unavailable metrics and use zero only when it is known. Coverage reports planned, observed, and unresolved counts for malicious and benign scenarios in each arm. Scenario rows and catching-layer counts are optional. Please share the genuine, synthetic, and unexecuted adapter fixtures to verify the exact wire shape.

## Mandate signing and PayPal consent (Vivek)

- The browser now matches the Go canonical mandate format and verifies the shared signing vector. Confirm authenticated principal enrollment and live buyer-order binding before enabling dashboard saves.
- For the demo, bind the displayed principal ID to its displayed public key in the gateway's trusted key configuration. The public key is padded standard base64 of the 32 raw Ed25519 bytes. The browser keeps the demo private key as base64 PKCS#8 in localStorage. Treat it as a demo key accessible to scripts on the dashboard origin. If you expose an authenticated POST /v1/principals/keys route, confirm its contract and enrollment rules before connecting the dashboard.
- The proposed POST /v1/mandates/:id/order accepts the draft mandate without a PayPal order ID or signature, creates a PayPal Orders v2 order with AUTHORIZE intent for the draft currency and total cap, and returns a JSON object with the order ID under id. Bind the order to the exact draft and principal on the gateway. On signed mandate save, verify the signature and order binding, confirm buyer approval server-side, and authorize funds through PayPal before activating the mandate. Make order creation and saving idempotent for the same mandate ID to support retries.

## Ledger replay (Vivek)

Send each event envelope in an SSE data frame with an id field equal to the envelope id. Honor the browser's Last-Event-ID header on reconnect. The ledger deduplicates replays by envelope ID and keeps the newest 5,000 events. To populate the initial live payment summaries, stream recent payment chains on first connection. Include merchant_id, amount, and currency on intent.received, the shared checks and paypal objects on decision.made, and signal rationale or factors in the corresponding check events.

## Step-up checkout (Vivek)

- Return only unresolved step-up payments from GET /v1/approvals. Use the decision timestamp in ts, and include items, checks, reasons, risk_score, injection_rationale and intent_rationale where available. Include payment item data on intent.received so new inbox cards can also be built from the stream.
- For external PayPal checkout, provide an HTTPS approval_url on paypal.com. The gateway owns the buyer return or webhook confirmation, verifies PayPal's result, and emits human.approved only after verification. For SDK checkout, supply approval_order_id and approval_intent (authorize or capture, default authorize). The dashboard can also use paypal.order_id for a pending checkout. Validate the returned order against the payment before recording the review. Omit checkout fields when a plain approval POST is appropriate.
- Treat both review actions as idempotent for the payment and reject conflicting decisions. Include a supplied rejection reason in human.rejected so it appears in the inbox and ledger.

## Hosted demo and CORS (Vivek and Tharun)

- In the gateway, set CORS_ALLOWED_ORIGINS to the exact public dashboard origin, https://agentguard-dashboard-ihnx.onrender.com. If a custom domain is added, use its exact origin with no path or trailing slash. For local development, allow http://localhost:5173 and http://127.0.0.1:5173 only in the development allowlist. Apply the same allowlist to a separate browser-facing demo trigger. Handle OPTIONS and allow GET, POST, Content-Type, and Last-Event-ID for the HTTP and event-stream routes, including GET /health. Use explicit origins for the live deployment.
- With no agent trigger in this branch, the proposed browser endpoint is POST /v1/demo/run. Set VITE_DEMO_RUN_URL to the exact URL if it is served by a different sandbox service. Accept JSON with scenario (happy_path, poisoned_page, or budget_splitting), optional mandate_id, and idempotency_key. Default to the seeded demo mandate if no ID is supplied. Return 2xx after accepting the run and publish its actual payment events to the gateway SSE stream. Enable this trigger only in sandbox, validate the selected mandate and allowed scenario, and limit public trigger usage on the server.
- For the manual seed, preserve idempotency for POST /v1/mandates and POST /v1/payments/intents across process restarts. Repeated payment requests using the same idempotency_key must return the original decision without new payment effects or ledger entries. The script also sends the same value as Idempotency-Key.
- To make first-time private-browser creation work in live mode, provide authenticated sandbox enrollment for the browser principal key, or a preapproved sandbox signing flow. The stub generates a local demo key and can be tested immediately.

## Stretch notification handoff (Vivek)

When the gateway can record step-up decisions durably, use a server-side notification hook after the decision commits. Keep the Zapier URL in server configuration. Deduplicate delivery by audit event ID, retry separately from payment processing, and send only the review identifiers and fields needed by the notification. Confirm delivery against an authorized test Zap before listing it as an integration.

## Merged gateway integration blockers

- The gateway lacks the dashboard mandate list, create, buyer-order, and demo-trigger routes.
- Live reviews require authenticated access and a single-use approval token. Keep server bearer credentials out of browser configuration. Remove step_up_token from public ledger and SSE responses before public deployment. Dashboard redaction does not protect the underlying HTTP response.
- The dashboard reads payment history from GET /v1/payments/:id and backfills from GET /v1/ledger/events. It consumes named SSE frames and maps created_at and data to the display envelope.

## Final sandbox handoffs

- **Vivek:** Deploy and share the contracts for the mandate list, signed mandate save, buyer-order creation, trusted browser principal enrollment, and the pending-approval list. The current hosted mandate and approval list calls return 404 and the router does not register those routes. Share a browser-safe authenticated review flow for both approval and rejection, including how the dashboard receives and submits the single-use token required for approval. Keep server credentials out of the browser build and review tokens out of public ledger and SSE responses. Confirm the Gateway's deployed commit, INTEL_TIMEOUT_MS=7000, PAYPAL_ENV=sandbox, and the sandbox app corresponding to the dashboard's configured PayPal client ID.
- **Atul:** Confirm the Intel deploy includes the Gemini fallback commits. Share a sandbox payment ID with actual scan and judge results and evidence of a verified PayPal sandbox webhook arriving in that payment's ledger. Coordinate the webhook registration with the sandbox app owner.
- **Tharun:** Send the measured dashboard report JSON exported by the adapter, the sandbox demo trigger contract and URL (coordinate with Vivek if it lives on the Gateway), and the exact SDK call for the recording. The current static report URL serves the dashboard HTML fallback.
