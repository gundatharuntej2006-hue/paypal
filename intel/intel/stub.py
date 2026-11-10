"""Canned but input-aware answers, so teammates can integrate before the real signals exist.

Enabled with INTEL_MODE=stub. Triggers are listed in the README.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from intel.models import (
    Factor,
    Influence,
    JudgeRequest,
    JudgeResponse,
    RiskRequest,
    RiskResponse,
    ScanRequest,
    ScanResponse,
    Snippet,
)

VERSION = "stub"
FORCE_UNKNOWN = "__force_unknown__"
_INJECTION_PHRASES = (
    "ignore previous",
    "ignore all previous",
    "ignore your",
    "system message",
    "[system]",
    "new instructions:",
)
_INFLUENCE_PHRASES = (
    "the page said",
    "the page told",
    "instructed me",
    "was told to",
    "told me to",
    "the review said to",
)


async def scan(req: ScanRequest) -> ScanResponse:
    if not req.contexts:
        return ScanResponse.unknown("no_contexts", version=VERSION)
    for ctx in req.contexts:
        # The raw HTML counts too: hidden-text attacks only show up there.
        content = ctx.text if not ctx.html else f"{ctx.text}\n{ctx.html}"
        low = content.lower()
        if FORCE_UNKNOWN in low:
            return ScanResponse.unknown("forced_unknown", version=VERSION)
        for phrase in _INJECTION_PHRASES:
            at = low.find(phrase)
            if at >= 0:
                snippet = Snippet(ref=ctx.ref, text=content[max(0, at - 40) : at + 160], rule=f"stub:{phrase}")
                return ScanResponse(
                    verdict="injection", confidence=0.95, snippets=[snippet], stage="heuristic", version=VERSION
                )
    return ScanResponse(verdict="clean", confidence=0.9, stage="heuristic", version=VERSION)


async def judge(req: JudgeRequest) -> JudgeResponse:
    reasoning = req.reasoning.lower()
    if FORCE_UNKNOWN in reasoning:
        return JudgeResponse.unknown("forced_unknown", version=VERSION)
    mandate, proposal = req.mandate, req.proposal

    def misaligned(rationale: str, influence: list[Influence] | None = None) -> JudgeResponse:
        return JudgeResponse(
            verdict="misaligned",
            confidence=0.8,
            rationale=rationale,
            influenced_by_external=influence or [],
            version=VERSION,
        )

    if mandate.allowed_merchants and proposal.merchant_id not in mandate.allowed_merchants:
        return misaligned(f"Stub: merchant {proposal.merchant_id} is not in the mandate.")
    if Decimal(proposal.amount) > Decimal(mandate.max_per_txn):
        return misaligned(f"Stub: amount {proposal.amount} is over the per-transaction cap {mandate.max_per_txn}.")
    for phrase in _INFLUENCE_PHRASES:
        at = reasoning.find(phrase)
        if at >= 0:
            ref = req.source_contexts[0].ref if req.source_contexts else None
            quote = Influence(reasoning_quote=req.reasoning[at : at + 200], source_ref=ref)
            return misaligned("Stub: the reasoning follows an instruction from page content.", [quote])
    return JudgeResponse(
        verdict="aligned", confidence=0.85, rationale="Stub: the purchase matches the mandate task.", version=VERSION
    )


async def risk(req: RiskRequest) -> RiskResponse:
    mandate, proposal = req.mandate, req.proposal
    now = req.now or datetime.now(UTC)
    cap = Decimal(mandate.max_per_txn)
    ratio = min(Decimal(proposal.amount) / cap, Decimal(2)) if cap > 0 else Decimal(2)
    amount_part = round(float(ratio) * 0.25, 3)
    recent = [
        h for h in req.history if h.merchant_id == proposal.merchant_id and timedelta(0) <= now - h.ts <= timedelta(hours=1)
    ]
    velocity_part = round(min(0.15 * len(recent), 0.45), 3)
    factors = [Factor(name="amount_vs_cap", contribution=amount_part, detail=f"{proposal.amount} of {cap} cap")]
    if recent:
        factors.append(
            Factor(
                name="velocity_same_merchant",
                contribution=velocity_part,
                detail=f"{len(recent)} payment(s) to {proposal.merchant_id} in the last hour",
            )
        )
    score = round(min(0.05 + amount_part + velocity_part, 1.0), 3)
    return RiskResponse(status="ok", score=score, factors=factors, version=VERSION)
