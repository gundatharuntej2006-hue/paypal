"""Full-stack plumbing with offline model/SDK doubles, never benchmark evidence."""

import ast
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
import json
import inspect
import socket
from urllib.request import OpenerDirector
from uuid import NAMESPACE_URL, uuid5

import pytest

from agentguard_workstream_c.agent.anthropic_model import AnthropicShoppingModel
from agentguard_workstream_c.agent.model import DeterministicDemoModel, ShoppingModelError
from agentguard_workstream_c.agent.state import ModelDecision, Selection, SelectionRationale
from agentguard_workstream_c.benchmark.full_stack_executor import (
    FullStackExecutionReport, FullStackReportError, GatewayExecution, FullStackCaseRecord,
    SubmissionConfiguration, _collect_evidence, _safe_audit_event, _validate_candidate, run_full_stack,
)
from agentguard_workstream_c.benchmark import full_stack_executor
from agentguard_workstream_c.benchmark.llm_executor import ModelExecution, run_llm_only
from agentguard_workstream_c.benchmark.materializer import (
    ModelConfiguration, inventory_hash, materialize_library,
)
from agentguard_workstream_c.benchmark.scenario_loader import load_scenarios
from agentguard_workstream_c.sdk import GuardClient
from agentguard_workstream_c.sdk.models import DecisionResponse
from agentguard_workstream_c.sdk.transport import TransportResponse, UrllibTransport

CONFIG = ModelConfiguration("anthropic", "offline-model")
SCENARIOS = load_scenarios()
CASES = materialize_library(SCENARIOS, CONFIG)
ELIGIBLE = tuple(c for c in CASES if c.capability == "shopping_agent")


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    attempts = []
    def forbidden(*args, **kwargs):
        attempts.append(True)
        raise AssertionError("full-stack tests forbid network, providers and fallback")
    for name in ("create_connection", "getaddrinfo", "gethostbyname", "gethostbyname_ex"):
        monkeypatch.setattr(socket, name, forbidden)
    for name in ("connect", "connect_ex"):
        monkeypatch.setattr(socket.socket, name, forbidden)
    monkeypatch.setattr(OpenerDirector, "open", forbidden)
    monkeypatch.setattr(AnthropicShoppingModel, "select", forbidden)
    monkeypatch.setattr(DeterministicDemoModel, "select", forbidden)
    yield
    assert attempts == []


class FakeModel:
    name = "offline_fake"
    def __init__(self, outcome="purchase_proposal", failure=None, invalid=None):
        self.outcome, self.failure, self.invalid = outcome, failure, invalid
        self.requests = []

    def select(self, request):
        case = ELIGIBLE[len(self.requests)]
        assert request == case.agent_input()
        self.requests.append(request)
        if self.failure:
            raise self.failure
        if self.invalid == "object":
            return object()
        rationale = SelectionRationale("Offline selection", "Matches supplied task")
        if self.outcome != "purchase_proposal":
            return ModelDecision(self.outcome, rationale=rationale)
        p = next(p for p in request.product_candidates
                 if (p.merchant_id, p.product_id) == (case.merchant.merchant_id, case.product.product_id))
        ref = next(page.source_ref for page in request.pages if page.url == p.url)
        selection = Selection(p.merchant_id if self.invalid != "merchant" else "invented-merchant",
                              p.product_id if self.invalid != "product" else "invented-product",
                              rationale.summary, (ref if self.invalid != "citation" else "invented-source",), rationale)
        return ModelDecision("purchase_proposal", selection, rationale)


class FakeTransport:
    def __init__(self, decision="allow", change=None, raw=None, status=200, failure=None):
        self.calls = []
        self.decision, self.change, self.raw = decision, change, raw
        self.status, self.failure = status, failure

    def send(self, **call):
        self.calls.append(call)
        if self.failure:
            raise self.failure
        if self.raw is not None:
            return TransportResponse(self.status, self.raw)
        body = json.loads(call["body"])
        payment = "pay_" + body["mandate"]["mandate_id"]
        checks = {"mandate": "pass", "injection": "pass", "intent": "pass", "risk": "pass"}
        if self.decision == "block":
            checks["injection"] = "flagged"
        data = {"payment_id": payment, "decision": self.decision, "risk_score": 0.1,
                "reasons": ["prompt_injection_detected"] if self.decision == "block" else ["offline_example"],
                "checks": checks, "audit_event_id": "evt_" + payment, "dry_run": True}
        if self.decision == "step_up":
            data["step_up_token"] = "fake-stepup-do-not-publish"
        if self.change:
            self.change(data)
        return TransportResponse(self.status, json.dumps(data).encode())


