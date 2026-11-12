"""Offline smoke-runner tests: rendered merchant doubles and fake SDK transport."""

import base64
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import socket
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import pytest

from agentguard_workstream_c.agent.graph import run_shopping
from agentguard_workstream_c.agent.merchant_client import MerchantClient
from agentguard_workstream_c.agent.model import ModelRequest, understand
from agentguard_workstream_c.agent.state import AgentConfig, CapturedPage
from agentguard_workstream_c.attacks.fixtures import get_attack
from agentguard_workstream_c.attacks.poisoning import poisoned_detail
from agentguard_workstream_c.integration import local_smoke as smoke
from agentguard_workstream_c.integration.prepare_credentials import _documents, canonical_mandate_bytes
from agentguard_workstream_c.merchants import rendering
from agentguard_workstream_c.merchants.data import get_merchant, get_product
from agentguard_workstream_c.sdk.exceptions import GatewayTimeoutError
from agentguard_workstream_c.sdk.transport import TransportResponse, UrllibTransport


NOW = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)
TOKEN_CANARY = "DUMMY-TOKEN-CANARY-NEVER-LOG-".ljust(43, "X")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("No socket or DNS attempts are permitted")
    for name in ("socket", "getaddrinfo", "create_connection"):
        monkeypatch.setattr(socket, name, forbidden)
    for name in tuple(__import__("os").environ):
        if name.lower() in {"http_proxy", "https_proxy", "all_proxy"}:
            monkeypatch.delenv(name)


@pytest.fixture
def bundle():
    ed = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519")
    key = ed.Ed25519PrivateKey.from_private_bytes(bytes(32))  # Fixed test material only.
    identifiers = iter(UUID(int=i) for i in range(1, 20))
    tokens = iter([TOKEN_CANARY, "B" * 43, "C" * 43, "D" * 43])
    report, configuration = _documents(lambda: key, lambda size: next(tokens), lambda: next(identifiers), NOW)
    return report, configuration


class FixtureClient(MerchantClient):
    """Use the real graph/parser on actual rendered fixtures with zero HTTP."""

    def fetch(self, path_or_url):
        url = self.local_url(path_or_url)
        parts = urlsplit(url)
        if parts.path == "/":
            html = rendering.listing()
        else:
            bits = parts.path.strip("/").split("/")
            merchant = get_merchant(bits[1])
            if len(bits) == 2:
                html = rendering.catalog(merchant)
            else:
                product = get_product(merchant, bits[3])
                attack_id = parse_qs(parts.query).get("attack", [None])[0]
                html = (poisoned_detail(merchant, product, get_attack(attack_id)) if attack_id
                        else rendering.detail(merchant, product))
        return CapturedPage(url, html)


def fixture_shopping(task, config, *, model):
    return run_shopping(task, model=model, merchant_client=FixtureClient(config))


def response(decision="allow", **changes):
    data = dict(payment_id="offline-payment", decision=decision, risk_score=0.2,
                reasons=["mandate_valid"], audit_event_id="offline-audit", dry_run=True,
                checks=dict(mandate="pass", injection="pass", intent="pass", risk="pass"))
    if decision == "block":
        data.update(reasons=["prompt_injection_detected"],
                    checks=dict(mandate="pass", injection="flagged", intent="unknown", risk="unknown"))
    data.update(changes)
    return TransportResponse(200, json.dumps(data).encode())


class FakeTransport:
    def __init__(self, responses=None):
        self.responses = list(responses if responses is not None else [response(), response("block")])
        self.calls = []

    def send(self, **kwargs):
        self.calls.append(kwargs)
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def execute(bundle, transport=None, shopping=fixture_shopping):
    return smoke.run_local_smoke(credential_dir="OFFLINE-DUMMY-DIRECTORY", confirm=True, _credential_loader=lambda directory: bundle, _shopping=shopping,
                                 _transport=transport or FakeTransport(), _clock=lambda: NOW)


def test_opt_in_precedes_credentials_and_network():
    def forbidden(*args):
        pytest.fail("No credential read without confirmation")
    with pytest.raises(smoke.SmokeError, match="confirm-local-stub-dryrun"):
        smoke.run_local_smoke(_credential_loader=forbidden)


