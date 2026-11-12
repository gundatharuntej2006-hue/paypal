"""Explicitly opted-in, two-intent local stub/dry-run smoke test, not a benchmark.

No service startup, enrollment, signing, retries, approval or demo operations.
Injected test dependencies are trusted code, not a network sandbox. Service
deployment settings must be verified independently before opting in.
"""

import argparse
import base64
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import re
from uuid import UUID

from ..agent.state import AgentConfig, ModelDecision, PurchaseProposal, Selection, SelectionRationale
from ..attacks.fixtures import get_attack
from ..merchants.data import get_merchant, get_product
from ..merchants.source_context import make_context
from ..merchants.visible_text import visible_text
from ..sdk import GuardClient, intent_from_proposal
from ..sdk.exceptions import GatewayError, GatewayTimeoutError
from ..sdk.transport import UrllibTransport
from .prepare_credentials import TASK, TOKEN_NAMES, MANDATES_FILE, SECRETS_FILE, canonical_mandate_bytes
from .windows_files import NativeWindows


GATEWAY_URL = "http://127.0.0.1:8080"
MERCHANT_URL = "http://127.0.0.1:8010"
INTENT_URL = GATEWAY_URL + "/v1/payments/intents"
_MESSAGES = {
    "opt_in": "Explicit --confirm-local-stub-dryrun is required before any network activity.",
    "arguments": "Unsupported command arguments; credentials must never be supplied on the command line.",
    "origin": "Only the exact configured loopback HTTP origins and ports are permitted.",
    "proxy": "Proxy configuration is prohibited for the local smoke run.",
    "credentials": "Local smoke credential validation failed; no requests were submitted.",
    "expired": "A prepared mandate is expired or not yet valid; stop and review credentials offline.",
    "selection": "The exact retrieved merchant_a USB-C hub and its citation are required.",
    "plan": "The complete two-case shopping/evidence plan could not be validated.",
    "route": "Only the planned payment-intent POST route is permitted.",
    "limit": "The two-request limit is exhausted or the smoke transport is closed.",
    "gateway_timeout": "Gateway timed out; outcome is unknown. Do not retry or rotate identifiers.",
    "gateway_error": "Gateway submission or response failed; outcome is unknown. Do not retry.",
    "clean_result": "Clean case did not return the required acknowledged allow decision; stopped.",
    "poison_result": "Poisoned case did not return the required acknowledged injection block; stopped.",
}


class SmokeError(ValueError):
    """Only predefined messages and safe outcome metadata; never underlying text."""

    def __init__(self, code, *, outcome_unknown=False, attempted_posts=0):
        self.code = code if code in _MESSAGES else "plan"
        self.outcome_unknown = outcome_unknown
        self.attempted_posts = attempted_posts
        super().__init__(_MESSAGES[self.code])


@dataclass(frozen=True)
class _Plan:
    cases: tuple = field(repr=False)
    gateway_token: str = field(repr=False)
    principal_id: str = field(repr=False)
    agent_id: str = field(repr=False)


@dataclass(frozen=True)
class SmokeObservation:
    case: str
    decision: str
    dry_run: bool
    audit_event_present: bool
    injection_flagged: bool


def _origins(gateway_url, merchant_url):
    if gateway_url != GATEWAY_URL or merchant_url != MERCHANT_URL:
        raise SmokeError("origin")


def _no_proxies():
    if any(name.lower() in {"http_proxy", "https_proxy", "all_proxy"} and value.strip()
           for name, value in os.environ.items()):
        raise SmokeError("proxy")


def _purchase():
    merchant = get_merchant("merchant_a")
    product = get_product(merchant, "usb-c-hub")
    if (merchant.name, product.name, product.price, product.currency, product.category) != (
            "Northstar Office", "USB-C hub 7-in-1", "29.99", "USD", "electronics_accessories"):
        raise SmokeError("plan")
    return PurchaseProposal("merchant_a", merchant.name, product.product_id, product.name, 1,
                            Decimal(product.price), Decimal(product.price), product.currency,
                            product.category, ())


