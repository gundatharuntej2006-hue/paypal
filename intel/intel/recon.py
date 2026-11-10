"""Reconciliation: compare what the gateway's ledger says with what PayPal actually recorded.

PayPal is the source of truth. Each captured payment ends as:
  ok        ledger and PayPal agree
  mismatch  they differ (amount, currency, status, payment id, or PayPal has no such capture);
            a `reconciliation.mismatch` event goes into the ledger
  unknown   PayPal could not be asked; never counts as ok

Transaction Search is tried first. In sandbox its permission and data can lag by hours, so any capture it
does not show is checked through the Captures API instead (GET /v2/payments/captures/{id}).

Run once from the intel/ folder:  py -m intel.recon        (exit code 1 when there are mismatches)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
try:
    from datetime import UTC
except ImportError:
    UTC = timezone.utc
from decimal import Decimal, InvalidOperation
from typing import Any

from intel import ledger, paypal
from intel.config import Settings, get_settings
from intel.models import Issue, LedgerPayment, ReconRequest, ReconResponse, ReconResult

VERSION = "recon_v1"
SEARCH_STATUS = {"S": "COMPLETED", "P": "PENDING", "V": "REVERSED", "D": "DECLINED"}
REFUND_CODE, REVERSAL_CODE = "T1107", "T1106"  # Transaction Search event codes that point back at a capture
WEBHOOK_STATUS = {
    "captured": "COMPLETED",
    "capture_pending": "PENDING",
    "capture_denied": "DECLINED",
    "capture_declined": "DECLINED",
    "reversed": "REVERSED",
}
MAX_SEARCH_PAGES = 10


@dataclass
class PayPalView:
    amount: str | None
    currency: str | None
    status: str
    custom_id: str | None


def _money(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value)) if value is not None else None
    except InvalidOperation:
        return None


# ---------- the ledger's side ----------


def payments_from_events(events: list[dict[str, Any]]) -> list[LedgerPayment]:
    """Build each capture's ledger view: the gateway's capture event, updated by PayPal webhooks."""
    captures: dict[str, dict[str, Any]] = {}
    refunded: dict[str, Decimal] = {}
    for event in sorted(events, key=lambda e: str(e.get("created_at") or "")):
        data = event.get("data") or {}
        if event.get("type") == "paypal.captured" and data.get("id") and event.get("payment_id"):
            captures[data["id"]] = {
                "payment_id": event["payment_id"],
                "capture_id": data["id"],
                "amount": data.get("amount"),
                "currency": data.get("currency"),
                "status": str(data.get("status") or "COMPLETED").upper(),
            }
        elif event.get("type") == "webhook.received" and data.get("capture_id") in captures:
            capture = captures[data["capture_id"]]
            if data.get("paypal_status") == "refunded":
                total = refunded.get(capture["capture_id"], Decimal(0)) + (_money(data.get("amount")) or Decimal(0))
                refunded[capture["capture_id"]] = total
                full = total >= (_money(capture["amount"]) or Decimal(0))
                capture["status"] = "REFUNDED" if full else "PARTIALLY_REFUNDED"
            elif data.get("paypal_status") in WEBHOOK_STATUS:
                capture["status"] = WEBHOOK_STATUS[data["paypal_status"]]
    payments = []
    for capture in captures.values():
        try:
            payments.append(LedgerPayment.model_validate(capture))
        except ValueError:
            continue  # incomplete capture record (no amount or currency): nothing to compare
    return payments


# ---------- PayPal's side ----------


async def search(client: paypal.PayPal, days: int, now: datetime) -> dict[str, PayPalView] | None:
    """All captures PayPal reports in the window, by capture id. None when Transaction Search is unavailable."""
    start = (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    end = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    views: dict[str, PayPalView] = {}
    refunds: dict[str, Decimal] = {}
    reversed_ids: set[str] = set()
    page, pages = 1, 1
    while page <= min(pages, MAX_SEARCH_PAGES):
        params = {"start_date": start, "end_date": end, "fields": "transaction_info", "page_size": "500", "page": str(page)}
        status, body = await client.call("GET", "/v1/reporting/transactions", params=params)
        if status != 200:
            return None  # 403 until the Transaction Search permission is active
        pages = int(body.get("total_pages") or 1)
        for detail in body.get("transaction_details") or []:
            info = detail.get("transaction_info") or {}
            amount = info.get("transaction_amount") or {}
            code, reference = info.get("transaction_event_code"), info.get("paypal_reference_id")
            if code == REFUND_CODE and reference:
                refunds[reference] = refunds.get(reference, Decimal(0)) + abs(_money(amount.get("value")) or Decimal(0))
            elif code == REVERSAL_CODE and reference:
                reversed_ids.add(reference)
            elif info.get("transaction_id"):
                views[info["transaction_id"]] = PayPalView(
                    amount=amount.get("value"),
                    currency=amount.get("currency_code"),
                    status=SEARCH_STATUS.get(str(info.get("transaction_status")), "UNKNOWN"),
                    custom_id=info.get("custom_field") or None,
                )
        page += 1
    for capture_id, total in refunds.items():
        if capture_id in views:
            full = total >= (_money(views[capture_id].amount) or Decimal(0))
            views[capture_id].status = "REFUNDED" if full else "PARTIALLY_REFUNDED"
    for capture_id in reversed_ids:
        if capture_id in views:
            views[capture_id].status = "REVERSED"
    return views


async def fetch_capture(client: paypal.PayPal, capture_id: str) -> PayPalView | None:
    """One capture from the Captures API. None means PayPal has no such capture."""
    status, body = await client.call("GET", f"/v2/payments/captures/{capture_id}")
    if status == 404:
        return None
    if status != 200:
        raise paypal.PayPalError(f"capture_http_{status}")
    amount = body.get("amount") or {}
    return PayPalView(
        amount=amount.get("value"),
        currency=amount.get("currency_code"),
        status=str(body.get("status") or "UNKNOWN").upper(),
        custom_id=body.get("custom_id") or None,
    )


# ---------- comparison ----------


def compare(payment: LedgerPayment, view: PayPalView | None) -> list[Issue]:
    if view is None:
        return [Issue(field="exists", ledger=payment.capture_id, paypal=None)]
    issues = []
    if _money(payment.amount) != _money(view.amount):
        issues.append(Issue(field="amount", ledger=payment.amount, paypal=view.amount))
    if payment.currency != view.currency:
        issues.append(Issue(field="currency", ledger=payment.currency, paypal=view.currency))
    if payment.status.upper() != view.status:
        issues.append(Issue(field="status", ledger=payment.status.upper(), paypal=view.status))
    if view.custom_id and view.custom_id != payment.payment_id:
        issues.append(Issue(field="payment_id", ledger=payment.payment_id, paypal=view.custom_id))
    return issues


def _mismatch_key(result: ReconResult) -> str:
    """The same payment with the same differences gives the same key, so repeated runs add no duplicates."""
    issues = json.dumps([issue.model_dump() for issue in result.issues], sort_keys=True)
    return "recon:" + hashlib.sha256(f"{result.payment_id}|{result.capture_id}|{issues}".encode()).hexdigest()[:32]


async def run(
    settings: Settings, req: ReconRequest, client: paypal.PayPal, now: datetime | None = None
) -> ReconResponse:
    now = now or datetime.now(UTC)
    run_id = "recon_" + uuid.uuid4().hex[:12]
    payments = req.payments if req.payments is not None else payments_from_events(await ledger.read_events(settings))

    views: dict[str, PayPalView] | None = None
    if req.use_search and payments:
        try:
            views = await search(client, req.days, now)
        except paypal.PayPalError:
            views = None

    async def check(payment: LedgerPayment) -> ReconResult:
        base = {"payment_id": payment.payment_id, "capture_id": payment.capture_id}
        if views is not None and payment.capture_id in views:
            view, source = views[payment.capture_id], "transaction_search"
        else:
            try:
                view, source = await fetch_capture(client, payment.capture_id), "captures_api"
            except paypal.PayPalError as error:
                return ReconResult(**base, result="unknown", source="none", reason=str(error))
        issues = compare(payment, view)
        return ReconResult(**base, result="mismatch" if issues else "ok", source=source, issues=issues)

    results = list(await asyncio.gather(*(check(p) for p in payments)))
    for result in results:
        if result.result == "mismatch":
            await ledger.append(
                settings, "reconciliation.mismatch", payment_id=result.payment_id,
                idempotency_key=_mismatch_key(result),
                data={**result.model_dump(mode="json"), "run_id": run_id, "version": VERSION},
            )
    summary = ReconResponse(
        run_id=run_id,
        version=VERSION,
        checked=len(results),
        ok=sum(r.result == "ok" for r in results),
        mismatched=sum(r.result == "mismatch" for r in results),
        unknown=sum(r.result == "unknown" for r in results),
        search_available=views is not None,
        results=results,
    )
    await ledger.append(
        settings, "reconciliation.completed", payment_id=None, idempotency_key=f"recon-run:{run_id}",
        data=summary.model_dump(mode="json", exclude={"results"}),
    )
    return summary


def main() -> None:
    settings = get_settings()
    try:
        summary = asyncio.run(run(settings, ReconRequest(), paypal.get_client(settings)))
    except (ledger.LedgerError, paypal.PayPalError) as error:
        print(f"reconciliation could not run: {error}")
        sys.exit(2)
    source = "used" if summary.search_available else "unavailable, Captures API used"
    print(f"checked {summary.checked}: {summary.ok} ok, {summary.mismatched} mismatched, "
          f"{summary.unknown} unknown (Transaction Search {source})")
    for result in summary.results:
        if result.result != "ok":
            detail = "; ".join(f"{i.field}: ledger {i.ledger} vs PayPal {i.paypal}" for i in result.issues)
            print(f"  {result.result:8} {result.payment_id} ({result.capture_id}) {detail or result.reason}")
    sys.exit(1 if summary.mismatched else 0)


if __name__ == "__main__":
    main()
