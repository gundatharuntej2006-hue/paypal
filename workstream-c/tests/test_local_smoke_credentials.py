"""Offline helper tests. Crypto fixtures skip individually without the optional extra."""

import base64
from copy import deepcopy
from datetime import datetime, timezone
import importlib.abc
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from uuid import UUID

import pytest

from agentguard_workstream_c.integration import prepare_credentials as helper


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Network access is forbidden")
    for name in ("socket", "getaddrinfo", "create_connection"):
        monkeypatch.setattr(socket, name, forbidden)


@pytest.fixture
def golden():
    path = Path(__file__).parents[1] / "fixtures/local-smoke/go-mandate-public-vector.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def crypto():
    ed = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519",
                            reason="requires optional local-smoke extra")
    return ed


@pytest.fixture
def fixed_documents(crypto):
    counts = {"token": 0, "id": 0}
    def token(length):
        counts["token"] += 1
        return "DUMMY-NOT-A-CREDENTIAL-" + str(counts["token"]).zfill(20)
    def identifier():
        counts["id"] += 1
        return UUID(int=counts["id"])
    def build(**overrides):
        args = dict(key_factory=lambda: crypto.Ed25519PrivateKey.from_private_bytes(bytes(32)),
                    token_factory=token, identifier_factory=identifier,
                    now=datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc))
        args.update(overrides)
        return helper._documents(**args)
    return build


class DummyStorage:
    """File I/O test double; no claim of Windows security verification."""
    def __init__(self, directory, fault=None):
        self.directory, self.fault = directory, fault
        self.created_files, self.streams = [], []
        self.verified = 0
    def __enter__(self):
        self.directory.mkdir(exist_ok=True)
        return self
    def create(self, name):
        if self.fault == ("open", len(self.created_files)):
            raise OSError("SENSITIVE-CANARY")
        raw = (self.directory / name).open("xb")
        self.created_files.append(name)
        stream = FaultStream(raw, len(self.streams), self.fault)
        self.streams.append(stream)
        return stream
    def verify(self):
        self.verified += 1
    def __exit__(self, *args):
        error = None
        for stream in reversed(self.streams):
            try:
                stream.close()
            except Exception as exc:
                error = exc
        if error:
            raise error


class FaultStream:
    def __init__(self, raw, index, fault):
        self.raw, self.index, self.fault = raw, index, fault
    def _fail(self, operation):
        if self.fault == (operation, self.index):
            raise OSError("SENSITIVE-CANARY")
    def write(self, body):
        self._fail("write")
        return self.raw.write(body)
    def flush(self):
        self._fail("flush")
        return self.raw.flush()
    def fileno(self):
        return self.raw.fileno()
    def close(self):
        self.raw.close()
        self._fail("close")


def dummy_documents():
    # No key/token creation, even when cryptography is absent.
    return {"cases": ["dummy"]}, {"principal_id": "dummy-principal", "test_value": "DUMMY-NOT-SECRET"}


def test_exact_go_golden_bytes(golden):
    assert helper.canonical_mandate_bytes(golden["mandate"]) == golden["canonical_json"].encode("utf-8")
    assert golden["source_commit"] == "d4471a2d2b00d3fa840c406b68c8915faee0b8dc"
    assert "private_key_base64" not in golden


def test_go_golden_signature_verifies(golden, crypto):
    crypto.Ed25519PublicKey.from_public_bytes(base64.b64decode(golden["public_key_base64"])).verify(
        base64.b64decode(golden["signature_base64"]), helper.canonical_mandate_bytes(golden["mandate"]))


def test_signatures_and_tampering(fixed_documents, crypto):
    from cryptography.exceptions import InvalidSignature
    document, configuration = fixed_documents()
    public = crypto.Ed25519PublicKey.from_public_bytes(base64.b64decode(configuration["public_key"]))
    for case in document["cases"]:
        mandate = case["mandate"]
        signature = base64.b64decode(mandate["signature"])
        public.verify(signature, helper.canonical_mandate_bytes(mandate))
        mandate["max_total"] = "40.01"
        with pytest.raises(InvalidSignature):
            public.verify(signature, helper.canonical_mandate_bytes(mandate))


