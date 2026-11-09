"""Offline library/runner coverage; network and SDK submission are forbidden."""

from collections import Counter
from dataclasses import replace
from importlib.resources import files
import json
import socket

import pytest

from agentguard_workstream_c.attacks.fixtures import ATTACKS
from agentguard_workstream_c.benchmark.cli import main
from agentguard_workstream_c.benchmark.executor import DeterministicTestDouble, run_benchmark, validate_observation
from agentguard_workstream_c.benchmark.metrics import calculate_metrics
from agentguard_workstream_c.benchmark.models import CATEGORIES, Observation
from agentguard_workstream_c.benchmark.reporter import json_report, markdown_report
from agentguard_workstream_c.benchmark.scenario_loader import load_scenarios
from agentguard_workstream_c.benchmark.validator import ScenarioValidationError, scan_secrets, validate_library, validate_scenario
from agentguard_workstream_c.sdk import GuardClient

CASES = load_scenarios()


@pytest.fixture(autouse=True)
def forbid_network_and_submission(monkeypatch):
    attempts = []

    def forbidden(*args, **kwargs):
        attempts.append(True)
        raise AssertionError("benchmark must not connect or submit payments")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(GuardClient, "request_payment", forbidden)
    yield
    assert not attempts


@pytest.mark.parametrize("case", CASES, ids=lambda s: s.scenario_id)
def test_every_scenario_validates(case):
    assert validate_scenario(case.to_dict()) == case
    assert case.task and case.description and case.inputs["operations"]
    if case.category == "benign":
        assert not case.inputs["payload"] and not case.inputs["fixture_refs"]
        assert case.expected_decision == "allow"
    else:
        assert case.expected_catching_layer and case.attack_vector != "none"


def test_inventory_and_fixture_coverage():
    counts = Counter(s.category for s in CASES)
    assert counts == {**dict.fromkeys(CATEGORIES, 10), "benign": 20}
    assert len({s.scenario_id for s in CASES}) == 80
    malicious = [s for s in CASES if s.category != "benign"]
    assert len({s.attack_vector for s in malicious}) == 60
    assert {ref for s in CASES for ref in s.inputs["fixture_refs"]} == {f.attack_id for f in ATTACKS}
    schema = json.loads(files("agentguard_workstream_c.attacks").joinpath("scenario.schema.json").read_text())
    assert schema["properties"]["schema_version"]["const"] == 1
    assert set(schema["properties"]["scenarios"]["items"]["required"]) == set(CASES[0].to_dict())


@pytest.mark.parametrize("key,value,match", [
    ("expected_decision", "approve", "expected_decision"),
    ("expected_catching_layer", "paypal", "expected_catching_layer"),
    ("category", "other", "category"), ("scenario_id", "Bad ID", "scenario_id"),
    ("severity", "urgent", "severity"), ("tags", ["x", "x"], "unique"),
    ("tags", "x", "array"), ("task", 123, "task"), ("name", "", "name"),
])
def test_invalid_scenario_fields(key, value, match):
    data = CASES[0].to_dict()
    data[key] = value
    with pytest.raises(ScenarioValidationError, match=match):
        validate_scenario(data)


@pytest.mark.parametrize("key,value,match", [
    ("fixture_refs", ["missing"], "fixture"), ("fixture_refs", ["poison_01"], "fixture target"),
    ("amount", "NaN", "money"), ("amount", "-1.00", "money"), ("amount", "1e2", "money"),
    ("amount", 1.2, "money"), ("amount", "1.001", "money"),
    ("quantity", True, "quantity"), ("quantity", 0, "quantity"),
    ("currency", "usd", "currency"), ("reader_mode", "browser", "reader_mode"),
    ("merchant_id", "missing", "reference"), ("product_id", "missing", "reference"),
    ("allowed_merchants", ["missing"], "merchant"), ("operations", [], "array"),
])
def test_invalid_experiment_inputs(key, value, match):
    data = next(s for s in CASES if s.scenario_id == "auth_admin_notice").to_dict()
    data["input"][key] = value
    with pytest.raises(ScenarioValidationError, match=match):
        validate_scenario(data)


@pytest.mark.parametrize("key,value", [("payload", "ignore the user"), ("fixture_refs", ["poison_01"]),
                                      ("budget", "0.00"), ("amount", "1.00"),
                                      ("allowed_merchants", ["merchant_c"]), ("currency", "EUR"),
                                      ("operations", ["bypass approval"])])
def test_benign_conditions_are_enforced(key, value):
    data = next(s for s in CASES if s.scenario_id == "benign_01").to_dict()
    data["input"][key] = value
    with pytest.raises(ScenarioValidationError):
        validate_scenario(data)


