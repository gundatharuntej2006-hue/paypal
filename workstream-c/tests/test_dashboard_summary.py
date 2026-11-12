"""Dashboard contract examples and pure metric tests; no genuine execution."""

from copy import deepcopy
import json
from pathlib import Path
import socket

import pytest

from agentguard_workstream_c.benchmark.dashboard import DashboardReportError, dashboard_summary
from agentguard_workstream_c.benchmark.executor import DeterministicTestDouble, run_benchmark
from agentguard_workstream_c.benchmark.metrics import calculate_metrics
from agentguard_workstream_c.benchmark.models import CATEGORIES, SYSTEMS
from agentguard_workstream_c.benchmark.reporter import json_report, markdown_report
from agentguard_workstream_c.benchmark.scenario_loader import load_scenarios
from agentguard_workstream_c.sdk import GuardClient

CASES = load_scenarios()
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "dashboard"


@pytest.fixture(autouse=True)
def no_network_or_submission(monkeypatch):
    attempts = []

    def forbidden(*args, **kwargs):
        attempts.append(True)
        raise AssertionError("dashboard adaptation must not execute network or payments")

    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(GuardClient, "request_payment", forbidden)
    yield
    assert not attempts


def example_report(source="synthetic_test_double"):
    """Hand-authored input metadata; never execute a genuine benchmark arm."""
    report = run_benchmark(CASES)
    report["mode"] = "contract_fixture"
    report["notice"] = "Hand-authored contract example only; not benchmark findings."
    for metadata in (report, *report["runs"].values()):
        metadata["data_source"] = source
        metadata["synthetic"] = source == "synthetic_test_double"
    return report


def observe(report, system, category, index=0, decision="allow", latency=None):
    row = [r for r in report["runs"][system]["records"] if r["category"] == category][index]
    row.update(outcome="observed", observed_decision=decision, latency_ms=latency)
    report["runs"][system]["metrics"] = calculate_metrics(report["runs"][system]["records"])
    return row


def fixture_source(name):
    """The explicit plans behind the JSON snapshots, all hand-authored examples."""
    if name == "unexecuted":
        return run_benchmark(CASES)
    if name == "genuine_partial_coverage":
        report = example_report("genuine")
        observe(report, "full_stack", CATEGORIES[0], 0, "allow", 10)
        observe(report, "full_stack", CATEGORIES[0], 1, "allow", 30)
        observe(report, "full_stack", CATEGORIES[1], 0, "block")
        observe(report, "llm_only", CATEGORIES[0], 0, "step_up")
        observe(report, "llm_only", "benign", 0, "allow")
        return report
    assert name == "synthetic"
    report = example_report()
    for i, decision in enumerate(("block", "block", "block", "allow")):
        observe(report, "full_stack", CATEGORIES[0], i, decision, (i + 1) * 10)
    observe(report, "full_stack", CATEGORIES[1], 0, "allow")
    for i, decision in enumerate(("allow", "block", "step_up")):
        observe(report, "full_stack", "benign", i, decision, 50 + i * 10)
    observe(report, "llm_only", CATEGORIES[0], 0, "block")
    observe(report, "llm_only", CATEGORIES[1], 0, "allow")
    observe(report, "llm_only", CATEGORIES[1], 1, "allow")
    observe(report, "llm_only", "benign", 0, "allow")
    observe(report, "llm_only", "benign", 1, "step_up")
    return report


def assert_contract(summary):
    assert set(summary) - {"run_id", "generated_at"} == {"report_version", "provenance", "by_class", "ablation"}
    assert type(summary["report_version"]) is int and summary["report_version"] == 1
    assert summary["provenance"] in ("genuine", "synthetic", "unexecuted")
    assert [c["name"] for c in summary["by_class"]] == list(CATEGORIES)
    for entry in summary["by_class"]:
        assert set(entry) == {"name", "blocked", "total", "block_rate"}
        assert type(entry["blocked"]) is type(entry["total"]) is int
        assert 0 <= entry["blocked"] <= entry["total"]
        assert entry["block_rate"] == (entry["blocked"] / entry["total"] if entry["total"] else None)
    assert set(summary["ablation"]) == set(SYSTEMS)
    for system, arm in summary["ablation"].items():
        keys = {"block_rate", "false_positive_rate", "coverage"}
        if system == "full_stack":
            keys |= {"median_latency_ms", "p95_latency_ms", "latency_sample_count"}
            assert type(arm["latency_sample_count"]) is int and arm["latency_sample_count"] >= 0
            for key in ("median_latency_ms", "p95_latency_ms"):
                assert arm[key] is None or type(arm[key]) in (int, float) and arm[key] >= 0
        assert set(arm) == keys
        for key in ("block_rate", "false_positive_rate"):
            assert arm[key] is None or 0 <= arm[key] <= 1
        assert set(arm["coverage"]) == {"malicious", "benign"}
        for coverage in arm["coverage"].values():
            assert set(coverage) == {"planned", "observed", "unresolved"}
            assert all(type(n) is int and n >= 0 for n in coverage.values())
            assert coverage["planned"] == coverage["observed"] + coverage["unresolved"]
    # This also asserts no NaN/Infinity reaches JSON output.
    assert json.loads(json.dumps(summary, allow_nan=False)) == summary