def submission(case):
    i = json.loads(case.declared_inputs_json)
    return SubmissionConfiguration({
        "mandate_id": "offline_" + case.scenario_id, "principal_id": "offline-principal",
        "agent_id": "offline-agent", "task": case.original_task, "currency": i["currency"],
        "max_total": i["budget"], "max_per_txn": i["max_per_txn"],
        "valid_from": "2026-11-01T00:00:00Z", "expires_at": "2026-12-01T00:00:00Z",
        "nonce": "offline_nonce_" + case.scenario_id,
        "public_key": "opaque-fake-public-key", "signature": "opaque-fake-signature",
        "allowed_merchants": i["allowed_merchants"],
    }, "offline-agent", str(uuid5(NAMESPACE_URL, case.scenario_id)))


def run(model=None, transport=None, **options):
    transport = FakeTransport() if transport is None else transport
    gateway = GuardClient("https://gateway.invalid", payment_submission_enabled=True,
                          gateway_token="offline-fake-token", transport=transport)
    kwargs = dict(model=FakeModel() if model is None else model, model_configuration=CONFIG,
                  gateway=gateway, offline_transport=transport, submission_for=submission,
                  allow_simulated_submission=True)
    kwargs.update(options)
    return run_full_stack(SCENARIOS, **kwargs), transport


@pytest.mark.parametrize("decision", ["allow", "step_up", "block"])
def test_valid_gateway_decisions_and_paired_inputs(decision):
    model = FakeModel()
    report, transport = run(model, FakeTransport(decision), paired_inputs=CASES)
    baseline = run_llm_only(SCENARIOS, model=FakeModel(), model_configuration=CONFIG, provenance="test_double")
    assert report.input_sha256 == baseline.input_sha256 == inventory_hash(CASES)
    assert tuple(r.case for r in report.records) == tuple(r.case for r in baseline.records)
    assert model.requests == [c.agent_input() for c in ELIGIBLE]
    assert len(transport.calls) == len(ELIGIBLE)
    for r in report.records:
        if r.case.capability != "shopping_agent":
            assert r.model_execution is None and r.candidate is None and r.gateway is None
            assert r.outcome == "unresolved" and r.unresolved_reason == r.case.reason
            continue
        assert r.model_execution.model_output.outcome == "purchase_proposal"
        assert r.gateway.decision.decision == decision and r.gateway.decision.dry_run is True
        assert r.gateway.decision.audit_event_id and r.outcome == "observed"
        assert r.gateway.intelligence is None
        assert r.gateway.actual_catching_layer == ("injection" if decision == "block" else None)
        assert r.candidate.amount == r.candidate.unit_price * r.candidate.quantity
        assert r.gateway.latency_ms >= 0 and not r.gateway.outcome_unknown
    for call, case in zip(transport.calls, ELIGIBLE):
        body = json.loads(call["body"])
        assert call["url"] == "https://gateway.invalid/v1/payments/intents"
        assert body["mandate"] == submission(case).mandate
        assert body["mandate"]["task"] == case.original_task
        assert body["idempotency_key"] == submission(case).idempotency_key
        assert body["dry_run"] is True and body["agent_id"] == "offline-agent"
        assert body["source_contexts"] == [{k: v for k, v in p.context.to_dict().items() if v is not None}
                                            for p in case.pages]
        assert [c["html"] for c in body["source_contexts"]] == [p.html for p in case.pages]
        assert "expected_catching_layer" not in call["body"].decode()
    exported = report.to_dict()
    assert exported["synthetic"] and exported["provenance"] == "test_double"
    assert exported["capability_counts"] == {"shopping_agent": 19, "future_gateway": 44, "unsupported": 17}
    text = json.dumps(exported, allow_nan=False)
    assert "fake-stepup-do-not-publish" not in text and "offline-fake-token" not in text
    assert "opaque-fake-signature" not in text
    assert "block_rate" not in exported and "ablation" not in exported


