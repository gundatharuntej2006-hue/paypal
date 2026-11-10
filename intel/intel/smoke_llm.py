"""Live check of the LLM signals (needs ANTHROPIC_API_KEY).

Runs N rounds of scan + judge in parallel, the way the gateway calls them, and prints each result.
Acceptance: every call returns a valid result or a clean 'unknown' with a reason.

Run from the intel/ folder:  py -m intel.smoke_llm 20
"""

from __future__ import annotations

import asyncio
import statistics
import sys
import time

from intel import judge, samples, scan
from intel.config import get_settings
from intel.main import run_signal
from intel.models import JudgeRequest, JudgeResponse, ScanRequest, ScanResponse


def _describe(result: ScanResponse | JudgeResponse) -> str:
    extra = f", {result.reason}" if result.reason else ""
    return f"{result.verdict:<10} ({result.latency_ms} ms{extra})"


async def _round(i: int, deadline_s: float) -> tuple[ScanResponse, JudgeResponse, int]:
    page = {**samples.CLEAN_PAGE, "text": f"{samples.CLEAN_PAGE['text']} (check {i})"}  # new text: no cache hits
    scan_req = ScanRequest.model_validate({"contexts": [page]})
    judge_req = JudgeRequest.model_validate({**samples.JUDGE_ALIGNED, "source_contexts": [page]})
    start = time.perf_counter()
    scan_out, judge_out = await asyncio.gather(
        run_signal(scan.run, scan_req, lambda r: ScanResponse.unknown(r, version="smoke"), deadline_s),
        run_signal(judge.run, judge_req, lambda r: JudgeResponse.unknown(r, version="smoke"), deadline_s),
    )
    return scan_out, judge_out, int((time.perf_counter() - start) * 1000)


async def main(rounds: int) -> int:
    settings = get_settings()
    if not settings.anthropic_api_key:
        print("ANTHROPIC_API_KEY is not set.")
        return 1
    print(f"scan model {settings.scan_model}, judge model {settings.judge_model}, deadline {settings.deadline_s}s\n")
    rows = []
    for i in range(1, rounds + 1):
        scan_out, judge_out, both_ms = await _round(i, settings.deadline_s)
        rows.append((scan_out, judge_out, both_ms))
        print(f"{i:>3}  scan {_describe(scan_out)}  judge {_describe(judge_out)}  both {both_ms} ms")

    combined = sorted(ms for _, _, ms in rows)
    unknown = sum(r.verdict == "unknown" for s, j, _ in rows for r in (s, j))
    expected = sum(s.verdict == "clean" and j.verdict == "aligned" for s, j, _ in rows)
    print(f"\n{2 * rounds} calls: {unknown} unknown. Combined latency p50 {statistics.median(combined):.0f} ms, "
          f"max {combined[-1]} ms (budget 3000 ms).")
    print(f"Expected answers (scan clean, judge aligned): {expected}/{rounds} rounds.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 20)))
