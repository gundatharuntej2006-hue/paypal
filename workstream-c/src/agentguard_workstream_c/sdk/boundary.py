"""Offline mapping from an internal agent proposal; never invokes a client."""

from typing import TYPE_CHECKING, Any, Mapping, Sequence

from ..merchants.source_context import SourceContext, validate_contexts
from .exceptions import ValidationError
from .models import PaymentIntent, PaymentItem

if TYPE_CHECKING:
    from ..agent.state import PurchaseProposal


def intent_from_proposal(proposal: "PurchaseProposal", *, mandate: Mapping[str, Any],
                         reasoning_summary: str, source_contexts: Sequence[SourceContext],
                         idempotency_key: str) -> PaymentIntent:
    """Build only. Signed mandate and UUID must be supplied externally.

    Product IDs and source-reference lists remain internal; they are not invented
    top-level fields in the gateway request. Preserve all supplied source evidence.
    """
    if proposal is None:
        raise ValidationError("a completed purchase proposal is required")
    try:
        contexts = validate_contexts(source_contexts)
    except (ValueError, TypeError):
        raise ValidationError("source_contexts must be a bounded collection of SourceContext objects") from None
    refs = {context.ref for context in contexts}
    if any(ref not in refs for ref in proposal.source_references):
        raise ValidationError("proposal cites unavailable source evidence")
    return PaymentIntent(
        mandate=mandate, merchant_id=proposal.merchant_id, merchant_name=proposal.merchant_name,
        amount=proposal.amount, currency=proposal.currency, category=proposal.category,
        items=(PaymentItem(proposal.product_name, proposal.quantity, proposal.unit_price, proposal.category),),
        agent_reasoning=reasoning_summary, source_contexts=contexts, idempotency_key=idempotency_key,
    )
