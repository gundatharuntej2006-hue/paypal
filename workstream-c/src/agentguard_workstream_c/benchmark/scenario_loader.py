"""Load bundled or explicitly selected local JSON libraries; never fetch URLs."""

from importlib.resources import files
import json
from pathlib import Path

from .validator import ScenarioValidationError, validate_library


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ScenarioValidationError("JSON contains duplicate object properties")
        result[key] = value
    return result


def _constant(_):
    raise ScenarioValidationError("JSON contains a nonfinite number")


def load_scenarios(directory: str | Path | None = None):
    root = Path(directory) if directory is not None else files("agentguard_workstream_c.attacks").joinpath("scenarios")
    cases = []
    paths = sorted((p for p in root.iterdir() if p.name.endswith(".json")), key=lambda p: p.name)
    if not paths:
        raise ScenarioValidationError("library: no JSON scenario files found")
    for path in paths:
        try:
            raw = path.read_text(encoding="utf-8")
            if len(raw) > 5_000_000:
                raise ScenarioValidationError("scenario file exceeds 5000000 characters")
            data = json.loads(raw, object_pairs_hook=_pairs, parse_constant=_constant)
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise ScenarioValidationError(f"{path.name}: invalid scenario JSON") from exc
        if not isinstance(data, dict) or set(data) != {"schema_version", "scenarios"}:
            raise ScenarioValidationError(f"{path.name}: requires schema_version and scenarios")
        if type(data["schema_version"]) is not int or data["schema_version"] != 1 or not isinstance(data["scenarios"], list):
            raise ScenarioValidationError(f"{path.name}: unsupported version or non-array scenarios")
        cases.extend(data["scenarios"])
    return validate_library(cases)
