# AgentGuard intelligence service

The gateway calls three signals in parallel: `POST /scan/content`, `POST /judge/intent` and `POST /risk/score`. The request and response formats are in [../contracts/intel/](../contracts/intel/).

## Run

From this folder:

```
py -m uvicorn intel.main:app --reload --port 8001   # API docs at http://localhost:8001/docs
py -m pytest -q
py -m intel.export_contracts                          # after changing intel/models.py
py -m intel.eval --misses                             # golden-set accuracy per endpoint
```

On a fresh machine, install first: `pip install -e ".[dev]"`

## Getting access (for judges and teammates)

- **No keys at all:** set `INTEL_MODE=stub`. Every endpoint answers with input-aware canned results (see "Stub triggers" below), which is enough to run the whole system end to end.
- **LLM key** (for the LLM part of scan and judge), either one:
  - **Google Gemini, free tier:** create a key at https://aistudio.google.com/apikey and set `GEMINI_API_KEY`. The free tier allows about 20 requests a day per model (checked on 2026-11-12; AI Studio shows your limits), so the default is a chain of three fast models, roughly 60 LLM calls a day, and each payment uses up to two. When every model is out, scan and judge return `unknown` (`llm_rate_limited`) and the gateway steps up. Daily quotas reset at midnight Pacific time. Run test scripts with `--rpm` to stay under the per-minute limit, and don't run the full golden eval on the key the live demo uses.
  - **Anthropic:** create a key at https://console.anthropic.com and set `ANTHROPIC_API_KEY`.

  Without a key the service still runs: clear injections are caught by the rules, risk scoring works fully, and everything the LLM would decide comes back `unknown`, so the gateway steps up instead of allowing.
- **PayPal sandbox** (for webhooks, reconciliation, payouts, invoices): create a free app at https://developer.paypal.com, then set `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET` and `PAYPAL_WEBHOOK_ID`. Tick **Transaction Search**, **Payouts** and **Invoicing** under the app's features.

## Settings (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `INTEL_MODE` | `live` | `stub` returns canned answers that react to the input, for integration |
| `INTEL_API_TOKEN` | none | When set, callers must send it in the `X-Internal-Token` header |
| `GEMINI_API_KEY` / `ANTHROPIC_API_KEY` | none | LLM key. Without one, the LLM-based signals return `unknown` |
| `LLM_PROVIDER` | `gemini` if only `GEMINI_API_KEY` is set, else `anthropic` | Which LLM serves scan and judge |
| `INTEL_SCAN_MODEL`, `INTEL_JUDGE_MODEL` | Gemini: `gemini-2.5-flash-lite,gemini-2.5-flash,gemini-3.5-flash-lite` (fastest first); Anthropic: `claude-haiku-5-5` | Model for each signal. A comma-separated list is a fallback chain: a model that hits its rate limit (or is down) rests until Google says it can be retried, and the next one answers |
| `INTEL_DEADLINE_S` | `6` with Gemini, `2.5` with Anthropic | Hard deadline per signal. A signal that runs over returns `unknown`. Free-tier Gemini took 1.4-1.7s warm and up to 4.3s on a first call, so with Gemini set the gateway's `INTEL_TIMEOUT_MS` to `7000` |
| `INTEL_LOCAL_UTC_OFFSET_MIN` | `330` (IST) | Time zone the risk scorer uses for its "unusual time" check |
| `PAYPAL_ENV` | `sandbox` | `sandbox` or `live` |
| `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET` | none | Sandbox app credentials (needed for postback verification) |
| `PAYPAL_WEBHOOK_ID` | none | The ID PayPal shows for our webhook. Use `WEBHOOK_ID` for simulator events |
| `PAYPAL_WEBHOOK_VERIFY` | `postback` | `postback` asks PayPal's verification API. `local` checks the signature ourselves, which also works for simulator events |
| `LEDGER_URL`, `LEDGER_TOKEN` | none | The gateway's internal event hook: a full URL, or a bare Render `host:port` (then `http://` and `/internal/ledger/events` are added). Without it, events stay in memory (development only) |
| `GATEWAY_URL` | host of `LEDGER_URL` | Where reconciliation reads the ledger (`GET /v1/ledger/events`) |

## PayPal webhooks

`POST /webhooks/paypal` is the URL to register in the PayPal sandbox app. It does four things:

