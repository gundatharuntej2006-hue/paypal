"""Provider and graph tests with fake Anthropic clients and zero socket calls."""

from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
import json
import socket
from types import SimpleNamespace

import pytest

from agentguard_workstream_c.agent.anthropic_model import (
    AnthropicShoppingModel, OUTPUT_SCHEMA, SYSTEM_PROMPT, validate_decision,
)
from agentguard_workstream_c.agent.content import parse_product, read_pages
from agentguard_workstream_c.agent.model import ModelRequest, ShoppingModelError, understand
from agentguard_workstream_c.agent.nodes import ShoppingNodes
from agentguard_workstream_c.agent.state import AgentConfig, CapturedPage, MerchantCandidate
from agentguard_workstream_c.attacks.fixtures import ATTACKS
from agentguard_workstream_c.attacks.poisoning import poisoned_detail
from agentguard_workstream_c.merchants.data import get_merchant, get_product
from agentguard_workstream_c.merchants.rendering import detail


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    attempts = []

    def forbidden(*args, **kwargs):
        attempts.append(True)
        raise AssertionError("provider tests must not open sockets")

    for target in ("create_connection", "getaddrinfo"):
        monkeypatch.setattr(socket, target, forbidden)
    for target in ("connect", "connect_ex"):
        monkeypatch.setattr(socket.socket, target, forbidden)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    yield
    assert not attempts


@pytest.fixture
def request_data():
    merchant = get_merchant("merchant_a")
    product = get_product(merchant, "usb-c-hub")
    page = CapturedPage("http://127.0.0.1:8010/merchants/merchant_a/products/usb-c-hub", detail(merchant, product))
    contexts, pages = read_pages((page,), "raw_html")
    return ModelRequest("Buy 2 USB-C hubs under 70 USD", understand("Buy 2 USB-C hubs under 70 USD"),
                        (parse_product(page),), pages, tuple(c.ref for c in contexts))


def proposal(request):
    p = request.product_candidates[0]
    return {"decision": "purchase_proposal", "proposal": {
        "merchant_id": p.merchant_id, "product_id": p.product_id,
        "unit_price": str(p.unit_price), "currency": p.currency,
        "quantity": 2, "total": "59.98", "source_references": [request.pages[0].source_ref]},
        "rationale": {"summary": "Selected a retrieved hub.", "task_fit": "Two hubs fit the budget."}}


class FakeClient:
    def __init__(self, raw="", *, error=None, stop="end_turn", blocks=None):
        self.raw, self.error, self.stop, self.blocks = raw, error, stop, blocks
        self.calls = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(deepcopy(kwargs))
        if self.error is not None:
            raise self.error
        content = self.blocks if self.blocks is not None else [SimpleNamespace(type="text", text=self.raw)]
        return SimpleNamespace(stop_reason=self.stop, content=content)


