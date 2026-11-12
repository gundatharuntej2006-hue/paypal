"""Pure dashboard projection of rich reports; no execution or network access.

Records and scenario_counts are authoritative. Cached integer counters must agree
with them. Dashboard totals are observed counts and latency is filtered here.
Explicit data_source='genuine' is supported for future imported reports, never
inferred from records, flags, or timings. No current executor emits that marker.
"""

from datetime import datetime
from collections import Counter
from collections.abc import Mapping
import math
import re
from typing import Any

from .models import CATEGORIES, DECISIONS, LAYERS, SYSTEMS


class DashboardReportError(ValueError):
    """Unsupported or inconsistent source report; messages omit source values."""


_SOURCES = {
    "not_executed": ("unexecuted", False),
    "synthetic_test_double": ("synthetic", True),
    "genuine": ("genuine", False),
}
_OUTCOMES = ("observed", "unknown", "missing", "error", "unsupported")
_CATEGORIES = (*CATEGORIES, "benign")


def _provenance(metadata: Mapping[str, Any]) -> str:
    source = metadata.get("data_source")
    if not isinstance(source, str) or source not in _SOURCES:
        raise DashboardReportError("explicit recognized data_source is required at report and arm level")
    provenance, synthetic = _SOURCES[source]
    if type(metadata.get("synthetic")) is not bool or metadata["synthetic"] is not synthetic:
        raise DashboardReportError("synthetic flag conflicts with explicit data_source")
    return provenance


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _valid_latency(value: Any) -> bool:
    if type(value) not in (int, float) or value < 0:
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False  # Not representable as a finite dashboard numeric value.


def _median(samples: list[int | float]) -> int | float | None:
    if not samples:
        return None
    middle = len(samples) // 2
    if len(samples) % 2:
        return samples[middle]
    low, high = samples[middle - 1:middle + 1]
    return low + (high - low) / 2  # Avoid overflowing a sum of finite latencies.


def _experiment_metadata(report: Mapping[str, Any], runs: Mapping[str, Any]) -> None:
    ablation = report.get("ablation")
    if not isinstance(ablation, Mapping) or ablation.get("scenario_sets_match") is not True:
        raise DashboardReportError("ablation.scenario_sets_match must be true before comparing arms")
    digest = report.get("scenario_set_sha256")
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise DashboardReportError("report.scenario_set_sha256 must be a lowercase SHA-256 hex digest")
    for system in SYSTEMS:
        if not isinstance(runs[system], Mapping) or runs[system].get("scenario_set_sha256") != digest:
            raise DashboardReportError(f"{system}.scenario_set_sha256 must match report.scenario_set_sha256")


def _cached_counters(metrics: Any, defined: Counter, observed: Counter,
                     blocked: Counter, false_positives: int, system: str) -> None:
    if not isinstance(metrics, Mapping):
        raise DashboardReportError(f"{system}.metrics must contain the cached integer counters")

    def check(container, field, expected, path):
        if type(container.get(field)) is not int or container[field] != expected:
            raise DashboardReportError(f"{path}.{field} must be an integer matching the scenario records")

    path = f"{system}.metrics"
    expected = {"total": sum(defined.values()), "observed": sum(observed.values()),
                "unresolved": sum(defined.values()) - sum(observed.values()),
                "benign_observed": observed["benign"], "benign_false_positives": false_positives}
    for field, count in expected.items():
        check(metrics, field, count, path)
    categories = metrics.get("by_attack_category")
    if not isinstance(categories, Mapping) or set(categories) != set(CATEGORIES):
        raise DashboardReportError(f"{path}.by_attack_category must contain exactly the six malicious categories")
    for category in CATEGORIES:
        entry = categories[category]
        if not isinstance(entry, Mapping):
            raise DashboardReportError(f"{path}.by_attack_category entries must contain cached integer counters")
        for field, count in (("total", defined[category]), ("observed", observed[category]), ("blocked", blocked[category])):
            check(entry, field, count, f"{path}.by_attack_category.{category}")


