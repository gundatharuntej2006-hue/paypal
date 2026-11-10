"""Requests shaped the way the Go gateway sends them (branch vivek, gateway/pkg/intelclient)."""

import copy

from intel.config import _ledger_url
from intel.samples import JUDGE_ALIGNED, RISK_FIRST


def test_null_lists_from_go_are_treated_as_empty(client_for):
    risk = copy.deepcopy(RISK_FIRST)
    risk["history"] = None  # var riskHistory []RiskHistoryItem marshals as null when empty
    risk["proposal"]["items"] = None
    judge = copy.deepcopy(JUDGE_ALIGNED)
    judge["proposal"]["items"] = None
    with client_for(mode="live") as client:
        scored = client.post("/risk/score", json=risk)
        judged = client.post("/judge/intent", json=judge)
    assert scored.status_code == 200 and scored.json()["status"] == "ok"
    assert judged.status_code == 200


def test_null_required_field_is_still_rejected(client_for):
    risk = copy.deepcopy(RISK_FIRST)
    risk["proposal"]["amount"] = None
    with client_for(mode="live") as client:
        assert client.post("/risk/score", json=risk).status_code == 422


def test_ledger_url_accepts_render_hostport():
    assert _ledger_url("agentguard-gateway:10000") == "http://agentguard-gateway:10000/internal/ledger/events"
    assert _ledger_url("https://gw.example.com/internal/ledger/events") == "https://gw.example.com/internal/ledger/events"
    assert _ledger_url(None) is None and _ledger_url("") is None