@pytest.mark.parametrize("field,value,remove", [
    ("dry_run", True, True), ("dry_run", False, False), ("dry_run", "true", False),
    ("audit_event_id", "", False), ("audit_event_id", None, True),
    ("checks", None, False), ("decision", "approved", False),
])
def test_invalid_response_never_becomes_observed_or_retried(field, value, remove):
    def change(data):
        if remove: del data[field]
        else: data[field] = value
    report, transport = run(transport=FakeTransport(change=change))
    for r in report.records:
        if r.gateway:
            assert r.gateway.decision is None and r.gateway.error_code == "InvalidResponseError"
            assert r.outcome == "unknown" and r.gateway.outcome_unknown is True
    assert len(transport.calls) == len(ELIGIBLE)
    assert all(json.loads(c["body"])["dry_run"] is True for c in transport.calls)


@pytest.mark.parametrize("status", [401, 403, 409, 502])
def test_authorization_and_http_errors_remain_distinct(status):
    report, transport = run(transport=FakeTransport(status=status, raw=b"SECRET server diagnostic"))
    assert len(transport.calls) == len(ELIGIBLE)
    assert all(r.gateway.error_code == "http_" + str(status) and r.outcome == "unknown"
               for r in report.records if r.gateway)
    assert "SECRET" not in json.dumps(report.to_dict())


@pytest.mark.parametrize("raw", [b"", b"not json", b"[]", b'{"decision":"allow","decision":"block"}'])
def test_malformed_gateway_json(raw):
    report, transport = run(transport=FakeTransport(raw=raw))
    assert all(r.gateway.decision is None and r.gateway.error_code == "InvalidResponseError"
               for r in report.records if r.gateway)
    assert len(transport.calls) == len(ELIGIBLE)


def test_ambiguous_timeout_no_retry_key_rotation_or_live_fallback():
    report, transport = run(transport=FakeTransport(failure=TimeoutError("SECRET timeout")))
    assert len(transport.calls) == len(ELIGIBLE)
    for r, call in zip((r for r in report.records if r.gateway), transport.calls):
        assert r.gateway.error_code == "GatewayTimeoutError" and r.outcome == "unknown"
        assert r.gateway.decision is None and r.gateway.outcome_unknown
        assert r.gateway.idempotency_key == submission(r.case).idempotency_key
        assert json.loads(call["body"])["dry_run"] is True
    assert "SECRET" not in json.dumps(report.to_dict())


@pytest.mark.parametrize("outcome", ["refusal", "no_match", "invalid_response"])
def test_nonpurchase_model_outcome_is_not_gateway_block(outcome):
    report, transport = run(FakeModel(outcome))
    assert not transport.calls
    for r in report.records:
        if r.model_execution:
            assert r.model_execution.outcome == outcome and r.candidate is None and r.gateway is None
            assert r.outcome == "unresolved" and r.unresolved_reason == outcome


@pytest.mark.parametrize("failure,code", [(ShoppingModelError("rate_limit"), "rate_limit"),
    (ShoppingModelError("timeout"), "timeout"), (RuntimeError("SECRET provider body"), "provider")])
def test_provider_failure_is_preserved_without_success_or_fallback(failure, code):
    report, transport = run(FakeModel(failure=failure))
    assert not transport.calls
    assert all(r.model_execution.error_code == code and r.model_execution.model_output is None
               and r.gateway is None and r.outcome == "unresolved" for r in report.records if r.model_execution)
    assert "SECRET" not in json.dumps(report.to_dict())


@pytest.mark.parametrize("invalid", ["merchant", "product", "citation", "object"])
def test_invalid_model_output_never_submitted(invalid):
    report, transport = run(FakeModel(invalid=invalid))
    assert not transport.calls
    assert all(r.gateway is None and r.candidate is None for r in report.records)
    assert all(r.validation_error == "invalid_candidate" for r in report.records
               if r.model_execution and invalid != "object")


@pytest.mark.parametrize("field,value", [("unit_price", Decimal("0.01")), ("currency", "EUR"),
                                        ("product_name", "Invented product"), ("merchant_name", "Invented merchant")])
