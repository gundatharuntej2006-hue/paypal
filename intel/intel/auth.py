"""Shared-secret check for internal callers (the gateway). PayPal's webhook route does not use it."""

from __future__ import annotations

import hmac

from fastapi import Depends, Header, HTTPException

from intel.config import Settings, get_settings


def require_token(
    x_internal_token: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    if settings.api_token and not hmac.compare_digest(
        (x_internal_token or "").encode(), settings.api_token.encode()
    ):
        raise HTTPException(status_code=401, detail="invalid or missing X-Internal-Token")