@pytest.mark.parametrize("field,url", [
    ("gateway_url", "http://example.com:8080"), ("gateway_url", "http://127.0.0.1:8081"),
    ("gateway_url", "https://127.0.0.1:8080"), ("gateway_url", "http://localhost:8080"),
    ("gateway_url", "http://127.0.0.1:8080/"), ("gateway_url", "http://user:password@127.0.0.1:8080"),
    ("merchant_url", "http://127.0.0.1:8011"), ("merchant_url", "http://other.local:8010"),
    ("merchant_url", "https://127.0.0.1:8010"), ("merchant_url", "http://127.0.0.1:8010?x=1"),
])
def test_strict_origins_rejected_before_credential_read(field, url):
    def forbidden(*args):
        pytest.fail("No reads or requests for invalid origin")
    with pytest.raises(smoke.SmokeError) as error:
        smoke.run_local_smoke(credential_dir="OFFLINE-DUMMY-DIRECTORY", confirm=True, _credential_loader=forbidden, **{field: url})
    assert error.value.code == "origin"
    assert "password" not in str(error.value)


@pytest.mark.parametrize("name", ["HTTP_PROXY", "https_proxy", "ALL_PROXY"])
def test_proxy_environment_rejected_before_any_network(name, monkeypatch):
    monkeypatch.setenv(name, "http://TOKEN-CANARY@proxy.invalid")
    with pytest.raises(smoke.SmokeError) as error:
        smoke.run_local_smoke(credential_dir="OFFLINE-DUMMY-DIRECTORY", confirm=True, _credential_loader=lambda directory: pytest.fail("No credential reads"))
    assert error.value.code == "proxy" and "CANARY" not in str(error.value)


def test_two_actual_graphs_and_sdk_requests_preserve_all_evidence(bundle, capsys):
    fake = FakeTransport()
    states = []
    def shopping(*args, **kwargs):
        state = fixture_shopping(*args, **kwargs)
        states.append(state)
        return state
    observations = execute(bundle, fake, shopping)
    assert [o.decision for o in observations] == ["allow", "block"]
    assert observations[1].injection_flagged and all(o.dry_run and o.audit_event_present for o in observations)
    assert len(fake.calls) == 2
    for call, state, case in zip(fake.calls, states, bundle[0]["cases"]):
        body = json.loads(call["body"])
        assert call["url"] == smoke.INTENT_URL
        assert call["headers"]["Authorization"] == "Bearer " + TOKEN_CANARY
        assert "X-Internal-Token" not in call["headers"]
        assert body["agent_id"] == bundle[1]["agent_id"] and body["dry_run"] is True
        assert body["mandate"] == case["mandate"] and body["idempotency_key"] == case["idempotency_key"]
        assert (body["merchant_id"], body["amount"], body["currency"]) == ("merchant_a", "29.99", "USD")
        assert len(body["source_contexts"]) == len(state["captured_pages"]) == 13
        assert body["source_contexts"] == [c.to_dict() for c in state["source_contexts"]]
        assert all(c["html"] for c in body["source_contexts"])
    assert any(c["url"].endswith(get_attack("poison_06").poisoned_path)
               for c in json.loads(fake.calls[1]["body"])["source_contexts"])
    assert TOKEN_CANARY not in repr(observations) + capsys.readouterr().out


@pytest.mark.parametrize("problem", ["signature", "principal", "agent", "public_key", "duplicate_id", "duplicate_nonce",
                                     "duplicate_key", "merchant", "category", "limit", "expired", "case_order", "purchase_amount"])
