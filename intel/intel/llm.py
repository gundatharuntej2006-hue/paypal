"""LLM wrapper: structured output, one retry on malformed output, and clean failure reasons.

Two providers: Anthropic (SDK) and Google Gemini (plain HTTP, so it works on the free tier
without another dependency). Every failure raises LLMUnavailable with a short reason.
Callers turn that into an 'unknown' result.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, TypeVar

import anthropic
import httpx
from pydantic import BaseModel

from intel.config import get_settings

T = TypeVar("T", bound=BaseModel)
PROMPTS_DIR = Path(__file__).parent / "prompts"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
_GEMINI_REFUSALS = {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION", "IMAGE_SAFETY"}
log = logging.getLogger(__name__)


class LLMUnavailable(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class _Retry(LLMUnavailable):
    """A failure worth one more try (malformed or truncated output)."""


class _NextModel(LLMUnavailable):
    """This model can't answer for a while (rate limit, outage, unknown model): rest it and try the next one."""

    def __init__(self, reason: str, rest_s: float) -> None:
        super().__init__(reason)
        self.rest_s = rest_s


def _chain(models: str) -> list[str]:
    """INTEL_SCAN_MODEL / INTEL_JUDGE_MODEL may list several models, comma-separated, best first."""
    return [m.strip() for m in models.split(",") if m.strip()]


