# README verification checklist

The browser-only quick start can be verified independently of the live services. The `<!-- VERIFY -->` markers in the README, architecture, and API docs refer to the open checks below.

| Owner | Verify before updating the public docs |
| --- | --- |
| Vivek | Confirm the Go entry point, Postgres schema and migrations, environment names, readiness response, browser principal enrollment, and canonical signing bytes. Verify all gateway paths against the implemented handlers. |
| Vivek and Atul | Audit the actual server-side PayPal calls in `gateway/internal/paypal` or the equivalent integration. Record each method, endpoint, purpose, and sandbox test. Check webhook signature verification, event mapping, and buyer approval before activation. |
| Atul | Confirm intelligence startup and route names, actual model IDs and providers, signal inputs and outputs, and failure handling. |
| Vivek | Confirm live SSE persistence, ordered history, replay after `Last-Event-ID`, review-event confirmation, and the exact production CORS origin. |
| Vivek and Tharun | Confirm the demo trigger path, sandbox scope, and idempotency. Run the seed command twice against the sandbox and compare the payment IDs. |
| Tharun | Confirm commands to launch the shopping agent and mock merchants and execute the benchmark. Publish a measured report and replace the pending results table. |
| Ritika | Deploy the verified branch on Render, fill the hosted URL, and complete the private-browser walkthrough against the hosted environment. |
| Integration owners | Verify APIMatic artifacts and record how they were generated or used if present. Add Channel3, Postman, Zapier, or Elastic only once a working integration can be demonstrated. |

Use the [deployment checklist](deployment-checklist.md) for the hosted walkthrough and [teammate requests](requests-to-teammates.md) for proposed contracts.
