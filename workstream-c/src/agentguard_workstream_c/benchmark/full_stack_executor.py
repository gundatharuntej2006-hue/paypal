"""Offline-testable SDK orchestration; no genuine executor or CLI registration.

Injected models, transports and callbacks are trusted Python, not a sandbox.
Only explicitly labeled test doubles are accepted. No signer, enrollment,
HTTP evidence collector, lifecycle operation or provider is constructed here.
"""

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
import json
import math
import re
from time import perf_counter

from ..agent.model import ShoppingModel
from ..agent.nodes import ShoppingNodes
from ..agent.state import MerchantCandidate, ModelDecision, PurchaseProposal, Selection
from ..sdk import GuardClient, intent_from_proposal
from ..sdk.exceptions import GatewayError, GatewayHTTPError, ValidationError
from ..sdk.models import DecisionResponse
from ..sdk.transport import Transport, UrllibTransport
from .attribution import attribute_block
from .evidence import IntelligenceEvidence, methodology_metadata
from .llm_executor import ModelExecution, run_llm_only
from .materializer import ModelConfiguration, RuntimeCase, inventory_hash, materialize_library, verify_paired_inputs


class FullStackReportError(ValueError):
    """An offline report violates its provenance or retention contract."""


# Fixed reason codes in the inspected gateway policy/handler, never free text
# (mandate failure descriptions can contain caller/merchant identity details).
SAFE_REASON_CODES = frozenset({
    "mandate_invalid", "prompt_injection_detected", "intent_drift_detected",
    "injection_scan_unknown", "injection_scan_unrecognized_verdict",
    "intent_judge_unknown", "intent_judge_unrecognized_verdict", "risk_score_unknown",
    "risk_score_exceeds_block_threshold", "risk_score_exceeds_step_up_threshold",
    "fail_closed_step_up_due_to_unknown_signal", "amount_exceeds_mandate_step_up_threshold",
    "mandate_valid", "intent_aligned", "no_injection", "risk_acceptable", "concurrent_budget_cap_exceeded",
})
AUDIT_SOURCES = {"scan.completed": "intel", "judge.completed": "intel", "risk.scored": "intel",
                 "decision.made": "gateway", "mandate.failed": "gateway"}


def _safe_reasons(reasons):
    if not isinstance(reasons, (list, tuple)):
        raise FullStackReportError("reason codes must be a sequence")
    return [reason for reason in reasons if isinstance(reason, str) and reason in SAFE_REASON_CODES]


def _safe_audit_event(event):
    """Project actual Go LedgerEvent/response fields, never arbitrary objects.

    Called at collection AND serialization. Unknown fields are omitted at every
    level. Malformed reviewed fields are rejected rather than coerced.
    """
    if not isinstance(event, Mapping):
        raise FullStackReportError("audit event must be an object")
    result = {}
    for name in ("id", "payment_id"):
        value = event.get(name)
        if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9_.:-]{1,200}", value) is None:
            raise FullStackReportError("invalid audit identifier")
        result[name] = value
    kind = event.get("type")
    if not isinstance(kind, str) or kind not in AUDIT_SOURCES or event.get("source") != AUDIT_SOURCES[kind]:
        raise FullStackReportError("unsupported audit event type or source")
    result.update(type=kind, source=AUDIT_SOURCES[kind])
    if "created_at" in event:
        stamp = event["created_at"]
        if not isinstance(stamp, str) or len(stamp) > 40:
            raise FullStackReportError("invalid audit timestamp")
        try:
            parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        except ValueError:
            raise FullStackReportError("invalid audit timestamp") from None
        if parsed.tzinfo is None:
            raise FullStackReportError("audit timestamp requires an offset")
        result["created_at"] = stamp
    raw = event.get("data")
    if not isinstance(raw, Mapping):
        raise FullStackReportError("audit data must be an object")
    if raw.get("is_synthetic", False) is not False or raw.get("demo_mode", False) is not False:
        raise FullStackReportError("synthetic demo evidence is forbidden")
    data = {}
    enums = {"scan.completed": {"verdict": ("clean", "injection", "unknown"),
                                 "stage": ("heuristic", "llm", "none")},
             "judge.completed": {"verdict": ("aligned", "misaligned", "unknown")},
             "risk.scored": {"status": ("ok", "unknown")},
             "decision.made": {"decision": ("allow", "step_up", "block")}}.get(kind, {})
    for key, allowed in enums.items():
        if key in raw:
            if not isinstance(raw[key], str) or raw[key] not in allowed:
                raise FullStackReportError("invalid audit outcome enum")
            data[key] = raw[key]
    if kind in ("scan.completed", "judge.completed", "risk.scored"):
        for key in ("version", "latency_ms"):
            if key not in raw or raw[key] is None:
                continue  # Absence remains absent/null in IntelligenceEvidence.
            value = raw[key]
            if key == "version" and (not isinstance(value, str)
                    or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,99}", value) is None):
                raise FullStackReportError("unapproved intelligence version format")
            if key == "latency_ms" and (type(value) is not int or value < 0):
                raise FullStackReportError("invalid intelligence latency")
            data[key] = value
    number_fields = {"scan.completed": ("confidence",), "judge.completed": ("confidence",),
                     "risk.scored": ("score",), "decision.made": ("risk_score",)}.get(kind, ())
    for key in number_fields:
        if key not in raw:
            continue
        value = raw[key]
        if value is None and key == "score":
            data[key] = None
        elif type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
            raise FullStackReportError("invalid audit score or confidence")
        else:
            data[key] = value
    if kind == "decision.made":
        if raw.get("dry_run") is not True:
            raise FullStackReportError("decision evidence requires dry_run=true")
        data["dry_run"] = True
        if "reasons" in raw:
            data["reasons"] = _safe_reasons(raw["reasons"])
        if "checks" in raw:
            if not isinstance(raw["checks"], Mapping):
                raise FullStackReportError("audit checks must be an object")
            checks = {}
            for key in ("mandate", "injection", "intent", "risk"):
                if key not in raw["checks"]:
                    continue
                value = raw["checks"][key]
                allowed = ("pass", "fail") if key == "mandate" else ("pass", "flagged", "unknown")
                if not isinstance(value, str) or value not in allowed:
                    raise FullStackReportError("invalid audit check")
                checks[key] = value
            data["checks"] = checks
    elif kind == "mandate.failed" and isinstance(raw.get("reason"), str) and raw["reason"] in SAFE_REASON_CODES:
        data["reason"] = raw["reason"]
    result["data"] = data
    return result


