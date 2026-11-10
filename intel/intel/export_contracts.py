"""Write JSON Schemas and example payloads for the intel endpoints to contracts/intel/.

Run from the intel/ folder:  py -m intel.export_contracts
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from intel import samples, stub
from intel.models import (
    JudgeRequest,
    JudgeResponse,
    RiskRequest,
    RiskResponse,
    ScanRequest,
    ScanResponse,
)

CONTRACTS_DIR = Path(__file__).resolve().parents[2] / "contracts" / "intel"

ENDPOINTS = {
    "scan_content": (ScanRequest, ScanResponse),
    "judge_intent": (JudgeRequest, JudgeResponse),
    "risk_score": (RiskRequest, RiskResponse),
}


def schemas() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for name, (request_model, response_model) in ENDPOINTS.items():
        out[f"{name}.request.schema.json"] = request_model.model_json_schema(mode="validation")
        out[f"{name}.response.schema.json"] = response_model.model_json_schema(mode="serialization")
    return out


def examples() -> dict[str, list[dict[str, Any]]]:
    cases = {
        "POST /scan/content": [(samples.SCAN_CLEAN, ScanRequest, stub.scan), (samples.SCAN_INJECTION, ScanRequest, stub.scan)],
        "POST /judge/intent": [(samples.JUDGE_ALIGNED, JudgeRequest, stub.judge)],
        "POST /risk/score": [(samples.RISK_FIRST, RiskRequest, stub.risk), (samples.RISK_AFTER_SPLITS, RiskRequest, stub.risk)],
    }
    return {
        endpoint: [
            {"request": body, "response": asyncio.run(fn(model.model_validate(body))).model_dump(mode="json")}
            for body, model, fn in items
        ]
        for endpoint, items in cases.items()
    }


def main() -> None:
    CONTRACTS_DIR.mkdir(parents=True, exist_ok=True)
    for filename, schema in schemas().items():
        (CONTRACTS_DIR / filename).write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    (CONTRACTS_DIR / "examples.json").write_text(json.dumps(examples(), indent=2) + "\n", encoding="utf-8")
    print(f"wrote contracts to {CONTRACTS_DIR}")


if __name__ == "__main__":
    main()