def test_catalog_snapshot_is_authoritative(field, value):
    case = next(c for c in ELIGIBLE if c.scenario_id == "benign_01")
    p = next(p for p in case.product_candidates if p.product_id == case.product.product_id
             and p.merchant_id == case.merchant.merchant_id)
    altered = replace(case, product_candidates=tuple(replace(x, **{field: value}) if x == p else x
                                                    for x in case.product_candidates))
    ref = next(page.context.ref for page in case.pages if page.url == p.url)
    selection = Selection(p.merchant_id, p.product_id, "offline", (ref,))
    execution = ModelExecution("purchase_proposal", selection, 0)
    with pytest.raises(ValueError): _validate_candidate(altered, execution, object())


@pytest.mark.parametrize("problem", ["missing", "live", "task", "agent", "key", "exception"])
def test_submission_configuration_fails_closed(problem):
    def config(case):
        original = submission(case)
        if problem == "missing": return None
        if problem == "live": return replace(original, dry_run=False)
        if problem == "task": return replace(original, mandate={**original.mandate, "task": "rewritten"})
        if problem == "agent": return replace(original, agent_id="not-the-mandate-agent")
        if problem == "key": return replace(original, idempotency_key="not-a-uuid")
        raise RuntimeError("SECRET configuration")
    report, transport = run(submission_for=config)
    assert not transport.calls
    assert all(r.validation_error == "submission_configuration" and r.candidate is not None
               and r.gateway is None for r in report.records if r.model_execution)
    assert "SECRET" not in json.dumps(report.to_dict())


@pytest.mark.parametrize("options", [{"allow_simulated_submission": False},
    {"provenance": "genuine_provider"}, {"provenance": "genuine"}])
def test_execution_requires_offline_opt_in(options):
    model = FakeModel()
    with pytest.raises(ValueError): run(model, **options)
    assert not model.requests


def test_default_network_transport_and_disabled_sdk_rejected_before_model():
    model, transport = FakeModel(), FakeTransport()
    for gateway, injected in [
        (GuardClient("https://gateway.invalid"), transport),
        (GuardClient("https://gateway.invalid", payment_submission_enabled=True, gateway_token="fake", transport=transport), FakeTransport()),
    ]:
        with pytest.raises(ValueError): run(model, gateway=gateway, offline_transport=injected)
    assert not model.requests and not transport.calls


def _assert_network_transport_rejected(executor):
    # All other guards are satisfied. no_match keeps a deliberately mutated
    # executor from ever reaching send(), even when the network guard is removed.
    model = FakeModel(outcome="no_match")
    gateway = GuardClient("https://gateway.invalid", payment_submission_enabled=True, gateway_token="fake")
    assert isinstance(gateway._transport, UrllibTransport)
    with pytest.raises(ValueError, match="^network transport is forbidden for offline full-stack execution$"):
        executor(SCENARIOS, model=model, model_configuration=CONFIG, gateway=gateway,
                 offline_transport=gateway._transport, submission_for=submission, allow_simulated_submission=True)
    assert not model.requests


def test_exact_sdk_network_transport_is_explicitly_forbidden():
    _assert_network_transport_rejected(run_full_stack)


def _without_guard(function, condition):
    """In-memory mutation only: never modify source or remove socket test guards."""
    tree = ast.parse(inspect.getsource(function))
    class Remove(ast.NodeTransformer):
        removed = 0
        def visit_If(self, node):
            if ast.unparse(node.test) == condition:
                self.removed += 1
                return None
            return self.generic_visit(node)
    transformer = Remove()
    tree = transformer.visit(tree)
    assert transformer.removed == 1
    namespace = vars(full_stack_executor).copy()
    exec(compile(ast.fix_missing_locations(tree), "<offline-in-memory-guard-mutation>", "exec"), namespace)
    return namespace[function.__name__]


def test_network_guard_removal_is_detected_without_socket_attempts():
    mutant = _without_guard(run_full_stack, "isinstance(offline_transport, UrllibTransport)")
    with pytest.raises(pytest.fail.Exception, match="DID NOT RAISE"):
        _assert_network_transport_rejected(mutant)


def test_real_provider_adapter_cannot_enter_test_double_execution():
    with pytest.raises(ValueError): run(AnthropicShoppingModel("offline-model"))