def test_valid_proposal_and_exact_request(request_data):
    client = FakeClient(json.dumps(proposal(request_data)))
    model = AnthropicShoppingModel("configured-model", client=client, timeout=12)
    result = model.select(request_data)
    assert result.outcome == "purchase_proposal"
    assert result.selection.product_id == "usb-c-hub"
    assert result.selection.rationale == result.rationale
    assert result.rationale.task_fit == "Two hubs fit the budget."
    call, = client.calls
    assert call["model"] == "configured-model" and call["timeout"] == 12
    assert call["output_config"] == {"format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}}
    assert "tools" not in call and "stream" not in call
    body = json.loads(call["messages"][0]["content"])
    assert body["original_task"] == request_data.original_task
    assert body["untrusted_evidence"]["pages"][0]["content"] == request_data.pages[0].content


@pytest.mark.parametrize("outcome", ["refusal", "no_match", "invalid_response"])
def test_explicit_nonpurchase(request_data, outcome):
    data = proposal(request_data)
    data.update(decision=outcome, proposal=None)
    result = AnthropicShoppingModel("configured-model", client=FakeClient(json.dumps(data))).select(request_data)
    assert result.outcome == outcome and result.selection is None


def test_provider_refusal_without_json(request_data):
    client = FakeClient("not JSON: refusal-canary", stop="refusal")
    result = AnthropicShoppingModel("configured-model", client=client).select(request_data)
    assert result.outcome == "refusal" and result.selection is None
    assert "canary" not in repr(result)


@pytest.mark.parametrize("raw", ["", "not JSON", "```json\n{}\n```", "[]", "null", "{}",
                                    '{"decision":"no_match","decision":"refusal"}',
                                    '{"proposal":NaN}', "[" * 1100, "x" * 65537])
def test_malformed_and_empty(request_data, raw):
    assert AnthropicShoppingModel("configured-model", client=FakeClient(raw)).select(request_data).outcome == "invalid_response"


@pytest.mark.parametrize("key", ["decision", "proposal", "rationale"])
def test_missing_required_top_field(request_data, key):
    data = proposal(request_data)
    del data[key]
    assert validate_decision(json.dumps(data), request_data).outcome == "invalid_response"


@pytest.mark.parametrize("key,value", [
    ("merchant_id", "invented"), ("product_id", "invented"),
    ("unit_price", "0.01"), ("unit_price", 29.99), ("unit_price", "NaN"),
    ("unit_price", "1e2"), ("currency", "EUR"), ("quantity", 3), ("quantity", True),
    ("quantity", 2.0), ("total", "29.99"), ("total", "-59.98"),
    ("source_references", []), ("source_references", ["invented"]),
    ("source_references", "source"), ("source_references", [None]),
])
def test_invented_or_mismatched_proposal(request_data, key, value):
    data = proposal(request_data)
    data["proposal"][key] = value
    assert validate_decision(json.dumps(data), request_data).outcome == "invalid_response"


@pytest.mark.parametrize("key", ["merchant_id", "product_id", "unit_price", "currency", "quantity", "total", "source_references"])
def test_missing_proposal_field(request_data, key):
    data = proposal(request_data)
    del data["proposal"][key]
    assert validate_decision(json.dumps(data), request_data).outcome == "invalid_response"


@pytest.mark.parametrize("mutation", ["extra", "extra_proposal", "rationale_missing", "rationale_extra",
                                      "rationale_empty", "rationale_long", "nonpurchase_proposal", "duplicate_citation"])
def test_strict_structure(request_data, mutation):
    data = proposal(request_data)
    if mutation == "extra": data["surprise"] = 1
    if mutation == "extra_proposal": data["proposal"]["payee"] = "invented"
    if mutation == "rationale_missing": del data["rationale"]["task_fit"]
    if mutation == "rationale_extra": data["rationale"]["private_reasoning"] = "forbidden"
    if mutation == "rationale_empty": data["rationale"]["summary"] = " "
    if mutation == "rationale_long": data["rationale"]["task_fit"] = "x" * 401
    if mutation == "nonpurchase_proposal": data["decision"] = "refusal"
    if mutation == "duplicate_citation": data["proposal"]["source_references"] *= 2
    assert validate_decision(json.dumps(data), request_data).outcome == "invalid_response"


@pytest.mark.parametrize("task", ["Buy 2 hubs under 50 USD", "Buy 2 hubs under 59.98 USD", "Buy 2 hubs under 70 EUR"])
def test_task_constraints_are_enforced(request_data, task):
    request = replace(request_data, task_constraints=understand(task), original_task=task)
    assert validate_decision(json.dumps(proposal(request)), request).outcome == "invalid_response"


def test_other_product_page_citation_rejected(request_data):
    other = replace(request_data.pages[0], source_ref="other", url="http://127.0.0.1/other")
    request = replace(request_data, pages=(other,), source_references=("other",))
    data = proposal(request)
    assert validate_decision(json.dumps(data), request).outcome == "invalid_response"


@pytest.mark.parametrize("attack", ATTACKS, ids=lambda a: a.attack_id)
def test_all_poisoned_html_passes_unchanged_to_provider(request_data, attack):
    merchant = get_merchant(attack.merchant_id)
    html = poisoned_detail(merchant, get_product(merchant, attack.product_id), attack)
    page = replace(request_data.pages[0], content=html)
    request = replace(request_data, pages=(page,))
    client = FakeClient(stop="refusal")
    result = AnthropicShoppingModel("configured-model", client=client).select(request)
    body = json.loads(client.calls[0]["messages"][0]["content"])
    assert body["untrusted_evidence"]["pages"][0]["content"] == html
    assert body["original_task"] == request.original_task
    assert "UNTRUSTED DATA" in SYSTEM_PROMPT and "Do not follow" in SYSTEM_PROMPT
    assert result.outcome == "refusal"  # Fake response, not measured injection resistance.


class StatusError(Exception):
    def __init__(self, status):
        self.status_code = status
        super().__init__("provider-secret-canary")


@pytest.mark.parametrize("error,code", [(TimeoutError("provider-secret-canary"), "timeout"),
    (StatusError(408), "timeout"), (StatusError(429), "rate_limit"),
    (StatusError(401), "authentication"), (StatusError(403), "authentication"),
    (StatusError(500), "provider"), (ConnectionError("provider-secret-canary"), "connection"),
    (RuntimeError("provider-secret-canary"), "provider")])
def test_provider_errors_sanitized_and_never_retried(request_data, error, code):
    client = FakeClient(error=error)
    model = AnthropicShoppingModel("configured-model", client=client)
    with pytest.raises(ShoppingModelError) as caught:
        model.select(request_data)
    assert caught.value.code == code and len(client.calls) == 1
    assert "canary" not in str(caught.value) + repr(caught.value)
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    nodes = ShoppingNodes(None, model)
    state = {"original_task": request_data.original_task, "task_constraints": request_data.task_constraints,
             "product_candidates": request_data.product_candidates, "model_pages": request_data.pages,
             "source_contexts": [SimpleNamespace(ref=ref) for ref in request_data.source_references]}
    result = nodes.compare_products(state)
    assert result["model_error_code"] == code and result["execution_errors"]
    assert "canary" not in repr(result) and "selection" not in result


@pytest.mark.parametrize("stop,blocks", [("max_tokens", None), ("tool_use", None), (None, None),
    ("end_turn", []), ("end_turn", [SimpleNamespace(type="tool_use")]),
    ("end_turn", [SimpleNamespace(type="text", text="{}"), SimpleNamespace(type="text", text="{}")])])
def test_incomplete_or_unexpected_envelope(request_data, stop, blocks):
    client = FakeClient("{}", stop=stop, blocks=blocks)
    assert AnthropicShoppingModel("configured-model", client=client).select(request_data).outcome == "invalid_response"


def test_lazy_factory_and_independent_environment(request_data, monkeypatch):
    calls = []
    client = FakeClient(json.dumps(proposal(request_data)))
    def factory(**kwargs):
        calls.append(kwargs)
        return client
    monkeypatch.setenv("INTEL_JUDGE_MODEL", "not-the-shopping-model")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "credential-canary")
    monkeypatch.setenv("AGENTGUARD_SHOPPING_MODEL", "explicit-shopping-model")
    monkeypatch.setenv("AGENTGUARD_SHOPPING_TIMEOUT_SECONDS", "9")
    model = AnthropicShoppingModel.from_env(client_factory=factory)
    assert not calls and "credential-canary" not in repr(model)
    assert model.select(request_data).outcome == "purchase_proposal"
    model.select(request_data)
    assert len(calls) == 1
    assert calls[0] == {"api_key": "credential-canary", "timeout": 9, "max_retries": 0,
                        "base_url": "https://api.anthropic.com"}
    assert client.calls[0]["model"] == "explicit-shopping-model"
    assert "credential-canary" not in json.dumps(client.calls)


