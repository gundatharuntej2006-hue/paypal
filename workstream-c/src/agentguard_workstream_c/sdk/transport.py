"""One-shot standard-library HTTP transport with no proxies or redirect following."""

from dataclasses import dataclass
from http.client import HTTPException
import socket
from typing import Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .exceptions import (
    GatewayConnectionError, GatewayTimeoutError, InvalidResponseError,
    RedirectRefusedError, ResponseTooLargeError,
)


@dataclass(frozen=True)
class TransportResponse:
    status_code: int
    body: bytes


class Transport(Protocol):
    def send(self, *, url: str, body: bytes, headers: Mapping[str, str], timeout: float,
             max_response_bytes: int) -> TransportResponse:
        """Perform exactly one POST, with no retries or redirects."""
        ...


class _RefuseRedirects(HTTPRedirectHandler):
    def http_error_302(self, req, fp, code, msg, headers):
        fp.close()
        raise RedirectRefusedError(code)

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302


class UrllibTransport:
    def __init__(self):
        # Do not inherit credentials/routing from proxy environment variables.
        self._opener = build_opener(ProxyHandler({}), _RefuseRedirects())

    def send(self, *, url: str, body: bytes, headers: Mapping[str, str], timeout: float,
             max_response_bytes: int) -> TransportResponse:
        request = Request(url, data=body, headers=dict(headers), method="POST")
        try:
            try:
                response = self._opener.open(request, timeout=timeout)
            except HTTPError as exc:
                response = exc  # Plain-text errors are bounded just like successful responses.
            with response:
                status = response.code
                if 300 <= status < 400:
                    raise RedirectRefusedError(status)
                raw = response.read(max_response_bytes + 1)
                if len(raw) > max_response_bytes:
                    raise ResponseTooLargeError("Gateway response exceeded the configured byte limit")
                declared = response.headers.get("Content-Length")
                if declared is not None and (not declared.isdigit() or int(declared) != len(raw)):
                    raise InvalidResponseError("Gateway response body was incomplete")
                return TransportResponse(status_code=status, body=raw)
        except URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise GatewayTimeoutError("Gateway request timed out; payment outcome is unknown") from None
            raise GatewayConnectionError("Gateway connection failed; payment outcome is unknown") from None
        except (TimeoutError, socket.timeout):
            raise GatewayTimeoutError("Gateway request timed out; payment outcome is unknown") from None
        except (OSError, HTTPException):
            raise GatewayConnectionError("Gateway connection/read failed; payment outcome is unknown") from None