def test_policy_purchase_identity_and_unique_values(fixed_documents):
    document, configuration = fixed_documents()
    assert document["task"] == helper.TASK
    assert document["purchase"] == dict(merchant_id="merchant_a", product_id="usb-c-hub",
        product_name="USB-C hub 7-in-1", quantity=1, unit_price="29.99", amount="29.99",
        currency="USD", category="electronics_accessories")
    first, second = document["cases"]
    assert [c["attack_id"] for c in document["cases"]] == [None, "poison_06"]
    assert first["idempotency_key"] != second["idempotency_key"]
    assert all(str(UUID(c["idempotency_key"])) == c["idempotency_key"] for c in document["cases"])
    for name in ("mandate_id", "nonce"):
        assert first["mandate"][name] != second["mandate"][name]
    for case in document["cases"]:
        m = case["mandate"]
        assert m["principal_id"] == configuration["principal_id"]
        assert m["agent_id"] == configuration["agent_id"]
        assert m["public_key"] == configuration["public_key"]
        assert m["task"] == helper.TASK and m["currency"] == "USD"
        assert all(m[f] == "40.00" for f in ("max_total", "max_per_txn", "step_up_over"))
        assert m["allowed_merchants"] == ["merchant_a"]
        assert m["allowed_categories"] == ["electronics_accessories"]
        assert "paypal_order_id" not in m
        assert m["valid_from"] == "2026-11-11T11:59:30Z"
        assert m["expires_at"] == "2026-11-11T12:15:00Z"
    tokens = [configuration[name] for name in helper.TOKEN_NAMES]
    assert len(set(tokens)) == 4
    assert set(configuration) == {"principal_id", "agent_id", "public_key", *helper.TOKEN_NAMES}
    raw = json.dumps([document, configuration])
    assert "private" not in raw.lower() and "seed" not in raw.lower()
    assert not any(token in json.dumps(document) for token in tokens)


def test_fixed_material_persistence_only(tmp_path, fixed_documents):
    paths = helper.prepare_credentials(tmp_path / "dummy", confirm=True,
        _storage_factory=DummyStorage, _document_factory=fixed_documents)
    assert set(p.name for p in paths[0].parent.iterdir()) == {helper.MANDATES_FILE, helper.SECRETS_FILE}
    assert "private" not in "".join(p.read_text() for p in paths[:2]).lower()


def test_opt_in_required_before_any_side_effect(tmp_path):
    def forbidden(*args):
        pytest.fail("No storage or documents without confirmation")
    with pytest.raises(helper.CredentialPreparationError, match="confirmation"):
        helper.prepare_credentials(tmp_path / "absent", _storage_factory=forbidden, _document_factory=forbidden)
    assert not (tmp_path / "absent").exists()


@pytest.mark.parametrize("filename", [helper.MANDATES_FILE, helper.SECRETS_FILE])
def test_refuses_overwrite(tmp_path, filename):
    existing = tmp_path / filename
    existing.write_text("DO NOT OVERWRITE")
    with pytest.raises(helper.CredentialPreparationError, match="never overwrite"):
        helper.prepare_credentials(tmp_path, confirm=True, _storage_factory=DummyStorage, _document_factory=dummy_documents)
    assert existing.read_text() == "DO NOT OVERWRITE"