@pytest.mark.parametrize("name", ["genuine_partial_coverage", "synthetic", "unexecuted"])
def test_exact_contract_fixtures_match_adapter(name):
    def reject_constant(_):
        raise AssertionError("fixture contains a nonfinite JSON number")

    def unique(pairs):
        obj = {}
        for key, value in pairs:
            assert key not in obj
            obj[key] = value
        return obj

    fixture = json.loads((FIXTURES / (name + ".json")).read_text(encoding="utf-8"),
                         parse_constant=reject_constant, object_pairs_hook=unique)
    source = fixture_source(name)
    # Static examples predate runtime metadata; do not fabricate fixture values.
    source.pop("run_id", None)
    source.pop("generated_at", None)
    result = dashboard_summary(source)
    assert_contract(fixture)
    assert result == fixture


def test_hand_authored_partial_example_zeros_nulls_and_coverage():
    summary = dashboard_summary(fixture_source("genuine_partial_coverage"))
    assert summary["by_class"][0] == {"name": CATEGORIES[0], "blocked": 0, "total": 2, "block_rate": 0.0}
    assert summary["by_class"][1]["block_rate"] == 1.0
    assert summary["by_class"][2]["total"] == 0 and summary["by_class"][2]["block_rate"] is None
    fs, llm = (summary["ablation"][s] for s in ("full_stack", "llm_only"))
    assert fs["block_rate"] == 1 / 3 and fs["false_positive_rate"] is None
    assert fs["median_latency_ms"] == 20 and fs["p95_latency_ms"] == 30 and fs["latency_sample_count"] == 2
    assert fs["coverage"] == {"malicious": {"planned": 60, "observed": 3, "unresolved": 57},
                               "benign": {"planned": 20, "observed": 0, "unresolved": 20}}
    assert llm["block_rate"] == 0 and llm["false_positive_rate"] == 0
    assert llm["coverage"]["benign"] == {"planned": 20, "observed": 1, "unresolved": 19}


def test_weighted_rates_step_up_and_false_positives_both_arms():
    report = fixture_source("synthetic")
    # Include step_up in the malicious denominator, never in the block numerator.
    observe(report, "full_stack", CATEGORIES[1], 1, "step_up")
    result = dashboard_summary(report)
    fs, llm = (result["ablation"][s] for s in ("full_stack", "llm_only"))
    assert fs["block_rate"] == 3 / 6  # Not mean(3/4, 0/2).
    assert llm["block_rate"] == 1 / 3
    assert fs["false_positive_rate"] == 2 / 3
    assert llm["false_positive_rate"] == 1 / 2
    assert result["by_class"][0]["total"] == 4
    assert result["by_class"][1]["total"] == 2


@pytest.mark.parametrize("category", CATEGORIES)
def test_every_malicious_category_uses_observed_counts(category):
    report = example_report()
    for index, decision in enumerate(("allow", "step_up", "block")):
        observe(report, "full_stack", category, index, decision)
    summary = dashboard_summary(report)
    entry = next(c for c in summary["by_class"] if c["name"] == category)
    assert entry == {"name": category, "blocked": 1, "total": 3, "block_rate": 1 / 3}
    assert len(summary["by_class"]) == 6


def test_existing_validation_and_synthetic_reports_are_accepted_unchanged():
    for report in (run_benchmark(CASES), run_benchmark(CASES, {s: DeterministicTestDouble() for s in SYSTEMS})):
        snapshot = deepcopy(report)
        serialized = json_report(report), markdown_report(report)
        summary = dashboard_summary(report)
        assert_contract(summary)
        assert report == snapshot
        assert (json_report(report), markdown_report(report)) == serialized
        assert summary["provenance"] == ("synthetic" if report["synthetic"] else "unexecuted")


