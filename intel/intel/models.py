"""Request and response models for the intelligence service.

These models are the source of truth for the JSON Schemas in contracts/intel/.
After changing them, run `py -m intel.export_contracts` from the intel/ folder.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

CONTRACT_VERSION = "0.1.0"

Money = Annotated[str, Field(pattern=r"^\d+(\.\d{1,2})?$", examples=["29.99"])]
Currency = Annotated[str, Field(pattern=r"^[A-Z]{3}$", examples=["USD"])]
Confidence = Annotated[float, Field(ge=0.0, le=1.0)]


class _Request(BaseModel):
    # Lenient: callers may send extra fields (for example the full signed mandate).
    model_config = ConfigDict(extra="ignore")


class _Response(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------- shared input pieces ----------


class Mandate(_Request):
    mandate_id: str
    agent_id: str | None = None
    task: str
    currency: Currency
    max_total: Money
    max_per_txn: Money
    step_up_over: Money | None = None
    allowed_merchants: list[str] = []
    allowed_categories: list[str] = []
    valid_from: AwareDatetime | None = None
    expires_at: AwareDatetime | None = None


class Item(_Request):
    name: str
    qty: int = Field(ge=1)
    unit_price: Money
    category: str | None = None


class Proposal(_Request):
    merchant_id: str
    merchant_name: str | None = None
    amount: Money
    currency: Currency
    category: str | None = Field(default=None, description="Category the agent declares for this purchase.")
    items: list[Item] = []


class SourceContext(_Request):
    ref: str
    kind: Literal["html", "text", "review", "email", "invoice", "tool_output"] = "text"
    url: str | None = None
    text: str = Field(max_length=200_000, description="Text the agent read.")
    html: str | None = Field(
        default=None,
        max_length=1_000_000,
        description="Raw HTML when available; needed to detect hidden-text tricks.",
    )


# ---------- shared output pieces ----------


class _Signal(_Response):
    version: str = Field(description="Engine and prompt version that produced this result, e.g. 'stub' or 'scan_v1'.")
    latency_ms: int = Field(default=0, ge=0)
    reason: str | None = Field(
        default=None,
        description="Why the result is unknown (e.g. 'timeout', 'no_llm_key'). Null otherwise.",
    )


# ---------- POST /scan/content ----------


class ScanRequest(_Request):
    contexts: list[SourceContext] = Field(max_length=50)


class Snippet(_Response):
    ref: str
    text: str = Field(max_length=500)
    rule: str = Field(description="Which rule or classifier flagged it.")


class ScanResponse(_Signal):
    verdict: Literal["clean", "injection", "unknown"]
    confidence: Confidence
    snippets: list[Snippet] = []
    stage: Literal["heuristic", "llm", "none"]

    @classmethod
    def unknown(cls, reason: str, version: str) -> ScanResponse:
        return cls(verdict="unknown", confidence=0.0, stage="none", reason=reason, version=version)


# ---------- POST /judge/intent ----------


class JudgeRequest(_Request):
    task: str | None = Field(default=None, description="Defaults to mandate.task.")
    mandate: Mandate
    proposal: Proposal
    reasoning: str = Field(max_length=50_000)
    source_contexts: list[SourceContext] = Field(default=[], max_length=50)


class Influence(_Response):
    reasoning_quote: str = Field(max_length=500)
    source_ref: str | None = None


class JudgeResponse(_Signal):
    verdict: Literal["aligned", "misaligned", "unknown"]
    confidence: Confidence
    rationale: str = Field(max_length=1000)
    influenced_by_external: list[Influence] = []

    @classmethod
    def unknown(cls, reason: str, version: str) -> JudgeResponse:
        return cls(verdict="unknown", confidence=0.0, rationale="", reason=reason, version=version)


# ---------- POST /risk/score ----------


class HistoryItem(_Request):
    payment_id: str | None = None
    merchant_id: str
    amount: Money
    currency: Currency
    category: str | None = None
    ts: AwareDatetime
    decision: Literal["allow", "step_up", "block", "pending"] = "allow"


class RiskRequest(_Request):
    mandate: Mandate
    proposal: Proposal
    history: list[HistoryItem] = Field(
        default=[],
        max_length=1000,
        description="Earlier payments under this mandate, sent by the gateway.",
    )
    now: AwareDatetime | None = Field(default=None, description="Defaults to server time; send it for repeatable tests.")


class Factor(_Response):
    name: str
    contribution: float = Field(ge=0.0, le=1.0, description="How much this factor added to the score.")
    detail: str


class RiskResponse(_Signal):
    status: Literal["ok", "unknown"]
    score: Confidence | None = Field(default=None, description="0 = safest, 1 = riskiest. Null when status is unknown.")
    factors: list[Factor] = []

    @model_validator(mode="after")
    def _score_matches_status(self) -> RiskResponse:
        if (self.status == "ok") != (self.score is not None):
            raise ValueError("score must be set exactly when status is 'ok'")
        return self

    @classmethod
    def unknown(cls, reason: str, version: str) -> RiskResponse:
        return cls(status="unknown", reason=reason, version=version)