@pytest.mark.parametrize("operation", ["serialize", "open", "write", "flush", "fsync", "close"])
@pytest.mark.parametrize("index", [0, 1])
def test_partial_failures_sanitized_and_identified(tmp_path, monkeypatch, operation, index):
    storage = DummyStorage(tmp_path / "dummy", (operation, index))
    original_dumps, original_fsync = helper.json.dumps, helper.os.fsync
    calls = {"serialize": 0, "fsync": 0}
    def dumps(*args, **kwargs):
        at = calls["serialize"]
        calls["serialize"] += 1
        if operation == "serialize" and at == index:
            raise ValueError("SENSITIVE-CANARY")
        return original_dumps(*args, **kwargs)
    def fsync(*args):
        at = calls["fsync"]
        calls["fsync"] += 1
        if operation == "fsync" and at == index:
            raise OSError("SENSITIVE-CANARY")
        return original_fsync(*args)
    monkeypatch.setattr(helper.json, "dumps", dumps)
    monkeypatch.setattr(helper.os, "fsync", fsync)
    with pytest.raises(helper.CredentialPreparationError) as caught:
        helper.prepare_credentials(storage.directory, confirm=True,
            _storage_factory=lambda path: storage, _document_factory=dummy_documents)
    error = caught.value
    assert "SENSITIVE-CANARY" not in str(error)
    assert error.created_files == tuple(storage.created_files)
    assert "No automatic deletion" in str(error) if storage.created_files else "No output files" in str(error)
    assert all(stream.raw.closed for stream in storage.streams)
    assert {p.name for p in storage.directory.iterdir()} == set(storage.created_files)
    if operation != "open" and index == 1:
        assert json.loads((storage.directory / helper.MANDATES_FILE).read_text()) == dummy_documents()[0]
    # Rerun cannot overwrite any output created by the failed invocation.
    if storage.created_files:
        before = {p.name: p.read_bytes() for p in storage.directory.iterdir()}
        with pytest.raises(helper.CredentialPreparationError):
            helper.prepare_credentials(storage.directory, confirm=True,
                _storage_factory=DummyStorage, _document_factory=dummy_documents)
        assert before == {name: (storage.directory / name).read_bytes() for name in before}


def test_crypto_failure_is_sanitized(tmp_path):
    def bad_document():
        raise RuntimeError("SENSITIVE-CANARY")
    with pytest.raises(helper.CredentialPreparationError) as error:
        helper.prepare_credentials(tmp_path, confirm=True, _storage_factory=DummyStorage, _document_factory=bad_document)
    assert "SENSITIVE-CANARY" not in str(error.value)
    assert error.value.__cause__ is None


def test_preparation_retains_safe_native_cause_without_arguments(tmp_path):
    from agentguard_workstream_c.integration.windows_files import NativeAPIError
    original = NativeAPIError("CreateDirectoryW", 5)
    class FailedStorage(DummyStorage):
        def __enter__(self):
            raise original
    with pytest.raises(helper.CredentialPreparationError) as caught:
        helper.prepare_credentials(tmp_path / "PATH-SECRET-CANARY", confirm=True,
            _storage_factory=FailedStorage, _document_factory=dummy_documents)
    assert caught.value.__cause__ is original
    assert caught.value.api_name == "CreateDirectoryW" and caught.value.winerror == 5
    assert "CreateDirectoryW failed (Win32 5): Access is denied." in str(caught.value)
    assert "CANARY" not in str(caught.value)
    assert not (tmp_path / "PATH-SECRET-CANARY").exists()


def test_cli_native_error_is_actionable_without_secret_values(tmp_path, monkeypatch, capsys):
    from agentguard_workstream_c.integration.windows_files import NativeAPIError
    class FailedStorage(DummyStorage):
        def __enter__(self):
            raise NativeAPIError("GetSecurityInfo", 5)
    original_prepare = helper.prepare_credentials
    def failing_prepare(path, **kwargs):
        return original_prepare(path, confirm=True, _storage_factory=FailedStorage,
                                _document_factory=dummy_documents)
    monkeypatch.setattr(helper, "prepare_credentials", failing_prepare)
    assert helper.main(["--confirm-create-local-smoke-credentials", "--output-dir",
                        str(tmp_path / "PATH-SECRET-CANARY")]) == 1
    output = capsys.readouterr()
    assert "GetSecurityInfo failed (Win32 5): Access is denied." in output.out
    assert "CANARY" not in output.out + output.err