@pytest.mark.parametrize("outcome", ["error", "unknown", "missing", "unsupported"])
def test_unresolved_outcomes_and_timeout_errors_remain_unresolved(outcome):
    report = example_report()
    for system in SYSTEMS:
        for row in report["runs"][system]["records"]:
            row.update(outcome=outcome, error_code="timeout" if outcome == "error" else None, latency_ms=999)
    result = dashboard_summary(report)
    for arm in result["ablation"].values():
        assert arm["block_rate"] is None and arm["false_positive_rate"] is None
        assert arm["coverage"] == {"malicious": {"planned": 60, "observed": 0, "unresolved": 60},
                                    "benign": {"planned": 20, "observed": 0, "unresolved": 20}}
    assert result["ablation"]["full_stack"]["latency_sample_count"] == 0


@pytest.mark.parametrize("invalid", [None, "12", True, False, -1, float("nan"), float("inf"), -float("inf"), {}, [], 10**400])
def test_invalid_latency_is_excluded_without_discarding_decision(invalid):
    report = example_report()
    row = observe(report, "full_stack", CATEGORIES[0], decision="block")
    row["latency_ms"] = invalid
    fs = dashboard_summary(report)["ablation"]["full_stack"]
    assert fs["block_rate"] == 1 and fs["coverage"]["malicious"]["observed"] == 1
    assert fs["median_latency_ms"] is fs["p95_latency_ms"] is None
    assert fs["latency_sample_count"] == 0


@pytest.mark.parametrize("samples,expected_median,expected_p95", [
    ([], None, None), ([0], 0, 0), ([1, 9], 5, 9), ([30, 10, 20], 20, 30),
    (list(range(1, 21)), 10.5, 19), ([1e308, 1e308], 1e308, 1e308),
])
def test_latency_median_and_nearest_rank_p95(samples, expected_median, expected_p95):
    report = example_report()
    # Benign samples and malicious samples both participate; llm-only does not.
    for i, value in enumerate(samples):
        observe(report, "full_stack", "benign" if i >= 10 else CATEGORIES[0], i % 10, latency=value)
    observe(report, "llm_only", CATEGORIES[0], latency=999)
    fs = dashboard_summary(report)["ablation"]["full_stack"]
    assert fs["median_latency_ms"] == expected_median
    assert fs["p95_latency_ms"] == expected_p95
    assert fs["latency_sample_count"] == len(samples)
    json.dumps(fs, allow_nan=False)


def test_missing_records_rejected_but_unresolved_records_preserve_partial_coverage():
    report = example_report()
    observe(report, "full_stack", CATEGORIES[0], latency=10)
    coverage = dashboard_summary(report)["ablation"]["full_stack"]["coverage"]
    assert coverage["malicious"] == {"planned": 60, "observed": 1, "unresolved": 59}
    assert coverage["benign"]["unresolved"] == 20
    report["runs"]["full_stack"]["records"] = [r for r in report["runs"]["full_stack"]["records"] if r["outcome"] == "observed"]
    with pytest.raises(DashboardReportError, match="inventory must match scenario_counts"):
        dashboard_summary(report)


@pytest.mark.parametrize("level", ["report", "full_stack", "llm_only"])
@pytest.mark.parametrize("source", [None, "", "unknown", [], "live", "not-synthetic"])
def test_missing_or_unrecognized_explicit_provenance_rejected(level, source):
    report = example_report()
    target = report if level == "report" else report["runs"][level]
    target["data_source"] = source
    target["synthetic"] = False
    with pytest.raises(DashboardReportError, match="data_source"):
        dashboard_summary(report)


@pytest.mark.parametrize("level", ["report", "full_stack", "llm_only"])
@pytest.mark.parametrize("flag", [False, None, 0, "true"])
def test_conflicting_or_missing_synthetic_flag_rejected(level, flag):
    report = example_report()
    target = report if level == "report" else report["runs"][level]
    target["synthetic"] = flag
    with pytest.raises(DashboardReportError, match="synthetic flag"):
        dashboard_summary(report)


@pytest.mark.parametrize("source", ["genuine", "not_executed"])
def test_conflicting_arm_provenance_is_not_combined(source):
    report = example_report()
    report["runs"]["llm_only"].update(data_source=source, synthetic=False)
    with pytest.raises(DashboardReportError, match="provenance"):
        dashboard_summary(report)


def test_unexecuted_with_observations_is_rejected():
    report = example_report("not_executed")
    observe(report, "full_stack", "benign", latency=1)
    with pytest.raises(DashboardReportError, match="unexecuted"):
        dashboard_summary(report)