@dataclass(frozen=True)
class SubmissionConfiguration:
    """Externally supplied mandate/key: no signing or ownership claims.

    Secrets belong in the SDK client, never this record or the model input.
    The signed mandate is excluded from repr and report serialization.
    """

    mandate: Mapping = field(repr=False)
    agent_id: str
    idempotency_key: str
    dry_run: bool = True

    def __post_init__(self):
        if self.dry_run is not True or not isinstance(self.mandate, Mapping):
            raise ValueError("an explicit dry-run mandate configuration is required")


@dataclass(frozen=True)
class GatewayExecution:
    decision: DecisionResponse | None
    error_code: str | None
    outcome_unknown: bool
    latency_ms: float
    request_sha256: str
    idempotency_key: str
    intelligence: IntelligenceEvidence | None = None
    audit_events_json: str = "[]"
    evidence_error: str | None = None
    actual_catching_layer: str | None = None


@dataclass(frozen=True)
class FullStackCaseRecord:
    case: RuntimeCase
    model_execution: ModelExecution | None = None
    candidate: PurchaseProposal | None = None
    validation_error: str | None = None
    gateway: GatewayExecution | None = None
    unresolved_reason: str | None = None

    @property
    def outcome(self):
        if self.gateway is not None and self.gateway.decision is not None:
            return "observed"
        if self.gateway is not None and self.gateway.outcome_unknown:
            return "unknown"
        return "unresolved"


@dataclass(frozen=True)
class FullStackExecutionReport:
    records: tuple[FullStackCaseRecord, ...]
    model_name: str
    input_sha256: str
    provenance: str = "test_double"
    synthetic: bool = True

    def __post_init__(self):
        self._validate_provenance()

    def _validate_provenance(self):
        if self.provenance != "test_double" or self.synthetic is not True:
            raise FullStackReportError("offline full-stack reports require provenance='test_double' and synthetic=True")

    def to_dict(self):
        self._validate_provenance()  # Frozen dataclasses can still be bypassed by object.__setattr__.
        records = []
        for r in self.records:
            gateway = None
            if r.gateway is not None:
                g = r.gateway
                d = g.decision
                # Step-up bearer tokens and signed mandates are not report fields.
                gateway = {
                    "decision": None if d is None else {
                        "payment_id": d.payment_id, "decision": d.decision,
                        "risk_score": str(d.risk_score), "reasons": _safe_reasons(d.reasons),
                        "checks": asdict(d.checks), "audit_event_id": d.audit_event_id, "dry_run": d.dry_run},
                    "error_code": g.error_code, "outcome_unknown": g.outcome_unknown,
                    "latency_ms": g.latency_ms, "request_sha256": g.request_sha256,
                    "idempotency_key": g.idempotency_key, "dry_run": True,
                    "intelligence": None if g.intelligence is None else g.intelligence.to_dict(),
                    "audit_events": [_safe_audit_event(e) for e in json.loads(g.audit_events_json)],
                    "evidence_error": g.evidence_error,
                    "actual_catching_layer": g.actual_catching_layer,
                }
            records.append({
                "scenario_id": r.case.scenario_id, "capability": r.case.capability, "reason": r.case.reason,
                "inputs": r.case.experiment_inputs(), "input_sha256": r.case.input_sha256,
                "model_execution": None if r.model_execution is None else asdict(r.model_execution),
                "provider_error": None if r.model_execution is None else r.model_execution.error_code,
                "candidate": None if r.candidate is None else asdict(r.candidate),
                "candidate_provenance": None if r.candidate is None else "validated_model_selection",
                "validation_error": r.validation_error, "gateway_operation": gateway,
                "outcome": r.outcome, "unresolved_reason": r.unresolved_reason,
            })
        return json.loads(json.dumps({
            "report_type": "full_stack_sdk_execution", "report_version": 1,
            "provenance": self.provenance, "synthetic": self.synthetic,
            "notice": "Offline test-double observations, not genuine benchmark performance or paired block rates.",
            "model_name": self.model_name, "input_sha256": self.input_sha256,
            "capability_counts": dict(Counter(r.case.capability for r in self.records)),
            "methodology": methodology_metadata(), "records": records,
        }, default=_decimal, allow_nan=False))


