"""Fake collected response metadata only; no service or model connections."""

import json
import socket

import pytest

from agentguard_workstream_c.agent.state import ModelDecision, Selection, SelectionRationale
from agentguard_workstream_c.benchmark.dashboard import dashboard_summary
from agentguard_workstream_c.benchmark.evidence import IntelligenceEvidence, SignalMetadata
from agentguard_workstream_c.benchmark.executor import DeterministicTestDouble, run_benchmark, validate_observation
from agentguard_workstream_c.benchmark.models import Observation
from agentguard_workstream_c.benchmark.reporter import json_report, markdown_report
from agentguard_workstream_c.benchmark.scenario_loader import load_scenarios


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    attempts = []
    def forbidden(*args, **kwargs):
        attempts.append(True)
        raise AssertionError("evidence tests must stay offline")
    for name in ("create_connection", "getaddrinfo"):
        monkeypatch.setattr(socket, name, forbidden)
    for name in ("connect", "connect_ex"):
        monkeypatch.setattr(socket.socket, name, forbidden)
    yield
    assert not attempts


def test_metadata_uses_response_fields_and_never_configuration():
    evidence = IntelligenceEvidence.from_responses(
        scan_response={"version": "fake_scan_version", "latency_ms": 0, "model_id": "not-a-version"},
        judge_response={"version": "fake_judge_version", "latency_ms": 12},
        risk_response={"version": "fake_risk_policy", "latency_ms": 2})
    assert evidence.scan == SignalMetadata("fake_scan_version", 0)
    assert evidence.judge == SignalMetadata("fake_judge_version", 12)
    assert evidence.risk == SignalMetadata("fake_risk_policy", 2)
    assert evidence.active_risk_policy_version == "fake_risk_policy"
    missing = IntelligenceEvidence.from_responses(risk_response={"model_id": "risk_rules_v2"})
    assert missing.risk == SignalMetadata() and missing.active_risk_policy_version is None
    assert IntelligenceEvidence.from_responses().to_dict() == {
        "scan": None, "judge": None, "risk": None, "active_risk_policy_version": None}


@pytest.mark.parametrize("response", [{"latency_ms": -1}, {"latency_ms": True}, {"latency_ms": 1.5},
                                      {"latency_ms": float("nan")}, {"version": 4}, {"version": ""}, []])
def test_invalid_metadata_rejected(response):
    with pytest.raises(ValueError): IntelligenceEvidence.from_responses(scan_response=response)


def test_rich_records_preserve_per_arm_metadata_without_inventing_results():
    class FakeCollectedEvidence(DeterministicTestDouble):
        def execute(self, scenario, *, system):
            return Observation("block", "risk", 123.0, "observed", intelligence=IntelligenceEvidence.from_responses(
                risk_response={"version": "fake_risk_policy", "latency_ms": 7}), model_output=ModelDecision("refusal"))
    # Synthetic plumbing test only: these deliberately fake values are not measurements.
    report = run_benchmark(load_scenarios(), {"full_stack": FakeCollectedEvidence()})
    a = report["runs"]["full_stack"]["records"][0]
    b = report["runs"]["llm_only"]["records"][0]
    assert a["latency_ms"] == 123.0 and a["intelligence"]["risk"]["latency_ms"] == 7
    assert a["intelligence"]["active_risk_policy_version"] == "fake_risk_policy"
    assert a["intelligence"]["scan"] is None and a["intelligence"]["judge"] is None
    assert a["model_output"]["outcome"] == "refusal" and b["model_output"] is None
    assert b["intelligence"] is None and b["observed_decision"] is None
    assert json.loads(json_report(report)) == report
    md = markdown_report(report)
    assert "fake_risk_policy / 7" in md and "separate from end-to-end decision latency" in md
    # Dashboard deliberately rejects mixed executed/unexecuted arm provenance.
    # Check the unchanged summary contract with a consistently unexecuted report.
    summary = dashboard_summary(run_benchmark(load_scenarios()))
    assert set(summary) == {"report_version", "provenance", "by_class", "ablation"}


def test_methodology_discloses_calibration_without_filling_active_version():
    report = run_benchmark(load_scenarios())
    calibration = report["methodology"]["risk_calibration"]
    assert calibration["reference_policy_version"] == "risk_rules_v2"
    assert calibration["calibration_scenario_ids"] == [f"benign_{n:02d}" for n in range(1, 21)]
    assert calibration["false_positive_rate_scope"] == "in_sample_on_these_calibration_cases"
    assert "in-sample" in markdown_report(report) and "held-out" in markdown_report(report)
    assert all(r["intelligence"] is None for run in report["runs"].values() for r in run["records"])
    assert all(run["metrics"]["false_positive_rate"] is None for run in report["runs"].values())


def test_typed_observation_does_not_accept_unvalidated_metadata():
    with pytest.raises(ValueError): validate_observation(Observation(intelligence={"version": "config-default"}))
    with pytest.raises(ValueError): IntelligenceEvidence(scan={"version": "untyped"})


def test_model_selection_citations_round_trip_as_json_arrays():
    rationale = SelectionRationale("Fake selection summary.", "Offline test only.")
    output = ModelDecision("purchase_proposal", Selection("merchant_a", "usb-c-hub", rationale.summary,
                                                         ("ctx_test",), rationale), rationale)
    class FakeObservedModel(DeterministicTestDouble):
        def execute(self, scenario, *, system):
            return Observation("allow", outcome="observed", model_output=output)
    report = run_benchmark(load_scenarios(), {"llm_only": FakeObservedModel(), "full_stack": FakeObservedModel()})
    row = report["runs"]["llm_only"]["records"][0]
    assert row["model_output"]["selection"]["source_references"] == ["ctx_test"]
    assert json.loads(json_report(report)) == report
    assert row["intelligence"] is None
