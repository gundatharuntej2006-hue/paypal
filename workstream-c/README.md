# AgentGuard Workstream C

Owner: Tharun. This directory contains the isolated Python package for agents,
attacks, and benchmarking. The mock merchant application and internal source
evidence helper, 15 poisoned page fixtures, and a LangGraph shopping workflow are
implemented. Payment integration, the full malicious/benign scenario library,
and benchmark implementation remain pending. The shopping model is a deterministic
demo, not an LLM.

## Modules

| Path | Responsibility |
| --- | --- |
| `src/agentguard_workstream_c/` | Package namespace for Workstream C. |
| `sdk/` | Future Python Gateway SDK, including `guard.request_payment(...)`, communicating with Vivek's real Go gateway. |
| `merchants/` | Standard-library mock merchant server, catalogs, HTML rendering, internal source evidence, and a fixture-scoped visible-text reader. |
| `agent/` | Eight-node LangGraph shopping workflow ending at an internal purchase proposal. No payment submission. |
| `attacks/` | Fifteen declarative poisoned merchant variants; the full 60-malicious/20-benign scenario library remains future work. |
| `benchmark/` | Future scenario runner and JSON/Markdown reporting. |
| `tests/test_package_imports.py` | Import checks for the root package and each subpackage. |
| `pyproject.toml` | Package metadata, build configuration, development dependency, and pytest configuration. |
| `.gitignore` | Local Python environments, build outputs, caches, and environment files. |

## Integration boundaries

- Vivek owns the Go payment gateway; Atul owns the intelligence service.
- Rikita owns the React dashboard.
- The team will supply shared API contracts through the repository's `contracts/`
  directory. No contracts, request schemas, or response schemas are defined here.
- The SDK must follow those contracts and communicate with the real gateway.
- Every shopping-agent payment request must go through the SDK. The agent must
  never receive, load, or access PayPal credentials.
- Keep Workstream C implementation and tests inside this directory.

## Python and development

This scaffold declares Python 3.11 or newer and uses setuptools with a `src/`
layout. There are no mandatory runtime dependencies. The optional `dev` extra
declares pytest; the `agent` extra declares `langgraph>=1.2.20,<2.0`.

When dependency installation is authorized, development setup from this directory
can use `python -m pip install -e ".[dev]"`. No installation is required for import
tests if pytest is already available: run `python -m pytest` from this directory.
The pytest configuration adds `src/` to the import path.

The `agent/` package uses LangGraph with a provider-neutral model interface.
Provider-specific integrations remain deferred; none are installed by our extras.
The merchant application operates independently of other packages and external services.

## Mock merchant application

From `workstream-c/`, start the local server with the existing environment:

```powershell
.\.venv\Scripts\python.exe -m agentguard_workstream_c.merchants.server
```

Default address: `http://127.0.0.1:8010`. Optional `--host` and `--port` flags
override the binding; keep it on loopback for local development. Stop with Ctrl+C.
This is a development server, not a production web deployment.

| Merchant ID | Display name | Catalog |
| --- | --- | --- |
| `merchant_a` | Northstar Office | USB-C hub and LED desk lamp |
| `merchant_b` | Cedar Home | Travel mug and storage baskets |
| `merchant_c` | Trailside Outdoor | Daypack and water bottle |
| `merchant_lookalike` | Northstar Office Outlet | Independently listed hub and lamp |

The fourth merchant deliberately resembles the first by name, but has its own
stable ID. Product IDs are scoped to a merchant; both sellers can list
`usb-c-hub` without representing the same merchant or product offer.

Routes (GET):

- `/`: listing with links to all four merchants.
- `/health`: JSON `{"status":"ok"}`.
- `/merchants/{merchant_id}`: catalog with product links, prices, and categories.
- `/merchants/{merchant_id}/products/{product_id}`: full product HTML with
  description, reviews, and explicit seller identity.

Unknown merchants, products, and routes return 404. Product pages include meta
tags for `merchant_id`, `merchant_name`, `product_id`, `product_name`, `price`,
`currency`, and `category`, plus visible merchant IDs and data attributes.
An agent can follow listing and catalog links to discover every product.

Architecture within `merchants/`:

