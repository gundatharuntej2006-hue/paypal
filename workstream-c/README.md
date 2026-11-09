# AgentGuard Workstream C

Owner: Tharun. This directory contains the isolated Python package for agents,
attacks, and benchmarking. The current contents are package scaffolding and
import tests only; no payment, merchant, agent, or benchmark behavior is implemented.

## Modules

| Path | Responsibility |
| --- | --- |
| `src/agentguard_workstream_c/` | Package namespace for Workstream C. |
| `sdk/` | Future Python Gateway SDK, including `guard.request_payment(...)`, communicating with Vivek's real Go gateway. |
| `merchants/` | Future mock web application with at least three merchants and controlled poisoned content. |
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
Subpackages currently have no imports of one another or external services.

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