def test_both_signed_cases_validated_before_even_merchant_requests(bundle, problem):
    report, config = deepcopy(bundle)
    second, first = report["cases"][1], report["cases"][0]
    if problem in {"signature", "principal", "agent", "public_key", "merchant", "category", "limit", "expired"}:
        field, value = {"signature": ("signature", "BAD"), "principal": ("principal_id", "wrong"),
            "agent": ("agent_id", "wrong"), "public_key": ("public_key", "BAD"),
            "merchant": ("allowed_merchants", ["merchant_lookalike"]), "category": ("allowed_categories", ["books"]),
            "limit": ("max_total", "99.00"), "expired": ("expires_at", "2020-01-01T00:00:00Z")}[problem]
        second["mandate"][field] = value
    elif problem in {"duplicate_id", "duplicate_nonce"}:
        field = "mandate_id" if problem == "duplicate_id" else "nonce"
        second["mandate"][field] = first["mandate"][field]
        # Keep this mutation correctly signed, so duplicate detection itself is exercised.
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        key = Ed25519PrivateKey.from_private_bytes(bytes(32))
        second["mandate"]["signature"] = base64.b64encode(key.sign(canonical_mandate_bytes(second["mandate"]))).decode()
    elif problem == "duplicate_key":
        second["idempotency_key"] = first["idempotency_key"]
    elif problem == "case_order":
        report["cases"].reverse()
    else:
        report["purchase"]["amount"] = "27.99"
    fake = FakeTransport()
    def forbidden(*args, **kwargs):
        pytest.fail("No merchant graph execution until BOTH signed cases validate")
    with pytest.raises(smoke.SmokeError):
        execute((report, config), fake, forbidden)
    assert fake.calls == []


def test_expired_credentials_error_is_sanitized_without_regeneration(bundle):
    with pytest.raises(smoke.SmokeError) as caught:
        smoke.run_local_smoke(credential_dir="OFFLINE-DUMMY-DIRECTORY", confirm=True, _credential_loader=lambda directory: bundle,
                             _clock=lambda: datetime(2030, 1, 1, tzinfo=timezone.utc))
    assert caught.value.code == "expired"
    assert TOKEN_CANARY not in str(caught.value)


@pytest.mark.parametrize("problem", ["product", "citation", "duplicate", "price", "currency", "merchant"])
def test_selector_fails_without_exact_product_and_valid_citation(problem):
    state = fixture_shopping(smoke.TASK, AgentConfig(), model=smoke.ExactHubSelector())
    products = state["product_candidates"]
    target = next(p for p in products if p.merchant_id == "merchant_a" and p.product_id == "usb-c-hub")
    pages = state["model_pages"]
    refs = tuple(c.ref for c in state["source_contexts"])
    if problem == "product":
        products = tuple(p for p in products if p != target)
    elif problem == "citation":
        pages = tuple(p for p in pages if p.url != target.url)
    elif problem == "duplicate":
        products = products + (target,)
    else:
        changed = replace(target, **{"price": {"unit_price": target.unit_price + 1},
                                    "currency": {"currency": "EUR"},
                                    "merchant": {"merchant_name": "Northstar Office Outlet"}}[problem])
        products = tuple(changed if p == target else p for p in products)
    request = ModelRequest(smoke.TASK, understand(smoke.TASK), products, pages, refs)
    with pytest.raises(smoke.SmokeError) as error:
        smoke.ExactHubSelector().select(request)
    assert error.value.code == "selection"


@pytest.mark.parametrize("bad", [response("step_up"), response("block"), response(dry_run=False),
                               response(dry_run=None), response(audit_event_id=" "),
                               TransportResponse(302, b"TOKEN-CANARY"), TransportResponse(401, b"TOKEN-CANARY"),
                               TransportResponse(200, b"TOKEN-CANARY")])
def test_unexpected_clean_response_aborts_before_second_post(bundle, bad, capsys):
    fake = FakeTransport([bad])
    with pytest.raises(smoke.SmokeError) as error:
        execute(bundle, fake)
    assert len(fake.calls) == 1
    assert "CANARY" not in str(error.value) + capsys.readouterr().out


@pytest.mark.parametrize("bad", [response(), response("block", reasons=["TOKEN-CANARY"]),
    response("block", checks=dict(mandate="pass", injection="pass", intent="flagged", risk="unknown")),
    response("block", dry_run=False), response("block", audit_event_id="")])
def test_poison_response_requires_actual_injection_block(bundle, bad):
    fake = FakeTransport([response(), bad])
    with pytest.raises(smoke.SmokeError):
        execute(bundle, fake)
    assert len(fake.calls) == 2


def test_timeout_is_ambiguous_and_never_retried(bundle):
    fake = FakeTransport([GatewayTimeoutError("TOKEN-CANARY")])
    with pytest.raises(smoke.SmokeError) as error:
        execute(bundle, fake)
    assert len(fake.calls) == 1
    assert error.value.outcome_unknown is True and error.value.attempted_posts == 1
    assert error.value.code == "gateway_timeout"
    assert "CANARY" not in str(error.value) and error.value.__cause__ is None


