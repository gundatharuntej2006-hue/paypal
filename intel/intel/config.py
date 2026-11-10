"""Settings read from environment variables (see README for the list)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal


@dataclass(frozen=True)
class Settings:
    mode: Literal["live", "stub"]
    api_token: str | None
    anthropic_api_key: str | None
    scan_model: str
    judge_model: str
    deadline_s: float
    # PayPal webhooks
    paypal_env: Literal["sandbox", "live"] = "sandbox"
    paypal_client_id: str | None = None
    paypal_client_secret: str | None = None
    paypal_webhook_id: str | None = None
    webhook_verify: Literal["postback", "local"] = "postback"
    # Ledger hook on the gateway (events from webhooks go there)
    ledger_url: str | None = None
    ledger_token: str | None = None
    # Gateway base URL, used by reconciliation to read the ledger (defaults to LEDGER_URL's host)
    gateway_url: str | None = None


def _choice(name: str, default: str, allowed: tuple[str, ...]) -> str:
    value = os.environ.get(name, default).strip().lower()
    if value not in allowed:
        raise ValueError(f"{name} must be one of {allowed}, got {value!r}")
    return value


LEDGER_PATH = "/internal/ledger/events"


def _ledger_url(value: str | None) -> str | None:
    """Accept a full URL, or a bare Render host[:port] (render.yaml fromService gives no scheme or path)."""
    if not value:
        return None
    return value if "://" in value else f"http://{value.strip('/')}{LEDGER_PATH}"


def _gateway_url(value: str | None, ledger_url: str | None) -> str | None:
    if value:
        value = value.strip().rstrip("/")
        return value if "://" in value else f"http://{value}"
    if ledger_url and ledger_url.endswith(LEDGER_PATH):
        return ledger_url[: -len(LEDGER_PATH)]
    return None


@lru_cache
def get_settings() -> Settings:
    env = os.environ.get
    ledger_url = _ledger_url(env("LEDGER_URL"))
    return Settings(
        mode=_choice("INTEL_MODE", "live", ("live", "stub")),  # type: ignore[arg-type]
        api_token=env("INTEL_API_TOKEN") or None,
        anthropic_api_key=env("ANTHROPIC_API_KEY") or None,
        scan_model=env("INTEL_SCAN_MODEL", "claude-haiku-5-5"),
        judge_model=env("INTEL_JUDGE_MODEL", "claude-haiku-5-5"),
        deadline_s=float(env("INTEL_DEADLINE_S", "2.5")),
        paypal_env=_choice("PAYPAL_ENV", "sandbox", ("sandbox", "live")),  # type: ignore[arg-type]
        paypal_client_id=env("PAYPAL_CLIENT_ID") or None,
        paypal_client_secret=env("PAYPAL_CLIENT_SECRET") or None,
        paypal_webhook_id=env("PAYPAL_WEBHOOK_ID") or None,
        webhook_verify=_choice("PAYPAL_WEBHOOK_VERIFY", "postback", ("postback", "local")),  # type: ignore[arg-type]
        ledger_url=ledger_url,
        ledger_token=env("LEDGER_TOKEN") or None,
        gateway_url=_gateway_url(env("GATEWAY_URL"), ledger_url),
    )
