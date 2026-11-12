# Rikita's workstream

## Completed on ritika

- [x] React dashboard shell with five pages and stub data (S)
- [x] Browser mandate validation, Ed25519 signing, and simulated buyer consent (M)
- [x] AG Grid ledger with SSE client, filters, grouping, row flashes, and verdict details (L)
- [x] Payment detail timeline with ordered history and live updates (M)
- [x] Approval inbox with approval and rejection actions, confirmation events, and PayPal SDK handoff (M)
- [x] Benchmark page, proposed report schema, and labeled sample data (S)
- [x] Render Blueprint, health-check configuration, and manual sandbox seed command (L)
- [x] README, architecture and API docs, deployment instructions, and MIT license (M)
- [x] Demo recording kit with a 2:58 script and recording mode (L)
- [x] Devpost copy, submission checklist, and final QA walkthrough (M)
- [x] Postman collection and environment verified against a local fixture (M)

## Open for submission

- [x] Gateway and Intel are present on ritika. Both hosted health endpoints returned HTTP 200 in the previous deployment check.
- [ ] Confirm the latest Intel deployment in Render, confirm the Gateway timeout is 7000 ms, and verify the final hosted dashboard URL. See [deployment checklist](deployment-checklist.md).
- [ ] Verify live principal enrollment, mandate acceptance, buyer consent, approvals, SSE history and reconnect, and signed PayPal webhook delivery against the deployed gateway.
- [ ] Publish a measured benchmark and verify provenance, coverage, and unavailable metrics with Tharun's adapter. Tharun supplies the measured JSON. His three exact test fixtures pass dashboard validation and rendering tests. The dashboard ships labeled sample data until a measured report is supplied.
- [ ] Record, publish, and privately verify the final YouTube video.
- [ ] Add all teammates, verify public links and the license, and confirm the Devpost submission.
- [ ] Verify the Postman collection against the deployed sandbox gateway.

## Stretch items deferred

- [ ] Zapier step-up notifications. The proposed server-side hook is in the teammate handoff.
- [ ] Elastic-backed audit search. Deferred after Zapier in the stretch sequence.