def _arm(records: list, planned: Mapping[str, int], provenance: str,
         identities: dict[str, tuple], metrics: Any, system: str) -> tuple[dict, list[dict], list[int | float]]:
    seen = set()
    defined = Counter()
    observed = Counter()
    blocked = Counter()
    false_positives = 0
    latencies = []
    for row in records:
        if not isinstance(row, Mapping):
            raise DashboardReportError("each record must be an object")
        scenario_id, category = row.get("scenario_id"), row.get("category")
        if not isinstance(scenario_id, str) or not scenario_id or scenario_id in seen:
            raise DashboardReportError("records require nonempty unique scenario_id values within each arm")
        if not isinstance(category, str) or category not in planned:
            raise DashboardReportError("record category is not defined in scenario_counts")
        if "expected_decision" not in row or row["expected_decision"] not in DECISIONS:
            raise DashboardReportError("scenario records require a valid expected_decision")
        if "expected_catching_layer" not in row or row["expected_catching_layer"] not in (*LAYERS, None):
            raise DashboardReportError("scenario records require a valid expected_catching_layer")
        identity = (category, row["expected_decision"], row["expected_catching_layer"])
        if system == "llm_only":
            if scenario_id not in identities:
                raise DashboardReportError("llm_only scenario IDs must match full_stack; renamed or extra scenario found")
            if identities[scenario_id] != identity:
                raise DashboardReportError("scenario category or expected fields conflicts across arms; compare the same experiment")
        else:
            identities[scenario_id] = identity
        seen.add(scenario_id)
        defined[category] += 1
        if defined[category] > planned[category]:
            raise DashboardReportError("record count exceeds planned scenario count")
        outcome, decision = row.get("outcome"), row.get("observed_decision")
        if outcome not in _OUTCOMES:
            raise DashboardReportError("record has unsupported outcome")
        if outcome != "observed":
            if decision is not None:
                raise DashboardReportError("unresolved outcome cannot claim a decision")
            continue
        if decision not in DECISIONS:
            raise DashboardReportError("observed outcome requires a valid decision")
        if provenance == "unexecuted":
            raise DashboardReportError("unexecuted metadata conflicts with observed decisions")
        observed[category] += 1
        blocked[category] += decision == "block"
        if category == "benign" and decision in ("block", "step_up"):
            false_positives += 1
        latency = row.get("latency_ms")
        if _valid_latency(latency):
            latencies.append(latency)

    if any(defined[c] != planned[c] for c in _CATEGORIES):
        raise DashboardReportError(f"{system} scenario inventory must match scenario_counts; retain unresolved cases as records")
    if system == "llm_only" and seen != set(identities):
        raise DashboardReportError("llm_only scenario IDs must match full_stack; missing scenarios found")
    _cached_counters(metrics, defined, observed, blocked, false_positives, system)
    coverage = {}
    for group, categories in (("malicious", CATEGORIES), ("benign", ("benign",))):
        total = sum(planned[c] for c in categories)
        count = sum(observed[c] for c in categories)
        if count > total:
            raise DashboardReportError("observed scenario count exceeds planned count")
        coverage[group] = {"planned": total, "observed": count, "unresolved": total - count}
    classes = [{"name": c, "blocked": blocked[c], "total": observed[c],
                "block_rate": _rate(blocked[c], observed[c])} for c in CATEGORIES]
    result = {"block_rate": _rate(sum(blocked[c] for c in CATEGORIES), coverage["malicious"]["observed"]),
              "false_positive_rate": _rate(false_positives, observed["benign"]), "coverage": coverage}
    return result, classes, latencies


def dashboard_summary(report: Mapping[str, Any]) -> dict:
    """Return the agreed dashboard object without mutating the source report.

    Both arms must retain the same complete planned scenario inventory, even for
    unresolved outcomes. Hashes, scenario definitions and cached integer counters
    are checked before export. Malformed latency samples alone are excluded.
    """
    if not isinstance(report, Mapping) or type(report.get("report_version")) is not int or report["report_version"] != 1:
        raise DashboardReportError("source report_version must be 1")
    planned = report.get("scenario_counts")
    if not isinstance(planned, Mapping) or set(planned) != set(_CATEGORIES):
        raise DashboardReportError("scenario_counts must define the six malicious categories and benign")
    if any(type(count) is not int or count < 0 for count in planned.values()):
        raise DashboardReportError("planned scenario counts must be nonnegative integers")
    runs = report.get("runs")
    if not isinstance(runs, Mapping) or set(runs) != set(SYSTEMS):
        raise DashboardReportError("source report must contain exactly full_stack and llm_only runs")
    _experiment_metadata(report, runs)
    provenance = _provenance(report)
    arms = {}
    full_classes = []
    identities: dict[str, tuple] = {}
    for system in ("full_stack", "llm_only"):
        run = runs[system]
        if not isinstance(run, Mapping) or _provenance(run) != provenance:
            raise DashboardReportError("report and arm provenance must agree")
        records = run.get("records")
        if not isinstance(records, list):
            raise DashboardReportError("each arm requires a records array")
        arm, classes, samples = _arm(records, planned, provenance, identities, run.get("metrics"), system)
        if system == "full_stack":
            full_classes = classes
            samples.sort()
            arm.update(median_latency_ms=_median(samples),
                       p95_latency_ms=samples[math.ceil(0.95 * len(samples)) - 1] if samples else None,
                       latency_sample_count=len(samples))
        arms[system] = arm
    summary = {"report_version": 1, "provenance": provenance, "by_class": full_classes, "ablation": arms}
    # Preserve runtime metadata; importing historical reports never invents it.
    for field in ("run_id", "generated_at"):
        value = report.get(field)
        if value is None:
            continue
        if not isinstance(value, str) or not value.strip():
            raise DashboardReportError(f"{field} must be a nonblank string when supplied")
        if field == "generated_at":
            try:
                timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if timestamp.utcoffset() is None:
                    raise ValueError
            except ValueError:
                raise DashboardReportError("generated_at must be a timezone-qualified ISO timestamp") from None
        summary[field] = value
    return summary
