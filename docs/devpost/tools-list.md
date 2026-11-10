# Tools and APIs used

This inventory reflects the implementation on `ritika`. It covers tools present in the code and deployment configuration.

| Tool or API | How AgentGuard uses it |
| --- | --- |
| PayPal JavaScript SDK via `@paypal/paypal-js` | Loads PayPal Buttons for live buyer approval, requests or supplies a gateway order ID through `createOrder`, and handles the approved ID through `onApprove`. |
| AG Grid Community | Renders the payment and event ledger and benchmark scenario results with sorting, resizing, and filtering. |
| Render Blueprint | Defines the planned Go gateway, Python intelligence service, Postgres database, and static dashboard deployment with health checks and environment bindings. |
| Web Crypto | Generates browser Ed25519 demo keys and signs and verifies canonical mandate JSON. |
| React, TypeScript, React Router, and Vite | Implement and build the browser dashboard, typed contracts, and page routes. |
| Recharts | Plots block rate by class, catching-layer counts, and the ablation comparison. |
| Vitest | Tests mandate validation and signatures, ledger event projection, and payment timeline order. |

The branch has no AI model implementation or server-side PayPal REST client to list. The dashboard’s checkout callbacks call the AgentGuard gateway interface. PayPal authorization, capture, and webhook rows in the stub are simulated. The [README verification checklist](../readme-verify.md) tracks the live integration audit.
