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

## Settings (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `INTEL_MODE` | `live` | `stub` returns canned answers that react to the input, for integration |
| `INTEL_API_TOKEN` | none | When set, callers must send it in the `X-Internal-Token` header |
| `ANTHROPIC_API_KEY` | none | LLM key. Without it, the LLM-based signals return `unknown` |
| `INTEL_SCAN_MODEL`, `INTEL_JUDGE_MODEL` | `claude-haiku-5-5` | Model for each signal |
| `INTEL_DEADLINE_S` | `2.5` | Hard deadline per signal. A signal that runs over returns `unknown` |
| `INTEL_LOCAL_UTC_OFFSET_MIN` | `330` (IST) | Time zone the risk scorer uses for its "unusual time" check |
| `PAYPAL_ENV` | `sandbox` | `sandbox` or `live` |
| `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET` | none | Sandbox app credentials (needed for postback verification) |
| `PAYPAL_WEBHOOK_ID` | none | The ID PayPal shows for our webhook. Use `WEBHOOK_ID` for simulator events |
| `PAYPAL_WEBHOOK_VERIFY` | `postback` | `postback` asks PayPal's verification API. `local` checks the signature ourselves, which also works for simulator events |
| `LEDGER_URL`, `LEDGER_TOKEN` | none | The gateway's internal event hook. Without it, events stay in memory (development only) |

## PayPal webhooks

`POST /webhooks/paypal` is the URL to register in the PayPal sandbox app. It does four things:

1. **Verifies the signature.** A forged or tampered delivery is rejected with 400 and logged as `webhook.rejected`.
2. **Drops duplicates by PayPal event ID.**
3. **Records the event** in the gateway ledger as `webhook.received`, with the payment, order, authorization, capture and refund IDs.
4. **Asks PayPal to retry** (a 5xx response) when PayPal or the ledger can't be reached.

Golden cases live in `intel/golden/*.json`. Add cases there; Tharun's library will be imported into the same format.

## Live mode status

- **risk:** weighted rules with no LLM, so it works without an API key.
- **scan:** clear injections are caught by fast rules (`stage: heuristic`). Everything else goes to one batched LLM call (`stage: llm`). Results are cached by content.
- **judge:** one LLM call with few-shot examples. Prompts are in `intel/prompts/`, and their version is returned in `version`.
- **Without `ANTHROPIC_API_KEY`:** scan and judge return `unknown` with `reason: no_llm_key` straight away, except clear injections, which the rules still catch.

Live check (needs the key): `py -m intel.smoke_llm 20` runs 20 rounds of scan + judge in parallel and prints verdicts and latency.

## Stub triggers (`INTEL_MODE=stub`)

Use these to test every gateway decision path:

- **scan:** text containing "ignore previous", "ignore your", "system message", "[system]" or "new instructions:" returns `injection`. Text containing `__force_unknown__` returns `unknown`. No contexts at all returns `unknown`.
- **judge:** a merchant outside the mandate, an amount over `max_per_txn`, or reasoning such as "the page said" or "was told to" returns `misaligned`. `__force_unknown__` in the reasoning returns `unknown`.
- **risk:** the score rises with the amount compared with the cap, and with payments to the same merchant in the last hour.

## Rule for the gateway

The gateway must never treat `unknown`, a timeout or any 4xx/5xx response as a pass. At minimum it should step up.
