"""Offline execution orchestration; no SDK, HTTP client or real adapter."""

from collections import Counter
from hashlib import sha256
import json
import math

from .metrics import calculate_metrics
from .models import DECISIONS, LAYERS, SYSTEMS, Executor, Observation
from .validator import validate_library


class DeterministicTestDouble:
    """Hash-derived plumbing results, independent of expected decisions/layers.

    These are deliberately arbitrary, not a policy engine, LLM or AgentGuard.
    Synthetic latencies are constants for metric tests, not measured timings.
    """

    name = "deterministic_test_double_v1"
    data_source = "synthetic_test_double"
    synthetic = True

    def execute(self, scenario, *, system):
        number = int.from_bytes(sha256((system + ":" + scenario.scenario_id).encode()).digest()[:4], "big")
        decision = DECISIONS[number % 3]
        return Observation(decision, None if decision == "allow" else LAYERS[number % len(LAYERS)],
                           float(1 + number % 20), "observed")


def validate_observation(result):
    if not isinstance(result, Observation) or result.outcome not in ("observed", "unknown", "missing", "error", "unsupported"):
        raise ValueError("invalid executor observation")
    if result.catching_layer not in (*LAYERS, None):
        raise ValueError("invalid observed layer")
    if result.latency_ms is not None and (type(result.latency_ms) not in (float, int)
                                         or not math.isfinite(result.latency_ms) or result.latency_ms < 0):
        raise ValueError("invalid decision latency")
    if result.outcome == "observed":
        if result.decision not in DECISIONS or (result.decision == "allow" and result.catching_layer is not None):
            raise ValueError("invalid observed decision/layer")
    elif result.decision is not None or result.catching_layer is not None or result.latency_ms is not None:
        raise ValueError("unresolved outcome cannot claim a decision, layer or decision latency")
    if result.error_code is not None and (not isinstance(result.error_code, str)
                                          or not result.error_code.isidentifier() or len(result.error_code) > 80):
        raise ValueError("invalid error code")
    return result


def run_benchmark(scenarios, executors: dict[str, Executor] | None = None) -> dict:
    """Validate once; supply the exact same immutable cases to both ablation arms.

    Empty executors means validation only. Real adapters are explicitly rejected
    until a separately reviewed gateway dry-run interface exists. Injected Python
    implementations are trusted code, not sandboxed by this protocol.
    """
    cases = tuple(sorted(validate_library(scenarios), key=lambda s: s.scenario_id))
    executors = {} if executors is None else dict(executors)
    if any(system not in SYSTEMS for system in executors):
        raise ValueError("unknown ablation system")
    for executor in executors.values():
        if executor.synthetic is not True or executor.data_source != "synthetic_test_double":
            raise ValueError("only synthetic test-double executors are enabled")
        if not isinstance(executor.name, str) or not executor.name.isidentifier():
            raise ValueError("executor name must be a public identifier")
    digest = sha256(json.dumps([s.to_dict() for s in cases], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    runs = {}
    for system in SYSTEMS:
        executor = executors.get(system)
        records = []
        for scenario in cases:
            result = Observation()
            if executor is not None:
                try:
                    result = validate_observation(executor.execute(scenario, system=system))
                except Exception:
                    # Do not log exceptions: arbitrary adapters may include secrets.
                    result = Observation(outcome="error", error_code="executor_failure")
            records.append({"scenario_id": scenario.scenario_id, "category": scenario.category,
                            "expected_decision": scenario.expected_decision,
                            "expected_catching_layer": scenario.expected_catching_layer,
                            "observed_decision": result.decision, "actual_catching_layer": result.catching_layer,
                            "latency_ms": result.latency_ms, "outcome": result.outcome, "error_code": result.error_code})
        runs[system] = {"executor": None if executor is None else executor.name, "scenario_set_sha256": digest,
                        "data_source": "not_executed" if executor is None else executor.data_source,
                        "synthetic": executor is not None, "records": records, "metrics": calculate_metrics(records)}
    paired = [(a, b) for a, b in zip(runs["llm_only"]["records"], runs["full_stack"]["records"])
              if a["outcome"] == b["outcome"] == "observed"]
    return {"report_version": 1, "mode": "test_double" if executors else "validation",
            "data_source": "synthetic_test_double" if executors else "not_executed", "synthetic": bool(executors),
            "notice": "Synthetic infrastructure test; NOT AgentGuard security performance." if executors else
                      "Definitions validated only; security behavior was not executed or measured.",
            "scenario_set_sha256": digest, "scenario_counts": dict(sorted(Counter(s.category for s in cases).items())),
            "runs": runs, "ablation": {"scenario_sets_match": True, "paired_observed": len(paired),
                "decision_disagreements": sum(a["observed_decision"] != b["observed_decision"] for a, b in paired) if paired else None,
                "notice": "Comparison is synthetic only." if executors else "Awaiting genuine safe dry-run adapters."}}