class LLMClient:
    def __init__(
        self,
        api_key: str | None,
        scan_model: str,
        judge_model: str,
        timeout_s: float,
        client: Any = None,
        provider: str = "anthropic",
    ):
        self.scan_model = scan_model
        self.judge_model = judge_model
        self.provider = provider
        self._api_key = api_key
        self._timeout_s = timeout_s
        self._http: httpx.AsyncClient | None = None
        self._http_loop: asyncio.AbstractEventLoop | None = None
        self._resting: dict[str, float] = {}  # model -> time.monotonic() when it may be tried again
        if provider == "gemini":
            self._client = client  # an httpx.AsyncClient in tests; otherwise made per event loop
            self._ready = api_key is not None
        else:
            # max_retries=0: the SDK would otherwise retry timeouts and blow the latency budget.
            self._client = client or (
                anthropic.AsyncAnthropic(api_key=api_key, max_retries=0, timeout=timeout_s) if api_key else None
            )
            self._ready = self._client is not None

    async def structured(self, *, model: str, system: str, user: str, output: type[T], max_tokens: int = 1024) -> T:
        if not self._ready:
            raise LLMUnavailable("no_llm_key")
        now = time.monotonic()
        ready = [m for m in _chain(model) if self._resting.get(m, 0.0) <= now]
        if not ready:  # every model is resting: answer at once instead of spending the deadline
            raise LLMUnavailable("llm_rate_limited")
        reason = "llm_rate_limited"
        for name in ready:
            try:
                result = await self._ask(name, system, user, output, max_tokens)
            except _NextModel as busy:
                self._resting[name] = time.monotonic() + busy.rest_s
                log.warning("model %s unavailable (%s), resting it %.0fs", name, busy.reason, busy.rest_s)
                reason = busy.reason
                continue
            log.info("answered by %s", name)
            return result
        raise LLMUnavailable(reason)

    async def _ask(self, model: str, system: str, user: str, output: type[T], max_tokens: int) -> T:
        call = self._gemini if self.provider == "gemini" else self._anthropic
        failure = "llm_invalid_output"
        for _ in range(2):  # one retry for malformed or truncated output
            try:
                return await call(model, system, user, output, max_tokens)
            except _Retry as retry:
                failure = retry.reason
        raise LLMUnavailable(failure)

    async def _anthropic(self, model: str, system: str, user: str, output: type[T], max_tokens: int) -> T:
        extra: dict[str, Any] = {}
        if model.startswith("claude-haiku"):
            extra["thinking"] = {"type": "disabled"}  # fastest setting; allowed on Haiku at default effort
        try:
            response = await self._client.messages.parse(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_format=output,
                **extra,
            )
        except anthropic.APITimeoutError:
            raise LLMUnavailable("llm_timeout") from None
        except anthropic.APIConnectionError:
            raise LLMUnavailable("llm_connection_error") from None
        except anthropic.RateLimitError:
            raise _NextModel("llm_rate_limited", 30) from None
        except anthropic.APIStatusError as error:
            if error.status_code >= 500:
                raise _NextModel(f"llm_http_{error.status_code}", 30) from None
            raise LLMUnavailable(f"llm_http_{error.status_code}") from None
        except ValueError:  # JSON or schema validation failed
            raise _Retry("llm_invalid_output") from None
        if response.stop_reason == "refusal":
            raise LLMUnavailable("llm_refusal")
        if response.stop_reason == "max_tokens":
            raise _Retry("llm_truncated")
        if response.parsed_output is None:
            raise _Retry("llm_invalid_output")
        return response.parsed_output

    async def _gemini(self, model: str, system: str, user: str, output: type[T], max_tokens: int) -> T:
        config: dict[str, Any] = {"temperature": 0, "maxOutputTokens": max_tokens, "responseMimeType": "application/json"}
        if model.startswith("gemini-2.5-flash"):
            config["thinkingConfig"] = {"thinkingBudget": 0}  # no thinking: fastest, and it can't eat the token cap
        schema = json.dumps(output.model_json_schema(), separators=(",", ":"))
        body = {
            # The schema goes in the instructions rather than responseJsonSchema: every Gemini model accepts that.
            "systemInstruction": {"parts": [{"text": f"{system}\n\nReply with one JSON object only, no other "
                                                       f"text, matching this JSON Schema:\n{schema}"}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": config,
        }
        try:
            # Key in a header, never in the URL, so it can't end up in logs.
            resp = await self._gemini_http().post(
                GEMINI_URL.format(model=model), json=body, headers={"x-goog-api-key": self._api_key or ""}
            )
        except httpx.TimeoutException:
            raise LLMUnavailable("llm_timeout") from None
        except httpx.HTTPError:
            raise LLMUnavailable("llm_connection_error") from None
        if resp.status_code == 429:
            raise _NextModel("llm_rate_limited", _gemini_retry_s(resp))
        if resp.status_code != 200:
            log.warning("gemini %s HTTP %s: %s", model, resp.status_code, resp.text[:300])
            if resp.status_code in (400, 404):  # this model rejects the request or doesn't exist
                raise _NextModel(f"llm_http_{resp.status_code}", 3600)
            if resp.status_code >= 500:
                raise _NextModel(f"llm_http_{resp.status_code}", 30)
            raise LLMUnavailable(f"llm_http_{resp.status_code}")  # 401/403: the key, same for every model
        try:
            candidates = resp.json().get("candidates") or []
        except ValueError:
            raise _Retry("llm_invalid_output") from None
        if not candidates:  # the prompt itself was blocked
            raise LLMUnavailable("llm_refusal")
        finish = candidates[0].get("finishReason")
        if finish in _GEMINI_REFUSALS:
            raise LLMUnavailable("llm_refusal")
        if finish == "MAX_TOKENS":
            raise _Retry("llm_truncated")
        parts = (candidates[0].get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        try:
            return output.model_validate_json(_strip_fences(text))
        except ValueError:
            raise _Retry("llm_invalid_output") from None

    def _gemini_http(self) -> httpx.AsyncClient:
        if self._client is not None:
            return self._client
        loop = asyncio.get_running_loop()  # an httpx client can't be reused across event loops
        if self._http is None or self._http_loop is not loop:
            self._http, self._http_loop = httpx.AsyncClient(timeout=self._timeout_s), loop
        return self._http


def _gemini_retry_s(resp: httpx.Response) -> float:
    """How long Google says to wait: seconds for a per-minute limit, hours once the daily quota is gone."""
    try:
        details = resp.json()["error"].get("details", [])
    except (ValueError, KeyError, AttributeError):
        return 30.0
    quotas = [v.get("quotaId", "") for d in details for v in d.get("violations", [])]
    log.warning("gemini quota hit: %s", ", ".join(quotas) or "unspecified")
    for detail in details:
        delay = str(detail.get("retryDelay", ""))
        if delay.endswith("s"):
            try:
                return max(1.0, min(float(delay[:-1]), 86400.0))
            except ValueError:
                pass
    return 30.0


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.rsplit("```", 1)[0]
    return text


@lru_cache
def get_llm() -> LLMClient:
    settings = get_settings()
    return LLMClient(
        api_key=settings.llm_key,
        scan_model=settings.scan_model,
        judge_model=settings.judge_model,
        timeout_s=settings.deadline_s,
        provider=settings.llm_provider,
    )


@lru_cache
def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8").strip()


def untrusted(text: str) -> str:
    """Stop untrusted text from closing the tag it is wrapped in."""
    return text.replace("</", "<\\/")


def clip(text: str, limit: int) -> str:
    """Keep the start and the end of long text; attacks often sit at the end of a page."""
    if len(text) <= limit:
        return text
    return text[: limit * 3 // 4] + " [...] " + text[-(limit // 4) :]


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