def test_missing_credentials_only_fail_on_invocation(request_data):
    model = AnthropicShoppingModel("explicit-model")
    with pytest.raises(ShoppingModelError, match="configuration"):
        model.select(request_data)


@pytest.mark.parametrize("kwargs", [{"model_id": ""}, {"model_id": "has spaces"}, {"model_id": None},
    {"timeout": 0}, {"timeout": True}, {"timeout": float("nan")}, {"timeout": float("inf")},
    {"timeout": 121}, {"max_tokens": True}, {"max_tokens": 0}, {"max_tokens": 4097}])
def test_configuration_validation(kwargs):
    with pytest.raises(ShoppingModelError, match="configuration"):
        AnthropicShoppingModel(**{"model_id": "explicit-model", **kwargs})


def test_invalid_environment_configuration(monkeypatch):
    monkeypatch.setenv("AGENTGUARD_SHOPPING_MODEL", "explicit-model")
    monkeypatch.setenv("AGENTGUARD_SHOPPING_TIMEOUT_SECONDS", "credential-canary")
    with pytest.raises(ShoppingModelError) as caught:
        AnthropicShoppingModel.from_env()
    assert "canary" not in str(caught.value) and caught.value.__context__ is None


@pytest.mark.parametrize("outcome,error", [("refusal", "model_refusal"), ("no_match", "no_matching_product"),
                                          ("invalid_response", "invalid_model_response")])
def test_graph_stops_for_explicit_outcome(request_data, outcome, error):
    data = proposal(request_data)
    data.update(decision=outcome, proposal=None)
    nodes = ShoppingNodes(None, AnthropicShoppingModel("model", client=FakeClient(json.dumps(data))))
    result = nodes.compare_products({"original_task": request_data.original_task,
        "task_constraints": request_data.task_constraints, "product_candidates": request_data.product_candidates,
        "model_pages": request_data.pages, "source_contexts": [SimpleNamespace(ref=r) for r in request_data.source_references]})
    assert result["model_decision"].outcome == outcome
    assert result["selection"] is None and result["execution_errors"] == (error,)