@pytest.mark.parametrize("route", ["/v1/mandates", "/v1/demo/run", "/v1/events", "/v1/benchmark/report",
                                  "/v1/payments/x/approve", "/v1/payments/x/reject", "/v1/payments/intents?x=1"])
def test_transport_exact_route_allowlist(route):
    fake = FakeTransport()
    gate = smoke.IntentOnlyTransport((b"one", b"two"), _delegate=fake)
    with pytest.raises(smoke.SmokeError):
        gate.send(url=smoke.GATEWAY_URL + route, body=b"one", headers={}, timeout=1, max_response_bytes=1000)
    assert fake.calls == [] and gate.attempted_posts == 0


def test_transport_two_posts_maximum_and_body_binding():
    fake = FakeTransport()
    gate = smoke.IntentOnlyTransport((b"one", b"two"), _delegate=fake)
    kwargs = dict(url=smoke.INTENT_URL, headers={}, timeout=1, max_response_bytes=1000)
    with pytest.raises(smoke.SmokeError):
        gate.send(body=b"unplanned-live-body", **kwargs)
    gate.send(body=b"one", **kwargs)
    gate.send(body=b"two", **kwargs)
    with pytest.raises(smoke.SmokeError):
        gate.send(body=b"two", **kwargs)
    assert len(fake.calls) == gate.attempted_posts == 2


def test_urllib_redirect_refused_without_following(monkeypatch):
    from urllib.error import HTTPError
    from agentguard_workstream_c.sdk.exceptions import RedirectRefusedError
    transport = UrllibTransport()
    calls = []
    def fake_open(request, timeout):
        calls.append(request)
        raise HTTPError(smoke.INTENT_URL, 302, "TOKEN-CANARY", {}, None)
    monkeypatch.setattr(transport._opener, "open", fake_open)
    with pytest.raises(RedirectRefusedError):
        transport.send(url=smoke.INTENT_URL, body=b"dummy", headers={}, timeout=1, max_response_bytes=100)
    assert len(calls) == 1
    assert calls[0].get_method() == "POST"


def test_no_post_if_second_graph_cannot_preserve_poison_evidence(bundle):
    fake = FakeTransport()
    def shopping(task, config, *, model):
        if config.attack_id:
            config = replace(config, attack_id=None)
        return fixture_shopping(task, config, model=model)
    with pytest.raises(smoke.SmokeError):
        execute(bundle, fake, shopping)
    assert fake.calls == []


def test_no_tokens_reach_shopping_graph(bundle):
    seen = []
    def shopping(task, config, *, model):
        seen.append((task, config, model))
        assert TOKEN_CANARY not in repr((task, config, model))
        return fixture_shopping(task, config, model=model)
    execute(bundle, shopping=shopping)
    assert len(seen) == 2


def test_cli_rejects_credential_arguments_without_echoing(capsys):
    assert smoke.main(["--gateway-token", "TOKEN-SECRET-CANARY"]) == 1
    assert "CANARY" not in capsys.readouterr().out


def test_confirmation_flag_cannot_be_abbreviated(monkeypatch, capsys):
    monkeypatch.setattr(smoke, "run_local_smoke", lambda **kwargs: pytest.fail("No abbreviated confirmation"))
    assert smoke.main(["--confirm"]) == 1
    assert "Unsupported command arguments" in capsys.readouterr().out


