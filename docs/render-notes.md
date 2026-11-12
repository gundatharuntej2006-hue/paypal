# Render service handoffs

- **Rikita** — Confirm both services deployed from ritika. In the Gateway Environment tab, confirm the 7000 ms Intel timeout and PayPal sandbox mode. Run the [hosted checks](deployment-checklist.md#hosted-checks) and fresh-browser acceptance test.
- **Vivek** — Confirm trusted demo principal enrollment and the gateway demo trigger before fresh-browser payment testing. Coordinate with Rikita if the dashboard cannot connect or receive ledger events.
- **Atul** — Confirm live Gemini inference and register the PayPal sandbox webhook at the public Intel URL with /webhooks/paypal appended. Verify a signed sandbox event reaches the gateway ledger.
- **Tharun** — Supply the measured dashboard benchmark JSON for publishing before the recording.
