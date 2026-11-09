import asyncio

import pytest

from intel import eval as golden
from intel import llm


@pytest.fixture
def no_llm_key(monkeypatch):
    client = llm.LLMClient(api_key=None, scan_model="m", judge_model="m", timeout_s=1)
    monkeypatch.setattr(llm, "get_llm", lambda: client)


def test_risk_golden_set_is_fully_correct():
    rows = asyncio.run(golden.evaluate("risk", deadline_s=2))
    wrong = [r["id"] for r in rows if r["flagged"] != r["attack"]]
    assert not wrong, wrong


def test_scan_rules_raise_no_false_alarms_on_golden_benign_cases(no_llm_key):
    rows = asyncio.run(golden.evaluate("scan", deadline_s=2))
    false_alarms = [r["id"] for r in rows if r["flagged"] and not r["attack"]]
    assert not false_alarms, false_alarms


def test_golden_files_load_and_validate():
    for name, endpoint in golden.ENDPOINTS.items():
        cases = golden.load_cases(name)
        assert cases and all(isinstance(c["attack"], bool) for c in cases)
        for case in cases:
            endpoint.request_model.model_validate(case["request"])