@pytest.mark.parametrize("bad", [-1, True, 1.5, "10"])
def test_bad_planned_counts_rejected(bad):
    report = example_report()
    report["scenario_counts"][CATEGORIES[0]] = bad
    with pytest.raises(DashboardReportError, match="planned scenario counts"):
        dashboard_summary(report)


def test_overfilled_plan_duplicate_records_and_conflicting_identity_rejected():
    report = example_report()
    report["scenario_counts"][CATEGORIES[0]] = 0
    with pytest.raises(DashboardReportError, match="exceeds planned"):
        dashboard_summary(report)
    report = example_report()
    report["runs"]["full_stack"]["records"].append(deepcopy(report["runs"]["full_stack"]["records"][0]))
    with pytest.raises(DashboardReportError, match="unique scenario_id"):
        dashboard_summary(report)
    report = example_report()
    report["runs"]["llm_only"]["records"][0]["category"] = "benign"
    with pytest.raises(DashboardReportError, match="conflicts across arms"):
        dashboard_summary(report)


@pytest.mark.parametrize("mutation", ["version", "categories", "arm", "records", "row", "outcome", "decision", "contradiction"])
def test_inconsistent_report_shapes_fail_with_typed_errors(mutation):
    report = example_report()
    row = report["runs"]["full_stack"]["records"][0]
    if mutation == "version": report["report_version"] = True
    elif mutation == "categories": report["scenario_counts"]["invented"] = 1
    elif mutation == "arm": del report["runs"]["llm_only"]
    elif mutation == "records": report["runs"]["full_stack"]["records"] = None
    elif mutation == "row": report["runs"]["full_stack"]["records"][0] = None
    elif mutation == "outcome": row["outcome"] = "invented"
    elif mutation == "decision": row.update(outcome="observed", observed_decision="approved")
    else: row["observed_decision"] = "allow"
    with pytest.raises(DashboardReportError):
        dashboard_summary(report)


@pytest.mark.parametrize("arm", SYSTEMS)
def test_different_arm_hashes_rejected(arm):
    report = fixture_source("synthetic")
    report["runs"][arm]["scenario_set_sha256"] = "b" * 64
    with pytest.raises(DashboardReportError, match=arm + r"\.scenario_set_sha256 must match report"):
        dashboard_summary(report)


def test_top_level_hash_must_match_both_equal_arm_hashes():
    report = example_report()
    report["scenario_set_sha256"] = "a" * 64
    with pytest.raises(DashboardReportError, match="scenario_set_sha256 must match report"):
        dashboard_summary(report)


@pytest.mark.parametrize("value", [None, "", "not-a-digest", "A" * 64, "a" * 63, 1, []])
def test_invalid_top_level_digest_rejected(value):
    report = example_report()
    report["scenario_set_sha256"] = value
    with pytest.raises(DashboardReportError, match="SHA-256"):
        dashboard_summary(report)


@pytest.mark.parametrize("value", [False, None, 0, 1, "true"])
def test_scenario_sets_match_must_be_explicit_true(value):
    report = example_report()
    report["ablation"]["scenario_sets_match"] = value
    with pytest.raises(DashboardReportError, match=r"ablation.scenario_sets_match must be true"):
        dashboard_summary(report)


@pytest.mark.parametrize("arm", SYSTEMS)
@pytest.mark.parametrize("mutation", ["renamed", "missing", "extra", "disjoint"])
def test_same_hashes_do_not_allow_inconsistent_scenario_ids(arm, mutation):
    report = example_report()
    records = report["runs"][arm]["records"]
    if mutation == "renamed":
        records[0]["scenario_id"] = "renamed_case"
    elif mutation == "missing":
        records.pop()
    elif mutation == "extra":
        records.append({**records[0], "scenario_id": "extra_case"})
    else:
        for row in records:
            row["scenario_id"] = "different_" + row["scenario_id"]
    with pytest.raises(DashboardReportError, match="scenario IDs|inventory|exceeds planned"):
        dashboard_summary(report)


@pytest.mark.parametrize("field", ["category", "expected_decision", "expected_catching_layer"])
def test_scenario_definition_fields_must_match_between_arms(field):
    report = example_report()
    records = report["runs"]["llm_only"]["records"]
    first = records[0]
    if field == "category":
        second = next(r for r in records if r["category"] != first["category"])
        first[field], second[field] = second[field], first[field]  # Preserve category totals.
    elif field == "expected_decision":
        first[field] = "allow" if first[field] != "allow" else "block"
    else:
        first[field] = "risk" if first[field] != "risk" else "intent"
    with pytest.raises(DashboardReportError, match="conflicts across arms"):
        dashboard_summary(report)


