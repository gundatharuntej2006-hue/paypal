# Tools and APIs used

This inventory reflects the implementation on `ritika`. It covers tools present in the code and deployment configuration.

| Tool or API | How AgentGuard uses it |
| --- | --- |
| PayPal JavaScript SDK via `@paypal/paypal-js` | Loads PayPal Buttons for live buyer approval, requests or supplies a gateway order ID through `createOrder`, and handles the approved ID through `onApprove`. |
| Anthropic API, Claude Haiku 5.5 (`claude-haiku-5-5`) | Configured for structured injection classification after heuristic screening and for intent judging against the signed task. Live-key verification is pending. |
| PayPal OAuth 2.0 | Gateway and intel obtain server-side access tokens. |
| PayPal Webhooks | Intel verifies signatures, deduplicates event IDs, and forwards events to the ledger. |
| PayPal Transaction Search and Captures lookup | Intel reconciles ledger captures against PayPal, falls back to capture lookup, and records discrepancies. |
| PayPal Payouts | Intel creates approved payouts with a payment-scoped idempotency key and records batch status updates. |
| PayPal Invoicing v2 | Intel converts invoices into payment proposals and records payment against a capture. |
| PayPal Orders v2 and Payments v2 | The gateway implements order creation and authorization plus capture, void, and refund. Live sandbox status awaits confirmation from Vivek. |
| AG Grid Community | Renders the payment and event ledger and benchmark scenario results with sorting, resizing, and filtering. |
| Render Blueprint | Defines the planned Go gateway, Python intelligence service, Postgres database, and static dashboard deployment with health checks and environment bindings. |
| Web Crypto | Generates browser Ed25519 demo keys and signs and verifies canonical mandate JSON. |
| React, TypeScript, React Router, and Vite | Implement and build the browser dashboard, typed contracts, and page routes. |
| Recharts | Plots block rate by class, catching-layer counts, and the ablation comparison. |
| Vitest | Tests mandate validation and signatures, ledger event projection, and payment timeline order. |

The intel PayPal integrations were tested with simulated PayPal servers. Real sandbox and live Anthropic-key verification are pending.
