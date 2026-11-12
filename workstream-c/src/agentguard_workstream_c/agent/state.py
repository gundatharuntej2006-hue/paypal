"""Internal workflow records, not the gateway payment-intent contract."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, TypedDict

from ..merchants.source_context import SourceContext

ReaderMode = Literal["visible_text", "raw_html"]


@dataclass(frozen=True)
class AgentConfig:
    base_url: str = "http://127.0.0.1:8010"
    reader_mode: ReaderMode = "visible_text"
    attack_id: str | None = None
    timeout: float = 3.0
    max_response_bytes: int = 4_000_000
    max_merchants: int = 10
    max_products: int = 20

    def __post_init__(self):
        if self.reader_mode not in ("visible_text", "raw_html"):
            raise ValueError("reader_mode must be visible_text or raw_html")
        if not 0 < self.timeout <= 30:
            raise ValueError("timeout must be greater than zero and at most 30 seconds")
        for value in (self.max_response_bytes, self.max_merchants, self.max_products):
            if type(value) is not int or value <= 0:
                raise ValueError("response and traversal limits must be positive integers")


@dataclass(frozen=True)
class TaskConstraints:
    keywords: tuple[str, ...]
    max_budget: Decimal | None
    currency: str | None
    quantity: int = 1
    budget_inclusive: bool = True


@dataclass(frozen=True)
class CapturedPage:
    url: str
    html: str


@dataclass(frozen=True)
class MerchantCandidate:
    merchant_id: str
    catalog_url: str


@dataclass(frozen=True)
class ProductCandidate:
    merchant_id: str
    merchant_name: str
    product_id: str
    product_name: str
    unit_price: Decimal
    currency: str
    category: str
    description: str
    reviews: tuple[str, ...]
    url: str


@dataclass(frozen=True)
class PageContent:
    source_ref: str
    url: str
    content: str


@dataclass(frozen=True)
class SelectionRationale:
    """Concise auditable justification, not private chain-of-thought."""

    summary: str
    task_fit: str

    def __post_init__(self):
        for value in (self.summary, self.task_fit):
            if not isinstance(value, str) or not value.strip() or len(value) > 400:
                raise ValueError("rationale fields require 1 to 400 characters")


@dataclass(frozen=True)
class Selection:
    merchant_id: str
    product_id: str
    decision_summary: str
    source_references: tuple[str, ...]
    rationale: SelectionRationale | None = None

    def __post_init__(self):
        for field in (self.merchant_id, self.product_id, self.decision_summary):
            if not isinstance(field, str) or not field.strip():
                raise ValueError("selection identity and summary must be nonempty strings")
        if len(self.decision_summary) > 1000:
            raise ValueError("decision_summary exceeds 1000 characters")
        if (not isinstance(self.source_references, tuple) or not self.source_references
                or any(not isinstance(ref, str) or not ref for ref in self.source_references)
                or len(set(self.source_references)) != len(self.source_references)):
            raise ValueError("selection requires unique nonempty source references")
        if self.rationale is not None and not isinstance(self.rationale, SelectionRationale):
            raise ValueError("selection rationale must be structured")


@dataclass(frozen=True)
class ModelDecision:
    """Explicit model outcome; a refusal is not a payment firewall block."""

    outcome: Literal["purchase_proposal", "refusal", "no_match", "invalid_response"]
    selection: Selection | None = None
    rationale: SelectionRationale | None = None

    def __post_init__(self):
        if self.outcome not in ("purchase_proposal", "refusal", "no_match", "invalid_response"):
            raise ValueError("unknown model outcome")
        if self.outcome == "purchase_proposal":
            if not isinstance(self.selection, Selection) or self.selection.rationale is None:
                raise ValueError("purchase outcome requires a selection with rationale")
            if self.rationale != self.selection.rationale:
                raise ValueError("purchase rationale must match selection")
        elif self.selection is not None:
            raise ValueError("non-purchase outcome cannot include a selection")
        if self.rationale is not None and not isinstance(self.rationale, SelectionRationale):
            raise ValueError("model rationale must be structured")


@dataclass(frozen=True)
class PurchaseProposal:
    merchant_id: str
    merchant_name: str
    product_id: str
    product_name: str
    quantity: int
    unit_price: Decimal
    amount: Decimal
    currency: str
    category: str
    source_references: tuple[str, ...]


class ShoppingState(TypedDict, total=False):
    original_task: str
    task_constraints: TaskConstraints
    merchant_candidates: tuple[MerchantCandidate, ...]
    product_candidates: tuple[ProductCandidate, ...]
    captured_pages: tuple[CapturedPage, ...]
    source_contexts: tuple[SourceContext, ...]
    model_pages: tuple[PageContent, ...]
    reader_mode: ReaderMode
    reasoning_summary: str
    selected_merchant: MerchantCandidate | None
    selected_product: ProductCandidate | None
    selection: Selection | None
    model_decision: ModelDecision | None
    model_error_code: str | None
    purchase_proposal: PurchaseProposal | None
    source_references: tuple[str, ...]
    execution_errors: tuple[str, ...]
