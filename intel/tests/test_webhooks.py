import base64
import json
import zlib
from datetime import datetime, timedelta, timezone
try:
    from datetime import UTC
except ImportError:
    UTC = timezone.utc

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient

from intel import ledger, paypal, webhooks
from intel.config import Settings, get_settings
from intel.main import app

CERT_URL = "https://api.sandbox.paypal.com/v1/notifications/certs/CERT-test"
WEBHOOK_ID = "WH-TEST-1"
EVENT = {
    "id": "WH-EVT-1",
    "event_type": "PAYMENT.CAPTURE.COMPLETED",
    "resource_type": "capture",
    "summary": "Payment completed for 29.99 USD",
    "create_time": "2026-11-11T10:00:00Z",
    "resource": {
        "id": "CAP-1",
        "status": "COMPLETED",
        "amount": {"currency_code": "USD", "value": "29.99"},
        "custom_id": "pay_881",
        "supplementary_data": {"related_ids": {"order_id": "ORD-1", "authorization_id": "AUTH-1"}},
    },
}


@pytest.fixture(scope="module")
def signer():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "messageverificationcerts.paypal.com")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=30))
        .sign(key, hashes.SHA256())
    )
    return key, cert.public_bytes(serialization.Encoding.PEM)


def signed_headers(key, body, cert_url=CERT_URL):
    sent_at = "2026-11-11T10:00:00Z"
    message = f"tx-1|{sent_at}|{WEBHOOK_ID}|{zlib.crc32(body)}".encode()
    signature = base64.b64encode(key.sign(message, padding.PKCS1v15(), hashes.SHA256())).decode()
    return {
        "PAYPAL-TRANSMISSION-ID": "tx-1",
        "PAYPAL-TRANSMISSION-TIME": sent_at,
        "PAYPAL-TRANSMISSION-SIG": signature,
        "PAYPAL-CERT-URL": cert_url,
        "PAYPAL-AUTH-ALGO": "SHA256withRSA",
        "Content-Type": "application/json",
    }


@pytest.fixture
def receiver(monkeypatch, signer):
    """Build a test client whose PayPal (and optionally ledger) calls go to fake servers."""

    def make(verify="local", paypal_server=None, ledger_server=None):
        values = dict(
            mode="live", api_token=None, anthropic_api_key=None, scan_model="m", judge_model="m", deadline_s=2.5,
            paypal_client_id="id", paypal_client_secret="secret", paypal_webhook_id=WEBHOOK_ID, webhook_verify=verify,
            ledger_url="https://gateway.test/internal/events" if ledger_server else None,
        )

        def cert_server(request):
            return httpx.Response(200, content=signer[1]) if request.url.path.endswith("CERT-test") else httpx.Response(404)

        client = paypal.PayPal("sandbox", "id", "secret",
                               http=httpx.AsyncClient(transport=httpx.MockTransport(paypal_server or cert_server)))
        monkeypatch.setattr(paypal, "get_client", lambda _settings: client)
        if ledger_server:
            monkeypatch.setattr(ledger, "get_http", lambda: httpx.AsyncClient(transport=httpx.MockTransport(ledger_server)))
        app.dependency_overrides[get_settings] = lambda: Settings(**values)
        return TestClient(app)

    webhooks._SEEN.clear()
    ledger.MEMORY.clear()
    yield make
    app.dependency_overrides.clear()


def received():
    return [e for e in ledger.MEMORY if e["type"] == "webhook.received"]


def test_capture_is_recorded_once_even_if_delivered_twice(receiver, signer):
    body = json.dumps(EVENT).encode()
    with receiver() as client:
        first = client.post("/webhooks/paypal", content=body, headers=signed_headers(signer[0], body))
        second = client.post("/webhooks/paypal", content=body, headers=signed_headers(signer[0], body))
    assert (first.json()["status"], second.json()["status"]) == ("recorded", "duplicate")
    [event] = received()
    assert (event["payment_id"], event["idempotency_key"]) == ("pay_881", "paypal:WH-EVT-1")
    data = event["data"]
    assert (data["paypal_status"], data["capture_id"], data["authorization_id"], data["order_id"]) == (
        "captured", "CAP-1", "AUTH-1", "ORD-1")


