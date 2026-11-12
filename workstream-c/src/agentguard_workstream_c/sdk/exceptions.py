"""SDK errors; transport/protocol failure does not prove a payment failed."""


class GuardError(Exception):
    """Base error for the Gateway SDK."""


class ValidationError(GuardError, ValueError):
    """Request is invalid locally; no submission was attempted."""


class SubmissionDisabledError(GuardError):
    """Payment submission requires an explicit client-level opt-in."""


class GatewayError(GuardError):
    """Remote/transport outcome may be unknown. Never retry automatically."""

    outcome_unknown = True

    def __init__(self, message: str):
        super().__init__(message)
        self.idempotency_key: str | None = None


class GatewayConnectionError(GatewayError):
    """Connection or response-read failure; execution outcome is not established."""


class GatewayTimeoutError(GatewayError):
    """Request timed out; the server may already have executed payment."""


class InvalidResponseError(GatewayError):
    """Gateway did not return a valid decision response."""


class ResponseTooLargeError(InvalidResponseError):
    """Response exceeded the configured bound; not parsed as a decision."""


class GatewayHTTPError(GatewayError):
    """HTTP error with bounded server text kept separately, never auto-logged."""

    def __init__(self, status_code: int, body_text: str = ""):
        super().__init__(f"Gateway returned HTTP {status_code}; payment outcome is not established")
        self.status_code = status_code
        self.body_text = body_text


class IdempotencyConflictError(GatewayHTTPError):
    """409 does not contain the original decision; do not rotate the key."""


class RedirectRefusedError(GatewayHTTPError):
    """A payment POST redirect was refused without following it."""
