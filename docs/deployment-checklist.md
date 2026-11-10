# Deployment checklist

## Deploy the dashboard

- [ ] For the full sandbox stack, connect the Render Blueprint to **ritika** and complete [Render setup](render-setup.md). The gateway and intelligence entry points must exist on that branch before deploying the Blueprint.
- [ ] For a browser-only preview while those services are pending, use Render **New > Static Site** with the same repository and **ritika** branch. Set **Root Directory** to `dashboard`, **Build Command** to `npm ci && npm run build`, and **Publish Directory** to `dist`. Set `VITE_USE_STUB=true` and add a rewrite from `/*` to `/index.html` under **Redirects/Rewrites**. Use a separate preview site name so the full Blueprint can keep `agentguard-dashboard`.
- [ ] Verify the dashboard root and a direct `/ledger` visit return the app. For a full deploy, check `/health` on the gateway and intelligence URLs and confirm the gateway allows the exact dashboard origin.
- [ ] For the live demo, enable sandbox demo mode and configure the server-side scenario trigger. Seed the ledger once using the manual command below.

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