class ExactHubSelector:
    """Fixture selector, explicitly not an LLM or injection-defense experiment."""

    name = "local_smoke_exact_hub (deterministic, not an LLM)"

    def select(self, request):
        expected = _purchase()
        products = [p for p in request.product_candidates
                    if (p.merchant_id, p.product_id) == (expected.merchant_id, expected.product_id)]
        if len(products) != 1 or request.original_task != TASK or request.task_constraints.quantity != 1:
            raise SmokeError("selection")
        product = products[0]
        if (product.merchant_name, product.product_name, product.unit_price, product.currency, product.category,
            product.url) != (expected.merchant_name, expected.product_name, expected.unit_price, expected.currency,
                             expected.category, MERCHANT_URL + "/merchants/merchant_a/products/usb-c-hub"):
            raise SmokeError("selection")
        pages = [p for p in request.pages if p.url == product.url and p.source_ref in request.source_references]
        if len(pages) != 1:
            raise SmokeError("selection")
        rationale = SelectionRationale("Selected the retrieved Northstar Office USB-C hub for 29.99 USD.",
                                       "One hub from merchant_a meets the original task and budget.")
        selection = Selection(product.merchant_id, product.product_id, rationale.summary,
                              (pages[0].source_ref,), rationale)
        return ModelDecision("purchase_proposal", selection, rationale)


def _read_credentials(credential_dir):
    """Read selected files through verified handles; never create or repair paths."""
    import msvcrt
    root = Path(os.path.abspath(credential_dir))
    api = NativeWindows()
    # Same local fixed-drive path policy as WindowsOutput.__enter__, without
    # entering that writable context or calling any directory-creation API.
    if not root.drive or str(root).startswith("\\\\") or ":" in str(root)[2:]:
        raise SmokeError("credentials")
    api._check(api._call("GetDriveTypeW", api.k.GetDriveTypeW, root.anchor, kind="drive") == 3)
    handles, streams = [], []
    try:
        for path in list(reversed(root.parents)) + [root]:
            handle = api.open_directory(path)
            handles.append(handle)
            api.identity(handle, path, True)
            api.verify_acl(handle, strict=path == root, directory=True, volume_root=path == Path(root.anchor))
        output = []
        for name in (MANDATES_FILE, SECRETS_FILE):
            path = root / name
            handle = api._call("CreateFileW", api.k.CreateFileW,
                               str(path), 0x80020080, 1, None, 3, 0x00200080, None, kind="handle")
            handles.append(handle)
            identity = api.identity(handle, path, False)
            api.verify_acl(handle, strict=True)
            descriptor = msvcrt.open_osfhandle(handle, os.O_BINARY | os.O_RDONLY)
            handles.remove(handle)
            try:
                stream = os.fdopen(descriptor, "rb")
            except Exception:
                os.close(descriptor)
                raise
            streams.append(stream)
            raw = stream.read(1_048_577)
            if not raw or len(raw) > 1_048_576:
                raise SmokeError("credentials")
            # Reuse the SDK's duplicate-property and non-finite JSON rejection.
            from ..sdk.client import _object, _invalid_constant
            output.append(json.loads(raw.decode("utf-8"), object_pairs_hook=_object, parse_constant=_invalid_constant))
            api._check(api.identity(handle, path, False) == identity)
            api.verify_acl(handle, strict=True)
        return tuple(output)
    finally:
        failed = False
        for stream in reversed(streams):
            try:
                stream.close()
            except Exception:
                failed = True
        for handle in reversed(handles):
            try:
                api.close(handle)
            except Exception:
                failed = True
        if failed:
            raise SmokeError("credentials") from None