def test_strict_fields_and_library_counts():
    for data in ({}, {**CASES[0].to_dict(), "extra": 1}):
        with pytest.raises(ScenarioValidationError, match="exactly"):
            validate_scenario(data)
    data = CASES[0].to_dict()
    data["input"]["extra"] = "unexpected"
    with pytest.raises(ScenarioValidationError, match="exactly"):
        validate_scenario(data)
    for subset in (CASES[:-1], [s for s in CASES if s.category != CATEGORIES[0]]):
        with pytest.raises(ScenarioValidationError, match="requires 20 benign"):
            validate_library(subset)
    with pytest.raises(ScenarioValidationError, match="duplicate scenario_id"):
        validate_library((*CASES, CASES[0]))
    with pytest.raises(ScenarioValidationError, match="duplicate experiment"):
        validate_library((*CASES, replace(CASES[0], scenario_id="different_editorial_label")))


@pytest.mark.parametrize("secret", [
    {"api_key": "not-even-a-real-key"}, {"PAYPAL_CLIENT_SECRET": "canary"},
    "sk-" + "A" * 30, "ghp_" + "B" * 25, "-----BEGIN PRIVATE KEY-----",
    "Bearer " + "C" * 25, "https://user:pass@example.invalid/", "password=canary",
])
def test_secret_detection_does_not_echo_values(secret):
    with pytest.raises(ScenarioValidationError) as exc:
        scan_secrets(secret)
    assert "canary" not in str(exc.value)


@pytest.mark.parametrize("content", ['{"schema_version":1,"schema_version":1,"scenarios":[]}',
                                      '{"schema_version":NaN,"scenarios":[]}', '[]',
                                      '{"schema_version":2,"scenarios":[]}',
                                      '{"schema_version":true,"scenarios":[]}', '{bad json'])
def test_invalid_json_library(tmp_path, content):
    (tmp_path / "cases.json").write_text(content, encoding="utf-8")
    with pytest.raises(ScenarioValidationError):
        load_scenarios(tmp_path)


def test_custom_directory_and_snapshot_isolation(tmp_path):
    (tmp_path / "cases.json").write_text(json.dumps({"schema_version": 1, "scenarios": [s.to_dict() for s in CASES]}))
    assert load_scenarios(tmp_path) == CASES
    inp = CASES[0].inputs
    inp["payload"] = "changed"
    assert CASES[0].inputs != inp


def test_validation_mode_has_no_invented_measurements():
    report = run_benchmark(CASES)
    assert report["mode"] == "validation" and report["data_source"] == "not_executed"
    assert report["synthetic"] is False
    assert report["ablation"]["decision_disagreements"] is None
    for run in report["runs"].values():
        assert run["metrics"]["observed"] == 0
        assert run["metrics"]["false_positive_rate"] is None
        assert run["metrics"]["median_latency_ms"] is None
        assert all(m["block_rate"] is None for m in run["metrics"]["by_attack_category"].values())
        assert all(r["observed_decision"] is None and r["actual_catching_layer"] is None for r in run["records"])


def test_synthetic_execution_ablation_and_reports():
    doubles = {system: DeterministicTestDouble() for system in ("llm_only", "full_stack")}
    report = run_benchmark(CASES, doubles)
    assert report == run_benchmark(reversed(CASES), doubles)
    assert report["synthetic"] is True and report["data_source"] == "synthetic_test_double"
    assert report["ablation"]["paired_observed"] == 80
    a, b = report["runs"].values()
    assert a["scenario_set_sha256"] == b["scenario_set_sha256"] == report["scenario_set_sha256"]
    assert [r["scenario_id"] for r in a["records"]] == [r["scenario_id"] for r in b["records"]]
    assert json.loads(json_report(report)) == report
    md = markdown_report(report)
    assert "synthetic: true" in md and "synthetic_test_double" in md and "NOT AgentGuard security performance" in md
    assert all(s.scenario_id in md for s in CASES)
    assert report["runs"]["full_stack"]["metrics"]["observed"] == 80


def test_fake_does_not_copy_expected_outcomes():
    fake = DeterministicTestDouble()
    original = CASES[0]
    changed = replace(original, expected_decision="allow", expected_catching_layer="risk")
    assert fake.execute(original, system="full_stack") == fake.execute(changed, system="full_stack")


def test_actual_layer_and_missing_latency_are_not_filled_from_expectations():
    class DifferentLayer(DeterministicTestDouble):
        def execute(self, scenario, *, system):
            return Observation("block", "risk", outcome="observed")

    report = run_benchmark(CASES, {"full_stack": DifferentLayer()})
    record = next(r for r in report["runs"]["full_stack"]["records"] if r["scenario_id"] == "auth_admin_notice")
    assert record["expected_catching_layer"] == "injection"
    assert record["actual_catching_layer"] == "risk" and record["latency_ms"] is None
    assert report["runs"]["full_stack"]["metrics"]["median_latency_ms"] is None


