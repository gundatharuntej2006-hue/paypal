# AgentGuard Shared Contracts

This directory contains the canonical JSON Schemas and payload examples for all AgentGuard services:

- **`mandate.schema.json`**: The human-signed spending mandate specification. Signed with Ed25519 over canonical RFC 8785 JSON representation of the mandate fields (excluding the `signature` field itself).
- **`payment_intent.schema.json`**: The `POST /v1/payments/intents` payload sent by Tharun's shopping agent to Vivek's Go gateway.
- **`decision.schema.json`**: The verdict returned by the gateway decision engine to the agent and Rikita's dashboard.
- **`events.schema.json`**: The append-only event schema persisted in PostgreSQL and streamed via SSE (`GET /v1/events`).
- **`examples.json`**: Concrete sample payloads for end-to-end integration and mock servers.
- **`intel/`**: The three intelligence endpoint schemas owned by Atul (`/scan/content`, `/judge/intent`, `/risk/score`).