def test_tampered_payload_is_rejected(receiver, signer):
    body = json.dumps(EVENT).encode()
    headers = signed_headers(signer[0], body)
    tampered = body.replace(b'"29.99"', b'"2999.00"')
    with receiver() as client:
        response = client.post("/webhooks/paypal", content=tampered, headers=headers)
    assert response.status_code == 400
    assert not received()
    assert [e["type"] for e in ledger.MEMORY] == ["webhook.rejected"]


@pytest.mark.parametrize("cert_url", ["https://evil.example.com/cert.pem", "https://paypal.com.evil.example/cert.pem",
                                      "http://api.sandbox.paypal.com/cert.pem"])
def test_certificate_from_outside_paypal_is_refused(receiver, signer, cert_url):
    body = json.dumps(EVENT).encode()
    with receiver() as client:
        response = client.post("/webhooks/paypal", content=body, headers=signed_headers(signer[0], body, cert_url))
    assert response.status_code == 400 and not received()


def test_postback_sends_the_event_exactly_as_received(receiver):
    body = json.dumps(EVENT, indent=1).encode()  # unusual formatting must survive untouched
    seen = {}

    def paypal_server(request):
        if request.url.path == "/v1/oauth2/token":
            seen["auth"] = request.headers["authorization"]
            return httpx.Response(200, json={"access_token": "token-1", "expires_in": 3600})
        if request.url.path == "/v1/notifications/verify-webhook-signature":
            posted = request.content.decode()
            seen["verbatim"] = body.decode() in posted
            ok = json.loads(posted)["webhook_event"] == EVENT and json.loads(posted)["webhook_id"] == WEBHOOK_ID
            return httpx.Response(200, json={"verification_status": "SUCCESS" if ok else "FAILURE"})
        return httpx.Response(404)

    headers = {name: "x" for name in ("PAYPAL-TRANSMISSION-ID", "PAYPAL-TRANSMISSION-TIME", "PAYPAL-TRANSMISSION-SIG")}
    headers |= {"PAYPAL-CERT-URL": CERT_URL, "PAYPAL-AUTH-ALGO": "SHA256withRSA"}
    with receiver(verify="postback", paypal_server=paypal_server) as client:
        response = client.post("/webhooks/paypal", content=body, headers=headers)
    assert response.json()["status"] == "recorded"
    assert seen["verbatim"] and seen["auth"].startswith("Basic ")


def test_paypal_unreachable_means_retry_later(receiver, signer):
    def paypal_server(request):
        return httpx.Response(500)

    body = json.dumps(EVENT).encode()
    with receiver(verify="postback", paypal_server=paypal_server) as client:
        response = client.post("/webhooks/paypal", content=body, headers=signed_headers(signer[0], body))
    assert response.status_code == 503 and not ledger.MEMORY


def test_ledger_down_means_retry_and_event_is_not_marked_seen(receiver, signer):
    calls = []

    def ledger_server(request):
        calls.append(json.loads(request.content))
        return httpx.Response(500 if len(calls) == 1 else 201)

    body = json.dumps(EVENT).encode()
    with receiver(ledger_server=ledger_server) as client:
        first = client.post("/webhooks/paypal", content=body, headers=signed_headers(signer[0], body))
        second = client.post("/webhooks/paypal", content=body, headers=signed_headers(signer[0], body))
    assert (first.status_code, second.json()["status"]) == (500, "recorded")
    assert calls[1]["idempotency_key"] == "paypal:WH-EVT-1"


def test_missing_signature_headers_are_rejected(receiver):
    with receiver() as client:
        response = client.post("/webhooks/paypal", content=json.dumps(EVENT).encode())
    assert response.status_code == 400


def test_refund_is_linked_to_its_capture():
    refund = {
        "id": "WH-EVT-9", "event_type": "PAYMENT.CAPTURE.REFUNDED", "resource_type": "refund",
        "resource": {"id": "REF-1", "status": "COMPLETED", "amount": {"currency_code": "USD", "value": "29.99"},
                     "links": [{"rel": "up", "href": "https://api.sandbox.paypal.com/v2/payments/captures/CAP-1"}]},
    }
    data = webhooks.describe(refund)
    assert (data["paypal_status"], data["refund_id"], data["capture_id"]) == ("refunded", "REF-1", "CAP-1")
