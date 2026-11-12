"""Offline model execution tests. No genuine benchmark or services."""

import json
import socket
from urllib.request import OpenerDirector

import pytest

from agentguard_workstream_c.agent.anthropic_model import AnthropicShoppingModel
from agentguard_workstream_c.agent.model import DeterministicDemoModel, ShoppingModelError
from agentguard_workstream_c.agent.state import ModelDecision, Selection, SelectionRationale
from agentguard_workstream_c.benchmark.llm_executor import run_llm_only
from agentguard_workstream_c.benchmark.materializer import ModelConfiguration, materialize_library, inventory_hash
from agentguard_workstream_c.benchmark.scenario_loader import load_scenarios

CONFIG = ModelConfiguration("anthropic", "offline-model")
SCENARIOS = load_scenarios()


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    attempts = []
    def forbidden(*args, **kwargs):
        attempts.append(True)
        raise AssertionError("network, provider, and fallback forbidden")
    for name in ("create_connection", "getaddrinfo", "gethostbyname", "gethostbyname_ex"):
        monkeypatch.setattr(socket, name, forbidden)
    for name in ("connect", "connect_ex"):
        monkeypatch.setattr(socket.socket, name, forbidden)
    monkeypatch.setattr(OpenerDirector, "open", forbidden)
    monkeypatch.setattr(AnthropicShoppingModel, "select", forbidden)
    monkeypatch.setattr(DeterministicDemoModel, "select", forbidden)
    yield
    assert attempts == []


class FakeModel:
    name = "offline_fake"
    def __init__(self, output=ModelDecision("no_match"), error=None):
        self.output, self.error, self.requests = output, error, []

    def select(self, request):
        self.requests.append(request)
        if self.error:
            raise self.error
        return self.output


def run(model, **kwargs):
    return run_llm_only(SCENARIOS, model=model, model_configuration=CONFIG,
                        provenance="test_double", **kwargs)


def test_all_supported_and_identical_snapshots():
    cases = materialize_library(SCENARIOS, CONFIG)
    fake = FakeModel()
    report = run(fake)
    assert report.input_sha256 == inventory_hash(cases)
    assert tuple(r.case for r in report.records) == cases
    assert fake.requests == [c.agent_input() for c in cases if c.capability == "shopping_agent"]
    assert len(fake.requests) == 19
    exported = report.to_dict()
    assert exported["capability_counts"] == {"shopping_agent": 19, "future_gateway": 44, "unsupported": 17}
    assert exported["synthetic"] and exported["provenance"] == "test_double"
    json.dumps(exported, allow_nan=False)
    for record in report.records:
        if record.case.capability != "shopping_agent":
            assert record.execution is None and record.case.reason
        else:
            assert record.execution.model_output is fake.output
        if record.case.constructed_mutation or record.case.constructed_sequence:
            assert record.execution is None


@pytest.mark.parametrize("outcome", ["refusal", "no_match", "invalid_response", "purchase_proposal"])
def test_structured_outputs_and_measured_latency(monkeypatch, outcome):
    from agentguard_workstream_c.benchmark import llm_executor
    rationale = SelectionRationale("offline summary", "offline task fit")
    selection = Selection("fake", "fake", "offline summary", ("fake-source",), rationale)
    output = ModelDecision(outcome, selection if outcome == "purchase_proposal" else None, rationale)
    ticks = iter(i * 0.125 for i in range(38))
    monkeypatch.setattr(llm_executor, "perf_counter", lambda: next(ticks))
    report = run(FakeModel(output))
    for r in report.records:
        if r.execution:
            assert r.execution.outcome == outcome
            assert r.execution.model_output is output
            assert r.execution.latency_ms == 125.0
            assert r.execution.error_code is None
    assert "observed_decision" not in json.dumps(report.to_dict())


@pytest.mark.parametrize("code", ["timeout", "rate_limit", "authentication", "connection", "provider", "configuration", "dependency"])
def test_provider_errors_are_separate_and_no_fallback(code):
    report = run(FakeModel(error=ShoppingModelError(code)))
    executed = [r.execution for r in report.records if r.execution]
    assert len(executed) == 19
    assert all(e.outcome == "provider_error" and e.error_code == code
               and e.model_output is None and e.latency_ms >= 0 for e in executed)


@pytest.mark.parametrize("output,outcome", [(object(), "invalid_response"), (None, "no_match"),
    (Selection("m", "p", "legacy", ("ref",)), "purchase_proposal")])
def test_legacy_and_wrong_interface_responses(output, outcome):
    report = run(FakeModel(output))
    assert all(r.execution.outcome == outcome for r in report.records if r.execution)


def test_unexpected_errors_are_sanitized():
    report = run(FakeModel(error=RuntimeError("SECRET provider body")))
    assert "SECRET" not in json.dumps(report.to_dict())
    assert all(r.execution.error_code == "provider" for r in report.records if r.execution)


def test_capability_is_revalidated_from_definitions():
    changed = [s.to_dict() for s in SCENARIOS]
    target = next(s for s in changed if s["scenario_id"] == "ipi_offscreen")
    target["input"]["operations"].append("unsupported new operation")
    fake = FakeModel()
    report = run_llm_only(changed, model=fake, model_configuration=CONFIG, provenance="test_double")
    record = next(r for r in report.records if r.case.scenario_id == "ipi_offscreen")
    assert record.case.capability == "unsupported" and record.execution is None
    assert len(fake.requests) == 18


def test_provenance_and_opt_in_fail_before_execution():
    fake = FakeModel()
    for opt_in in (False, True):
        with pytest.raises(ValueError):
            run_llm_only(SCENARIOS, model=fake, model_configuration=CONFIG,
                         provenance="genuine_provider", allow_provider_execution=opt_in)
    provider = AnthropicShoppingModel("offline-model")
    with pytest.raises(ValueError, match="opt-in"):
        run_llm_only(SCENARIOS, model=provider, model_configuration=CONFIG, provenance="genuine_provider")
    with pytest.raises(ValueError):
        run(provider)
    injected = AnthropicShoppingModel("offline-model", client=object())
    with pytest.raises(ValueError, match="uninjected"):
        run_llm_only(SCENARIOS, model=injected, model_configuration=CONFIG,
                     provenance="genuine_provider", allow_provider_execution=True)
    assert not fake.requests


def test_invalid_inventory_fails_before_model():
    fake = FakeModel()
    with pytest.raises(ValueError):
        run_llm_only(SCENARIOS[:-1], model=fake, model_configuration=CONFIG, provenance="test_double")
    assert not fake.requests
