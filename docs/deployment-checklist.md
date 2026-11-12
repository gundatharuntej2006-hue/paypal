# Deployment checklist

Public static site: [AgentGuard Dashboard](https://agentguard-dashboard-ihnx.onrender.com).

## Deploy the dashboard

- [ ] For the full sandbox stack, connect the Render Blueprint to **ritika** and complete [Render setup](render-setup.md). The gateway and Intel entry points are present on that branch. For existing services, use their Render website settings.
- [ ] For a standalone stub preview, use Render **New > Static Site** with the same repository and **ritika** branch. Set **Root Directory** to `dashboard`, **Build Command** to `npm ci && npm run build`, and **Publish Directory** to `dist`. Set `VITE_USE_STUB=true` and add a rewrite from `/*` to `/index.html` under **Redirects/Rewrites**. Use a separate preview site name so the full Blueprint can keep `agentguard-dashboard`.
- [x] Verify the dashboard root and direct visits and refreshes for `/ledger`, `/mandates`, `/approvals`, and `/benchmark`. Chrome rendered every route. Gateway health, SSE, and ledger history responded successfully from the hosted origin.
- [ ] Resolve Gateway 404s for `GET /v1/mandates` and `GET /v1/approvals`. Publish the measured static benchmark report and use the static source to avoid the unavailable Gateway benchmark endpoint.
- [ ] For the live demo, enable sandbox demo mode and configure the server-side scenario trigger. Seed the ledger once using the manual command below.

## Hosted checks

1. In Render, confirm Intel deployed the commit containing Gemini fallback and the six-second Intel deadline. In the Gateway service environment, verify `INTEL_TIMEOUT_MS=7000` and `PAYPAL_ENV=sandbox`.
2. Run `node dashboard/scripts/check-hosted.mjs https://agentguard-dashboard-ihnx.onrender.com` from the repo root. It checks Intel and Gateway health, the dashboard root and direct ledger route, and the Gateway CORS response. Run it about a minute before recording to wake both services.
3. After the checks pass, run a sandbox payment and inspect the scan and judge events in **Payment Detail**. Confirm both have a verdict and rationale, then verify PayPal webhook delivery and the ledger event in the sandbox.

## Fresh-browser acceptance

1. Open a private window and visit the dashboard URL. No local environment or browser keys should be needed. In live mode after idle, expect **Waking up the server**, followed by the dashboard when `/health` responds.
2. In **Mandates**, enter a task such as “Restock office supplies”, set an approved merchant and category, and use **24h**. Continue to PayPal, use **Simulate PayPal approval** in stub mode or the sandbox buyer account in live mode, then **Sign and save mandate**. Verify the mandate is active. For live mode, Vivek must enable trusted demo principal enrollment for first-time browser keys.
3. Open **Live Ledger**. Under **Run demo scenario**, select the new mandate and run **Happy path**. Check that an **Allow** decision and audit events arrive without refreshing.
4. Run **Poisoned page** and verify a **Block** decision. Open its timeline to confirm the blocked event references the new mandate. Run **Budget splitting** and confirm several related payment events appear, including the risk block in stub mode.
5. Refresh the direct timeline URL. Stub payments and mandates live in the current page session, so use a seeded payment to verify a refreshable stub timeline. In live mode the gateway should return the stored run.

## Manual seed trigger

Run once against the sandbox gateway after deployment. Use Node.js 22+ and an active mandate already approved in the PayPal sandbox, signed by a principal the gateway trusts. Keep the signed JSON outside the repository. Use USD, a 60.00 total cap, 40.00 per transaction, a 25.00 step-up threshold, and an approved merchant. The seeder uses the first approved merchant.

```powershell
$env:DEMO_GATEWAY_URL = 'https://<gateway-host>'
$env:DEMO_SANDBOX = '1'
node scripts/demo/seed.mjs path/to/signed-sandbox-mandate.json
```

The command creates the mandate if absent, then posts a happy path, a step-up, a poisoned-page fixture, and four related budget attempts. It reports the actual gateway decision for each attempt. Repeat with the same signed file to verify that the payment IDs and decision results are reused. The gateway must persist idempotency keys before executing payment side effects. Use a separate signed mandate for a deliberately new run.