def test_paired_mismatch_fails_before_model_and_sdk():
    model, transport = FakeModel(), FakeTransport()
    changed = (replace(CASES[0], original_task=CASES[0].original_task + " changed"), *CASES[1:])
    with pytest.raises(ValueError, match="paired experiment inputs differ"):
        run(model, transport, paired_inputs=changed)
    assert not model.requests and not transport.calls


def audit_events(case, decision):
    common = {"payment_id": decision.payment_id, "source": "intel"}
    events = [{**common, "id": "scan_" + decision.payment_id, "type": "scan.completed",
               "data": {"version": "offline-scan", "latency_ms": 11}},
              {**common, "id": "judge_" + decision.payment_id, "type": "judge.completed",
               "data": {"version": "offline-judge", "latency_ms": 23}},
              {**common, "id": "risk_" + decision.payment_id, "type": "risk.scored",
               "data": {"version": "offline-risk", "latency_ms": 5}}]
    events.append({**common, "source": "gateway", "id": decision.audit_event_id, "type": "decision.made",
                   "data": {"decision": decision.decision, "dry_run": True, "reasons": list(decision.reasons)}})
    return events


def test_observed_metadata_preserved_separately_from_sdk_latency():
    report, _ = run(audit_events_for=audit_events)
    for r in report.records:
        if r.gateway:
            e = r.gateway.intelligence
            assert (e.scan.version, e.judge.version, e.risk.version) == ("offline-scan", "offline-judge", "offline-risk")
            assert (e.scan.latency_ms, e.judge.latency_ms, e.risk.latency_ms) == (11, 23, 5)
            assert e.active_risk_policy_version == "offline-risk"
            assert len(json.loads(r.gateway.audit_events_json)) == 4 and not r.gateway.evidence_error
    assert report.to_dict()["methodology"]["risk_calibration"]["reference_policy_version"] == "risk_rules_v2"


def test_absent_signal_metadata_stays_null():
    def events(case, decision):
        result = audit_events(case, decision)
        for e in result[:3]: e["data"] = {}
        return result
    report, _ = run(audit_events_for=events)
    for r in report.records:
        if r.gateway:
            e = r.gateway.intelligence
            assert all(s.version is None and s.latency_ms is None for s in (e.scan, e.judge, e.risk))
            assert e.active_risk_policy_version is None


@pytest.mark.parametrize("problem", ["demo_source", "synthetic", "demo_flag", "wrong_payment", "duplicate_signal",
                                     "negative_latency", "wrong_decision", "wrong_audit_id", "live", "exception"])
def test_bad_audit_evidence_cannot_supply_metadata_or_attribution(problem):
    def events(case, decision):
        result = audit_events(case, decision)
        if problem == "demo_source": result[0]["source"] = "synthetic_demo"
        elif problem == "synthetic": result[0]["data"]["is_synthetic"] = True
        elif problem == "demo_flag": result[0]["data"]["demo_mode"] = True
        elif problem == "wrong_payment": result[0]["payment_id"] = "other"
        elif problem == "duplicate_signal": result.append(result[0])
        elif problem == "negative_latency": result[0]["data"]["latency_ms"] = -1
        elif problem == "wrong_decision": result[-1]["data"]["decision"] = "block"
        elif problem == "wrong_audit_id": result[-1]["id"] = "unrelated-audit-id"
        elif problem == "live": result[-1]["data"]["dry_run"] = False
        else: raise RuntimeError("SECRET evidence body")
        return result
    report, _ = run(audit_events_for=events)
    for r in report.records:
        if r.gateway:
            assert r.gateway.decision.decision == "allow" and r.outcome == "observed"
            assert r.gateway.evidence_error == "invalid_audit_evidence"
            assert r.gateway.intelligence is None and r.gateway.actual_catching_layer is None
            assert json.loads(r.gateway.audit_events_json) == []
    assert "SECRET" not in json.dumps(report.to_dict())


def test_constructed_counterfactuals_remain_separate_and_unexecuted():
    report, _ = run()
    for r in report.records:
        if r.case.constructed_mutation:
            assert r.case.constructed_mutation.provenance == "harness_constructed"
            assert r.case.capability == "future_gateway" and r.model_execution is None
            assert r.candidate is None and r.gateway is None and r.outcome == "unresolved"