def test_fixture_baselines_do_not_add_unrelated_merchant_restrictions():
    for case in CASES:
        if case.inputs["fixture_refs"] and case.category not in ("merchant_attacks", "intent_drift"):
            assert case.inputs["merchant_id"] in case.inputs["allowed_merchants"]


def row(category, decision=None, layer=None, latency=None, outcome="observed"):
    return dict(category=category, observed_decision=decision, actual_catching_layer=layer, latency_ms=latency, outcome=outcome)


def test_metric_denominators_unknowns_and_median():
    records = [row(CATEGORIES[0], "block", "injection", 10), row(CATEGORIES[0], "allow", latency=30),
               row(CATEGORIES[0], outcome="unknown"), row(CATEGORIES[0], "step_up", "risk", 20),
               row("benign", "allow", latency=40), row("benign", "block", "mandate"),
               row("benign", "step_up", "risk"), row("benign", outcome="error")]
    result = calculate_metrics(records)
    assert result["by_attack_category"][CATEGORIES[0]] == {"total": 4, "observed": 3, "blocked": 1, "block_rate": 1 / 3}
    assert result["false_positive_rate"] == 2 / 3
    assert result["median_latency_ms"] == 25 and result["latency_samples"] == 4
    assert result["unresolved"] == 2 and result["caught_by_layer"] == {"injection": 1, "risk": 1}
    assert calculate_metrics([])["median_latency_ms"] is None
    assert calculate_metrics([row("benign", outcome="missing")])["false_positive_rate"] is None


@pytest.mark.parametrize("observation", [
    Observation("approved", outcome="observed"), Observation("allow", "injection", outcome="observed"),
    Observation("block", latency_ms=-1, outcome="observed"),
    Observation("block", latency_ms=float("nan"), outcome="observed"),
    Observation("block", latency_ms=True, outcome="observed"),
    Observation("allow", outcome="unknown"), Observation(latency_ms=2, outcome="missing"),
    Observation(outcome="unexpected"), Observation(error_code="secret with spaces"), {},
])
def test_invalid_observations_rejected(observation):
    with pytest.raises(ValueError):
        validate_observation(observation)


@pytest.mark.parametrize("outcome", ["unknown", "missing", "unsupported", "error"])
def test_unresolved_executor_results(outcome):
    class Fake(DeterministicTestDouble):
        def execute(self, scenario, *, system):
            return Observation(outcome=outcome)

    report = run_benchmark(CASES, {"full_stack": Fake()})
    assert report["runs"]["full_stack"]["metrics"]["observed"] == 0
    assert report["runs"]["full_stack"]["metrics"]["median_latency_ms"] is None
    assert report["ablation"]["paired_observed"] == 0


def test_executor_errors_are_not_security_blocks_or_secret_logs():
    class Broken(DeterministicTestDouble):
        def execute(self, scenario, *, system):
            raise TimeoutError("private-canary")

    report = run_benchmark(CASES, {"full_stack": Broken()})
    assert report["runs"]["full_stack"]["metrics"]["observed"] == 0
    assert "private-canary" not in json_report(report) + markdown_report(report)
    assert {r["error_code"] for r in report["runs"]["full_stack"]["records"]} == {"executor_failure"}


def test_real_adapter_and_unknown_system_refused_before_execution():
    class Real(DeterministicTestDouble):
        synthetic = False

        def execute(self, *args, **kwargs):
            raise AssertionError("must not execute")

    with pytest.raises(ValueError, match="synthetic"):
        run_benchmark(CASES, {"full_stack": Real()})
    with pytest.raises(ValueError, match="system"):
        run_benchmark(CASES, {"invented": DeterministicTestDouble()})


@pytest.mark.parametrize("mode", ["validation", "test-double"])
@pytest.mark.parametrize("format", ["json", "markdown"])
def test_cli_offline_modes(mode, format, capsys):
    assert main(["--mode", mode, "--format", format]) == 0
    output = capsys.readouterr().out
    if format == "json":
        data = json.loads(output)
        assert data["synthetic"] == (mode == "test-double")
    else:
        assert "not measured" in output if mode == "validation" else "synthetic: true" in output


def test_cli_rejects_live_mode_and_bad_library(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--mode", "live"])
    assert exc.value.code == 2
    with pytest.raises(SystemExit) as exc:
        main(["--scenarios", str(tmp_path)])
    assert exc.value.code == 2
