"""Prepare disposable, ACL-protected smoke inputs. No services or network calls.

Invoke with --confirm-create-local-smoke-credentials on Windows only. The private
key exists only in memory; only signed mandates, public identity and tokens persist.
Injected internal test dependencies are trusted code, not a security sandbox.
"""

import argparse
import base64
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import secrets
from uuid import uuid4

from .windows_files import NativeAPIError, WindowsOutput


class CredentialPreparationError(ValueError):
    """Sanitized preparation failure; never includes underlying exception text."""


TASK = "Buy 1 USB-C hub from merchant_a under 40 USD."
MANDATES_FILE = "local-smoke-mandates.json"
SECRETS_FILE = "local-smoke-secrets.json"
TOKEN_NAMES = ("AGENT_AUTH_TOKEN", "HUMAN_AUTH_TOKEN", "INTERNAL_LEDGER_TOKEN", "INTEL_API_TOKEN")


def _timestamp(value):
    instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if instant.tzinfo is None:
        raise ValueError("timezone required")
    t = instant.astimezone(timezone.utc)
    return f"{t.year:04d}-{t.month:02d}-{t.day:02d}T{t.hour:02d}:{t.minute:02d}:{t.second:02d}Z"


def canonical_mandate_bytes(mandate):
    """Match gateway/pkg/crypto/crypto.go at d4471a2 (not generic RFC 8785)."""
    try:
        fields = ("mandate_id", "principal_id", "agent_id", "task", "currency",
                  "max_total", "max_per_txn", "nonce", "public_key")
        payload = {key: mandate[key] for key in fields}
        if any(not isinstance(value, str) for value in payload.values()):
            raise ValueError
        for key in ("valid_from", "expires_at"):
            payload[key] = _timestamp(mandate[key])
        for key in ("step_up_over", "paypal_order_id"):
            if mandate.get(key):
                if not isinstance(mandate[key], str):
                    raise ValueError
                payload[key] = mandate[key]
        for key in ("allowed_merchants", "allowed_categories"):
            if mandate.get(key):
                values = mandate[key]
                if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
                    raise ValueError
                payload[key] = sorted(values)
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        for char, escaped in (("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026"),
                              ("\u2028", "\\u2028"), ("\u2029", "\\u2029")):
            encoded = encoded.replace(char, escaped)
        return encoded.encode("utf-8")
    except Exception:
        raise CredentialPreparationError("Mandate canonicalization failed.") from None


def _require_crypto():
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    except ImportError:
        raise CredentialPreparationError(
            'Optional cryptography dependency is unavailable. Install the local-smoke extra '
            'in the selected environment: python -m pip install -e ".[local-smoke]".'
        ) from None
    return Ed25519PrivateKey


def _new_key():
    return _require_crypto().generate()


def _documents(key_factory, token_factory, identifier_factory, now):
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    private = key_factory()
    public = private.public_key()
    public_key = base64.b64encode(public.public_bytes(Encoding.Raw, PublicFormat.Raw)).decode("ascii")
    principal = "principal_smoke_" + str(identifier_factory())
    agent = "agent_smoke_" + str(identifier_factory())
    tokens = {name: token_factory(32) for name in TOKEN_NAMES}
    if len(set(tokens.values())) != 4 or any(not isinstance(v, str) or len(v) < 32 for v in tokens.values()):
        raise ValueError("invalid random source")
    cases = []
    for label in ("clean", "poisoned_evidence"):
        mandate = dict(mandate_id="mdt_smoke_" + str(identifier_factory()), principal_id=principal,
                       agent_id=agent, task=TASK, currency="USD", max_total="40.00", max_per_txn="40.00",
                       step_up_over="40.00", allowed_merchants=["merchant_a"],
                       allowed_categories=["electronics_accessories"],
                       valid_from=(now - timedelta(seconds=30)).isoformat(),
                       expires_at=(now + timedelta(minutes=15)).isoformat(),
                       nonce=str(identifier_factory()), public_key=public_key)
        for field in ("valid_from", "expires_at"):
            mandate[field] = _timestamp(mandate[field])
        canonical = canonical_mandate_bytes(mandate)
        signature = private.sign(canonical)
        public.verify(signature, canonical)
        mandate["signature"] = base64.b64encode(signature).decode("ascii")
        cases.append(dict(case=label, mandate=mandate, idempotency_key=str(identifier_factory()),
                          attack_id=None if label == "clean" else "poison_06"))
    for field in ("mandate_id", "nonce"):
        if cases[0]["mandate"][field] == cases[1]["mandate"][field]:
            raise ValueError("invalid identifier source")
    if cases[0]["idempotency_key"] == cases[1]["idempotency_key"]:
        raise ValueError("invalid identifier source")
    documents = dict(format_version=1, task=TASK, purchase=dict(merchant_id="merchant_a", product_id="usb-c-hub",
                     product_name="USB-C hub 7-in-1", quantity=1, unit_price="29.99", amount="29.99",
                     currency="USD", category="electronics_accessories"), cases=cases)
    configuration = dict(principal_id=principal, public_key=public_key, agent_id=agent, **tokens)
    return documents, configuration