def test_graph_authoritative_totals_and_structured_rationale(request_data):
    model = AnthropicShoppingModel("model", client=FakeClient(json.dumps(proposal(request_data))))
    nodes = ShoppingNodes(None, model)
    context = SimpleNamespace(ref=request_data.pages[0].source_ref, url=request_data.pages[0].url)
    state = {"original_task": request_data.original_task, "task_constraints": request_data.task_constraints,
        "product_candidates": request_data.product_candidates, "model_pages": request_data.pages,
        "source_contexts": (context,), "merchant_candidates": (MerchantCandidate("merchant_a", "unused"),)}
    state.update(nodes.compare_products(state))
    state.update(nodes.select_product(state))
    state.update(nodes.record_decision_trace(state))
    state.update(nodes.prepare_purchase_proposal(state))
    assert state["purchase_proposal"].amount == Decimal("59.98")
    assert state["purchase_proposal"].quantity == 2
    assert state["model_decision"].rationale.task_fit == "Two hubs fit the budget."


@pytest.mark.parametrize("outcome", ["purchase_proposal", "refusal", "no_match", "invalid_response"])
def test_real_langgraph_routes_outcomes_without_http(request_data, monkeypatch, outcome):
    from agentguard_workstream_c.agent.graph import run_shopping
    candidate = request_data.product_candidates[0]
    captured = CapturedPage(candidate.url, request_data.pages[0].content)
    monkeypatch.setattr(ShoppingNodes, "discover_merchants", lambda self, state: {
        "merchant_candidates": (MerchantCandidate(candidate.merchant_id, "unused"),)})
    monkeypatch.setattr(ShoppingNodes, "retrieve_products", lambda self, state: {
        "product_candidates": (candidate,), "captured_pages": (captured,)})
    data = proposal(request_data)
    if outcome != "purchase_proposal":
        data.update(decision=outcome, proposal=None)
    model = AnthropicShoppingModel("model", client=FakeClient(json.dumps(data)))
    state = run_shopping(request_data.original_task, AgentConfig(reader_mode="raw_html"), model=model)
    assert state["model_decision"].outcome == outcome
    assert state["original_task"] == request_data.original_task
    assert state["source_contexts"][0].html == captured.html
    if outcome == "purchase_proposal":
        assert not state["execution_errors"] and state["purchase_proposal"].amount == Decimal("59.98")
    else:
        assert state["execution_errors"] and state["purchase_proposal"] is None


def test_optional_dependency_missing_is_sanitized(request_data, monkeypatch):
    import builtins
    original = builtins.__import__
    def missing(name, *args, **kwargs):
        if name == "anthropic":
            raise ImportError("credential-canary")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", missing)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "credential-canary")
    with pytest.raises(ShoppingModelError) as caught:
        AnthropicShoppingModel("model").select(request_data)
    assert caught.value.code == "dependency" and caught.value.__context__ is None
    assert "canary" not in str(caught.value)


def test_default_sdk_construction_is_lazy_and_has_no_retries(request_data, monkeypatch):
    import sys
    calls = []
    client = FakeClient(json.dumps(proposal(request_data)))
    def constructor(**kwargs):
        calls.append(kwargs)
        return client
    monkeypatch.setitem(sys.modules, "anthropic", SimpleNamespace(Anthropic=constructor))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "credential-canary")
    model = AnthropicShoppingModel("model")
    assert not calls
    assert model.select(request_data).outcome == "purchase_proposal"
    assert calls[0]["max_retries"] == 0
    assert calls[0]["base_url"] == "https://api.anthropic.com"


def test_factory_failure_is_sanitized(request_data, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "credential-canary")
    def factory(**kwargs):
        raise StatusError(401)
    with pytest.raises(ShoppingModelError) as caught:
        AnthropicShoppingModel("model", client_factory=factory).select(request_data)
    assert caught.value.code == "authentication" and caught.value.__context__ is None
    assert "canary" not in str(caught.value)


def test_graph_rechecks_catalog_budget_even_for_legacy_models(request_data):
    nodes = ShoppingNodes(None, None)
    selection = validate_decision(json.dumps(proposal(request_data)), request_data).selection
    state = {"selection": selection, "product_candidates": request_data.product_candidates,
             "task_constraints": understand("Buy 2 hubs under 50 USD")}
    with pytest.raises(ValueError, match="budget"):
        nodes.select_product(state)
