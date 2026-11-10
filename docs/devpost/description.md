# AgentGuard

## Inspiration

An AI shopping agent can find a product and request payment in seconds. A buyer still needs a way to control which merchants it can use, how much it can spend, and when it must ask for help. A merchant page can also contain instructions aimed at the agent, even though the buyer never authorized them.

## What it does

AgentGuard is a payment firewall prototype. The reviewer sets spending limits and signs a mandate. The dashboard demonstrates how a payment request can pass, require human approval, or be blocked. The Live Ledger and payment timeline show the checks behind each outcome.

Four layers contribute to the decision.

- **Signed mandate** binds the buyer’s spending limits, merchant and category allowlists, validity window, and approved order ID to an Ed25519 signature.
- **Injection scan** looks for merchant content that tries to override the buyer’s instructions.
- **Intent judge** checks whether the proposed purchase matches the task.
- **Risk engine** evaluates amount and behavior signals, including repeated partial purchases.

The browser demo simulates all four verdicts and includes a happy path, a poisoned-page block, and a budget-splitting block.

## How we built it

We built the dashboard in React and TypeScript. It signs and verifies mandates locally with Web Crypto, uses AG Grid for the ledger and scenario table, and uses Recharts for benchmark charts. The stub and live clients share the dashboard interface. The live client is wired for gateway events and PayPal JavaScript SDK buyer approval. We defined the deployment topology in a Render Blueprint and documented the gateway handoff through shared JSON contracts.

## Challenges

We needed to keep audit history readable when events arrive live or replay after reconnect. The ledger deduplicates event IDs, and Payment Detail preserves the gateway’s stored order before appending new events. We also needed to distinguish a submitted human review from a recorded one, so the inbox waits for the corresponding audit event before showing a confirmed decision.

## Accomplishments

From a fresh browser, a reviewer can sign a demo mandate, run an allowed purchase and a blocked purchase, and inspect the resulting timelines. The seeded approvals can be approved or rejected in stub mode, with both actions appearing in the ledger. The Benchmark page supports class-level block rates, false positives, latency, catching layers, and an LLM-only versus full-stack comparison. The bundled report is visibly labeled sample data.

## What we learned

A decision is easier to review when the spending authority, signal verdicts, and payment events appear in one trace. Benchmark results also need clear denominators and provenance. A partial run should expose its coverage, and an unavailable metric should remain distinguishable from a measured zero.

## What's next

Connect the dashboard to the team’s gateway and intelligence service, verify the PayPal Sandbox payment lifecycle, and publish a measured benchmark with provenance and coverage. Then run the private-browser walkthrough on the hosted deployment.
