# AgentGuard demo script

**Target cut 2:58.** The closing window starts at 2:40 and ends at 2:58, leaving two seconds below the 3:00 limit. Record in `?demo=1` and stage the timeline and benchmark tabs before the take. For missing live evidence, use the disclosure lines in the [runbook](runbook.md).

## 0:00–0:20 · Problem

**On screen**

- **0:00–0:06** Open **Live Ledger** and hold on the AgentGuard title and outcome counters.
- **0:06–0:20** Move across the merchant, amount, and decision columns. Open one payment inspector so the layer verdicts are visible.

**Say**

> An AI shopping agent can read a product page, choose a seller, and ask to pay. The same page can contain instructions the buyer never approved. AgentGuard checks what the buyer authorized and gives the reviewer a record of every decision.

## 0:20–0:50 · Mandate and buyer consent

**On screen**

- **0:20–0:30** Click **Mandates**. Enter **Restock office supplies**. Show **USD**, total cap **60.00**, per-transaction cap **40.00**, and step-up threshold **25.00**. Select the sandbox merchant, enter its category, and click **24h**.
- **0:30–0:43** Click **Continue to PayPal**. Open the sandbox buyer approval popup and approve the order. Cut around sign-in and mask account details and third-party logos.
- **0:43–0:50** Return to the dashboard. Show the approved order ID, click **Sign and save mandate**, and hold on the **Active** card.

**Say**

> I authorize one agent to restock office supplies with a sixty-dollar total cap. Purchases over twenty-five dollars need review. I approve the order with a sandbox buyer, then sign the mandate. The signature binds the terms and the approved order ID.

## 0:50–1:25 · Happy path

**On screen**

- **0:50–0:57** Click **Live Ledger**, select the saved mandate, and click **Happy path**. Open the newest **Allow** row as it arrives.
- **0:57–1:08** Hold the inspector on the four passing verdicts. Point out mandate, injection scan, intent judge, and risk.
- **1:08–1:25** Click **Open full timeline**. In the same payment, show **PayPal authorized**, **PayPal captured**, and **Webhook received** in recorded order. Show the authorization and capture IDs beside the purchase amount. Use a short cut between nodes if scrolling takes too long.

**Say**

> The mandate passes, the page is clean, the item matches the task, and risk is low. The gateway allows this purchase. Here are the sandbox authorization and capture, followed by the webhook in the same payment timeline.

## 1:25–2:10 · Poisoned page and budget splitting

**On screen**

- **1:25–1:34** Switch to the staged mock merchant tab. Reveal the hidden instruction using the mock page's source or the approved reveal view. Highlight just the instruction aimed at overriding the buyer's limits.
- **1:34–1:51** Switch to **Live Ledger**. With the same mandate selected, click **Poisoned page**. Open the new **Block** row, then **Open full timeline**. Show the highlighted snippet under **Injection scan completed** and the **PayPal authorization voided** node. Confirm the authorization ID matches the blocked payment.
- **1:51–2:10** Click **Back to Live Ledger**, reselect the mandate, and click **Budget splitting**. Show the related partial requests. Open the blocked row and show its **Risk engine** rationale or **Risk scored** factors.

**Say**

> This merchant page contains a hidden instruction to ignore the buyer's limits. The scan flags that instruction, the payment is blocked, and the sandbox authorization is voided. Next, the agent splits a purchase into smaller requests. The risk engine spots the repeated partial purchases and blocks the attempt.

## 2:10–2:40 · Benchmark

**On screen**

- **2:10–2:20** Click **Benchmark**. Hold on the measured **Last run**, run ID, **Overall block rate**, and **False-positive rate**.
- **2:20–2:31** Point to **Ablation comparison** and compare **LLM only** with **Full stack** using the report's values.
- **2:31–2:40** In **Scenario results**, filter **Class** to the injection class and show the **Expected**, **Actual**, and **Layer** columns.

**Say**

> This run blocked [X percent] of attacks and recorded [Y percent] false positives. The LLM-only configuration blocked [A percent], versus [B percent] for the full stack. Each scenario row shows what we expected, what happened, and which layer caught it.

## 2:40–2:58 · Close

**On screen**

- **2:40–2:52** Show the confirmed agent SDK call on a clean editor slide. Use the following one-liner as a staging placeholder until Tharun confirms the actual signature.

  ```python
  decision = agentguard.request_payment(mandate, cart)
  ```

- **2:52–2:58** Return to the AgentGuard title and hold on the repository link. End before 3:00.

**Say**

> The agent submits a payment intent in one call. AgentGuard checks that request against the buyer's authority and records the result. The repository and demo links are in the description.