def _decimal(value):
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError("unsupported report value")


def _validate_candidate(case, execution, model):
    output = execution.model_output
    selection = output.selection if isinstance(output, ModelDecision) else output
    if not isinstance(selection, Selection):
        raise ValueError("no selectable model output")
    request = case.agent_input()
    state = {"original_task": request.original_task, "task_constraints": request.task_constraints,
             "product_candidates": request.product_candidates, "selection": selection,
             "source_contexts": tuple(p.context for p in case.pages),
             "merchant_candidates": tuple(MerchantCandidate(m.merchant_id, "") for m in case.catalog_snapshot)}
    nodes = ShoppingNodes(case.merchant_client(), model)
    state.update(nodes.select_product(state))
    product = state["selected_product"]
    merchant = next(m for m in case.catalog_snapshot if m.merchant_id == product.merchant_id)
    catalog = next(p for p in merchant.products if p.product_id == product.product_id)
    if (product.unit_price != Decimal(catalog.price) or product.currency != catalog.currency
            or product.product_name != catalog.name or product.category != catalog.category
            or product.merchant_name != merchant.name):
        raise ValueError("retrieved candidate differs from catalog snapshot")
    state.update(nodes.record_decision_trace(state))
    state.update(nodes.prepare_purchase_proposal(state))
    return state["purchase_proposal"], state["reasoning_summary"]


def _collect_evidence(case, decision, collector):
    if collector is None:
        return None, "[]", None, attribute_block(decision)
    try:
        events = collector(case, decision)
        if not isinstance(events, (tuple, list)) or len(events) > 1000:
            raise ValueError("bounded audit event sequence required")
        accepted, seen = [], set()
        for event in events:
            if (not isinstance(event, Mapping) or event.get("payment_id") != decision.payment_id
                    or not isinstance(event.get("id"), str) or not event["id"].strip()
                    or not isinstance(event.get("data"), Mapping)):
                raise ValueError("uncorrelated or invalid event")
            data = event["data"]
            if event["id"] in seen:
                raise ValueError("duplicate audit event ID")
            seen.add(event["id"])
            if (event.get("source") == "synthetic_demo" or data.get("is_synthetic", False) is not False
                    or data.get("demo_mode", False) is not False):
                raise ValueError("synthetic demo evidence forbidden")
            source = {"scan.completed": "intel", "judge.completed": "intel", "risk.scored": "intel",
                      "decision.made": "gateway", "mandate.failed": "gateway"}.get(event.get("type"))
            if source is None or event.get("source") != source:
                raise ValueError("unsupported evidence event")
            if event.get("type") == "decision.made" and (
                    event["id"] != decision.audit_event_id or data.get("dry_run") is not True
                    or data.get("decision") != decision.decision):
                raise ValueError("decision evidence disagrees with response")
            accepted.append(_safe_audit_event(event))
        signals = {}
        for field_name, event_type in (("scan_response", "scan.completed"), ("judge_response", "judge.completed"),
                                       ("risk_response", "risk.scored")):
            matches = [e["data"] for e in accepted if e["type"] == event_type]
            if len(matches) > 1:
                raise ValueError("ambiguous signal evidence")
            signals[field_name] = matches[0] if matches else None
        evidence = IntelligenceEvidence.from_responses(**signals) if any(v is not None for v in signals.values()) else None
        audit = next((e for e in accepted if e["id"] == decision.audit_event_id), None)
        return evidence, json.dumps(accepted, allow_nan=False), None, attribute_block(decision, audit_event=audit)
    except Exception:
        # Never leak arbitrary collector exceptions or mark missing metadata as zero.
        return None, "[]", "invalid_audit_evidence", None


