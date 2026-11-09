"""PayPal webhook receiver: verify, dedupe, then record in the ledger.

Answers: 200 recorded or duplicate; 400 bad or forged request; 503/500 when PayPal or the ledger
cannot be reached, so PayPal retries (it retries non-2xx up to 25 times over 3 days).
"""

from __future__ import annotations

import json
import logging
from collections import OrderedDict
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request

from intel import ledger, paypal, payouts
from intel.config import Settings, get_settings

log = logging.getLogger("intel.webhooks")
router = APIRouter()

REQUIRED_HEADERS = (
    "paypal-transmission-id",
    "paypal-transmission-time",
    "paypal-transmission-sig",
    "paypal-cert-url",
    "paypal-auth-algo",
)
# First-line dedupe. The real guarantee is the ledger's unique idempotency key ("paypal:<event id>").
_SEEN: OrderedDict[str, None] = OrderedDict()
_SEEN_SIZE = 10_000

STATUS_BY_EVENT = {
    "PAYMENT.AUTHORIZATION.CREATED": "authorized",
    "PAYMENT.AUTHORIZATION.VOIDED": "voided",
    "PAYMENT.CAPTURE.COMPLETED": "captured",
    "PAYMENT.CAPTURE.PENDING": "capture_pending",
    "PAYMENT.CAPTURE.DENIED": "capture_denied",
    "PAYMENT.CAPTURE.DECLINED": "capture_declined",
    "PAYMENT.CAPTURE.REFUNDED": "refunded",
    "PAYMENT.CAPTURE.REVERSED": "reversed",
    "INVOICING.INVOICE.PAID": "invoice_paid",
    "INVOICING.INVOICE.CANCELLED": "invoice_cancelled",
    "PAYMENT.PAYOUTSBATCH.SUCCESS": "payout_batch_succeeded",
    "PAYMENT.PAYOUTSBATCH.DENIED": "payout_batch_denied",
    "PAYMENT.PAYOUTS-ITEM.SUCCEEDED": "payout_item_succeeded",
    "PAYMENT.PAYOUTS-ITEM.FAILED": "payout_item_failed",
}


@router.post("/webhooks/paypal")
async def paypal_webhook(request: Request, settings: Settings = Depends(get_settings)) -> dict[str, str]:
    raw = await request.body()
    headers = {name.lower(): value for name, value in request.headers.items()}
    missing = [name for name in REQUIRED_HEADERS if not headers.get(name)]
    if missing:
        raise HTTPException(400, f"missing headers: {', '.join(missing)}")
    try:
        event = json.loads(raw)
        event_id, event_type = str(event["id"]), str(event["event_type"])
    except (ValueError, KeyError, TypeError):
        raise HTTPException(400, "body is not a PayPal event") from None

    if event_id in _SEEN:
        return {"status": "duplicate"}
    if not settings.paypal_webhook_id:
        log.error("PAYPAL_WEBHOOK_ID is not set; cannot verify webhooks")
        raise HTTPException(503, "webhook verification not configured")

    client = paypal.get_client(settings)
    verify = client.verify_locally if settings.webhook_verify == "local" else client.verify_by_postback
    try:
        verified = await verify(headers, raw, settings.paypal_webhook_id)
    except (paypal.PayPalError, httpx.HTTPError) as error:
        log.warning("webhook verification unavailable: %s", error)
        raise HTTPException(503, "verification unavailable, retry later") from None

    if not verified:
        await _record_rejection(settings, event_id, event_type)
        raise HTTPException(400, "signature verification failed")

    record = describe(event)
    try:
        await ledger.append(
            settings, "webhook.received", payment_id=record.pop("payment_id"),
            idempotency_key=f"paypal:{event_id}", data=record,
        )
    except ledger.LedgerError as error:
        log.error("could not record webhook %s: %s", event_id, error)
        raise HTTPException(500, "ledger unavailable, retry later") from None
    _SEEN[event_id] = None
    if len(_SEEN) > _SEEN_SIZE:
        _SEEN.popitem(last=False)
    return {"status": "recorded"}


async def _record_rejection(settings: Settings, event_id: str, event_type: str) -> None:
    """Best effort: show forged or tampered deliveries in the ledger. Fields are unverified claims."""
    try:
        await ledger.append(
            settings, "webhook.rejected", payment_id=None, idempotency_key=None,
            data={"claimed_event_id": event_id[:100], "claimed_event_type": event_type[:100], "reason": "signature_invalid"},
        )
    except ledger.LedgerError as error:
        log.warning("could not record rejected webhook: %s", error)


def describe(event: dict[str, Any]) -> dict[str, Any]:
    """Map a PayPal event to the ledger's webhook.received payload."""
    resource = event.get("resource") or {}
    related = (resource.get("supplementary_data") or {}).get("related_ids") or {}
    resource_type = event.get("resource_type")
    batch, payout_item = resource.get("batch_header") or {}, resource.get("payout_item") or {}
    invoice = resource.get("invoice") or (resource if resource_type == "invoices" else {})
    # Captures use {"value", "currency_code"}; payouts use {"value", "currency"}.
    amount = (resource.get("amount") or payout_item.get("amount") or batch.get("amount")
              or invoice.get("amount") or {})
    payment_id = (resource.get("custom_id") or resource.get("invoice_id") or payout_item.get("sender_item_id")
                  or payouts.payment_id_from_batch((batch.get("sender_batch_header") or {}).get("sender_batch_id")))
    ids = {
        "order_id": related.get("order_id"),
        "authorization_id": related.get("authorization_id"),
        "capture_id": related.get("capture_id"),
    }
    if resource_type == "authorization":
        ids["authorization_id"] = resource.get("id")
    elif resource_type == "capture":
        ids["capture_id"] = resource.get("id")
    elif resource_type == "refund":
        ids["refund_id"] = resource.get("id")
        ids["capture_id"] = ids["capture_id"] or _parent_capture(resource)
    if batch:
        ids["payout_batch_id"] = batch.get("payout_batch_id")
    if payout_item or resource.get("payout_item_id"):
        ids["payout_batch_id"] = resource.get("payout_batch_id")
        ids["payout_item_id"] = resource.get("payout_item_id")
    if invoice:
        ids["invoice_id"] = invoice.get("id")
    return {
        # Orders carry custom_id = payment_id (set by the gateway); payouts carry it as sender_item_id and
        # in sender_batch_id ("ag-<payment_id>"). Invoice events link through invoice_id.
        "payment_id": payment_id,
        "paypal_event_id": event.get("id"),
        "paypal_event_type": event.get("event_type"),
        "paypal_status": STATUS_BY_EVENT.get(str(event.get("event_type")), "other"),
        "resource_type": resource_type,
        "resource_status": (resource.get("status") or resource.get("transaction_status")
                            or batch.get("batch_status") or invoice.get("status")),
        "amount": amount.get("value"),
        "currency": amount.get("currency_code") or amount.get("currency"),
        "summary": event.get("summary"),
        "create_time": event.get("create_time"),
        **{name: value for name, value in ids.items() if value},
    }


def _parent_capture(resource: dict[str, Any]) -> str | None:
    for link in resource.get("links") or []:
        href = str(link.get("href", ""))
        if link.get("rel") == "up" and "/captures/" in href:
            return href.rstrip("/").rsplit("/", 1)[-1]
    return None