def test_cli_without_confirmation_never_prepares(monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        pytest.fail("No preparation without confirmation")
    monkeypatch.setattr(helper, "prepare_credentials", forbidden)
    assert helper.main([]) == 1
    assert "confirmation" in capsys.readouterr().out


def test_cli_success_prints_only_paths_principal_and_safe_message(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(helper, "prepare_credentials", lambda *a, **k:
                        (tmp_path / helper.MANDATES_FILE, tmp_path / helper.SECRETS_FILE, "dummy-principal"))
    assert helper.main(["--confirm-create-local-smoke-credentials", "--output-dir", str(tmp_path)]) == 0
    assert capsys.readouterr().out.splitlines() == [f"Mandates: {tmp_path / helper.MANDATES_FILE}",
        f"Secrets: {tmp_path / helper.SECRETS_FILE}", "Principal: dummy-principal",
        "Local smoke credentials prepared. No services or requests were started."]


def test_canonical_go_escaping_sorting_timestamp_and_exclusions(golden):
    mandate = deepcopy(golden["mandate"])
    mandate.update(task="<>&\u2028\u2029 café\n\"\\", valid_from="2026-11-09T12:00:00.123456+02:00",
                   signature="excluded", unknown="excluded", paypal_order_id="order_1")
    result = helper.canonical_mandate_bytes(mandate)
    assert b'"task":"\\u003c\\u003e\\u0026\\u2028\\u2029 caf\xc3\xa9\\n\\\"\\\\"' in result
    assert b'"valid_from":"2026-11-09T10:00:00Z"' in result
    assert b'"paypal_order_id":"order_1"' in result
    assert b'excluded' not in result
    assert json.loads(result)["allowed_merchants"] == ["merchant_books_123", "merchant_staples"]
    mandate.update(allowed_merchants=[], allowed_categories=[], step_up_over="", paypal_order_id="")
    output = json.loads(helper.canonical_mandate_bytes(mandate))
    assert all(key not in output for key in ("allowed_merchants", "allowed_categories", "step_up_over", "paypal_order_id"))


@pytest.mark.parametrize("source", ["tokens", "identifiers"])
def test_duplicate_sources_fail_closed(fixed_documents, source):
    kwargs = ({"token_factory": lambda size: "DUMMY-DUPLICATE-TOKEN-NEVER-USE-THIS"} if source == "tokens"
              else {"identifier_factory": lambda: UUID(int=1)})
    with pytest.raises(ValueError):
        fixed_documents(**kwargs)


# Child process blocks imports even when the parent has the optional extra.
_BLOCK_CRYPTO = '''
import importlib.abc, sys
class BlockCrypto(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'cryptography' or fullname.startswith('cryptography.'):
            raise ModuleNotFoundError('optional crypto blocked for test')
sys.meta_path.insert(0, BlockCrypto())
'''


def test_ordinary_collection_without_optional_crypto():
    code = _BLOCK_CRYPTO + '''
import pytest
raise SystemExit(pytest.main(['tests/test_local_smoke_credentials.py', '--collect-only', '-q', '-p', 'no:cacheprovider']))
'''
    result = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout.decode(errors="replace")
    assert b"collected" in result.stdout


def test_import_and_opt_in_without_optional_crypto(tmp_path):
    code = _BLOCK_CRYPTO + '''
from agentguard_workstream_c.integration import prepare_credentials as helper
assert not any(name.startswith('cryptography') for name in sys.modules)
assert helper.main([]) == 1
try:
    helper.prepare_credentials(sys.argv[1], confirm=True)
except helper.CredentialPreparationError as error:
    assert 'local-smoke' in str(error)
else:
    raise AssertionError('missing dependency must fail closed')
'''
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")
    path = tmp_path / "must-not-exist"
    result = subprocess.run([sys.executable, "-B", "-c", code, str(path)], env=environment,
                            capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert not path.exists()
