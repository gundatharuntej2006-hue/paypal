"""Explicit LangGraph StateGraph ending at an internal proposal, never a payment."""

from langgraph.graph import END, START, StateGraph

from .merchant_client import MerchantClient
from .model import DeterministicDemoModel, ShoppingModel
from .nodes import ShoppingNodes
from .state import AgentConfig, ShoppingState

NODE_NAMES = (
    "understand_task", "discover_merchants", "retrieve_products", "read_product_content",
    "compare_products", "select_product", "record_decision_trace", "prepare_purchase_proposal",
)


def build_graph(config: AgentConfig | None = None, model: ShoppingModel | None = None):
    nodes = ShoppingNodes(MerchantClient(config or AgentConfig()), model if model is not None else DeterministicDemoModel())
    graph = StateGraph(ShoppingState)

    def guarded(name):
        def run(state):
            try:
                return getattr(nodes, name)(state)
            except (ValueError, TypeError, OSError) as exc:
                return {"execution_errors": (f"{name}: {exc}",)}
        return run

    for name in NODE_NAMES:
        graph.add_node(name, guarded(name))
    graph.add_edge(START, NODE_NAMES[0])
    for current, following in zip(NODE_NAMES, NODE_NAMES[1:]):
        graph.add_conditional_edges(current, lambda state: "stop" if state.get("execution_errors") else "next",
                                    {"stop": END, "next": following})
    graph.add_edge(NODE_NAMES[-1], END)
    return graph.compile()


def run_shopping(task: str, config: AgentConfig | None = None, model: ShoppingModel | None = None) -> ShoppingState:
    return build_graph(config, model).invoke({"original_task": task})
