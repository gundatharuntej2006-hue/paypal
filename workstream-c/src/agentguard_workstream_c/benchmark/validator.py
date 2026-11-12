"""Strict dependency-free validation of the version-1 internal scenario schema."""

from collections import Counter
from decimal import Decimal, localcontext
import json
import re

from ..attacks.fixtures import get_attack
from ..merchants.data import get_merchant, get_product
from .models import CATEGORIES, DECISIONS, LAYERS, Scenario


class ScenarioValidationError(ValueError):
    """Malformed library, with a field path and no sensitive input echoed."""


def fail(path, message):
    raise ScenarioValidationError(f"{path}: {message}")


def scan_secrets(value, path="scenario"):
    """Reject credential fields and recognizable secret material, not a DLP guarantee."""
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                fail(path, "object keys must be strings")
            if re.search(r"api[_-]?key|client[_-]?secret|password|authorization|access[_-]?token|internal[_-]?token|private[_-]?key", key, re.I):
                fail(path, "credential-bearing field is forbidden")
            scan_secrets(item, path + "." + key)
    elif isinstance(value, list):
        for item in value:
            scan_secrets(item, path + "[]")
    elif isinstance(value, str):
        patterns = (r"-----BEGIN .*PRIVATE KEY-----", r"\bsk-[A-Za-z0-9_-]{16,}",
                    r"\b(?:ghp_|github_pat_)[A-Za-z0-9_]{16,}", r"\bAKIA[0-9A-Z]{16}\b",
                    r"\bBearer\s+[A-Za-z0-9._~+/-]{8,}", r"https?://[^\s/:]+:[^\s/@]+@",
                    r"(?i)(?:api[_-]?key|client[_-]?secret|password|access[_-]?token|internal[_-]?token)\s*[=:]\s*\S+")
        if any(re.search(pattern, value) for pattern in patterns):
            fail(path, "possible secret material is forbidden")


def text(value, path):
    if not isinstance(value, str) or not value.strip() or len(value) > 10_000:
        fail(path, "must be a nonempty string of at most 10000 characters")


def money(value, path):
    if not isinstance(value, str) or re.fullmatch(r"[0-9]{1,12}(?:\.[0-9]{1,2})?", value) is None:
        fail(path, "must be an unsigned decimal money string with at most two decimal places")
    return Decimal(value)


def strings(value, path, *, nonempty=False):
    if not isinstance(value, list) or (nonempty and not value):
        fail(path, "must be an array" + (" containing strings" if nonempty else " of strings"))
    for item in value:
        text(item, path + "[]")
    if len(set(value)) != len(value):
        fail(path, "entries must be unique")


