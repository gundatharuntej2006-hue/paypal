"""Live injection scanner: stage 1 heuristics, then one batched LLM call for anything not already decided."""

from __future__ import annotations

import asyncio
import hashlib
from collections import OrderedDict, defaultdict

from pydantic import BaseModel

from intel import heuristics, llm
from intel.models import ScanRequest, ScanResponse, Snippet, SourceContext

PROMPT = "scan_v1"
VERSION = f"heuristics_v1+{PROMPT}"
_CACHE_SIZE = 2000
_CACHE: OrderedDict[str, tuple[bool, float, str]] = OrderedDict()  # content hash -> (injection, confidence, evidence)


class _BlockResult(BaseModel):
    ref: str
    injection: bool
    confidence: float
    evidence: str


class _ScanOutput(BaseModel):
    results: list[_BlockResult]


async def run(req: ScanRequest) -> ScanResponse:
    if not req.contexts:
        return ScanResponse.unknown("no_contexts", version=VERSION)
    hits = await asyncio.to_thread(heuristics.scan_all, req.contexts)
    strong = [h for h in hits if h.strong]
    if strong:
        return ScanResponse(
            verdict="injection",
            confidence=min(0.99, 0.9 + 0.02 * (len(strong) - 1)),
            snippets=[Snippet(ref=h.ref, text=h.snippet[:500], rule=h.rule) for h in strong[:5]],
            stage="heuristic",
            version=VERSION,
        )
    try:
        results = await _classify(req.contexts, hits)
    except llm.LLMUnavailable as error:
        return ScanResponse.unknown(error.reason, version=VERSION)

    flagged = [(i, result) for i, result in results.items() if result[0]]
    if flagged:
        snippets = [
            Snippet(ref=req.contexts[i].ref, text=(evidence or req.contexts[i].text[:200])[:500], rule="llm_classifier")
            for i, (_, _, evidence) in flagged[:5]
        ]
        confidence = max(conf for _, (_, conf, _) in flagged)
        return ScanResponse(verdict="injection", confidence=confidence, snippets=snippets, stage="llm", version=VERSION)
    confidence = min(conf for _, conf, _ in results.values())
    return ScanResponse(verdict="clean", confidence=confidence, stage="llm", version=VERSION)


async def _classify(contexts: list[SourceContext], hits: list[heuristics.Hit]) -> dict[int, tuple[bool, float, str]]:
    client = llm.get_llm()
    hints: dict[str, set[str]] = defaultdict(set)
    for hit in hits:
        hints[hit.ref].add(hit.rule)

    blocks, keys, results = {}, {}, {}
    for i, ctx in enumerate(contexts):
        hint = ", ".join(sorted(hints.get(ctx.ref, ())))
        blocks[i] = (ctx.kind, hint, _view(ctx))
        keys[i] = hashlib.sha256(f"{client.scan_model}|{PROMPT}|{blocks[i]}".encode()).hexdigest()
        if keys[i] in _CACHE:
            _CACHE.move_to_end(keys[i])
            results[i] = _CACHE[keys[i]]

    pending = [i for i in blocks if i not in results]
    if pending:
        user = "Classify each content block.\n\n" + "\n\n".join(_render(i, *blocks[i]) for i in pending)
        output = await client.structured(
            model=client.scan_model, system=llm.load_prompt(PROMPT), user=user, output=_ScanOutput
        )
        by_ref = {result.ref: result for result in output.results}
        for i in pending:
            result = by_ref.get(f"c{i}")
            if result is None:
                raise llm.LLMUnavailable("llm_incomplete")
            results[i] = (result.injection, llm.clamp01(result.confidence), result.evidence[:300])
            _CACHE[keys[i]] = results[i]
            if len(_CACHE) > _CACHE_SIZE:
                _CACHE.popitem(last=False)
    return results


def _view(ctx: SourceContext) -> str:
    """What the classifier sees: visible text plus anything hidden in the HTML."""
    text = llm.clip(heuristics.normalize(ctx.text), 8000)
    if ctx.html:
        hidden, comments, attributes = heuristics.extract_html(ctx.html)
        if hidden:
            text += "\n[text hidden from human readers]: " + llm.clip(hidden, 2000)
        if comments:
            text += "\n[HTML comments]: " + llm.clip(" | ".join(comments), 1000)
        if attributes:
            text += "\n[image and attribute text]: " + llm.clip(" | ".join(attributes), 1000)
    return text


def _render(index: int, kind: str, hint: str, text: str) -> str:
    attributes = f'ref="c{index}" kind="{kind}"' + (f' hints="{hint}"' if hint else "")
    return f"<content {attributes}>\n{llm.untrusted(text)}\n</content>"
