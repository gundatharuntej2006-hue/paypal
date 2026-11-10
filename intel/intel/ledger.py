"""Write events into the gateway's ledger through its internal hook (contract: contracts/intel/README.md).

Without LEDGER_URL, events are kept in memory and logged (local development only).
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

import httpx

from intel.config import Settings

log = logging.getLogger("intel.ledger")
MEMORY: list[dict[str, Any]] = []


class LedgerError(Exception):
    pass


@lru_cache
def get_http() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=5)


async def append(
    settings: Settings, event_type: str, *, payment_id: str | None, idempotency_key: str | None, data: dict[str, Any]
) -> None:
    event = {"type": event_type, "payment_id": payment_id, "idempotency_key": idempotency_key, "source": "intel", "data": data}
    if not settings.ledger_url:
        MEMORY.append(event)
        log.info("ledger (memory): %s %s", event_type, idempotency_key)
        return
    try:
        response = await get_http().post(
            settings.ledger_url, json=event, headers={"X-Internal-Token": settings.ledger_token or ""}
        )
    except httpx.HTTPError as error:
        raise LedgerError(f"ledger_unreachable: {error}") from None
    if response.status_code == 409:  # already recorded under this idempotency key
        return
    if response.status_code >= 300:
        raise LedgerError(f"ledger_http_{response.status_code}")
