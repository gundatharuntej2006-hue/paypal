from intel import samples
from intel.models import RiskRequest
from intel.risk import score


def risk(proposal=None, history=(), now="2026-11-10T10:00:00Z"):
    body = {
        "mandate": samples.MANDATE,
        "proposal": {**samples.PROPOSAL, **(proposal or {})},
        "history": list(history),
        "now": now,
    }
    return score(RiskRequest.model_validate(body))


def names(result):
    return {f.name for f in result.factors}


def payment(amount, ts, merchant="merchant_a", decision="allow"):
    return {"merchant_id": merchant, "amount": amount, "currency": "USD", "ts": ts, "decision": decision}


def test_normal_purchase_scores_low():
    result = risk()
    assert result.status == "ok" and result.score < 0.3


def test_budget_split_scores_rise_with_each_payment():
    history, scores = [], []
    for minute in (0, 5, 10, 15):
        ts = f"2026-11-10T10:{minute:02d}:00Z"
        result = risk(proposal={"amount": "24.99"}, history=history, now=ts)
        scores.append(result.score)
        history.append(payment("24.99", ts))
    assert all(a < b for a, b in zip(scores, scores[1:])), scores
    assert "budget_split" in names(result)


def test_lookalike_merchant_is_flagged():
    assert "lookalike_merchant" in names(risk(proposal={"merchant_id": "rnerchant_a"}))
    assert "lookalike_merchant" in names(risk(proposal={"merchant_id": "merchant-a"}))
    assert "lookalike_merchant" not in names(risk(proposal={"merchant_id": "merchant_b"}))


def test_currency_mismatch_is_flagged():
    assert "currency_mismatch" in names(risk(proposal={"currency": "EUR"}))


def test_duplicate_request_is_flagged():
    result = risk(history=[payment("29.99", "2026-11-10T09:59:30Z")])
    assert "duplicate_request" in names(result)


def test_category_outside_mandate_is_flagged():
    assert "category_not_allowed" in names(risk(proposal={"category": "gift_cards"}))


def test_blocked_payments_do_not_count_as_spent():
    blocked = [payment("29.99", "2026-11-10T07:00:00Z", decision="block")] * 2
    allowed = [payment("29.99", "2026-11-10T07:00:00Z")] * 2
    assert "budget_used" not in names(risk(history=blocked))
    assert "budget_used" in names(risk(history=allowed))


def test_score_stays_in_range():
    worst = risk(
        proposal={"merchant_id": "rnerchant_a", "currency": "EUR", "amount": "99.00", "category": "gift_cards"},
        history=[payment("99.00", "2026-11-10T09:59:00Z", merchant="rnerchant_a")] * 5,
        now="2026-11-10T20:00:00Z",
    )
    assert worst.score == 1.0
