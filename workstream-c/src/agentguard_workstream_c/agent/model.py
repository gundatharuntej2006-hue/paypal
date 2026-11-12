"""Provider-neutral selection interface and an explicitly non-LLM demo model."""

from dataclasses import dataclass
from decimal import Decimal, localcontext
import re
from typing import Protocol

from .state import ModelDecision, PageContent, ProductCandidate, Selection, TaskConstraints


class ShoppingModelError(RuntimeError):
    """Sanitized invocation failure; never retain provider text or exceptions."""

    def __init__(self, code: str):
        if code not in {"timeout", "rate_limit", "authentication", "connection", "provider",
                        "configuration", "dependency"}:
            code = "provider"
        self.code = code
        super().__init__("shopping model error: " + code)


@dataclass(frozen=True)
class ModelRequest:
    original_task: str
    task_constraints: TaskConstraints
    product_candidates: tuple[ProductCandidate, ...]
    pages: tuple[PageContent, ...]
    source_references: tuple[str, ...]


class ShoppingModel(Protocol):
    """Adapters receive immutable input, not graph state, credentials, or HTTP tools."""

    name: str

    def select(self, request: ModelRequest) -> ModelDecision | Selection | None:
        """Explicit outcomes, or legacy demo Selection/None; never private reasoning."""
        ...


def amount_for(price: Decimal, quantity: int) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = max(28, len(price.as_tuple().digits) + len(str(quantity)) + 4)
        return price * quantity


def understand(task: str) -> TaskConstraints:
    """Small documented parser for keywords, quantity, and a single currency/budget."""
    if not isinstance(task, str) or not task.strip() or len(task) > 10_000:
        raise ValueError("task must contain 1 to 10000 characters")
    budget = re.search(r"\b(under|below|at most|up to|budget(?: of)?)\s*\$?([0-9]+(?:\.[0-9]{1,2})?)(?![0-9.])", task, re.I)
    currencies = set(re.findall(r"\b(?:USD|EUR|GBP|INR)\b", task.upper()))
    if "$" in task:
        currencies.add("USD")
    if len(currencies) > 1:
        raise ValueError("multiple task currencies are unsupported")
    currency = next(iter(currencies), None)
    quantity_match = re.search(r"\b(?:buy|purchase|order)\s+([0-9]+)\b", task, re.I)
    quantity = int(quantity_match[1]) if quantity_match else 1
    if not 1 <= quantity <= 1000:
        raise ValueError("quantity must be between 1 and 1000")
    words = task.lower()
    if budget:
        words = words[:budget.start()] + " " + words[budget.end():]
    stop = {"buy", "purchase", "order", "a", "an", "one", "the", "for", "me", "please", "some", "usd", "eur", "gbp", "inr"}
    keywords = tuple(dict.fromkeys(word for word in re.findall(r"[a-z]+", words) if word not in stop))
    return TaskConstraints(keywords, Decimal(budget[2]) if budget else None, currency, quantity,
                           budget is None or budget[1].lower() not in {"under", "below"})


class DeterministicDemoModel:
    """Keyword/budget matching only: no LLM and no injection protection claim."""

    name = "deterministic_demo (not an LLM)"

    def select(self, request: ModelRequest) -> Selection | None:
        constraints = request.task_constraints
        eligible = []
        for product in request.product_candidates:
            tokens = set(re.findall(r"[a-z]+", (product.product_name + " " + product.category).lower()))
            if not all(keyword in tokens for keyword in constraints.keywords):
                continue
            if constraints.currency is not None and product.currency != constraints.currency:
                continue
            total = amount_for(product.unit_price, constraints.quantity)
            if constraints.max_budget is not None:
                if total > constraints.max_budget or (total == constraints.max_budget and not constraints.budget_inclusive):
                    continue
            eligible.append(product)
        if not eligible:
            return None
        # No exchange-rate assumptions: deterministic ordering includes currency.
        selected = min(eligible, key=lambda p: (p.currency, p.unit_price, p.merchant_id, p.product_id))
        ref = next(page.source_ref for page in request.pages if page.url == selected.url)
        return Selection(selected.merchant_id, selected.product_id,
                         "Deterministic demo selected the lowest-priced keyword match in the requested currency; seller identity is unverified.",
                         (ref,))
