# AgentGuard: A Payment Firewall for AI Agents

PayPal AI Hackathon 2026. Team: Vivek, Atul, Tharun, Rikita.

## What is AgentGuard?

AgentGuard is an out-of-model payment firewall service that sits between any AI agent and PayPal. It only lets a payment through if it is provably authorized by a human-signed mandate, matches what the user actually asked for, is not the result of a prompt injection, and passes a risk check.

## Repository Structure

- **[`contracts/`](file:///contracts)**: Canonical JSON Schemas and payload examples for all services (`mandate`, `payment_intent`, `decision`, `events`, and `intel/`).
- **[`gateway/`](file:///gateway)** (Workstream A - Vivek): Go core service handling cryptographic mandates, PayPal client, payment state machine, decision engine, event-sourced ledger, and SSE stream.
- **[`intel/`](file:///intel)** (Workstream B - Atul): Python FastAPI intelligence service providing `/scan/content`, `/judge/intent`, `/risk/score`, and PayPal webhook ingestion.
- **`agent/`** (Workstream C - Tharun): LangGraph shopping agent, mock merchants, and red-team benchmark runner.
- **`dashboard/`** (Workstream D - Rikita): React + AG Grid live ledger, mandate builder, and approval inbox.

## Getting Started

### 1. Gateway (Go)
```bash
cd gateway
go test ./...
go run ./cmd/gateway/main.go
```

### 2. Intelligence Service (Python)
```bash
cd intel
pip install -e .
pytest
uvicorn intel.main:app --port 8000
```

## License

[Apache License 2.0](LICENSE)