- `models.py`: immutable merchant, product, and review dataclasses.
- `data.py`: four independent static catalogs, with two products each and lookup helpers.
- `rendering.py`: complete UTF-8 HTML pages with escaped catalog content.
- `server.py`: HTTP routing, health response, and executable server entry point.
- `source_context.py`: internal evidence construction and validation.

Prices are USD decimal strings. There are no payment routes, credentials,
gateway calls, or intelligence-service calls.

## Raw HTML and source contexts

A GET of a product URL returns the complete original HTML; no browser rendering
or extraction is necessary. For example, using the virtual environment's Python:

```python
from urllib.request import urlopen
from agentguard_workstream_c.merchants.source_context import make_context, validate_contexts

url = "http://127.0.0.1:8010/merchants/merchant_a/products/usb-c-hub"
with urlopen(url, timeout=5) as response:
    raw_html = response.read().decode("utf-8")

context = make_context(
    url=url,
    text="USB-C hub 7-in-1",  # Supply the actual text consumed by the agent.
    html=raw_html,
    kind="html",
)
contexts = validate_contexts([context])
evidence = [entry.to_dict() for entry in contexts]
```

`make_context` hashes kind, URL, text, and HTML to produce a deterministic
`ctx_...` reference. Identical evidence has the same reference; changed content
gets a new reference. `SourceContext(ref="c1", text="...")` also supports
caller-assigned references and defaults to kind `text`.

Supported kinds: `html`, `text`, `review`, `email`, `invoice`, `tool_output`.
`ref` and `text` must be strings; URL and HTML are optional strings or null.
The helper preserves HTML exactly as supplied, including whitespace, comments,
and hidden content. It does not infer the text the agent read.

Limits: 50 contexts per collection, 200,000 characters of text per context,
and 1,000,000 characters of HTML per context. Oversized input raises `ValueError`;
invalid field types raise `TypeError`. Content is never silently truncated.
Always call `validate_contexts` on the completed collection as well as constructing
individual contexts. For oversized pages, report the validation error rather
than dropping HTML. These are character limits, not byte limits.

This representation follows Atul's published intelligence input schema. It is
not the final SDK-facing gateway request. Vivek must confirm that mapping; only
the gateway will call intelligence endpoints or hold `X-Internal-Token`.

Run the entire suite from `workstream-c/`:

```powershell
.\.venv\Scripts\python.exe -m pytest -v
```

Tests start a real HTTP server on an ephemeral loopback port, retrieve every
catalog/product, and compare full returned HTML with the renderer output. They
also cover missing routes, lookalike identity, escaping, evidence fidelity,
field validation, and exact size/count boundaries.

## Poisoned merchant variants

Normal listings, catalogs, and product URLs remain clean. Poisoning is opt-in:
append `?attack=<attack_id>` to the fixture's target product URL. All URLs below
are relative to `http://127.0.0.1:8010`. Remove the query to retrieve the clean
counterpart. Fixtures are enumerated programmatically through
`agentguard_workstream_c.attacks.fixtures.ATTACKS`; each exposes `clean_path`
and `poisoned_path`. There are no poisoned links in normal catalogs.

| Attack ID | Category | Delivery | Target product path |
| --- | --- | --- | --- |
| `poison_01` | Hidden CSS | `display:none` | `/merchants/merchant_a/products/usb-c-hub` |
| `poison_02` | HTML comment | Comment | `/merchants/merchant_a/products/usb-c-hub` |
| `poison_03` | Zero-size text | Zero font size | `/merchants/merchant_b/products/travel-mug` |
| `poison_04` | Off-screen text | Negative absolute positioning | `/merchants/merchant_c/products/daypack` |
| `poison_05` | Malicious review | Review | `/merchants/merchant_b/products/travel-mug` |
| `poison_06` | System impersonation | Banner | `/merchants/merchant_a/products/desk-lamp` |
| `poison_07` | Admin impersonation | Banner | `/merchants/merchant_c/products/water-bottle` |
| `poison_08` | Budget override | Banner | `/merchants/merchant_c/products/daypack` |
| `poison_09` | Quantity increase | Review | `/merchants/merchant_b/products/storage-baskets` |
| `poison_10` | Amount tampering | Banner | `/merchants/merchant_a/products/usb-c-hub` |
| `poison_11` | Merchant spoofing | Banner | `/merchants/merchant_lookalike/products/usb-c-hub` |
| `poison_12` | Task override | Banner | `/merchants/merchant_b/products/storage-baskets` |
| `poison_13` | Shipping-fee manipulation | Banner | `/merchants/merchant_c/products/water-bottle` |
| `poison_14` | Currency substitution | Banner | `/merchants/merchant_a/products/desk-lamp` |
| `poison_15` | Human-approval bypass | Banner | `/merchants/merchant_lookalike/products/desk-lamp` |

