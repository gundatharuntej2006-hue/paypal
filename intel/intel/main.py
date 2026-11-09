"""AgentGuard intelligence service.

Run locally from the intel/ folder:  py -m uvicorn intel.main:app --reload --port 8001
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from fastapi import Depends, FastAPI, Header, HTTPException

from intel import judge, risk, scan, stub, webhooks
from intel.config import Settings, get_settings
from intel.models import (
    CONTRACT_VERSION,
    JudgeRequest,
    JudgeResponse,
    RiskRequest,
    RiskResponse,
    ScanRequest,
    ScanResponse,
)

log = logging.getLogger("intel")
app = FastAPI(title="AgentGuard Intelligence Service", version=CONTRACT_VERSION)
app.include_router(webhooks.router)  # PayPal calls this directly, so no X-Internal-Token

R = TypeVar("R", ScanResponse, JudgeResponse, RiskResponse)
Handler = Callable[[Any], Awaitable[Any]]


LIVE: dict[str, Handler] = {"scan": scan.run, "judge": judge.run, "risk": risk.run}
STUB: dict[str, Handler] = {"scan": stub.scan, "judge": stub.judge, "risk": stub.risk}


async def run_signal(handler: Handler, req: object, unknown: Callable[[str], R], deadline_s: float) -> R:
    """Run one signal under a hard deadline. Any failure becomes an explicit unknown, never a pass."""
    start = time.perf_counter()
    try:
        result = await asyncio.wait_for(handler(req), timeout=deadline_s)
    except TimeoutError:
        result = unknown("timeout")
    except NotImplementedError:
        result = unknown("not_implemented")
    except Exception:
        log.exception("signal failed")
        result = unknown("internal_error")
    elapsed_ms = int((time.perf_counter() - start) * 1000)
    return result.model_copy(update={"latency_ms": elapsed_ms})


def require_token(
    x_internal_token: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    if settings.api_token and not hmac.compare_digest(
        (x_internal_token or "").encode(), settings.api_token.encode()
    ):
        raise HTTPException(status_code=401, detail="invalid or missing X-Internal-Token")


def _handler(name: str, settings: Settings) -> Handler:
    return (STUB if settings.mode == "stub" else LIVE)[name]


@app.get("/health")
def health(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    return {
        "status": "ok",
        "mode": settings.mode,
        "llm_configured": settings.anthropic_api_key is not None,
        "contract_version": CONTRACT_VERSION,
    }


@app.post("/scan/content", response_model=ScanResponse, dependencies=[Depends(require_token)])
async def scan_content(req: ScanRequest, settings: Settings = Depends(get_settings)) -> ScanResponse:
    return await run_signal(
        _handler("scan", settings), req, lambda r: ScanResponse.unknown(r, version=settings.mode), settings.deadline_s
    )


@app.post("/judge/intent", response_model=JudgeResponse, dependencies=[Depends(require_token)])
async def judge_intent(req: JudgeRequest, settings: Settings = Depends(get_settings)) -> JudgeResponse:
    return await run_signal(
        _handler("judge", settings), req, lambda r: JudgeResponse.unknown(r, version=settings.mode), settings.deadline_s
    )


@app.post("/risk/score", response_model=RiskResponse, dependencies=[Depends(require_token)])
async def risk_score(req: RiskRequest, settings: Settings = Depends(get_settings)) -> RiskResponse:
    return await run_signal(
        _handler("risk", settings), req, lambda r: RiskResponse.unknown(r, version=settings.mode), settings.deadline_s
    )
