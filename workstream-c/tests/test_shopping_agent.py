"""Real graph execution with immutable deterministic model doubles and local HTTP."""

from dataclasses import FrozenInstanceError
from decimal import Decimal
import json

import pytest

from agentguard_workstream_c.agent.cli import main
from agentguard_workstream_c.agent.content import read_pages
from agentguard_workstream_c.agent.graph import NODE_NAMES, build_graph, run_shopping
from agentguard_workstream_c.agent.model import DeterministicDemoModel, amount_for, understand
from agentguard_workstream_c.agent.state import AgentConfig, CapturedPage, Selection
from agentguard_workstream_c.attacks.fixtures import ATTACKS
from agentguard_workstream_c.merchants.data import MERCHANTS
from agentguard_workstream_c.merchants.rendering import detail
from agentguard_workstream_c.merchants.visible_text import visible_text


class RecordingModel:
    name = "test_double (not an LLM)"

    def __init__(self, target=None, invalid=None):
        self.requests = []
        self.target = target
        self.invalid = invalid

    def select(self, request):
        self.requests.append(request)
        if self.invalid == "error":
            raise RuntimeError("secret-canary-should-not-leak")
        if self.invalid == "type":
            return {"merchant_id": "fake"}
        product = next((p for p in request.product_candidates
                        if self.target is None or (p.merchant_id, p.product_id) == self.target))
        ref = next(p.source_ref for p in request.pages if p.url == product.url)
        if self.invalid == "source":
            ref = "fabricated-source"
        if self.invalid == "other_source":
            ref = next(p.source_ref for p in request.pages if p.url != product.url)
        return Selection(product.merchant_id, "fabricated" if self.invalid == "product" else product.product_id,
                         "Test double selected a retrieved offer.", (ref,))


def test_compilation_and_complete_workflow(agent_server):
    graph = build_graph(AgentConfig(base_url=agent_server))
    assert set(NODE_NAMES).issubset(graph.get_graph().nodes)
    task = "Buy a USB-C hub under 40 USD"
    updates = list(graph.stream({"original_task": task}, stream_mode="updates"))
    assert [next(iter(update)) for update in updates] == list(NODE_NAMES)
    state = graph.invoke({"original_task": task})
    assert state["original_task"] == task
    assert state["execution_errors"] == ()
    assert len(state["merchant_candidates"]) == 4
    assert len(state["product_candidates"]) == 8
    assert len(state["captured_pages"]) == len(state["source_contexts"]) == 13
    proposal = state["purchase_proposal"]
    assert proposal.merchant_id == "merchant_lookalike"  # Cheapest; no allowlist was provided.
    assert proposal.product_id == "usb-c-hub"
    assert proposal.amount == Decimal("27.99") and proposal.currency == "USD"
    assert state["selected_merchant"].merchant_id == proposal.merchant_id
    assert proposal.source_references == state["source_references"]
    assert set(proposal.source_references).issubset(c.ref for c in state["source_contexts"])
    # Invocations have independent fresh state and deterministic evidence references.
    again = graph.invoke({"original_task": task})
    assert again["purchase_proposal"] == proposal
    assert again["source_contexts"] == state["source_contexts"]


@pytest.mark.parametrize("mode", ["visible_text", "raw_html"])
@pytest.mark.parametrize("attack", ATTACKS, ids=lambda a: a.attack_id)
def test_all_attacks_and_exact_model_content(agent_server, mode, attack):
    model = RecordingModel((attack.merchant_id, attack.product_id))
    task = "Buy one product under 100 USD"
    state = run_shopping(task, AgentConfig(base_url=agent_server, reader_mode=mode, attack_id=attack.attack_id), model)
    assert not state["execution_errors"]
    assert state["original_task"] == task
    request = model.requests[0]
    assert request.original_task == task
    assert len(request.pages) == len(state["source_contexts"]) == 13
    for page, context, captured in zip(request.pages, state["source_contexts"], state["captured_pages"]):
        assert page.content == context.text
        assert context.html == captured.html and context.url == page.url == captured.url
        assert page.source_ref == context.ref
        assert page.content == (captured.html if mode == "raw_html" else visible_text(captured.html))
    attacked = next(c for c in state["source_contexts"] if c.url.endswith(attack.poisoned_path))
    assert attacked.url == agent_server + attack.poisoned_path
    if attack.delivery in {"css_hidden", "comment", "zero_size", "offscreen"}:
        assert (attack.malicious_content in attacked.text) == (mode == "raw_html")
    else:
        assert attack.malicious_content in attacked.text
    assert state["selected_product"].url == attacked.url
    for candidate in request.product_candidates:
        merchant = next(m for m in MERCHANTS if m.merchant_id == candidate.merchant_id)
        product = next(p for p in merchant.products if p.product_id == candidate.product_id)
        assert candidate.unit_price == Decimal(product.price)
        assert candidate.merchant_name == merchant.name and candidate.product_name == product.name
        assert candidate.description == product.description
        assert candidate.category == product.category and candidate.reviews
    with pytest.raises(FrozenInstanceError):
        request.original_task = "changed"