def test_invalid_inventory_fails_before_model_or_gateway():
    model, transport = FakeModel(), FakeTransport()
    gateway = GuardClient("https://gateway.invalid", payment_submission_enabled=True,
                          gateway_token="fake", transport=transport)
    with pytest.raises(ValueError):
        run_full_stack(SCENARIOS[:-1], model=model, model_configuration=CONFIG, gateway=gateway,
                       offline_transport=transport, submission_for=submission, allow_simulated_submission=True)
    assert not model.requests and not transport.calls


@pytest.mark.parametrize("difference", ["evidence", "configuration", "inventory"])
def test_other_paired_input_mismatches_fail_before_execution(difference):
    model, transport = FakeModel(), FakeTransport()
    case = CASES[0]
    if difference == "evidence":
        page = replace(case.pages[0], html=case.pages[0].html + "<!-- changed -->")
        changed = (replace(case, pages=(page, *case.pages[1:])), *CASES[1:])
    elif difference == "configuration":
        changed = (replace(case, model_configuration=replace(CONFIG, max_tokens=100)), *CASES[1:])
    else:
        changed = CASES[:-1]
    with pytest.raises(ValueError): run(model, transport, paired_inputs=changed)
    assert not model.requests and not transport.calls


@pytest.mark.parametrize("provenance,synthetic", [
    ("genuine", True), ("genuine", False), ("genuine_provider", True), ("synthetic", True),
    ("test_double", False), ("test_double", 1), (None, True), ("", True),
])
def test_report_rejects_invalid_provenance_at_construction(provenance, synthetic):
    with pytest.raises(FullStackReportError, match="require provenance='test_double' and synthetic=True"):
        FullStackExecutionReport((), "offline_fake", "0" * 64, provenance, synthetic)


@pytest.mark.parametrize("name,value", [("provenance", "genuine"), ("provenance", "genuine_provider"),
                                       ("synthetic", False), ("synthetic", 1)])
def test_report_rechecks_provenance_after_frozen_mutation(name, value):
    report = FullStackExecutionReport((), "offline_fake", "0" * 64)
    object.__setattr__(report, name, value)
    with pytest.raises(FullStackReportError): report.to_dict()


def test_valid_empty_report_is_unambiguously_synthetic():
    report = FullStackExecutionReport((), "offline_fake", "0" * 64).to_dict()
    assert report["provenance"] == "test_double" and report["synthetic"] is True
    assert report["records"] == []


def _decision():
    return DecisionResponse.from_dict({
        "payment_id": "pay_retention_test", "decision": "block", "risk_score": 0.9,
        "reasons": ["risk_score_exceeds_block_threshold"], "dry_run": True,
        "checks": {"mandate": "pass", "injection": "pass", "intent": "pass", "risk": "flagged"},
        "audit_event_id": "evt_retention_test",
    })


def _assert_distinct_duplicate_signals_rejected(collector):
    decision = _decision()
    events = audit_events(None, decision)
    duplicate = deepcopy(events[0])
    duplicate["id"] = "evt_distinct_duplicate_scan"
    events.append(duplicate)
    assert len({e["id"] for e in events}) == len(events)
    evidence, retained, error, layer = collector(None, decision, lambda *_: events)
    assert error == "invalid_audit_evidence"
    assert evidence is None and json.loads(retained) == [] and layer is None


def test_distinct_event_ids_with_duplicate_signal_evidence_are_rejected():
    _assert_distinct_duplicate_signals_rejected(_collect_evidence)


def test_removing_duplicate_signal_guard_is_detected():
    mutant = _without_guard(_collect_evidence, "len(matches) > 1")
    with pytest.raises(AssertionError): _assert_distinct_duplicate_signals_rejected(mutant)