def test_cli_reports_only_safe_observations(monkeypatch, capsys):
    monkeypatch.setattr(smoke, "run_local_smoke", lambda **kwargs:
                        (smoke.SmokeObservation("clean", "allow", True, True, False),
                         smoke.SmokeObservation("poisoned_evidence", "block", True, True, True)))
    assert smoke.main(["--confirm-local-stub-dryrun", "--credential-dir", "OFFLINE-DUMMY-DIRECTORY"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "clean: PASS; observed=allow; dry_run=true; audit_reference_present=true",
        "poisoned_evidence: PASS; observed=block; dry_run=true; audit_reference_present=true"]


def test_missing_acknowledgment_is_not_accepted(bundle):
    data = json.loads(response().body)
    del data["dry_run"]
    fake = FakeTransport([TransportResponse(200, json.dumps(data).encode())])
    with pytest.raises(smoke.SmokeError) as caught:
        execute(bundle, fake)
    assert len(fake.calls) == 1
    assert caught.value.outcome_unknown is True


def test_credential_read_failure_stops_before_graph_and_hides_os_error():
    def bad_loader(directory):
        raise OSError("PATH-TOKEN-SECRET-CANARY")
    def forbidden(*args, **kwargs):
        pytest.fail("Graph must not run after credential read failure")
    with pytest.raises(smoke.SmokeError) as caught:
        smoke.run_local_smoke(credential_dir="OFFLINE-DUMMY-DIRECTORY", confirm=True, _credential_loader=bad_loader, _shopping=forbidden)
    assert caught.value.code == "credentials"
    assert "CANARY" not in str(caught.value) and caught.value.__cause__ is None


def test_timeout_on_second_submission_has_two_attempts_only(bundle):
    fake = FakeTransport([response(), GatewayTimeoutError("TOKEN-CANARY")])
    with pytest.raises(smoke.SmokeError) as caught:
        execute(bundle, fake)
    assert len(fake.calls) == caught.value.attempted_posts == 2
    assert caught.value.outcome_unknown is True


def test_failed_transport_cannot_be_reused():
    fake = FakeTransport([GatewayTimeoutError("TOKEN-CANARY")])
    gate = smoke.IntentOnlyTransport((b"one", b"two"), _delegate=fake)
    kwargs = dict(url=smoke.INTENT_URL, headers={}, timeout=1, max_response_bytes=1000)
    with pytest.raises(GatewayTimeoutError):
        gate.send(body=b"one", **kwargs)
    with pytest.raises(smoke.SmokeError):
        gate.send(body=b"two", **kwargs)
    assert gate.closed and len(fake.calls) == 1


@pytest.mark.skipif(__import__("os").name != "nt", reason="Windows read-only credential handles")
def test_read_only_loader_on_protected_dummy_files_only(bundle, tmp_path, monkeypatch):
    from agentguard_workstream_c.integration.windows_files import WindowsOutput
    root = tmp_path / "AgentGuardSecrets"
    with WindowsOutput(root) as storage:
        for name, document in zip((smoke.MANDATES_FILE, smoke.SECRETS_FILE), bundle):
            stream = storage.create(name)
            stream.write(json.dumps(document).encode())
            stream.flush()
    before = {p.name: (p.stat().st_size, p.stat().st_mtime_ns) for p in root.iterdir()}
    monkeypatch.setenv("USERPROFILE", "IGNORED-ENVIRONMENT-OVERRIDE")
    assert smoke._read_credentials(root) == bundle
    assert before == {p.name: (p.stat().st_size, p.stat().st_mtime_ns) for p in root.iterdir()}


@pytest.mark.skipif(__import__("os").name != "nt", reason="Windows read-only credential handles")
def test_duplicate_json_in_protected_dummy_file_fails_before_network(tmp_path, monkeypatch):
    from agentguard_workstream_c.integration.windows_files import WindowsOutput
    with WindowsOutput(tmp_path / "AgentGuardSecrets") as storage:
        storage.create(smoke.MANDATES_FILE).write(b'{"case":"one","case":"TOKEN-CANARY"}')
        storage.create(smoke.SECRETS_FILE).write(b'{}')
    with pytest.raises(smoke.SmokeError) as caught:
        smoke.run_local_smoke(credential_dir=tmp_path / "AgentGuardSecrets", confirm=True)
    assert caught.value.code == "credentials" and "CANARY" not in str(caught.value)


def test_missing_credential_files_do_not_start_graph(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("No graph after missing credential files")
    with pytest.raises(smoke.SmokeError) as caught:
        smoke.run_local_smoke(credential_dir=tmp_path / "AgentGuardSecrets", confirm=True, _shopping=forbidden)
    assert caught.value.code == "credentials"


@pytest.mark.parametrize("offset", [101, 202])
def test_fresh_identity_bundle_drives_both_sdk_intents(bundle, offset):
    # Deterministic UUID4-shaped dummy IDs, never real random credential creation.
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    key = Ed25519PrivateKey.from_private_bytes(bytes(32))
    report, config = deepcopy(bundle)
    principal = "principal_smoke_" + str(UUID(int=offset, version=4))
    agent = "agent_smoke_" + str(UUID(int=offset + 1, version=4))
    config.update(principal_id=principal, agent_id=agent)
    for case in report["cases"]:
        case["mandate"].update(principal_id=principal, agent_id=agent)
        case["mandate"]["signature"] = base64.b64encode(key.sign(canonical_mandate_bytes(case["mandate"]))).decode()
    fake = FakeTransport()
    observations = execute((report, config), fake)
    assert [o.decision for o in observations] == ["allow", "block"]
    assert len(fake.calls) == 2
    for call in fake.calls:
        body = json.loads(call["body"])
        assert body["agent_id"] == body["mandate"]["agent_id"] == agent
        assert body["mandate"]["principal_id"] == principal


@pytest.mark.parametrize("field", ["principal_id", "agent_id"])
def test_consistent_but_blank_or_control_identity_is_rejected(bundle, field):
    report, config = deepcopy(bundle)
    config[field] = "\n"
    for case in report["cases"]:
        case["mandate"][field] = config[field]
    fake = FakeTransport()
    with pytest.raises(smoke.SmokeError):
        execute((report, config), fake, shopping=lambda *a, **k: pytest.fail("No graph before identity validation"))
    assert fake.calls == []


@pytest.mark.parametrize("field", ["principal_id", "agent_id"])
def test_tampered_secrets_identity_rejected_before_network(bundle, field):
    report, config = deepcopy(bundle)
    config[field] += "-different"
    fake = FakeTransport()
    with pytest.raises(smoke.SmokeError):
        execute((report, config), fake, shopping=lambda *a, **k: pytest.fail("Both identities must agree first"))
    assert fake.calls == []


def test_matching_new_public_key_still_requires_valid_signatures(bundle):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    report, config = deepcopy(bundle)
    key = Ed25519PrivateKey.from_private_bytes(bytes([7]) * 32)
    config["public_key"] = base64.b64encode(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
    for case in report["cases"]:
        case["mandate"]["public_key"] = config["public_key"]
    fake = FakeTransport()
    with pytest.raises(smoke.SmokeError):
        execute((report, config), fake, shopping=lambda *a, **k: pytest.fail("Internal consistency is not signature verification"))
    assert fake.calls == []
    # Properly signed dummy replacement keys are supported; no public-key pin remains.
    for case in report["cases"]:
        case["mandate"]["signature"] = base64.b64encode(key.sign(canonical_mandate_bytes(case["mandate"]))).decode()
    assert [o.decision for o in execute((report, config))] == ["allow", "block"]


def test_second_well_formed_invalid_signature_rejected_before_graph(bundle):
    report, config = deepcopy(bundle)
    report["cases"][1]["mandate"]["signature"] = base64.b64encode(bytes(64)).decode()
    fake = FakeTransport()
    with pytest.raises(smoke.SmokeError):
        execute((report, config), fake, shopping=lambda *a, **k: pytest.fail("Second signature must verify first"))
    assert fake.calls == []


@pytest.mark.parametrize("directory", [None, "", "   "])
def test_missing_directory_has_no_fallback(directory):
    with pytest.raises(smoke.SmokeError) as error:
        smoke.run_local_smoke(credential_dir=directory, confirm=True,
                             _credential_loader=lambda *a: pytest.fail("No implicit directory reads"))
    assert error.value.code == "arguments"


def test_cli_requires_directory_and_does_not_echo_arguments(monkeypatch, capsys):
    monkeypatch.setattr(smoke, "run_local_smoke", lambda **k: pytest.fail("No implicit old bundle"))
    assert smoke.main(["--confirm-local-stub-dryrun"]) == 1
    assert capsys.readouterr().out == smoke._MESSAGES["arguments"] + "\n"


def test_cli_passes_explicit_directory_only(monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(smoke, "run_local_smoke", lambda **kwargs: seen.append(kwargs) or ())
    assert smoke.main(["--confirm-local-stub-dryrun", "--credential-dir", "DUMMY-NEW-BUNDLE"]) == 0
    assert seen[0]["credential_dir"] == "DUMMY-NEW-BUNDLE"
    assert seen[0]["confirm"] is True
    assert capsys.readouterr().out == ""


@pytest.mark.skipif(__import__("os").name != "nt", reason="Windows path and ACL policy")
@pytest.mark.parametrize("directory", [r"\\untrusted.invalid\share\credentials", r"\\?\C:\credentials",
                                       r"C:\credentials:stream", "http://untrusted.invalid/credentials"])
def test_unsupported_directory_rejected_before_path_open(directory, monkeypatch):
    from agentguard_workstream_c.integration.windows_files import NativeWindows
    api = NativeWindows()
    monkeypatch.setattr(smoke, "NativeWindows", lambda: api)
    monkeypatch.setattr(api, "open_directory", lambda *a: pytest.fail("No UNC, device or ADS path opens"))
    with pytest.raises(smoke.SmokeError) as error:
        smoke.run_local_smoke(credential_dir=directory, confirm=True)
    assert error.value.code == "credentials"


@pytest.mark.skipif(__import__("os").name != "nt", reason="Windows path and ACL policy")
def test_remote_mapped_drive_rejected_before_path_open(monkeypatch):
    from agentguard_workstream_c.integration.windows_files import NativeWindows
    api = NativeWindows()
    monkeypatch.setattr(smoke, "NativeWindows", lambda: api)
    monkeypatch.setattr(api.k, "GetDriveTypeW", lambda *a: 4)
    monkeypatch.setattr(api, "open_directory", lambda *a: pytest.fail("No remote drive opens"))
    with pytest.raises(smoke.SmokeError) as error:
        smoke.run_local_smoke(credential_dir=r"Z:\dummy-credentials", confirm=True)
    assert error.value.code == "credentials"


@pytest.mark.skipif(__import__("os").name != "nt", reason="Windows path and ACL policy")
def test_native_acl_failure_aborts_before_file_content_read(tmp_path, monkeypatch):
    from agentguard_workstream_c.integration.windows_files import NativeWindows
    api = NativeWindows()
    monkeypatch.setattr(smoke, "NativeWindows", lambda: api)
    monkeypatch.setattr(api.a, "GetSecurityInfo", lambda *a: 5)
    monkeypatch.setattr(api.k, "CreateDirectoryW", lambda *a: pytest.fail("Read loader must not create directories"))
    with pytest.raises(smoke.SmokeError) as error:
        smoke.run_local_smoke(credential_dir=tmp_path, confirm=True,
                             _shopping=lambda *a, **k: pytest.fail("No graph after ACL failure"))
    assert error.value.code == "credentials"
    assert list(tmp_path.iterdir()) == []


@pytest.mark.skipif(__import__("os").name != "nt", reason="Windows path and ACL policy")
def test_permissive_selected_directory_rejected_without_reading_contents(tmp_path, monkeypatch):
    from agentguard_workstream_c.integration.windows_files import NativeWindows, WindowsOutput
    from test_local_smoke_windows_files import set_test_acl
    root = tmp_path / "unsafe-output"
    with WindowsOutput(root):
        pass
    api = NativeWindows()
    rules = "".join(f"(A;OICI;FA;;;{sid})" for sid in sorted(api.trusted))
    set_test_acl(api, root, "D:P" + rules + "(A;;FR;;;WD)")
    original = api.k.CreateFileW
    opened = []
    def record(path, *args):
        opened.append(path)
        return original(path, *args)
    monkeypatch.setattr(api.k, "CreateFileW", record)
    monkeypatch.setattr(smoke, "NativeWindows", lambda: api)
    with pytest.raises(smoke.SmokeError):
        smoke.run_local_smoke(credential_dir=root, confirm=True)
    assert not any(Path(path).name in (smoke.MANDATES_FILE, smoke.SECRETS_FILE) for path in opened)


@pytest.mark.skipif(__import__("os").name != "nt", reason="Windows path and ACL policy")
def test_unsafe_ancestor_rejected_before_selected_directory_read(tmp_path, monkeypatch):
    from agentguard_workstream_c.integration.windows_files import NativeWindows, WindowsOutput
    from test_local_smoke_windows_files import set_test_acl
    ancestor = tmp_path / "unsafe-parent"
    with WindowsOutput(ancestor):
        pass
    root = ancestor / "selected-output"
    with WindowsOutput(root):
        pass
    api = NativeWindows()
    rules = "".join(f"(A;OICI;FA;;;{sid})" for sid in sorted(api.trusted))
    set_test_acl(api, ancestor, "D:P" + rules + "(A;;0x40;;;WD)")
    opened = []
    original = api.open_directory
    def record(path):
        opened.append(path)
        return original(path)
    monkeypatch.setattr(api, "open_directory", record)
    monkeypatch.setattr(smoke, "NativeWindows", lambda: api)
    with pytest.raises(smoke.SmokeError):
        smoke.run_local_smoke(credential_dir=root, confirm=True)
    assert root not in opened


@pytest.mark.skipif(__import__("os").name != "nt", reason="Windows path and ACL policy")
def test_reparse_ancestor_rejected_before_any_bundle_read(monkeypatch):
    import os
    from agentguard_workstream_c.integration.windows_files import NativeWindows
    api = NativeWindows()
    root = Path(os.environ["SystemDrive"] + "/Users/All Users/AgentGuard-unused-diagnostic")
    original = api.k.CreateFileW
    opened = []
    def record(path, *args):
        opened.append(path)
        return original(path, *args)
    monkeypatch.setattr(api.k, "CreateFileW", record)
    monkeypatch.setattr(smoke, "NativeWindows", lambda: api)
    with pytest.raises(smoke.SmokeError):
        smoke.run_local_smoke(credential_dir=root, confirm=True)
    assert not any(Path(path).name in (smoke.MANDATES_FILE, smoke.SECRETS_FILE) for path in opened)


@pytest.mark.skipif(__import__("os").name != "nt", reason="Windows protected dummy bundle")
def test_new_directory_bundle_loaded_end_to_end_with_fake_transport(bundle, tmp_path):
    from agentguard_workstream_c.integration.windows_files import WindowsOutput
    root = tmp_path / "AgentGuardSecrets-NEW"
    with WindowsOutput(root) as storage:
        for name, document in zip((smoke.MANDATES_FILE, smoke.SECRETS_FILE), bundle):
            stream = storage.create(name)
            stream.write(json.dumps(document).encode())
            stream.flush()
    fake = FakeTransport()
    observed = smoke.run_local_smoke(credential_dir=root, confirm=True, _shopping=fixture_shopping,
                                    _transport=fake, _clock=lambda: NOW)
    assert [item.decision for item in observed] == ["allow", "block"]
    assert all(json.loads(call["body"])["agent_id"] == bundle[1]["agent_id"] for call in fake.calls)
    assert not (tmp_path / "AgentGuardSecrets").exists()  # No fallback path created or needed.


@pytest.mark.skipif(__import__("os").name != "nt", reason="Windows protected dummy bundle")
def test_file_acl_failure_prevents_content_stream_open(bundle, tmp_path, monkeypatch):
    import msvcrt
    from agentguard_workstream_c.integration.windows_files import NativeWindows, StorageSecurityError, WindowsOutput
    root = tmp_path / "protected-dummy-bundle"
    with WindowsOutput(root) as storage:
        for name, document in zip((smoke.MANDATES_FILE, smoke.SECRETS_FILE), bundle):
            stream = storage.create(name)
            stream.write(json.dumps(document).encode())
            stream.flush()
    api = NativeWindows()
    original = api.verify_acl
    def fail_file_only(handle, **kwargs):
        if kwargs.get("strict") and not kwargs.get("directory"):
            raise StorageSecurityError("Simulated file ACL failure")
        return original(handle, **kwargs)
    monkeypatch.setattr(api, "verify_acl", fail_file_only)
    monkeypatch.setattr(smoke, "NativeWindows", lambda: api)
    monkeypatch.setattr(msvcrt, "open_osfhandle", lambda *a: pytest.fail("No file contents before handle ACL verification"))
    with pytest.raises(smoke.SmokeError) as caught:
        smoke.run_local_smoke(credential_dir=root, confirm=True,
                             _shopping=lambda *a, **k: pytest.fail("No graph after file ACL failure"))
    assert caught.value.code == "credentials"
