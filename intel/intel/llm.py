"""LLM wrapper: structured output, one retry on malformed output, and clean failure reasons.

Every failure raises LLMUnavailable with a short reason. Callers turn that into an 'unknown' result.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, TypeVar

import anthropic
from pydantic import BaseModel

from intel.config import get_settings

T = TypeVar("T", bound=BaseModel)
PROMPTS_DIR = Path(__file__).parent / "prompts"


class LLMUnavailable(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class LLMClient:
    def __init__(self, api_key: str | None, scan_model: str, judge_model: str, timeout_s: float, client: Any = None):
        self.scan_model = scan_model
        self.judge_model = judge_model
        # max_retries=0: the SDK would otherwise retry timeouts and blow the latency budget.
        self._client = client or (
            anthropic.AsyncAnthropic(api_key=api_key, max_retries=0, timeout=timeout_s) if api_key else None
        )

    async def structured(self, *, model: str, system: str, user: str, output: type[T], max_tokens: int = 1024) -> T:
        if self._client is None:
            raise LLMUnavailable("no_llm_key")
        extra: dict[str, Any] = {}
        if model.startswith("claude-haiku"):
            extra["thinking"] = {"type": "disabled"}  # fastest setting; allowed on Haiku at default effort
        failure = "llm_invalid_output"
        for _ in range(2):  # one retry for malformed or truncated output
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
                raise LLMUnavailable("llm_rate_limited") from None
            except anthropic.APIStatusError as error:
                raise LLMUnavailable(f"llm_http_{error.status_code}") from None
            except ValueError:  # JSON or schema validation failed
                failure = "llm_invalid_output"
                continue
            if response.stop_reason == "refusal":
                raise LLMUnavailable("llm_refusal")
            if response.stop_reason == "max_tokens":
                failure = "llm_truncated"
                continue
            if response.parsed_output is None:
                failure = "llm_invalid_output"
                continue
            return response.parsed_output
        raise LLMUnavailable(failure)


@lru_cache
def get_llm() -> LLMClient:
    settings = get_settings()
    return LLMClient(
        api_key=settings.anthropic_api_key,
        scan_model=settings.scan_model,
        judge_model=settings.judge_model,
        timeout_s=settings.deadline_s,
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
