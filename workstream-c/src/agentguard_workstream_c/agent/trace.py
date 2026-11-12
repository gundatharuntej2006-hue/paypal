"""Record concise decisions and validated evidence citations, never private reasoning."""

from .state import ShoppingState


def decision_trace(state: ShoppingState) -> dict:
    selection = state["selection"]
    return {"reasoning_summary": selection.decision_summary,
            "source_references": tuple(selection.source_references)}
