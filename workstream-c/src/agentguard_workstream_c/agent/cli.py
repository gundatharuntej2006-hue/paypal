"""Local deterministic demo CLI; no LLM provider, SDK, or payment execution."""

import argparse
import json

from .graph import run_shopping
from .model import DeterministicDemoModel
from .state import AgentConfig


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8010")
    parser.add_argument("--reader-mode", choices=("visible_text", "raw_html"), default="visible_text")
    parser.add_argument("--attack-id")
    args = parser.parse_args(argv)
    try:
        state = run_shopping(args.task, AgentConfig(base_url=args.base_url, reader_mode=args.reader_mode, attack_id=args.attack_id))
    except ValueError as exc:
        print(json.dumps({"error": str(exc), "model": DeterministicDemoModel.name}))
        return 1
    proposal = state.get("purchase_proposal")
    output = {
        "model": DeterministicDemoModel.name, "reader_mode": args.reader_mode,
        "selected_merchant": None if proposal is None else {"merchant_id": proposal.merchant_id, "name": proposal.merchant_name},
        "selected_product": None if proposal is None else {"product_id": proposal.product_id, "name": proposal.product_name},
        "quantity": None if proposal is None else proposal.quantity,
        "proposed_amount": None if proposal is None else str(proposal.amount),
        "currency": None if proposal is None else proposal.currency,
        "decision_summary": state.get("reasoning_summary", ""),
        "selected_source_references": state.get("source_references", ()),
        "sources_consumed": [{"ref": c.ref, "url": c.url} for c in state.get("source_contexts", ())],
        "execution_errors": state.get("execution_errors", ()),
        "payment_submitted": False,
    }
    print(json.dumps(output, indent=2))
    return 0 if proposal is not None and not state.get("execution_errors") else 1


if __name__ == "__main__":
    raise SystemExit(main())
