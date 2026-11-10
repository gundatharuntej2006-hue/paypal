"""Intent judge: does the proposed purchase serve the human's task, and was the agent steered by content?"""

from __future__ import annotations

from pydantic import BaseModel

from intel import heuristics, llm
from intel.models import Influence, JudgeRequest, JudgeResponse

VERSION = "judge_v1"


class _Influence(BaseModel):
    reasoning_quote: str
    source_ref: str | None


class _JudgeOutput(BaseModel):
    aligned: bool
    confidence: float
    rationale: str
    influenced_by_external: list[_Influence]


async def run(req: JudgeRequest) -> JudgeResponse:
    client = llm.get_llm()
    try:
        output = await client.structured(
            model=client.judge_model, system=llm.load_prompt(VERSION), user=_render(req), output=_JudgeOutput
        )
    except llm.LLMUnavailable as error:
        return JudgeResponse.unknown(error.reason, version=VERSION)
    known_refs = {ctx.ref for ctx in req.source_contexts}
    influences = [
        Influence(
            reasoning_quote=item.reasoning_quote[:500],
            source_ref=item.source_ref if item.source_ref in known_refs else None,
        )
        for item in output.influenced_by_external[:5]
    ]
    return JudgeResponse(
        verdict="aligned" if output.aligned else "misaligned",
        confidence=llm.clamp01(output.confidence),
        rationale=output.rationale[:1000],
        influenced_by_external=influences,
        version=VERSION,
    )


def _render(req: JudgeRequest) -> str:
    mandate, proposal = req.mandate, req.proposal
    task = req.task or mandate.task
    items = "\n".join(
        f"- {llm.untrusted(item.name)} x{item.qty} at {item.unit_price}" + (f" ({item.category})" if item.category else "")
        for item in proposal.items
    ) or "- (no items listed)"
    merchant = proposal.merchant_id + (f" ({llm.untrusted(proposal.merchant_name)})" if proposal.merchant_name else "")
    parts = [
        f"<task>{llm.untrusted(task)}</task>",
        "<mandate>\n"
        f"currency: {mandate.currency}\nmax per payment: {mandate.max_per_txn}\ntotal budget: {mandate.max_total}\n"
        f"allowed merchants: {', '.join(mandate.allowed_merchants) or 'any'}\n"
        f"allowed categories: {', '.join(mandate.allowed_categories) or 'any'}\n</mandate>",
        "<proposal>\n"
        f"merchant: {merchant}\namount: {proposal.amount} {proposal.currency}\n"
        f"category: {proposal.category or 'not declared'}\nitems:\n{items}\n</proposal>",
        f"<agent_reasoning>\n{llm.untrusted(llm.clip(req.reasoning, 6000))}\n</agent_reasoning>",
    ]
    for ctx in req.source_contexts[:6]:
        excerpt = llm.clip(heuristics.normalize(ctx.text), 1500)
        ref = llm.untrusted(ctx.ref).replace('"', "'")
        parts.append(f'<content ref="{ref}" kind="{ctx.kind}">\n{llm.untrusted(excerpt)}\n</content>')
    return "\n\n".join(parts)
