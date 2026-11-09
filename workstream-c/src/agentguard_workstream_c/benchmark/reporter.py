"""Serialize reports without exporting tasks, raw payloads or exception messages."""

import json


def json_report(report: dict) -> str:
    return json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n"


def _cell(value):
    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ").replace("<", "&lt;").replace(">", "&gt;")


def markdown_report(report: dict) -> str:
    lines = ["# AgentGuard benchmark infrastructure report", "", "**" + report["notice"] + "**", "",
             f"data_source: {report['data_source']}", f"synthetic: {str(report['synthetic']).lower()}",
             f"mode: {report['mode']}", f"scenario_set_sha256: {report['scenario_set_sha256']}", "",
             "## Scenario counts", "", "| Category | Count |", "| --- | ---: |"]
    lines.extend(f"| {category} | {count} |" for category, count in report["scenario_counts"].items())
    for system, run in report["runs"].items():
        metrics = run["metrics"]
        value = lambda x: "not measured" if x is None else str(x)
        lines += ["", "## " + system, "", f"data_source: {run['data_source']}; synthetic: {str(run['synthetic']).lower()}",
                  f"Observed: {metrics['observed']}/{metrics['total']}; unresolved: {metrics['unresolved']}",
                  "False-positive rate (block or step_up / observed benign): " + value(metrics["false_positive_rate"]),
                  "Median decision latency (ms): " + value(metrics["median_latency_ms"]), "",
                  "| Attack category | Observed / total | Block rate |", "| --- | ---: | ---: |"]
        lines.extend(f"| {category} | {m['observed']} / {m['total']} | {value(m['block_rate'])} |"
                     for category, m in metrics["by_attack_category"].items())
        lines += ["", "| Scenario | Expected | Observed | Actual layer | Outcome | Latency ms |",
                  "| --- | --- | --- | --- | --- | ---: |"]
        lines.extend("| " + " | ".join(_cell(value(row[k])) for k in
                     ("scenario_id", "expected_decision", "observed_decision", "actual_catching_layer", "outcome", "latency_ms")) + " |"
                     for row in run["records"])
    lines += ["", "## Ablation", "", report["ablation"]["notice"],
              f"Identical scenario sets: {report['ablation']['scenario_sets_match']}",
              f"Paired observed cases: {report['ablation']['paired_observed']}", ""]
    return "\n".join(lines)
