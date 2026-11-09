"""Offline validation or synthetic plumbing tests. No real gateway mode exists."""

import argparse
from pathlib import Path

from .executor import DeterministicTestDouble, run_benchmark
from .models import SYSTEMS
from .reporter import json_report, markdown_report
from .scenario_loader import load_scenarios
from .validator import ScenarioValidationError


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("validation", "test-double"), default="validation")
    parser.add_argument("--scenarios", type=Path, help="local scenario directory; defaults to bundled library")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    args = parser.parse_args(argv)
    try:
        scenarios = load_scenarios(args.scenarios)
        executors = {system: DeterministicTestDouble() for system in SYSTEMS} if args.mode == "test-double" else None
        report = run_benchmark(scenarios, executors)
    except (OSError, ScenarioValidationError) as exc:
        parser.exit(2, f"Scenario validation failed: {exc}\n")
    print(json_report(report) if args.format == "json" else markdown_report(report), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