1. **Verifies the signature.** A forged or tampered delivery is rejected with 400 and logged as `webhook.rejected`.
2. **Drops duplicates by PayPal event ID.**
3. **Records the event** in the gateway ledger as `webhook.received`, with the payment, order, authorization, capture and refund IDs.
4. **Asks PayPal to retry** (a 5xx response) when PayPal or the ledger can't be reached.

Payout and invoice events are linked to their payment too: payouts through `sender_item_id`, invoices through `invoice_id`.

## PayPal operations (internal, called by the gateway)

All need `X-Internal-Token`. None of them decides whether money may move: the gateway calls payouts and invoice payments only after its own `allow`. Schemas are in [../contracts/intel/](../contracts/intel/).

| Endpoint | What it does |
|---|---|
| `POST /recon/run` | Compares each captured payment in the ledger with PayPal: amount, currency, status, payment id, and whether PayPal has it at all. Uses Transaction Search, and falls back to the Captures API for anything search doesn't show yet (sandbox search lags by hours). Each difference is written once as `reconciliation.mismatch`; every run writes `reconciliation.completed`. A payment PayPal couldn't be asked about is `unknown`, never `ok` |
| `POST /payouts` | Sends a payout to a PayPal email. The payment id is the idempotency key, so a retry never pays twice. Writes `payout.created` |
| `GET /payouts/{batch_id}` | Current payout status from PayPal. Writes `payout.updated` once per new status |
| `POST /invoices/{id}/intent` | Turns a PayPal invoice into a payment proposal (merchant = invoicer email, amount due, items) plus the invoice's own text as a `kind: invoice` source context. Submit both to the gateway, so the injection scan reads the invoice note and the mandate and risk checks apply |
| `POST /invoices/{id}/payments` | After `allow` and capture: marks the invoice paid with that capture. Refuses cancelled or already-paid invoices and amounts above what is due. Writes `invoice.paid` |

PayPal has no API for a buyer to pay an invoice directly, so an invoice is paid through the normal Orders flow and then marked paid. A blocked invoice is never marked paid.

Commands:

```
py -m intel.recon                                     # one reconciliation run (exit 1 when mismatches); use as a Render cron job
py -m intel.invoices create --to BUYER_EMAIL --amount 29.99 --item "A4 paper, 5 reams" [--note "..."]
py -m intel.invoices show INV2-XXXX                   # the proposal the gateway would receive
```

Golden cases live in `intel/golden/<endpoint>.json`. `intel/golden/<endpoint>.wsc.json` are generated from Tharun's Workstream C attack library: `py -m intel.import_wsc PATH/TO/workstream-c/src`. Its 15 poisoned pages and clean pages go to scan; its benign scenarios go to judge and risk; its intent-drift scenarios are rebuilt as concrete drifted purchases for judge (see the module docstring for what is left out and why). `py -m intel.eval` reads both. Risk cases count as caught at 0.35, the gateway's step-up line.

## Live mode status

- **risk:** weighted rules with no LLM, so it works without an API key.
- **scan:** clear injections are caught by fast rules (`stage: heuristic`). Everything else goes to one batched LLM call (`stage: llm`). Results are cached by content.
- **judge:** one LLM call with few-shot examples. Prompts are in `intel/prompts/`, and their version is returned in `version`.
- **Without an LLM key:** scan and judge return `unknown` with `reason: no_llm_key` straight away, except clear injections, which the rules still catch.

Live check (needs a key): `py -m intel.smoke_llm 20` runs 20 rounds of scan + judge in parallel and prints verdicts and latency. On the Gemini free tier, add `--rpm 10` (and use `py -m intel.eval --rpm 10`) to stay under the per-minute limit.

## Stub triggers (`INTEL_MODE=stub`)

Use these to test every gateway decision path:

- **scan:** text containing "ignore previous", "ignore your", "system message", "[system]" or "new instructions:" returns `injection`. Text containing `__force_unknown__` returns `unknown`. No contexts at all returns `unknown`.
- **judge:** a merchant outside the mandate, an amount over `max_per_txn`, or reasoning such as "the page said" or "was told to" returns `misaligned`. `__force_unknown__` in the reasoning returns `unknown`.
- **risk:** the score rises with the amount compared with the cap, and with payments to the same merchant in the last hour.

## Rule for the gateway

The gateway must never treat `unknown`, a timeout or any 4xx/5xx response as a pass. At minimum it should step up.