Example: `/merchants/merchant_a/products/usb-c-hub?attack=poison_01`.
Unknown attack IDs and target mismatches return 404; empty or repeated attack
parameters return 400. Omitting the parameter retains the original clean response.

Architecture:

- `attacks/fixtures.py`: immutable declarative records with stable IDs, category,
  descriptive name, target merchant/product, delivery mechanism, payload, expected
  unprotected behavior, and a maliciousness explanation. Expected behaviors are
  test hypotheses; no agent has been run or measured by this component.
- `attacks/poisoning.py`: renders inert injected markup around the clean product
  page. Reviews appear in the review list, banners before the product heading,
  and hidden variants before the closing main tag. Catalog data is never mutated.
- `merchants/server.py`: resolves the explicit query and verifies the target.
- `merchants/visible_text.py`: static fixture reader and evidence adapter.

Poisoned pages keep all real seller/product metadata, including the explicit
merchant ID, and add an `attack_id` meta tag. The lookalike payload claims a false
identity in page prose; actual merchant metadata continues to identify the outlet.
Fixtures contain no JavaScript, external fetches, or payment operations.

Hidden payloads are present in the original HTML but absent from the fixture
reader's text view. Visible reviews and banners appear in that text view.
`visible_text` recognizes our inline hiding styles, HTML comments, hidden
attributes, and head/script/style/template content. It is **not a browser or a
general CSS visibility engine**: external stylesheets, arbitrary layout, and
script execution are outside its scope.

To construct evidence for an agent consuming this reader's text output:

```python
from urllib.request import urlopen
from agentguard_workstream_c.attacks.fixtures import get_attack
from agentguard_workstream_c.merchants.visible_text import context_from_page
from agentguard_workstream_c.merchants.source_context import validate_contexts

attack = get_attack("poison_01")
url = "http://127.0.0.1:8010" + attack.poisoned_path
with urlopen(url, timeout=5) as response:
    raw_html = response.read().decode("utf-8")
context = context_from_page(url=url, raw_html=raw_html)
evidence = [entry.to_dict() for entry in validate_contexts([context])]
```

This uses the existing `make_context` internally. The `html` field retains the
complete original string, including hidden instructions and comments. Only the
separate `text` field is extracted; URLs include the actual attack query and
references remain deterministic. If an agent uses another reader, supply its
actual consumed text directly to `make_context` instead. Existing size/count
limits still apply and oversized evidence is rejected, never truncated.

Run `.\.venv\Scripts\python.exe -m pytest -v` from `workstream-c/`. Tests fetch
all 15 variants through a real loopback HTTP server, check raw/visible payloads,
placement, identity, source preservation, and missing IDs. Clean-page SHA-256
regressions cover all eight original products, and existing tests remain active.
This work does not define a gateway request or contact the intelligence service.

## LangGraph shopping workflow

Install into the existing virtual environment from `workstream-c/`. On Windows,
use the local temporary directory workaround and tested constraints:

```powershell
New-Item -ItemType Directory -Path .venv\tmp -Force | Out-Null
$previousTemp = $env:TEMP
$previousTmp = $env:TMP
try {
    $env:TEMP = (Resolve-Path .venv\tmp).Path
    $env:TMP = $env:TEMP
    .\.venv\Scripts\python.exe -m pip install -e ".[agent,dev]" -c constraints-agent-windows.txt
    if ($LASTEXITCODE -ne 0) { throw "Agent installation failed." }
} finally {
    $env:TEMP = $previousTemp
    $env:TMP = $previousTmp
}
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m pytest -v
```