def _valid_time(mandate, now):
    try:
        start = datetime.fromisoformat(mandate["valid_from"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(mandate["expires_at"].replace("Z", "+00:00"))
        if start.tzinfo is None or end.tzinfo is None or not start <= now < end:
            raise ValueError
        if not 0 < (end - start).total_seconds() <= 930:
            raise ValueError
    except Exception:
        raise SmokeError("expired") from None


def _validate_credentials(documents, now):
    """Validate both complete signed cases before any merchant or gateway request."""
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        report, configuration = deepcopy(documents)
        if set(configuration) != {"principal_id", "agent_id", "public_key", *TOKEN_NAMES}:
            raise ValueError
        principal, agent = configuration["principal_id"], configuration["agent_id"]
        for identity in (principal, agent):
            if (not isinstance(identity, str) or not identity or identity != identity.strip()
                    or any(ord(char) < 32 or ord(char) == 127 for char in identity)):
                raise ValueError
        tokens = [configuration[name] for name in TOKEN_NAMES]
        if any(not isinstance(t, str) or re.fullmatch(r"[A-Za-z0-9_-]{43}", t) is None for t in tokens) or len(set(tokens)) != 4:
            raise ValueError
        proposal = _purchase()
        purchase = dict(merchant_id=proposal.merchant_id, product_id=proposal.product_id,
                        product_name=proposal.product_name, quantity=1, unit_price="29.99", amount="29.99",
                        currency="USD", category="electronics_accessories")
        if (set(report) != {"format_version", "task", "purchase", "cases"} or type(report["format_version"]) is not int
                or report["format_version"] != 1 or report["task"] != TASK or report["purchase"] != purchase
                or not isinstance(report["cases"], list) or len(report["cases"]) != 2):
            raise ValueError
        public = Ed25519PublicKey.from_public_bytes(base64.b64decode(configuration["public_key"], validate=True))
        fields = {"mandate_id", "principal_id", "agent_id", "task", "currency", "max_total", "max_per_txn",
                  "step_up_over", "allowed_merchants", "allowed_categories", "valid_from", "expires_at",
                  "nonce", "public_key", "signature"}
        for case, label, attack in zip(report["cases"], ("clean", "poisoned_evidence"), (None, "poison_06")):
            if set(case) != {"case", "mandate", "idempotency_key", "attack_id"} or case["case"] != label or case["attack_id"] != attack:
                raise ValueError
            m = case["mandate"]
            if (set(m) != fields or m["principal_id"] != principal or m["agent_id"] != agent or m["task"] != TASK
                    or m["public_key"] != configuration["public_key"] or m["currency"] != "USD"
                    or m["allowed_merchants"] != ["merchant_a"] or m["allowed_categories"] != ["electronics_accessories"]
                    or any(m[name] != "40.00" for name in ("max_total", "max_per_txn", "step_up_over"))):
                raise ValueError
            _valid_time(m, now)
            public.verify(base64.b64decode(m["signature"], validate=True), canonical_mandate_bytes(m))
            for value in (m["nonce"], case["idempotency_key"]):
                if str(UUID(value)) != value:
                    raise ValueError
            intent_from_proposal(proposal, mandate=m, reasoning_summary="Local smoke fixture preflight.",
                                 source_contexts=(), idempotency_key=case["idempotency_key"],
                                 agent_id=agent, dry_run=True).to_json()
        for name in ("mandate_id", "nonce"):
            if len({c["mandate"][name] for c in report["cases"]}) != 2:
                raise ValueError
        if len({c["idempotency_key"] for c in report["cases"]}) != 2:
            raise ValueError
        return _Plan(tuple(report["cases"]), configuration["AGENT_AUTH_TOKEN"], principal, agent)
    except SmokeError:
        raise
    except Exception:
        raise SmokeError("credentials") from None


class IntentOnlyTransport:
    """One invocation, two planned POST bodies maximum; trusted delegate injection."""

    def __init__(self, bodies, *, _delegate=None):
        if len(bodies) != 2:
            raise SmokeError("plan")
        self._bodies = tuple(bodies)
        self._delegate = _delegate if _delegate is not None else UrllibTransport()
        self.attempted_posts = 0
        self.closed = False

    def send(self, *, url, body, headers, timeout, max_response_bytes):
        if self.closed or self.attempted_posts >= 2:
            raise SmokeError("limit")
        _no_proxies()
        if url != INTENT_URL or body != self._bodies[self.attempted_posts]:
            raise SmokeError("route")
        self.attempted_posts += 1  # Consume allowance before an ambiguous send.
        try:
            response = self._delegate.send(url=url, body=body, headers=headers, timeout=timeout,
                                           max_response_bytes=max_response_bytes)
            if response.status_code != 200:
                self.closed = True
            return response
        except Exception:
            self.closed = True
            raise


def _validate_state(state, case, *, agent_id):
    if state.get("execution_errors") or state.get("original_task") != TASK or state.get("reader_mode") != "visible_text":
        raise SmokeError("plan")
    expected = _purchase()
    proposal = state.get("purchase_proposal")
    if not isinstance(proposal, PurchaseProposal):
        raise SmokeError("plan")
    for name in ("merchant_id", "merchant_name", "product_id", "product_name", "quantity", "unit_price", "amount", "currency", "category"):
        if getattr(proposal, name) != getattr(expected, name):
            raise SmokeError("plan")
    contexts = state.get("source_contexts", ())
    captured = state.get("captured_pages", ())
    consumed = state.get("model_pages", ())
    if not contexts or len(contexts) != len(captured) or len(contexts) != len(consumed):
        raise SmokeError("plan")
    for context, page, model_page in zip(contexts, captured, consumed):
        if (context.url != page.url or context.html != page.html or context.text != visible_text(page.html)
                or model_page.source_ref != context.ref or model_page.url != context.url or model_page.content != context.text
                or make_context(text=context.text, url=context.url, html=context.html).ref != context.ref):
            raise SmokeError("plan")
        if not context.url.startswith(MERCHANT_URL + "/"):
            raise SmokeError("origin")
    attack = get_attack("poison_06")
    poisoned = [c for c in contexts if c.url == MERCHANT_URL + attack.poisoned_path]
    if case["attack_id"] is None:
        if any("?" in c.url for c in contexts):
            raise SmokeError("plan")
    elif len(poisoned) != 1 or attack.malicious_content not in poisoned[0].text:
        raise SmokeError("plan")
    return intent_from_proposal(proposal, mandate=case["mandate"], reasoning_summary=state["reasoning_summary"],
                                source_contexts=contexts, idempotency_key=case["idempotency_key"],
                                agent_id=agent_id, dry_run=True)


def run_local_smoke(*, credential_dir=None, confirm=False, gateway_url=GATEWAY_URL, merchant_url=MERCHANT_URL,
                    _credential_loader=None, _shopping=None, _transport=None, _clock=None):
    """Explicit opt-in is required even with trusted offline dependency injection."""
    if confirm is not True:
        raise SmokeError("opt_in")
    _origins(gateway_url, merchant_url)
    _no_proxies()
    if (not isinstance(credential_dir, (str, os.PathLike))
            or not isinstance(os.fspath(credential_dir), str) or not os.fspath(credential_dir).strip()):
        raise SmokeError("arguments")
    clock = _clock or (lambda: datetime.now(timezone.utc))
    try:
        plan = _validate_credentials((_credential_loader or _read_credentials)(credential_dir), clock())
    except SmokeError:
        raise
    except Exception:
        raise SmokeError("credentials") from None
    gate = None
    try:
        if _shopping is None:
            from ..agent.graph import run_shopping
            _shopping = run_shopping
        states = [_shopping(TASK, AgentConfig(base_url=merchant_url, reader_mode="visible_text", attack_id=c["attack_id"]),
                            model=ExactHubSelector()) for c in plan.cases]
        intents = [_validate_state(state, case, agent_id=plan.agent_id) for state, case in zip(states, plan.cases)]
        bodies = tuple(intent.to_json() for intent in intents)
        gate = IntentOnlyTransport(bodies, _delegate=_transport)
        guard = GuardClient(gateway_url, gateway_token=plan.gateway_token, payment_submission_enabled=True, transport=gate)
        observations = []
        for index, (case, intent) in enumerate(zip(plan.cases, intents)):
            _valid_time(case["mandate"], clock())
            decision = guard.request_payment(intent)
            if decision.dry_run is not True or not decision.audit_event_id.strip():
                raise SmokeError("clean_result" if index == 0 else "poison_result", attempted_posts=gate.attempted_posts)
            if index == 0:
                valid = decision.decision == "allow" and all(getattr(decision.checks, k) == "pass" for k in ("mandate", "injection", "intent", "risk"))
            else:
                valid = (decision.decision == "block" and decision.checks.mandate == "pass"
                         and decision.checks.injection == "flagged" and "prompt_injection_detected" in decision.reasons)
            if not valid:
                raise SmokeError("clean_result" if index == 0 else "poison_result", attempted_posts=gate.attempted_posts)
            observations.append(SmokeObservation(case["case"], decision.decision, decision.dry_run,
                                                  bool(decision.audit_event_id.strip()), decision.checks.injection == "flagged"))
        return tuple(observations)
    except GatewayTimeoutError:
        raise SmokeError("gateway_timeout", outcome_unknown=True, attempted_posts=gate.attempted_posts if gate else 0) from None
    except GatewayError:
        raise SmokeError("gateway_error", outcome_unknown=True, attempted_posts=gate.attempted_posts if gate else 0) from None
    except SmokeError as error:
        if gate is not None:
            error.attempted_posts = gate.attempted_posts
        raise
    except Exception:
        raise SmokeError("gateway_error" if gate and gate.attempted_posts else "plan",
                         outcome_unknown=bool(gate and gate.attempted_posts),
                         attempted_posts=gate.attempted_posts if gate else 0) from None
    finally:
        if gate is not None:
            gate.closed = True


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise SmokeError("arguments")  # Never echo unknown arguments/credential values.


def main(argv=None):
    parser = _Parser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--confirm-local-stub-dryrun", action="store_true")
    parser.add_argument("--credential-dir", required=True, help="Protected local directory containing the two prepared bundle files")
    parser.add_argument("--gateway-url", default=GATEWAY_URL)
    parser.add_argument("--merchant-url", default=MERCHANT_URL)
    try:
        args = parser.parse_args(argv)
        observations = run_local_smoke(confirm=args.confirm_local_stub_dryrun,
                                       credential_dir=args.credential_dir,
                                       gateway_url=args.gateway_url, merchant_url=args.merchant_url)
    except SmokeError as error:
        print(str(error))
        return 1
    except Exception:
        print("Local smoke run stopped; no credential or response contents are shown.")
        return 1
    for observed in observations:
        print(f"{observed.case}: PASS; observed={observed.decision}; dry_run=true; audit_reference_present=true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
