# AgentGuard Workstream C

Owner: Tharun. This directory contains the isolated Python package for agents,
attacks, and benchmarking. The mock merchant application and internal source
evidence helper are implemented. Payment, agent, attack-library, and benchmark
implementation remain pending.

## Modules

| Path | Responsibility |
| --- | --- |
| `src/agentguard_workstream_c/` | Package namespace for Workstream C. |
| `sdk/` | Future Python Gateway SDK, including `guard.request_payment(...)`, communicating with Vivek's real Go gateway. |
| `merchants/` | Standard-library mock merchant server, catalogs, HTML rendering, and internal source evidence. Poisoned catalog variants remain future work. |
| `agent/` | Future LangGraph shopping workflow, calling the SDK for every payment request. |
| `attacks/` | Future library of at least 60 malicious and 20 benign scenarios. |
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
layout. There are no runtime dependencies. The optional `dev` extra declares
pytest. The Python baseline should be confirmed with the team before integration.

When dependency installation is authorized, development setup from this directory
can use `python -m pip install -e ".[dev]"`. No installation is required for import
tests if pytest is already available: run `python -m pytest` from this directory.
The pytest configuration adds `src/` to the import path.

The separate `agent/` package provides a location for future LangGraph graph,
state, and tool modules. LangGraph and model-provider dependencies are deferred
until implementation is authorized and compatible versions are selected.
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