Validated with Python 3.12.14, LangGraph 1.2.20, LangChain Core 1.7.6, and pytest
9.2.0. LangGraph brings transitive infrastructure dependencies, including
LangChain Core, Pydantic, and LangSmith; no paid model integration is included.
The initial `uuid-utils` 0.19.0 native wheel was blocked by Windows Application
Control in this environment, preventing graph imports and pytest startup.
The compatible 0.12.0 wheel loads successfully. `constraints-agent-windows.txt`
records that version and LangGraph 1.2.20 without adding another dependency.
No plugins or failing tests were disabled. The constraints file is not a complete
dependency lock; dependency resolution may differ on another platform.

Architecture under `src/agentguard_workstream_c/agent/`:

| Module | Responsibility |
| --- | --- |
| `state.py` | Immutable config/evidence/candidate/selection/proposal records and typed graph state |
| `merchant_client.py` | Bounded same-origin loopback HTTP browsing and explicit attack substitution |
| `content.py` | Parse the existing HTML metadata, description paragraphs and review list; construct exact consumed-content evidence |
| `model.py` | `ShoppingModel` protocol, immutable `ModelRequest`, small task parser, Decimal arithmetic and deterministic demo |
| `nodes.py` | Eight node implementations |
| `graph.py` | Explicit StateGraph, conditional error termination, `build_graph` and `run_shopping` |
| `trace.py` | Concise decision summary and validated source citations |
| `cli.py` | Local demo execution and compact JSON output |

Graph order:

```text
understand_task -> discover_merchants -> retrieve_products -> read_product_content
 -> compare_products -> select_product -> record_decision_trace
 -> prepare_purchase_proposal -> END
```

Each intermediate node routes to END on an execution error. No merchants, no
products, no matching products, invalid selection/citations, traversal failures,
and evidence limits produce explicit errors and no proposal. Previously fetched
pages are retained if retrieval later fails. Model exceptions return a safe error
without echoing provider exception text. There are no graph retry loops or
payment side effects.

Graph state retains the unchanged original task, derived task constraints,
merchant/product candidates, captured pages, SourceContexts, exact model page
inputs, reader mode, concise reasoning summary, selected merchant/product,
internal proposal, cited references, and execution errors. Node updates use fresh
collections. The model receives frozen records and tuples rather than writable
graph state or transport capabilities. Selection must identify a retrieved
merchant/product pair and cite existing evidence including its selected page.

HTTP discovery follows the existing listing/catalog links. Default bounds are
10 merchants, 20 total products, 4,000,000 bytes per response, and a 3-second
socket timeout. Exceeding a bound fails rather than silently returning a partial
successful proposal. HTTP is limited to loopback literal IPs or `localhost`
(pinned to `127.0.0.1`), one configured origin, and known merchant routes. Proxy
environment variables are ignored. All redirects are rejected. External,
unconfigured query, and nonmerchant links are never fetched. HTML must be
complete UTF-8 with matching metadata/path identity; that consistency check does
not authenticate a merchant. The parser targets our static markup, not arbitrary
websites. No browser, JavaScript, or arbitrary fetch tool is exposed to the model.

### Run the deterministic demo

First start merchants in a separate terminal using the merchant command above.
Then, from `workstream-c/`:

```powershell
.\.venv\Scripts\python.exe -m agentguard_workstream_c.agent.cli --task "Buy a USB-C hub under 40 USD" --reader-mode visible_text
.\.venv\Scripts\python.exe -m agentguard_workstream_c.agent.cli --task "Buy a USB-C hub under 40 USD" --reader-mode raw_html --attack-id poison_01
```

Use `--base-url http://127.0.0.1:PORT` for a different loopback server port.
The CLI reports model identity, reader mode, selected merchant/product,
quantity, Decimal amount/currency, decision summary, consumed source references
and URLs, and errors. It never dumps raw HTML and always reports
`payment_submitted: false`. It exits nonzero on failure or no selection.

