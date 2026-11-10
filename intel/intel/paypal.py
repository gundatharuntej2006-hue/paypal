"""Small PayPal REST client: OAuth token caching and webhook signature checks."""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import time
import zlib
from collections.abc import Mapping
from datetime import datetime, timezone
try:
    from datetime import UTC
except ImportError:
    UTC = timezone.utc
from functools import lru_cache
from urllib.parse import urlsplit

import httpx
from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding

from intel.config import Settings

BASE_URLS = {"sandbox": "https://api-m.sandbox.paypal.com", "live": "https://api-m.paypal.com"}


class PayPalError(Exception):
    """PayPal could not be reached or refused the call. Webhooks answer 503 so PayPal retries."""


class PayPal:
    def __init__(self, env: str, client_id: str | None, client_secret: str | None, http: httpx.AsyncClient | None = None):
        self.base_url = BASE_URLS[env]
        self._client_id, self._client_secret = client_id, client_secret
        self._http = http or httpx.AsyncClient(timeout=10)
        self._token: str | None = None
        self._token_expires = 0.0
        self._token_lock = asyncio.Lock()
        self._certs: dict[str, x509.Certificate] = {}

    async def access_token(self) -> str:
        async with self._token_lock:
            if self._token and time.monotonic() < self._token_expires:
                return self._token
            if not (self._client_id and self._client_secret):
                raise PayPalError("paypal_credentials_missing")
            response = await self._http.post(
                f"{self.base_url}/v1/oauth2/token",
                auth=(self._client_id, self._client_secret),
                data={"grant_type": "client_credentials"},
            )
            if response.status_code != 200:
                raise PayPalError(f"oauth_http_{response.status_code}")
            body = response.json()
            self._token = body["access_token"]
            self._token_expires = time.monotonic() + int(body.get("expires_in", 300)) - 60
            return self._token

    async def verify_by_postback(self, headers: Mapping[str, str], raw_body: bytes, webhook_id: str) -> bool:
        """Ask PayPal's verify-webhook-signature API. Does not work for webhook-simulator events."""
        fields = {
            "auth_algo": headers["paypal-auth-algo"],
            "cert_url": headers["paypal-cert-url"],
            "transmission_id": headers["paypal-transmission-id"],
            "transmission_sig": headers["paypal-transmission-sig"],
            "transmission_time": headers["paypal-transmission-time"],
            "webhook_id": webhook_id,
        }
        # PayPal needs the event exactly as received, so splice in the raw body instead of re-serializing it.
        body = json.dumps(fields)[:-1] + ', "webhook_event": ' + raw_body.decode("utf-8") + "}"
        response = await self._http.post(
            f"{self.base_url}/v1/notifications/verify-webhook-signature",
            content=body,
            headers={"Authorization": f"Bearer {await self.access_token()}", "Content-Type": "application/json"},
        )
        if response.status_code != 200:
            raise PayPalError(f"verify_http_{response.status_code}")
        return response.json().get("verification_status") == "SUCCESS"

    async def verify_locally(self, headers: Mapping[str, str], raw_body: bytes, webhook_id: str) -> bool:
        """Check the signature ourselves: works for simulator events (webhook id 'WEBHOOK_ID') too."""
        cert_url = headers["paypal-cert-url"]
        parts = urlsplit(cert_url)
        host = parts.hostname or ""
        if parts.scheme != "https" or not (host == "paypal.com" or host.endswith(".paypal.com")):
            return False  # never trust a certificate from anywhere else
        if headers["paypal-auth-algo"].upper() != "SHA256WITHRSA":
            return False
        cert = await self._certificate(cert_url)
        now = datetime.now(UTC)
        if not (cert.not_valid_before_utc <= now <= cert.not_valid_after_utc):
            return False
        message = "|".join(
            [headers["paypal-transmission-id"], headers["paypal-transmission-time"], webhook_id, str(zlib.crc32(raw_body))]
        ).encode()
        try:
            signature = base64.b64decode(headers["paypal-transmission-sig"], validate=True)
            cert.public_key().verify(signature, message, padding.PKCS1v15(), hashes.SHA256())  # type: ignore[call-arg, arg-type]
        except (InvalidSignature, binascii.Error, ValueError, TypeError):
            return False
        return True

    async def _certificate(self, url: str) -> x509.Certificate:
        if url not in self._certs:
            response = await self._http.get(url)
            if response.status_code != 200:
                raise PayPalError(f"cert_http_{response.status_code}")
            self._certs[url] = x509.load_pem_x509_certificate(response.content)
        return self._certs[url]


@lru_cache
def get_client(settings: Settings) -> PayPal:
    return PayPal(settings.paypal_env, settings.paypal_client_id, settings.paypal_client_secret)