def validate_scenario(data: dict) -> Scenario:
    required = {"scenario_id", "name", "category", "description", "attack_vector", "task", "input",
                "expected_decision", "expected_catching_layer", "severity", "tags"}
    if not isinstance(data, dict) or set(data) != required:
        fail("scenario", "requires exactly the version-1 scenario fields")
    scan_secrets(data)
    for key in required - {"input", "tags", "expected_catching_layer"}:
        text(data[key], key)
    if re.fullmatch(r"[a-z][a-z0-9_]{2,79}", data["scenario_id"]) is None:
        fail("scenario_id", "must be a stable lowercase identifier")
    if data["category"] not in (*CATEGORIES, "benign"):
        fail("category", "unknown category")
    if data["expected_decision"] not in DECISIONS:
        fail("expected_decision", "must be allow, step_up or block")
    if data["expected_catching_layer"] not in (*LAYERS, None):
        fail("expected_catching_layer", "unknown enforcement layer")
    if data["severity"] not in ("none", "low", "medium", "high", "critical"):
        fail("severity", "unknown severity")
    strings(data["tags"], "tags", nonempty=True)
    inp = data["input"]
    fields = {"merchant_id", "product_id", "quantity", "unit_price", "amount", "currency", "budget",
              "max_per_txn", "prior_spend", "allowed_merchants", "reader_mode", "fixture_refs", "payload", "operations"}
    if not isinstance(inp, dict) or set(inp) != fields:
        fail("input", "requires exactly the version-1 experiment input fields")
    for key in ("merchant_id", "product_id", "currency", "reader_mode"):
        text(inp[key], "input." + key)
    if re.fullmatch(r"[A-Z]{3}", inp["currency"]) is None:
        fail("input.currency", "must contain three uppercase letters")
    if type(inp["quantity"]) is not int or not 1 <= inp["quantity"] <= 1000:
        fail("input.quantity", "must be an integer from 1 to 1000")
    values = {key: money(inp[key], "input." + key) for key in
              ("unit_price", "amount", "budget", "max_per_txn", "prior_spend")}
    if inp["reader_mode"] not in ("visible_text", "raw_html"):
        fail("input.reader_mode", "unknown reader mode")
    for key in ("allowed_merchants", "fixture_refs", "operations"):
        strings(inp[key], "input." + key, nonempty=key != "fixture_refs")
    if not isinstance(inp["payload"], str) or len(inp["payload"]) > 10_000:
        fail("input.payload", "must be a string of at most 10000 characters")
    merchant = get_merchant(inp["merchant_id"])
    if merchant is None or get_product(merchant, inp["product_id"]) is None:
        fail("input", "merchant/product reference does not exist")
    for merchant_id in inp["allowed_merchants"]:
        if get_merchant(merchant_id) is None:
            fail("input.allowed_merchants", "unknown mock merchant")
    for ref in inp["fixture_refs"]:
        fixture = get_attack(ref)
        if fixture is None:
            fail("input.fixture_refs", "unknown poisoned fixture reference")
        if (fixture.merchant_id, fixture.product_id) != (inp["merchant_id"], inp["product_id"]):
            fail("input.fixture_refs", "fixture target does not match merchant/product")
    if data["category"] == "benign":
        product = get_product(merchant, inp["product_id"])
        with localcontext() as ctx:
            ctx.prec = 32
            total = values["unit_price"] * inp["quantity"]
        if (inp["fixture_refs"] or inp["payload"] or data["attack_vector"] != "none"
                or data["severity"] != "none" or data["expected_catching_layer"] is not None
                or data["expected_decision"] != "allow"
                or inp["operations"] != ["propose the requested clean catalog purchase"]
                or inp["merchant_id"] not in inp["allowed_merchants"]
                or inp["currency"] != product.currency or values["unit_price"] != Decimal(product.price)
                or total <= 0 or values["amount"] != total
                or total > values["max_per_txn"] or total + values["prior_spend"] > values["budget"]):
            fail("input", "benign case must be clean, catalog-priced, allowed and within budget")
    elif data["expected_catching_layer"] is None or data["severity"] == "none" or data["attack_vector"] == "none":
        fail("scenario", "malicious cases require an attack vector, severity and catching layer")
    return Scenario(**{key: data[key] for key in required - {"input", "tags"}},
                    tags=tuple(data["tags"]), input_json=json.dumps(inp, sort_keys=True, ensure_ascii=False))


def validate_library(scenarios) -> tuple[Scenario, ...]:
    cases = tuple(validate_scenario(s.to_dict() if isinstance(s, Scenario) else s) for s in scenarios)
    counts = Counter(s.category for s in cases)
    if len({s.scenario_id for s in cases}) != len(cases):
        fail("library", "duplicate scenario_id")
    if counts["benign"] < 20 or sum(counts[c] for c in CATEGORIES) < 60 or any(counts[c] < 10 for c in CATEGORIES):
        fail("library", "requires 20 benign and at least 10 malicious cases in each of six categories")
    # Ignore editorial labels/expectations: identical experiment inputs are not new attacks.
    fingerprints = set()
    for scenario in cases:
        fingerprint = (scenario.task, scenario.input_json)
        if fingerprint in fingerprints:
            fail("library", "duplicate experiment inputs/task")
        fingerprints.add(fingerprint)
    return cases
