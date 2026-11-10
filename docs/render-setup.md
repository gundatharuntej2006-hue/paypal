# Deploy AgentGuard on Render

The Blueprint can be reviewed now. Deploy after the gateway and intelligence entry points land on `ritika`. See [service handoffs](render-notes.md).

1. Sign in to the [Render Dashboard](https://dashboard.render.com/). Click **New > Blueprint**.
2. Under GitHub, connect your account if prompted. Grant Render access to `gundatharuntej2006-hue/paypal`, then click **Connect** next to that repository.
3. Enter `agentguard` as the Blueprint name, choose **ritika** in **Branch**, and leave **Blueprint Path** at `render.yaml`.
4. Review the three services and the Postgres database. Fill the prompted values from the table below. Confirm the free plan availability in your workspace before deploying.
5. Click **Deploy Blueprint**. Open each web service's **Events** and **Logs** to check the deploy. Open the gateway and intelligence service URLs with `/health` appended and verify both return HTTP 200. Open the dashboard URL to verify the static build.
6. Open **agentguard-gateway** in Render. Copy the public HTTPS URL shown below its name and append `/webhooks/paypal`. Share that full URL with Atul once the gateway is healthy. Register it for the sandbox app once the POST handler is ready.
7. After sandbox registration, open **agentguard-gateway > Environment > Edit** and replace the initial `PAYPAL_WEBHOOK_ID` value with the ID PayPal assigned. Save and redeploy. Verify delivery with a sandbox event.

The [Render Blueprint setup guide](https://render.com/docs/infrastructure-as-code) shows the connection and deployment screens.

## Environment variables

| Resource | Variable | Value |
| --- | --- | --- |
| Gateway | `DATABASE_URL` | Render injects the internal Postgres connection string from `agentguard-db`. |
| Gateway | `INTELLIGENCE_HOSTPORT` | Render injects the intelligence service's private hostname and port. |
| Gateway | `CORS_ALLOWED_ORIGINS` | Set to the exact dashboard origin, normally `https://agentguard-dashboard.onrender.com`. |
| Gateway | `PAYPAL_BASE_URL` | Set by the Blueprint to the PayPal sandbox API. |
| Gateway | `PAYPAL_CLIENT_ID` | Enter the sandbox application's client ID. |
| Gateway | `PAYPAL_CLIENT_SECRET` | Enter the sandbox application's client secret. |
| Gateway | `PAYPAL_WEBHOOK_ID` | Enter the sandbox registration ID if available. For the first deployment, coordinate an unset or pending value with Vivek so health can pass while webhook verification stays disabled or rejects deliveries. Replace it after registration. |
| Gateway | `MANDATE_PUBLIC_KEY` | Enter the Ed25519 public key in the format Vivek confirms. |
| Gateway | `MANDATE_PRIVATE_KEY` | Enter a sandbox-only signing key if Vivek confirms the gateway needs it. Human signing keys stay with the signer. |
| Gateway and intelligence | `PORT` | Render supplies the listening port, which defaults to `10000`. |
| Intelligence | `LLM_API_KEY` | Enter Atul's provider API key. |
| Dashboard | `VITE_GATEWAY_URL` | Render resolves this at build time from the gateway's public `RENDER_EXTERNAL_URL`. |
| Dashboard | `VITE_PAYPAL_CLIENT_ID` | Blueprint copies the public sandbox application client ID from the gateway. |
| Dashboard | `VITE_USE_STUB` | Blueprint sets false for the live sandbox. Set true on a standalone preview static site. |
| Dashboard | `VITE_DEMO_MODE` | Blueprint sets true to show the demo panel. |
| Dashboard | `VITE_DEMO_RUN_URL` | Optional override for the sandbox trigger. Defaults to the gateway URL plus `/v1/demo/run`. |
| Dashboard | `VITE_BENCHMARK_SOURCE` | Optional. Defaults to `auto`, which checks for a published static report before querying the configured gateway. Set `static` or `gateway` to use one source. |
| Dashboard | `VITE_BENCHMARK_REPORT_URL` | Optional. Defaults to `/benchmark-report.json`. Copy a measured run to `dashboard/public/benchmark-report.json` before building to publish it at this URL. |


Render prompts for `sync: false` values on initial creation. For later changes, open the relevant service's **Environment** tab. Vite exposes `VITE_` variables to the browser, so use that prefix only for public settings. See [Render environment variables](https://render.com/docs/environment-variables).

See [deployment checklist](deployment-checklist.md) for the manual seed and private-browser walkthrough.