def test_audit_allowlist_removes_sensitive_fields_at_all_levels_and_preserves_evidence():
    decision = _decision()
    events = audit_events(None, decision)
    sensitive = {"step_up_token": "CANARY_STEP_UP", "Authorization": "CANARY_AUTH",
                 "api_key": "CANARY_API", "X-Internal-Token": "CANARY_INTERNAL",
                 "signature": "CANARY_SIGNATURE", "private_key": "CANARY_PRIVATE",
                 "prompt": "CANARY_PROMPT", "html": "CANARY_HTML",
                 "mandate": {"signature": "CANARY_NESTED_SIGNATURE"}}
    for event in events:
        event.update(deepcopy(sensitive))
        event["created_at"] = "2026-11-11T10:00:00Z"
        event["data"].update(deepcopy(sensitive))
        event["data"]["unreviewed"] = {"nested": [deepcopy(sensitive)]}
        event["data"]["rationale"] = "CANARY_RATIONALE"
        event["data"]["snippets"] = [{"text": "CANARY_SOURCE_HTML", "nested": sensitive}]
    checks = {**as_checks(decision), "unreviewed": {"step_up_token": "CANARY_CHECK_TOKEN"}}
    events[-1]["data"]["checks"] = checks
    events[-1]["data"]["reasons"] += ["CANARY_FREE_FORM_REASON", {"nested": sensitive}]
    evidence, raw, error, layer = _collect_evidence(None, decision, lambda *_: events)
    assert error is None and evidence.scan.version == "offline-scan" and evidence.scan.latency_ms == 11
    assert evidence.risk.version == "offline-risk" and layer == "risk"
    assert "CANARY" not in raw
    retained = json.loads(raw)
    for event in retained:
        assert set(event) == {"id", "payment_id", "type", "source", "created_at", "data"}
        assert event["created_at"] == "2026-11-11T10:00:00Z"
    assert retained[-1]["data"]["checks"] == as_checks(decision)
    assert retained[-1]["data"]["reasons"] == ["risk_score_exceeds_block_threshold"]
    # Reprojection during report serialization also covers manually inserted or
    # later-mutated audit JSON, rather than trusting the collection stage alone.
    contaminated = deepcopy(retained)
    contaminated[0]["data"]["step_up_token"] = "CANARY_LATE_TOKEN"
    operation = GatewayExecution(decision, None, False, 0, "0" * 64, "offline-key",
                                 intelligence=evidence, audit_events_json=json.dumps(contaminated))
    report = FullStackExecutionReport((FullStackCaseRecord(CASES[0], gateway=operation),), "offline_fake", "0" * 64)
    exported = report.to_dict()["records"][0]["gateway_operation"]
    assert "CANARY" not in json.dumps(exported)
    assert exported["intelligence"]["scan"]["version"] == "offline-scan"


def as_checks(decision):
    return {name: getattr(decision.checks, name) for name in ("mandate", "injection", "intent", "risk")}


@pytest.mark.parametrize("kind,data,expected", [
    ("scan.completed", {"verdict": "injection", "confidence": 0.8, "stage": "llm", "version": "scan_v1", "latency_ms": 0},
     {"verdict": "injection", "confidence": 0.8, "stage": "llm", "version": "scan_v1", "latency_ms": 0}),
    ("judge.completed", {"verdict": "misaligned", "confidence": 0.9, "rationale": "DO_NOT_RETAIN"},
     {"verdict": "misaligned", "confidence": 0.9}),
    ("risk.scored", {"status": "unknown", "score": None, "reason": "DO_NOT_RETAIN", "factors": [{"detail": "DO_NOT_RETAIN"}]},
     {"status": "unknown", "score": None}),
    ("mandate.failed", {"reason": "mandate_invalid", "mandate": {"task": "DO_NOT_RETAIN"}}, {"reason": "mandate_invalid"}),
    ("mandate.failed", {"reason": "caller secret in arbitrary failure description"}, {}),
])
def test_reviewed_event_data_fields_only(kind, data, expected):
    source = "gateway" if kind == "mandate.failed" else "intel"
    event = {"id": "evt_reviewed", "payment_id": "pay_reviewed", "type": kind, "source": source, "data": data}
    assert _safe_audit_event(event)["data"] == expected


@pytest.mark.parametrize("field,value", [("version", {"step_up_token": "CANARY"}), ("version", "unsafe version text"),
                                       ("latency_ms", -1), ("latency_ms", True), ("confidence", float("nan"))])
def test_unreviewed_nested_or_invalid_metadata_values_fail_closed(field, value):
    event = {"id": "evt_invalid", "payment_id": "pay_invalid", "source": "intel", "type": "scan.completed",
             "data": {field: value}}
    with pytest.raises(FullStackReportError): _safe_audit_event(event)
