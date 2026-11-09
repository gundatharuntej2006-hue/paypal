import asyncio
import json

import jsonschema
import pytest

from intel import llm, samples
from intel.export_contracts import CONTRACTS_DIR, schemas
from intel.main import app, run_signal
from intel.models import ScanResponse

ENDPOINTS = [
    ("/scan/content", "scan_content", samples.SCAN_CLEAN),
    ("/judge/intent", "judge_intent", samples.JUDGE_ALIGNED),
    ("/risk/score", "risk_score", samples.RISK_FIRST),
]



@pytest.mark.parametrize("path,name,body", ENDPOINTS)
def test_stub_responses_match_contract(client_for, path, name, body):
    resp = client_for().post(path, json=body)
    assert resp.status_code == 200
    jsonschema.validate(resp.json(), schemas()[f"{name}.response.schema.json"])


def test_stub_flags_obvious_injection(client_for):
    out = client_for().post("/scan/content", json=samples.SCAN_INJECTION).json()
    assert out["verdict"] == "injection"
    assert out["snippets"][0]["ref"] == "page_18"


def test_stub_risk_rises_with_repeat_payments(client_for):
    client = client_for()
    first = client.post("/risk/score", json=samples.RISK_FIRST).json()["score"]
    later = client.post("/risk/score", json=samples.RISK_AFTER_SPLITS).json()["score"]
    assert later > first


@pytest.mark.parametrize("path,body", [("/scan/content", samples.SCAN_CLEAN), ("/judge/intent", samples.JUDGE_ALIGNED)])
def test_without_llm_key_scan_and_judge_never_pass(client_for, monkeypatch, path, body):
    no_key = llm.LLMClient(api_key=None, scan_model="m", judge_model="m", timeout_s=1)
    monkeypatch.setattr(llm, "get_llm", lambda: no_key)
    out = client_for(mode="live").post(path, json=body).json()
    assert (out["verdict"], out["reason"]) == ("unknown", "no_llm_key")
    assert out["latency_ms"] < 500


def test_live_scan_catches_obvious_injection_without_llm(client_for):
    out = client_for(mode="live").post("/scan/content", json=samples.SCAN_INJECTION).json()
    assert (out["verdict"], out["stage"]) == ("injection", "heuristic")
    assert out["snippets"][0]["ref"] == "page_18"


def test_live_risk_returns_a_score(client_for):
    out = client_for(mode="live").post("/risk/score", json=samples.RISK_FIRST).json()
    assert out["status"] == "ok" and 0 <= out["score"] <= 1


def test_run_signal_turns_failures_into_unknown():
    async def slow(_):
        await asyncio.sleep(1)

    async def broken(_):
        raise RuntimeError("boom")

    def unknown(reason):
        return ScanResponse.unknown(reason, version="test")

    timed_out = asyncio.run(run_signal(slow, None, unknown, deadline_s=0.05))
    failed = asyncio.run(run_signal(broken, None, unknown, deadline_s=1))
    assert (timed_out.verdict, timed_out.reason) == ("unknown", "timeout")
    assert (failed.verdict, failed.reason) == ("unknown", "internal_error")


def test_token_required_when_configured(client_for):
    client = client_for(api_token="s3cret")
    assert client.post("/scan/content", json=samples.SCAN_CLEAN).status_code == 401
    ok = client.post("/scan/content", json=samples.SCAN_CLEAN, headers={"X-Internal-Token": "s3cret"})
    assert ok.status_code == 200


def test_health_needs_no_token(client_for):
    assert client_for(api_token="s3cret").get("/health").json()["status"] == "ok"


def test_naive_timestamps_are_rejected(client_for):
    body = {**samples.RISK_FIRST, "now": "2026-11-10T10:00:00"}
    assert client_for().post("/risk/score", json=body).status_code == 422


def test_contract_files_are_up_to_date():
    for filename, schema in schemas().items():
        on_disk = json.loads((CONTRACTS_DIR / filename).read_text(encoding="utf-8"))
        assert on_disk == schema, f"{filename} is stale: run py -m intel.export_contracts"
