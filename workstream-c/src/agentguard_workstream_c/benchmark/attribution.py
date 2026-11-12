"""Conservative attribution from observed gateway decisions, never expectations.

These are local evidence requirements, not new gateway fields. Conflicting or
ambiguous evidence yields null. 'Other' needs a correlated observed audit event;
absence of known-layer evidence alone never implies 'other'.
"""

from collections.abc import Mapping

from ..sdk.models import DecisionResponse


def attribute_block(decision: DecisionResponse | None, *, audit_event: Mapping | None = None) -> str | None:
    """Only explicit blocks with matching checks/reasons are attributable.

    The caller must obtain an actual gateway response (and correlated event for
    'other'). This function does not establish provenance or call any service.
    Refusals, invalid model output, timeouts and step-ups are not gateway blocks.
    """
    if not isinstance(decision, DecisionResponse) or decision.decision != "block" or not decision.audit_event_id:
        return None
    checks = decision.checks
    candidates = []
    if checks.mandate == "fail":
        candidates.append("mandate")
    if checks.injection == "flagged" and "prompt_injection_detected" in decision.reasons:
        candidates.append("injection")
    if checks.intent == "flagged" and "intent_drift_detected" in decision.reasons:
        candidates.append("intent")
    if checks.risk == "flagged" and "risk_score_exceeds_block_threshold" in decision.reasons:
        candidates.append("risk")
    if len(candidates) == 1:
        return candidates[0]
    if candidates:
        return None
    if (isinstance(audit_event, Mapping) and audit_event.get("payment_id") == decision.payment_id
            and audit_event.get("id") == decision.audit_event_id and audit_event.get("type") == "decision.made"
            and audit_event.get("source") == "gateway"):
        data = audit_event.get("data")
        if (isinstance(data, Mapping) and data.get("decision") == "block"
                and data.get("is_synthetic", False) is False and data.get("demo_mode", False) is False
                and isinstance(data.get("reasons"), list)
                and "concurrent_budget_cap_exceeded" in data["reasons"]
                and "concurrent_budget_cap_exceeded" in decision.reasons):
            return "other"
    return None
