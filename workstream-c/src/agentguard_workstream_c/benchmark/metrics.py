"""Metrics from observed decisions only, with explicit coverage and denominators."""

from statistics import median

from .models import CATEGORIES


def calculate_metrics(records: list[dict]) -> dict:
    def known(row):
        return row["outcome"] == "observed" and row["observed_decision"] in ("allow", "step_up", "block")

    by_category = {}
    for category in CATEGORIES:
        rows = [r for r in records if r["category"] == category]
        observed = [r for r in rows if known(r)]
        blocks = sum(r["observed_decision"] == "block" for r in observed)
        by_category[category] = {"total": len(rows), "observed": len(observed), "blocked": blocks,
                                 "block_rate": blocks / len(observed) if observed else None}
    benign = [r for r in records if r["category"] == "benign" and known(r)]
    positives = sum(r["observed_decision"] in ("block", "step_up") for r in benign)
    latencies = [r["latency_ms"] for r in records if known(r) and r["latency_ms"] is not None]
    layers = {}
    for row in records:
        if row["category"] != "benign" and known(row) and row["observed_decision"] in ("block", "step_up"):
            layer = row["actual_catching_layer"] or "unattributed"
            layers[layer] = layers.get(layer, 0) + 1
    observed_count = sum(known(r) for r in records)
    return {"total": len(records), "observed": observed_count, "unresolved": len(records) - observed_count,
            "by_attack_category": by_category, "benign_observed": len(benign),
            "benign_false_positives": positives,
            "false_positive_rate": positives / len(benign) if benign else None,
            "latency_samples": len(latencies), "median_latency_ms": median(latencies) if latencies else None,
            "caught_by_layer": layers}