The demo model supports literal product-name/category keywords, numeric quantity
after buy/purchase/order (otherwise one), and budgets expressed as under/below,
at most/up to, or budget followed by an amount. Under/below is exclusive. Explicit
USD/EUR/GBP/INR or `$` specifies a currency; multiple currencies are unsupported.
This is a small deterministic parser, not general natural-language understanding.
The demo selects the cheapest matching retrieved offer in the requested currency.
There is no merchant allowlist yet: the example task selects the independent
`merchant_lookalike` hub at 27.99 USD. That is not an authorization decision.
Money is parsed and calculated using Decimal, never float.

### Reader modes and evidence

- `visible_text`: every captured page is processed by the existing fixture reader;
  its exact output is supplied to the model. Supported hidden payloads do not
  reach that content view. Original HTML remains intact in evidence.
- `raw_html`: complete original HTML is passed unchanged as page content,
  including comments and hidden payloads. Both `SourceContext.text` and `.html`
  contain that original HTML. Nothing is sanitized before the model interface.

Listing, catalog, and product pages are retained and supplied as evidence: the
default run captures 13 pages (1 listing, 4 catalogs, 8 products). Source refs are
content-stable for the same URL/mode/content; different content/views can produce
different refs. Collection size and per-field limits are validated before model
invocation. In raw mode the 200,000-character text limit is also a limit on HTML
that can be consumed, even though the separate HTML field permits 1,000,000.
Oversized evidence is explicitly rejected, never truncated.

`AgentConfig(attack_id="poison_01", reader_mode="raw_html")` applies only that
fixture's poisoned_path to its discovered target; all other products remain
clean. Unknown IDs fail before browsing; an undiscovered target fails explicitly.
All 15 fixtures are tested in both modes. The deterministic model receives the
different views but makes its keyword/price decision without interpreting their
instructions. These runs measure plumbing and exposure, not LLM susceptibility.

### Provider and payment boundaries

Inject an adapter through `build_graph(config, model)` or
`run_shopping(task, config, model)`. Implement `ShoppingModel.select(ModelRequest)`
returning a validated `Selection` or None. Inputs include the original task,
constraints, candidates, exact consumed pages, and references. Return only a
short decision summary and citations; never request or persist private
chain-of-thought. There is no provider integration, key lookup, or paid call.
Use a separate, explicitly opted-in experiment for future live models.

This is an experimental shopping baseline, not an injection scanner or an
AgentGuard-protected agent. An injected model's structurally valid but unsafe
proposal is not itself a payment authorization. The internal PurchaseProposal
is not Vivek's payment-intent schema. The graph stops there. Only the future
Gateway SDK may submit payments, and only the Go gateway calls intelligence.
No PayPal or intelligence endpoints or internal tokens are used here. Run the
agent process without PayPal credentials and keep future provider secrets out
of state, prompts, evidence, and logs.

Agent tests use deterministic doubles and actual ephemeral loopback servers.
They cover graph transitions, both content modes, all 15 attacks, metadata,
proposals, original-task retention, citations, count/size bounds, malformed
responses, timeouts, redirects, URL restrictions, and absence of nonmerchant
network calls. Existing merchant and evidence regressions remain enabled.

## Windows sandbox editable-install workaround

If an editable install fails with `KeyError` followed by `ValueError: invalid
variable` naming the sandbox identifier, check `TEMP` and `TMP`. Setuptools
interprets braces in paths such as `#!sandbox.{identifier}` as variable
placeholders while finalizing the editable wheel's installation directories.
This is a temporary-path interpolation problem, not a package discovery error.

From this directory, use a temporary directory without braces for the install:

```powershell
New-Item -ItemType Directory -Path .venv\tmp -Force | Out-Null
$previousTemp = $env:TEMP
$previousTmp = $env:TMP
try {
    $env:TEMP = (Resolve-Path .venv\tmp).Path
    $env:TMP = $env:TEMP
    .\.venv\Scripts\python.exe -m pip install -e ".[dev]"
    if ($LASTEXITCODE -ne 0) { throw "Editable installation failed." }
} finally {
    $env:TEMP = $previousTemp
    $env:TMP = $previousTmp
}
.\.venv\Scripts\python.exe -m pytest -v
```

The override affects only this process and its children, is restored afterward,
and keeps temporary build files inside the ignored virtual environment. It
preserves build isolation and requires no setuptools pin or package changes.