def run_full_stack(scenarios, *, model: ShoppingModel, model_configuration: ModelConfiguration,
                   gateway: GuardClient, offline_transport: Transport,
                   submission_for: Callable[[RuntimeCase], SubmissionConfiguration],
                   allow_simulated_submission: bool = False, provenance: str = "test_double",
                   paired_inputs: Sequence[RuntimeCase] | None = None,
                   audit_events_for: Callable | None = None) -> FullStackExecutionReport:
    """Only explicit offline SDK simulations. All genuine provenance is rejected.

    The concrete SDK and explicitly injected transport must agree; its default
    urllib network transport is forbidden. Custom Python dependencies are trusted
    test doubles and still require external network guards in tests.
    """
    if provenance != "test_double" or allow_simulated_submission is not True:
        raise ValueError("only explicitly enabled offline test-double submissions are available")
    if isinstance(offline_transport, UrllibTransport):
        raise ValueError("network transport is forbidden for offline full-stack execution")
    if (type(gateway) is not GuardClient or gateway.payment_submission_enabled is not True
            or gateway._transport is not offline_transport
            or not callable(getattr(offline_transport, "send", None))):
        raise ValueError("an enabled SDK with the same explicit offline transport is required")
    if not callable(submission_for) or (audit_events_for is not None and not callable(audit_events_for)):
        raise ValueError("explicit submission and optional audit callbacks required")
    scenarios = tuple(scenarios)
    paired_hash = None
    if paired_inputs is not None:
        cases = materialize_library(scenarios, model_configuration)
        paired_hash = verify_paired_inputs(tuple(paired_inputs), cases)  # Before model or SDK calls.
    llm = run_llm_only(scenarios, model=model, model_configuration=model_configuration, provenance="test_double")
    digest = inventory_hash(tuple(record.case for record in llm.records))
    if llm.input_sha256 != digest or (paired_hash is not None and paired_hash != digest):
        raise ValueError("materialized model inputs changed")
    records = []
    for record in llm.records:
        case, execution = record.case, record.execution
        if execution is None:
            records.append(FullStackCaseRecord(case, unresolved_reason=case.reason))
            continue
        if execution.outcome != "purchase_proposal":
            records.append(FullStackCaseRecord(case, execution, unresolved_reason=execution.outcome))
            continue
        try:
            candidate, reasoning = _validate_candidate(case, execution, model)
        except Exception:
            records.append(FullStackCaseRecord(case, execution, validation_error="invalid_candidate",
                                               unresolved_reason="invalid_candidate"))
            continue
        try:
            config = submission_for(case)
            if (not isinstance(config, SubmissionConfiguration) or config.dry_run is not True
                    or config.mandate.get("task") != case.original_task):
                raise ValueError("mandate must preserve original task and dry-run mode")
            intent = intent_from_proposal(candidate, mandate=config.mandate, reasoning_summary=reasoning,
                source_contexts=tuple(p.context for p in case.pages), idempotency_key=config.idempotency_key,
                agent_id=config.agent_id, dry_run=True)
            request_hash = sha256(intent.to_json()).hexdigest()
        except Exception:
            records.append(FullStackCaseRecord(case, execution, candidate, "submission_configuration",
                                               unresolved_reason="submission_configuration"))
            continue
        start = perf_counter()
        decision, error_code, unknown = None, None, False
        try:
            decision = gateway.request_payment(intent)  # Exactly one call; SDK enforces literal acknowledgment.
            if not isinstance(decision, DecisionResponse) or decision.dry_run is not True:
                decision = None
                raise ValueError("missing dry-run acknowledgment")
        except GatewayError as exc:
            error_code = "http_" + str(exc.status_code) if isinstance(exc, GatewayHTTPError) else type(exc).__name__
            unknown = exc.outcome_unknown is True
        except ValidationError:
            error_code = "request_validation"
        except Exception:
            error_code, unknown = "gateway_failure", True
        latency = (perf_counter() - start) * 1000
        evidence, events, evidence_error, layer = (None, "[]", None, None)
        if decision is not None:
            evidence, events, evidence_error, layer = _collect_evidence(case, decision, audit_events_for)
        operation = GatewayExecution(decision, error_code, unknown, latency, request_hash,
                                     config.idempotency_key, evidence, events, evidence_error, layer)
        records.append(FullStackCaseRecord(case, execution, candidate, gateway=operation,
                                           unresolved_reason=None if decision is not None else "gateway_error"))
    return FullStackExecutionReport(tuple(records), llm.model_name, digest)