@pytest.mark.parametrize("invalid", ["product", "source", "other_source", "type", "error"])
def test_invalid_model_output_terminates(agent_server, invalid):
    state = run_shopping("Buy a hub", AgentConfig(base_url=agent_server), RecordingModel(invalid=invalid))
    assert state["execution_errors"]
    assert state["purchase_proposal"] is None
    assert state["selected_product"] is None
    assert "secret-canary" not in repr(state)


@pytest.mark.parametrize("task", ["Buy a spaceship under 40 USD", "Buy a hub under 1 USD"])
def test_no_matching_products(agent_server, task):
    state = run_shopping(task, AgentConfig(base_url=agent_server))
    assert state["execution_errors"] == ("no_matching_product",)
    assert state["purchase_proposal"] is None


def test_invalid_task_and_attack_fail_before_fetch(agent_server):
    state = run_shopping("", AgentConfig(base_url=agent_server))
    assert state["execution_errors"][0].startswith("understand_task:")
    assert not state.get("purchase_proposal")
    with pytest.raises(ValueError, match="unknown attack_id"):
        build_graph(AgentConfig(base_url=agent_server, attack_id="missing"))


@pytest.mark.parametrize("config_field,limit", [("max_merchants", 3), ("max_products", 1)])
def test_traversal_limits_terminate(agent_server, config_field, limit):
    model = RecordingModel()
    state = run_shopping("Buy a hub", AgentConfig(base_url=agent_server, **{config_field: limit}), model)
    assert state["execution_errors"] and not state["purchase_proposal"]
    assert not model.requests


@pytest.mark.parametrize("mode", ["visible_text", "raw_html"])
def test_context_count_limits(mode):
    page = CapturedPage("http://127.0.0.1:8010/", "<html><body>text</body></html>")
    assert len(read_pages((page,) * 50, mode)[0]) == 50
    with pytest.raises(ValueError, match="50"):
        read_pages((page,) * 51, mode)


@pytest.mark.parametrize("mode,size,error", [("raw_html", 200_001, "text"), ("visible_text", 200_001, "text"),
                                           ("visible_text", 1_000_001, "html")])
def test_context_size_limits(mode, size, error):
    raw = ("<html><body><!--" + "x" * size + "--></body></html>" if error == "html"
           else "<html><body>" + "x" * size + "</body></html>")
    with pytest.raises(ValueError, match=error + " exceeds"):
        read_pages((CapturedPage("http://127.0.0.1:8010/", raw),), mode)


def test_decimal_quantity_and_concise_trace(agent_server):
    model = RecordingModel(("merchant_b", "travel-mug"))
    state = run_shopping("Buy 3 travel mugs under 60 USD", AgentConfig(base_url=agent_server), model)
    assert state["purchase_proposal"].quantity == 3
    assert state["purchase_proposal"].amount == Decimal("54.00")
    assert state["reasoning_summary"] == "Test double selected a retrieved offer."
    assert amount_for(Decimal("0.10"), 3) == Decimal("0.30")
    assert understand("Buy a hub under 40 USD").max_budget == Decimal("40")


@pytest.mark.parametrize("mode,attack", [("visible_text", None), ("raw_html", "poison_01")])
def test_cli_real_graph(agent_server, capsys, mode, attack):
    args = ["--task", "Buy a USB-C hub under 40 USD", "--base-url", agent_server, "--reader-mode", mode]
    if attack:
        args += ["--attack-id", attack]
    assert main(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["model"] == DeterministicDemoModel.name
    assert result["reader_mode"] == mode and result["payment_submitted"] is False
    assert result["selected_product"]["product_id"] == "usb-c-hub"
    assert result["proposed_amount"] == "27.99"
    assert len(result["sources_consumed"]) == 13
    assert "<html" not in json.dumps(result)


def test_no_credentials_or_nonmerchant_calls(agent_server, monkeypatch):
    import http.client
    connections = []
    original = http.client.HTTPConnection.connect

    def connect(connection):
        assert connection.host == "127.0.0.1"
        assert str(connection.port) == agent_server.rsplit(":", 1)[1]
        connections.append((connection.host, connection.port))
        return original(connection)

    monkeypatch.setattr(http.client.HTTPConnection, "connect", connect)
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "secret-canary")
    monkeypatch.setenv("INTEL_API_TOKEN", "intel-canary")
    monkeypatch.setenv("HTTP_PROXY", "http://untrusted.invalid:8080")
    model = RecordingModel()
    state = run_shopping("Buy a hub", AgentConfig(base_url=agent_server), model)
    assert not state["execution_errors"] and len(connections) == 13
    assert "secret-canary" not in repr(state) + repr(model.requests)
    assert "intel-canary" not in repr(state) + repr(model.requests)
