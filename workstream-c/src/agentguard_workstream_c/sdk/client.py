"""Offline by default; explicitly gated, authenticated dry-run gateway requests."""

from decimal import Decimal
from http.client import HTTPException
import json
import math
from urllib.parse import urlsplit
from urllib.error import URLError

from .exceptions import (
    GatewayError, GatewayConnectionError, GatewayTimeoutError, GatewayHTTPError, IdempotencyConflictError, InvalidResponseError,
    RedirectRefusedError, ResponseTooLargeError, SubmissionDisabledError, ValidationError,
)
from .models import DecisionResponse, PaymentIntent
from .transport import Transport, TransportResponse, UrllibTransport


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate response property")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError("nonfinite JSON number")


class GuardClient:
    def __init__(self, base_url: str, *, payment_submission_enabled: bool = False,
                 gateway_token: str | None = None,
                 timeout: float = 10.0, max_response_bytes: int = 1_000_000,
                 transport: Transport | None = None):
        if not isinstance(base_url, str) or any(ord(char) < 33 for char in base_url) or "\\" in base_url:
            raise ValidationError("base_url must be an HTTP(S) origin")
        try:
            parsed = urlsplit(base_url)
            port = parsed.port
        except ValueError:
            raise ValidationError("invalid base_url") from None
        if (parsed.scheme not in ("http", "https") or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.path not in ("", "/") or parsed.query or parsed.fragment or port == 0):
            raise ValidationError("base_url must be an HTTP(S) origin without credentials, path, query or fragment")
        if type(payment_submission_enabled) is not bool:
            raise ValidationError("payment_submission_enabled must be an explicit bool")
        if gateway_token is not None and (not isinstance(gateway_token, str) or not gateway_token
                                         or any(not 33 <= ord(char) <= 126 for char in gateway_token)):
            raise ValidationError("gateway_token must be a nonempty visible ASCII token without whitespace")
        if payment_submission_enabled and gateway_token is None:
            raise ValidationError("explicit gateway_token is required for network submission")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise ValidationError("timeout must be a positive finite number")
        if type(max_response_bytes) is not int or max_response_bytes <= 0:
            raise ValidationError("max_response_bytes must be a positive integer")
        self.base_url = base_url.rstrip("/")
        self.payment_submission_enabled = payment_submission_enabled
        self._gateway_token = gateway_token
        self.timeout = timeout
        self.max_response_bytes = max_response_bytes
        self._transport = transport if transport is not None else UrllibTransport()

    def request_payment(self, intent: PaymentIntent | None = None, **fields) -> DecisionResponse:
        """Submit a dry-run intent once only with explicit opt-in and authentication.

        Supply a validated PaymentIntent or keyword arguments for its constructor.
        Timeouts/errors may follow authorization or capture. Do not retry or infer
        payment failure from an exception.
        """
        if self.payment_submission_enabled is not True:
            raise SubmissionDisabledError("Network submission is disabled; explicit opt-in is required for dry-run")
        if self._gateway_token is None:
            raise ValidationError("explicit gateway_token is required for network submission")
        if intent is not None and fields:
            raise ValidationError("supply either PaymentIntent or its keyword fields")
        if intent is None:
            try:
                intent = PaymentIntent(**fields)
            except TypeError:
                raise ValidationError("missing or unsupported payment-intent fields") from None
        if not isinstance(intent, PaymentIntent):
            raise ValidationError("intent must be a PaymentIntent")
        if intent.dry_run is not True:
            raise ValidationError("only dry-run requests may be submitted")
        body = intent.to_json()
        try:
            response = self._transport.send(
                url=self.base_url + "/v1/payments/intents", body=body,
                headers={"Content-Type": "application/json", "Accept": "application/json",
                         "Authorization": "Bearer " + self._gateway_token},
                timeout=self.timeout, max_response_bytes=self.max_response_bytes,
            )
            if not isinstance(response, TransportResponse) or type(response.status_code) is not int or not isinstance(response.body, bytes):
                raise InvalidResponseError("Transport returned an invalid HTTP response")
            if len(response.body) > self.max_response_bytes:
                raise ResponseTooLargeError("Gateway response exceeded the configured byte limit")
            if 300 <= response.status_code < 400:
                raise RedirectRefusedError(response.status_code)
            if response.status_code != 200:
                error_type = IdempotencyConflictError if response.status_code == 409 else GatewayHTTPError
                raise error_type(response.status_code, response.body.decode("utf-8", errors="replace"))
            try:
                data = json.loads(response.body.decode("utf-8"), parse_float=Decimal,
                                  parse_constant=_invalid_constant, object_pairs_hook=_object)
                decision = DecisionResponse.from_dict(data)
            except (ValueError, TypeError, RecursionError):
                raise InvalidResponseError("Gateway returned invalid JSON or an invalid decision structure") from None
            if decision.dry_run is not True:
                raise InvalidResponseError("Gateway did not explicitly acknowledge dry_run=true; execution outcome is unknown")
            return decision
        except GatewayError as exc:
            exc.idempotency_key = intent.idempotency_key
            raise
        except (OSError, URLError, HTTPException) as exc:
            reason = exc.reason if isinstance(exc, URLError) else exc
            error_type = GatewayTimeoutError if isinstance(reason, TimeoutError) else GatewayConnectionError
            error = error_type("Gateway transport failed; payment outcome is unknown")
            error.idempotency_key = intent.idempotency_key
            raise error from None
