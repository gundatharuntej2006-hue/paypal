# Local two-case integration smoke runner

This is an opt-in wiring check, not a genuine benchmark or an LLM evaluation.
It does not start services, enroll principals, sign/renew credentials, or invoke
the offline benchmark executors. It uses an injected deterministic selector for
merchant_a / usb-c-hub, 1 x 29.99 USD, electronics_accessories. The real shopping
graph retrieves all pages; it does not select the cheaper merchant_lookalike.

Before explicitly authorizing a real run, independently verify the running
gateway is ENV=production, GATEWAY_MODE=dryrun and bound to 127.0.0.1:8080. Verify
INTEL_SERVICE_URL=http://127.0.0.1:8001, intelligence INTEL_MODE=stub on loopback,
and the merchant server on 127.0.0.1:8010. No PayPal/provider credentials are
required. Load configuration securely outside the repository: bootstrap using
TEST_PRINCIPAL_ID/TEST_PRINCIPAL_PUBLIC_KEY and set the four production tokens.
INTEL_API_TOKEN must match between gateway and intelligence. Never register via
POST /v1/mandates. The runner sends only AGENT_AUTH_TOKEN to the gateway, never
to merchants, and has no intelligence endpoint/configuration override.

The explicit confirmation flag attests these operator-verified prerequisites.
The current gateway API cannot prove its deployment mode or intelligence stub
mode before submission. Per-request dry_run and response acknowledgment are
required but do not establish those deployment facts. No extra health, SSE,
dashboard or event requests are made to infer them.

Future invocation, only after a separate execution authorization:

```powershell
& C:\Users\ayush\AgentGuardEnvs\workstream-c-py312\Scripts\python.exe -m agentguard_workstream_c.integration.local_smoke --confirm-local-stub-dryrun --credential-dir "C:\Users\ayush\AgentGuardSecrets-NEW"
```

`--credential-dir` is required; it selects a directory, never individual secret
values. There is no implicit directory, environment override, search for the
newest bundle, or fallback to the previous expired bundle. Select the exact fresh
directory explicitly. The loader reads only local-smoke-mandates.json and
local-smoke-secrets.json there. Old bundle files and directories remain untouched.

The selected directory must satisfy the preparation helper's same local
fixed-drive policy. UNC/network drives, device paths and alternate data streams
are rejected before path opening. Every ancestor is pinned and verified with the
existing Windows APIs; unsafe ACLs, reparse points and unsupported paths fail
closed. The selected directory and both files must pass protected ACL checks.
Identity, final path and ACL checks use the actual opened handles that supply the
read-only streams; the loader never reopens a checked filename for content. It
does not create, repair, overwrite or delete credential paths.

Principal ID, agent ID and public key come from the protected configuration
document. Nonempty valid identities are required, both mandates must agree with
those loaded identities and key, and the validated agent ID is used in both SDK
requests. No principal, agent or public key is hardcoded. Internal consistency
is not principal authorization: the gateway must separately bootstrap that same
principal/public-key pair and use the new bundle's tokens. Both
signatures are checked with the existing Go-compatible canonicalization, all
purchase/policy metadata is validated, identifiers must be distinct, and validity
is checked before any network activity and again before each submission. Expired
files cause a local refusal; the runner never regenerates, rotates or re-signs.

Prepare code, dependencies, executable checks, ports and startup commands before
an independently authorized refresh. Prepare the fresh bundle once in a unique
secured directory without overwriting old files, securely configure and start
the gateway/intelligence with its public identity and tokens, then submit promptly.
The helper expires mandates about 15 minutes after preparation (with valid_from
backdated 30 seconds). Do not edit signed timestamps or automatically refresh.

Only the exact HTTP origins 127.0.0.1:8080 and 127.0.0.1:8010 are accepted. URL
aliases, other schemes/hosts/ports, paths, credentials and query strings are
rejected. Nonempty HTTP_PROXY/HTTPS_PROXY/ALL_PROXY configuration is rejected;
the SDK and merchant client additionally disable proxy discovery and redirects.

Both credential cases and local fixture metadata are validated before merchant
GETs. Both actual graphs and SDK intents are prepared/validated before the first
gateway POST. The poisoned graph uses visible_text and attack_id=poison_06: its
desk-lamp banner is additional encountered evidence while the selected hub stays
the same. All encountered contexts, actual consumed text and original HTML are
passed through intent_from_proposal unchanged; no source evidence is truncated
or filtered to just the hub.

The route-bound transport permits at most two sends, only POST
/v1/payments/intents, with bodies bound to the two SDK-serialized plans in order.
The clean response must be allow, acknowledge dry_run=true, have a nonblank audit
reference, and show passing checks. Only then is the poisoned request submitted;
it must be block with mandate=pass, injection=flagged and the gateway's actual
prompt_injection_detected reason. Expected results are assertions, never generated
observations. Output contains safe observed decision/acknowledgment booleans only;
no full responses, token values, step-up tokens, prompts or source HTML are logged.

A timeout or protocol/HTTP failure stops immediately with unknown-outcome
semantics. No automatic retry, key rotation or live fallback exists. An unexpected
clean decision prevents the second POST. Do not rerun after an ambiguous result
without separate investigation and authorization. The invocation does not persist
results or expose a genuine benchmark execution path.

The optional local-smoke extra supplies cryptography; the agent extra supplies
LangGraph. Offline tests use fixed dummy keys/tokens, a rendered merchant-client
double, the real graph/SDK, fake transport and socket/DNS guards. The test identity
variations are deterministic dummy UUIDs and fixed test keys, not fresh real
credentials. Injected loaders receive the selected directory; injected loaders,
shopping functions, clocks and transports are trusted test code, not a sandbox.

Current gateway limitations remain: shared-token ownership mapping, incomplete
idempotency body binding and some blank-audit-ID error paths are not fixed by this
runner. Gateway checks attest injection detection; raw scan versions/evidence are
not exposed in the decision response and are not independently fetched here.

## Scope and recorded evidence

Credential preparation is a separate, explicitly authorized operation described
in `local-smoke-credentials.md`; it does not run this integration check.
This runner performs only the fixed two-case local network check. The full-stack
benchmark executor remains offline-only; neither path enables arbitrary local
scenario execution or a full network benchmark.

See `local-smoke-evidence.md` for operator-reported successful runtime results.
The runner does not persist them, and documentation does not reproduce a run.