def prepare_credentials(output_dir, *, confirm=False, _storage_factory=None, _document_factory=None):
    """Create a protected pair, retaining incomplete files for identity-safe review.

    Internal factories are trusted test code, never CLI configuration. No cleanup
    deletes a name: every successfully created file is reported on failure.
    """
    if confirm is not True:
        raise CredentialPreparationError("Explicit credential-creation confirmation is required.")
    if _document_factory is None:
        _require_crypto()  # Actionable dependency failure before touching the filesystem.
    storage = None
    try:
        directory = Path(output_dir).absolute()
        paths = (directory / MANDATES_FILE, directory / SECRETS_FILE)
        storage = (_storage_factory or WindowsOutput)(directory)
        with storage:
            streams = [storage.create(path.name) for path in paths]
            storage.verify()
            documents = (_document_factory() if _document_factory is not None else
                         _documents(_new_key, secrets.token_urlsafe, uuid4, datetime.now(timezone.utc)))
            for stream, document in zip(streams, documents):
                encoded = (json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
                if stream.write(encoded) != len(encoded):
                    raise OSError("incomplete output")
                stream.flush()
                os.fsync(stream.fileno())
            storage.verify()
        return paths[0], paths[1], documents[1]["principal_id"]
    except Exception as original:
        names = tuple(name for name in getattr(storage, "created_files", ())
                      if name in (MANDATES_FILE, SECRETS_FILE))
        message = "Secure credential output failed; no credential values are shown. "
        if names:
            message += "Files created by this invocation (possibly incomplete): " + ", ".join(names) + ". "
            message += ("Do not use this pair. Review these files in the selected output directory; "
                        "never overwrite existing files; use a fresh secured directory for a new attempt. "
                        "No automatic deletion was performed.")
        else:
            message += "No output files were created. Check local path security and existing destination files; never overwrite them."
        error = CredentialPreparationError(message)
        error.created_files = names
        if isinstance(original, NativeAPIError):
            # Reconstruct from allowlisted API + numeric code, never arbitrary
            # exception text. Only this sanitized native exception is chained.
            diagnostic = NativeAPIError(original.api_name, original.winerror, original.captured_last_error)
            error.args = (message + " " + str(diagnostic),)
            error.api_name = diagnostic.api_name
            error.winerror = diagnostic.winerror
            raise error from original
        raise error from None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-create-local-smoke-credentials", action="store_true")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args(argv)
    try:
        if not args.confirm_create_local_smoke_credentials:
            raise CredentialPreparationError("Explicit credential-creation confirmation is required.")
        directory = args.output_dir or Path(os.environ["USERPROFILE"]) / "AgentGuardSecrets"
        mandates, configuration, principal = prepare_credentials(directory, confirm=True)
    except CredentialPreparationError as error:
        print(str(error))
        return 1
    except Exception:
        print("Local smoke credential preparation failed; no credential values are shown.")
        return 1
    print(f"Mandates: {mandates}")
    print(f"Secrets: {configuration}")
    print(f"Principal: {principal}")
    print("Local smoke credentials prepared. No services or requests were started.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
