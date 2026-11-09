"""Typed values for Vivek's published contract; no signing or authorization logic."""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
import math
import re
from types import MappingProxyType
from typing import Any, Literal, Mapping
from uuid import UUID

from ..merchants.source_context import SourceContext, validate_contexts
from .exceptions import ValidationError

Decision = Literal["allow", "step_up", "block"]


def string(value, name: str) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{name} must be a string")
    return value


def money(value: str | Decimal, name: str = "amount") -> str:
    if not isinstance(value, (str, Decimal)):
        raise ValidationError(f"{name} must be a money string or Decimal, never float")
    try:
        number = Decimal(value)
        if not number.is_finite():
            raise ValidationError(f"{name} must be finite")
    except InvalidOperation:
        raise ValidationError(f"{name} is not a money value") from None
    result = format(value, "f") if isinstance(value, Decimal) else value
    if re.fullmatch(r"[0-9]+(?:\.[0-9]{1,2})?", result) is None:
        raise ValidationError(f"{name} must match the published unsigned money pattern with at most two decimal places")
    return result


def currency(value: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[A-Z]{3}", value) is None:
        raise ValidationError("currency must match ^[A-Z]{3}$")
    return value


def _timestamp(value, name):
    string(value, name)
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}[Tt][0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?(?:[Zz]|[+-][0-9]{2}:[0-9]{2})", value):
        raise ValidationError(f"{name} must be a timezone-aware RFC3339 timestamp")
    try:
        datetime.fromisoformat(value.upper().replace("Z", "+00:00"))
    except ValueError:
        raise ValidationError(f"{name} is not a supported RFC3339 timestamp") from None


def _freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _json_value(value):
    """Reject data JSON encoding would silently coerce, including non-string keys."""
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float and math.isfinite(value):
        return
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        for item in value.values():
            _json_value(item)
        return
    if isinstance(value, list):
        for item in value:
            _json_value(item)
        return
    raise ValidationError("mandate extensions must contain finite JSON values with string object keys")


def _mandate_snapshot(value: Mapping[str, Any]) -> str:
    if not isinstance(value, Mapping):
        raise ValidationError("mandate must be an already signed JSON object")
    required_strings = ("mandate_id", "principal_id", "agent_id", "task", "nonce", "public_key", "signature")
    for key in required_strings:
        string(value.get(key), "mandate." + key)
    currency(value.get("currency"))
    for key in ("max_total", "max_per_txn"):
        string(value.get(key), "mandate." + key)
        money(value[key], "mandate." + key)
    for key in ("valid_from", "expires_at"):
        _timestamp(value.get(key), "mandate." + key)
    if value.get("step_up_over") is not None:
        string(value["step_up_over"], "mandate.step_up_over")
        money(value["step_up_over"], "mandate.step_up_over")
    if value.get("paypal_order_id") is not None:
        string(value["paypal_order_id"], "mandate.paypal_order_id")
    for key in ("allowed_merchants", "allowed_categories"):
        if key in value and (not isinstance(value[key], list) or any(not isinstance(item, str) for item in value[key])):
            raise ValidationError("mandate." + key + " must be an array of strings")
    # Snapshot JSON values without sorting arrays, normalizing dates, filling defaults,
    # stripping extensions, or otherwise changing the supplied signed mandate.
    try:
        snapshot = dict(value)
        _json_value(snapshot)
        return json.dumps(snapshot, ensure_ascii=False, allow_nan=False)
    except (ValueError, TypeError, RecursionError):
        raise ValidationError("mandate must contain finite, serializable JSON data") from None


@dataclass(frozen=True)
class PaymentItem:
    name: str
    qty: int
    unit_price: str | Decimal
    category: str | None = None

    def __post_init__(self):
        string(self.name, "item.name")
        if type(self.qty) is not int or self.qty < 1:
            raise ValidationError("item.qty must be an integer >= 1")
        object.__setattr__(self, "unit_price", money(self.unit_price, "item.unit_price"))
        if self.category is not None:
            string(self.category, "item.category")

    def to_dict(self) -> dict:
        result = {"name": self.name, "qty": self.qty, "unit_price": self.unit_price}
        if self.category is not None:
            result["category"] = self.category
        return result


@dataclass(frozen=True)
class PaymentIntent:
    mandate: Mapping[str, Any] = field(repr=False)
    merchant_id: str
    amount: str | Decimal
    currency: str
    items: tuple[PaymentItem, ...]
    agent_reasoning: str = field(repr=False)
    idempotency_key: str
    merchant_name: str | None = None
    category: str | None = None
    source_contexts: tuple[SourceContext, ...] | None = field(default=None, repr=False)
    _mandate_json: str = field(init=False, repr=False)

    def __post_init__(self):
        snapshot = _mandate_snapshot(self.mandate)
        object.__setattr__(self, "_mandate_json", snapshot)
        object.__setattr__(self, "mandate", _freeze(json.loads(snapshot)))
        string(self.merchant_id, "merchant_id")
        string(self.agent_reasoning, "agent_reasoning")
        object.__setattr__(self, "amount", money(self.amount))
        currency(self.currency)
        if not isinstance(self.items, (tuple, list)) or not self.items or any(not isinstance(item, PaymentItem) for item in self.items):
            raise ValidationError("items must contain at least one PaymentItem")
        object.__setattr__(self, "items", tuple(self.items))
        key = self.idempotency_key
        if not isinstance(key, str) or re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", key) is None:
            raise ValidationError("idempotency_key must be a hyphenated UUID string")
        UUID(key)  # Validate, but never replace or normalize the caller's spelling.
        for name in ("merchant_name", "category"):
            if getattr(self, name) is not None:
                string(getattr(self, name), name)
        if self.source_contexts is not None:
            try:
                contexts = validate_contexts(self.source_contexts)
            except (ValueError, TypeError):
                raise ValidationError("source_contexts must contain at most 50 validated SourceContext objects") from None
            object.__setattr__(self, "source_contexts", contexts)

    def to_dict(self) -> dict:
        result = {"mandate": json.loads(self._mandate_json), "merchant_id": self.merchant_id,
                  "amount": self.amount, "currency": self.currency,
                  "items": [item.to_dict() for item in self.items],
                  "agent_reasoning": self.agent_reasoning, "idempotency_key": self.idempotency_key}
        for name in ("merchant_name", "category"):
            if getattr(self, name) is not None:
                result[name] = getattr(self, name)
        if self.source_contexts is not None:
            result["source_contexts"] = [
                {key: value for key, value in context.to_dict().items() if value is not None}
                for context in self.source_contexts
            ]
        return result

    def to_json(self) -> bytes:
        try:
            return json.dumps(self.to_dict(), ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        except (UnicodeError, ValueError):
            raise ValidationError("intent cannot be encoded as UTF-8 JSON") from None


@dataclass(frozen=True)
class Checks:
    mandate: Literal["pass", "fail"]
    injection: Literal["pass", "flagged", "unknown"]
    intent: Literal["pass", "flagged", "unknown"]
    risk: Literal["pass", "flagged", "unknown"]


@dataclass(frozen=True)
class PayPalDetails:
    order_id: str | None = None
    authorization_id: str | None = None
    capture_id: str | None = None
    approval_url: str | None = None
    refund_id: str | None = None  # Also present in the actual Go PayPalDetails struct.


@dataclass(frozen=True)
class DecisionResponse:
    payment_id: str
    decision: Decision
    risk_score: Decimal
    reasons: tuple[str, ...]
    checks: Checks
    audit_event_id: str
    paypal: PayPalDetails | None = None
    step_up_token: str | None = field(default=None, repr=False)

    @classmethod
    def from_dict(cls, data: dict) -> "DecisionResponse":
        if not isinstance(data, dict):
            raise ValidationError("decision response must be an object")
        for key in ("payment_id", "audit_event_id"):
            string(data.get(key), key)
        if data.get("decision") not in ("allow", "step_up", "block"):
            raise ValidationError("invalid decision enum")
        score = data.get("risk_score")
        if isinstance(score, bool) or not isinstance(score, (int, float, Decimal)):
            raise ValidationError("risk_score must be a number")
        score = Decimal(str(score))
        if not score.is_finite() or not 0 <= score <= 1:
            raise ValidationError("risk_score must be finite and between 0 and 1")
        reasons = data.get("reasons")
        if not isinstance(reasons, list) or any(not isinstance(reason, str) for reason in reasons):
            raise ValidationError("reasons must be an array of strings")
        checks = data.get("checks")
        if not isinstance(checks, dict):
            raise ValidationError("checks must be an object")
        for key in ("mandate", "injection", "intent", "risk"):
            allowed = ("pass", "fail") if key == "mandate" else ("pass", "flagged", "unknown")
            if checks.get(key) not in allowed:
                raise ValidationError("missing or invalid check enum")
        paypal = data.get("paypal")
        if paypal is not None:
            if not isinstance(paypal, dict):
                raise ValidationError("paypal must be an object or null")
            fields = {key: string(paypal[key], "paypal." + key) for key in PayPalDetails.__dataclass_fields__ if key in paypal}
            paypal = PayPalDetails(**fields)
        token = data.get("step_up_token")
        if token is not None:
            string(token, "step_up_token")
        return cls(data["payment_id"], data["decision"], score, tuple(reasons),
                   Checks(**{key: checks[key] for key in Checks.__dataclass_fields__}),
                   data["audit_event_id"], paypal, token)
