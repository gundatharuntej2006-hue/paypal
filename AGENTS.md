You are implementing Workstream D (Rikita) of the AgentGuard hackathon project: dashboard, deployment, docs and submission. Repo: https://github.com/gundatharuntej2006-hue/paypal

GIT RULES (strict):
- Work ONLY on branch `ritika`. Run `git fetch origin && git checkout ritika` (create from origin/ritika if it exists, else from main). Never commit to main or any other branch.
- Before starting: `git pull --rebase origin ritika`.
- You own ONLY: /dashboard, /render.yaml, /README.md, /docs, /postman, /LICENSE, /scripts/demo (for seeding). Do NOT edit /gateway (Vivek), /intelligence (Atul), /agent or /benchmark (Tharun). If you need a change there, write it in /docs/requests-to-teammates.md instead.
- After completing the prompt's tasks and verifying acceptance tests: `git add -A && git commit -m "<clear message>" && git push origin ritika`. Confirm the push succeeded and print `git log -1 --stat`.
- If the push is rejected, `git pull --rebase origin ritika` and push again. Never force-push.

PROJECT CONTEXT:
AgentGuard is a Go gateway between AI agents and PayPal. A payment must pass: signed mandate (Ed25519), intent judge, injection scanner, risk engine, producing allow / step_up / block. Everything is written to an event-sourced ledger streamed over SSE at GET /v1/events. The shared payload formats live in /contracts (mandate, payment intent, decision response, event types: intent.received, mandate.verified, mandate.failed, scan.completed, judge.completed, risk.scored, decision.made, human.approved, human.rejected, paypal.order_created, paypal.authorized, paypal.captured, paypal.voided, paypal.refunded, webhook.received, reconciliation.mismatch). Read /contracts first and treat it as the source of truth.

First, inspect the repo tree and /contracts, summarize what exists in 5 lines, then proceed. Prefer TypeScript, React 18, Vite. Keep code clean, typed, and runnable on a fresh clone. Never hardcode secrets; use .env.example files.
