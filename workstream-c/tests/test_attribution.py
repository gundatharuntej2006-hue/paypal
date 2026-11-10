"""Attribution uses observed responses/audit evidence, never scenario labels."""

from copy import deepcopy

import pytest

from agentguard_workstream_c.benchmark.attribution import attribute_block
from agentguard_workstream_c.sdk.models import DecisionResponse


def response(layer=None, decision="block"):
    checks = {"mandate": "pass", "injection": "pass", "intent": "pass", "risk": "pass"}
    reasons = []
    if layer == "mandate": checks["mandate"] = "fail"
    elif layer:
        checks[layer] = "flagged"
        reasons = [{"injection": "prompt_injection_detected", "intent": "intent_drift_detected",
                    "risk": "risk_score_exceeds_block_threshold"}[layer]]
    return DecisionResponse.from_dict({"payment_id": "pay_test", "audit_event_id": "evt_test",
        "decision": decision, "risk_score": 0.9, "checks": checks, "reasons": reasons, "dry_run": True})


@pytest.mark.parametrize("layer", ["mandate", "injection", "intent", "risk"])
def test_explicit_observed_layer(layer):
    assert attribute_block(response(layer)) == layer


@pytest.mark.parametrize("decision", ["allow", "step_up"])
def test_nonblocks_are_never_catches(decision):
    assert attribute_block(response("risk", decision)) is None


def test_missing_ambiguous_or_inconsistent_evidence_is_null():
    from dataclasses import replace
    assert attribute_block(None) is None
    assert attribute_block(response()) is None
    assert attribute_block(replace(response("risk"), reasons=())) is None
    assert attribute_block(replace(response("risk"), audit_event_id="")) is None
    r = response("injection")
    assert attribute_block(replace(r, checks=replace(r.checks, mandate="fail"))) is None


def test_other_needs_correlated_nonsynthetic_block_audit():
    from dataclasses import replace
    r = replace(response(), reasons=("concurrent_budget_cap_exceeded",))
    event = {"id": "evt_test", "payment_id": "pay_test", "type": "decision.made", "source": "gateway",
             "data": {"decision": "block", "reasons": ["concurrent_budget_cap_exceeded"]}}
    assert attribute_block(r) is None
    assert attribute_block(r, audit_event=event) == "other"
    for field, value in (("id", "other"), ("payment_id", "other"), ("source", "synthetic_demo"), ("type", "scan.completed")):
        assert attribute_block(r, audit_event={**event, field: value}) is None
    for field, value in (("decision", "allow"), ("is_synthetic", True), ("demo_mode", True), ("reasons", [])):
        changed = deepcopy(event)
        changed["data"][field] = value
        assert attribute_block(r, audit_event=changed) is None
