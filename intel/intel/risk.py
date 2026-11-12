"""Risk scorer: transparent weighted rules, no LLM.

The score is the sum of the factor contributions, capped at 1.0 (0 = safest).
Each weight is the most that factor can add.
"""

from __future__ import annotations

import os
import re
import unicodedata
from datetime import datetime, timedelta, timezone
try:
    from datetime import UTC
except ImportError:
    UTC = timezone.utc
from decimal import Decimal

from intel.models import Factor, RiskRequest, RiskResponse

VERSION = "risk_rules_v2"

# Spending within what the human signed (up to the cap, most of the budget) is context, not risk:
# amount_vs_cap + budget_used + new_merchant stay under the gateway's 0.35 step-up line on their own.
WEIGHTS = {
    "lookalike_merchant": 0.6,
    "currency_mismatch": 0.5,
    "budget_split": 0.45,
    "duplicate_request": 0.3,
    "category_not_allowed": 0.3,
    "amount_vs_cap": 0.15,
    "velocity": 0.2,
    "budget_used": 0.1,
    "category_undeclared": 0.1,
    "new_merchant": 0.05,
    "unusual_time": 0.05,
}
SPLIT_WINDOW = timedelta(hours=1)
VELOCITY_WINDOW = timedelta(minutes=10)
DUPLICATE_WINDOW = timedelta(minutes=2)
NEAR_THRESHOLD = Decimal("0.85")  # amounts in [85%, 100%) of step_up_over are "just under" it
QUIET_HOURS = range(0, 6)  # local 00:00-05:59
LOCAL_UTC_OFFSET = timedelta(minutes=int(os.environ.get("INTEL_LOCAL_UTC_OFFSET_MIN", "330")))  # default IST


async def run(req: RiskRequest) -> RiskResponse:
    return score(req)


def score(req: RiskRequest) -> RiskResponse:
    mandate, proposal = req.mandate, req.proposal
    now = req.now or datetime.now(UTC)
    amount = Decimal(proposal.amount)
    cap = Decimal(mandate.max_per_txn)
    past = [h for h in req.history if h.ts <= now]
    factors: list[Factor] = []

    def add(name: str, fraction: float, detail: str) -> None:
        contribution = round(WEIGHTS[name] * max(0.0, min(1.0, fraction)), 3)
        if contribution > 0:
            factors.append(Factor(name=name, contribution=contribution, detail=detail))

    if proposal.currency != mandate.currency:
        add("currency_mismatch", 1, f"{proposal.currency} payment under a {mandate.currency} mandate")

    lookalike = _lookalike(proposal.merchant_id, mandate.allowed_merchants)
    if lookalike:
        add("lookalike_merchant", 1, f"{proposal.merchant_id!r} looks like allowed merchant {lookalike!r}")

    ratio = _ratio(amount, cap)
    add("amount_vs_cap", (ratio - 0.7) / 0.3, f"{proposal.amount} is {ratio:.0%} of the {mandate.max_per_txn} per-payment cap")

    spent = sum((Decimal(h.amount) for h in past if h.decision != "block" and h.currency == mandate.currency), Decimal(0))
    used = _ratio(spent + amount, Decimal(mandate.max_total))
    add("budget_used", (used - 0.7) / 0.3, f"{used:.0%} of the {mandate.max_total} total budget used after this payment")

    recent = [h for h in past if now - h.ts <= VELOCITY_WINDOW]
    add("velocity", len(recent) / 3, f"{len(recent)} payment(s) in the last {VELOCITY_WINDOW.seconds // 60} minutes")

    same_merchant = [h for h in past if h.merchant_id == proposal.merchant_id and now - h.ts <= SPLIT_WINDOW]
    if same_merchant:
        fraction, details = 0.0, []
        total = amount + sum((Decimal(h.amount) for h in same_merchant), Decimal(0))
        if total > cap:
            fraction = (1 + len(same_merchant)) / 4
            details.append(f"{len(same_merchant) + 1} payments to {proposal.merchant_id} within an hour total {total}, over the {cap} cap")
        if mandate.step_up_over:
            limit = Decimal(mandate.step_up_over)
            hugging = [h for h in same_merchant if _just_under(Decimal(h.amount), limit)]
            if hugging and _just_under(amount, limit):
                fraction = max(fraction, (1 + len(hugging)) / 4)
                details.append(f"{len(hugging) + 1} payments just under the {limit} approval threshold")
        add("budget_split", fraction, "; ".join(details))

    if any(
        h.merchant_id == proposal.merchant_id and Decimal(h.amount) == amount and now - h.ts <= DUPLICATE_WINDOW
        for h in past
    ):
        add("duplicate_request", 1, f"same amount to {proposal.merchant_id} within {DUPLICATE_WINDOW.seconds // 60} minutes")

    if proposal.category is None:
        add("category_undeclared", 1, "no category declared")
    elif mandate.allowed_categories and proposal.category not in mandate.allowed_categories:
        add("category_not_allowed", 1, f"category {proposal.category!r} is not in the mandate")

    if not any(h.merchant_id == proposal.merchant_id for h in past):
        add("new_merchant", 1, f"first payment to {proposal.merchant_id} under this mandate")

    local = now + LOCAL_UTC_OFFSET
    if local.hour in QUIET_HOURS:
        add("unusual_time", 1, f"{local:%H:%M} local time")

    factors.sort(key=lambda f: f.contribution, reverse=True)
    total_score = round(min(1.0, sum(f.contribution for f in factors)), 3)
    return RiskResponse(status="ok", score=total_score, factors=factors, version=VERSION)


def _ratio(numerator: Decimal, denominator: Decimal) -> float:
    return float(numerator / denominator) if denominator > 0 else 2.0


def _just_under(amount: Decimal, limit: Decimal) -> bool:
    return limit * NEAR_THRESHOLD <= amount < limit


# ---------- lookalike merchants ----------

_CONFUSABLES = str.maketrans(
    {"0": "o", "1": "l", "3": "e", "5": "s", "|": "l", "!": "i",
     "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x", "і": "i", "ј": "j", "ѕ": "s",
     "ο": "o", "α": "a", "ε": "e", "ι": "i", "κ": "k", "ν": "v", "ρ": "p", "τ": "t", "υ": "u", "χ": "x"}
)


def _skeleton(name: str) -> str:
    text = unicodedata.normalize("NFKC", name).lower().translate(_CONFUSABLES)
    text = text.replace("rn", "m").replace("vv", "w")
    return re.sub(r"[^a-z0-9]", "", text)


def _lookalike(merchant_id: str, allowed: list[str]) -> str | None:
    if merchant_id in allowed:
        return None
    candidate = _skeleton(merchant_id)
    for name in allowed:
        target = _skeleton(name)
        max_distance = 1 if len(target) <= 8 else 2
        if _edit_distance(candidate, target) <= max_distance:
            return name
    return None


def _edit_distance(a: str, b: str) -> int:
    previous = list(range(len(b) + 1))
    for i, char_a in enumerate(a, 1):
        current = [i]
        for j, char_b in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (char_a != char_b)))
        previous = current
    return previous[-1]