@pytest.mark.parametrize("field", ["expected_decision", "expected_catching_layer"])
def test_missing_scenario_definition_fields_rejected(field):
    report = example_report()
    for arm in SYSTEMS:
        del report["runs"][arm]["records"][0][field]
    with pytest.raises(DashboardReportError, match=field):
        dashboard_summary(report)


def test_matching_arm_omissions_cannot_shrink_the_planned_inventory():
    report = example_report()
    for arm in SYSTEMS:
        report["runs"][arm]["records"].pop()
    with pytest.raises(DashboardReportError, match="inventory must match scenario_counts"):
        dashboard_summary(report)


@pytest.mark.parametrize("arm", SYSTEMS)
@pytest.mark.parametrize("field", ["total", "observed", "unresolved", "benign_observed", "benign_false_positives"])
def test_cached_arm_integer_counters_must_match_records(arm, field):
    report = fixture_source("synthetic")
    metrics = report["runs"][arm]["metrics"]
    metrics[field] = 81 if field == "observed" else metrics[field] + 1
    with pytest.raises(DashboardReportError, match=arm + r"\.metrics\." + field):
        dashboard_summary(report)


@pytest.mark.parametrize("arm", SYSTEMS)
@pytest.mark.parametrize("category", CATEGORIES)
@pytest.mark.parametrize("field", ["total", "observed", "blocked"])
def test_cached_category_counters_must_match_records(arm, category, field):
    report = fixture_source("synthetic")
    report["runs"][arm]["metrics"]["by_attack_category"][category][field] += 1
    with pytest.raises(DashboardReportError, match="by_attack_category.*" + field):
        dashboard_summary(report)


@pytest.mark.parametrize("bad", [True, 0.0, None, -1, "0"])
def test_cached_counters_require_exact_integer_values(bad):
    report = example_report()
    report["runs"]["full_stack"]["metrics"]["observed"] = bad
    with pytest.raises(DashboardReportError, match=r"metrics.observed must be an integer"):
        dashboard_summary(report)


@pytest.mark.parametrize("mutation", ["metrics", "counter", "categories", "category", "category_counter"])
def test_missing_cached_counter_structures_fail_with_typed_errors(mutation):
    report = example_report()
    run = report["runs"]["full_stack"]
    if mutation == "metrics": del run["metrics"]
    elif mutation == "counter": del run["metrics"]["observed"]
    elif mutation == "categories": del run["metrics"]["by_attack_category"]
    elif mutation == "category": del run["metrics"]["by_attack_category"][CATEGORIES[0]]
    else: del run["metrics"]["by_attack_category"][CATEGORIES[0]]["blocked"]
    with pytest.raises(DashboardReportError, match="metrics"):
        dashboard_summary(report)


def test_inventory_order_is_irrelevant_and_different_observations_are_valid():
    report = fixture_source("synthetic")
    before = dashboard_summary(report)
    for arm in SYSTEMS:
        report["runs"][arm]["records"].reverse()
    assert dashboard_summary(report) == before
    assert before["ablation"]["full_stack"]["block_rate"] != before["ablation"]["llm_only"]["block_rate"]


def test_cached_latency_and_float_aggregates_are_not_used_as_integer_counter_evidence():
    report = fixture_source("genuine_partial_coverage")
    before = dashboard_summary(report)
    metrics = report["runs"]["full_stack"]["metrics"]
    # Legacy latency_samples counts every non-None sample, while the adapter
    # excludes invalid values. Its float aggregates are not integrity evidence.
    metrics.update(latency_samples=99, median_latency_ms=999, false_positive_rate=0.0)
    metrics["by_attack_category"][CATEGORIES[0]]["block_rate"] = 0.75
    assert dashboard_summary(report) == before


@pytest.mark.parametrize("field,value", [
    ("run_id", " "), ("run_id", 1), ("generated_at", "not-a-date"),
    ("generated_at", "2026-11-12T10:00:00"), ("generated_at", 1),
])
def test_invalid_optional_runtime_metadata(field, value):
    report = run_benchmark(CASES)
    report[field] = value
    with pytest.raises(DashboardReportError, match=field):
        dashboard_summary(report)


def test_absent_or_null_metadata_is_not_invented():
    report = run_benchmark(CASES)
    report.pop("run_id")
    report["generated_at"] = None
    summary = dashboard_summary(report)
    assert "run_id" not in summary and "generated_at" not in summary
