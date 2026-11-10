"""PayPal Invoicing: turn an invoice into a payment proposal, and mark it paid after the gateway captured.

Flow (no PayPal API lets a buyer pay an invoice directly):
  1. POST /invoices/{id}/intent   reads the invoice and returns a proposal (merchant = invoicer email,
     amount = amount due, items) plus the invoice's own text as a source context, kind "invoice".
     The agent submits these to the gateway's POST /v1/payments/intents, so the injection scan reads
     the invoice note, terms and item text, and the mandate and risk checks apply as for any payment.
  2. Only after 'allow' and a completed capture: POST /invoices/{id}/payments records that capture on the
     invoice (POST /v2/invoicing/invoices/{id}/payments). A blocked invoice is never marked paid.

Demo helpers, from the intel/ folder:
  py -m intel.invoices create --to buyer@example.com --amount 29.99 --item "A4 paper, 5 reams" [--note "..."]
  py -m intel.invoices show INV2-XXXX
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
try:
    from datetime import UTC
except ImportError:
    UTC = timezone.utc
from decimal import Decimal, InvalidOperation
from typing import Any

from intel import ledger, paypal
from intel.config import Settings, get_settings
from intel.models import (
    InvoiceIntentResponse,
    InvoicePaymentRequest,
    InvoicePaymentResponse,
    Item,
    Proposal,
    SourceContext,
)

PAYABLE = {"SENT", "UNPAID", "PARTIALLY_PAID"}
MAX_TEXT = 200_000


class InvoiceConflict(Exception):
    """The invoice can't take this payment: wrong state, amount or currency."""


async def fetch(client: paypal.PayPal, invoice_id: str) -> dict[str, Any] | None:
    status, body = await client.call("GET", f"/v2/invoicing/invoices/{invoice_id}")
    if status == 404:
        return None
    if status != 200:
        raise paypal.PayPalError(f"invoice_http_{status}")
    return body


def _items(invoice: dict[str, Any]) -> list[Item]:
    items = []
    for raw in invoice.get("items") or []:
        try:
            qty = Decimal(str(raw.get("quantity") or "1"))
            if qty != qty.to_integral_value() or qty < 1:
                continue  # fractional quantities (hours, kg) don't fit the item model; the text still has them
            items.append(Item(
                name=str(raw.get("name") or "item")[:200],
                qty=int(qty),
                unit_price=str((raw.get("unit_amount") or {}).get("value")),
            ))
        except (InvalidOperation, ValueError):
            continue
    return items


def _text(invoice: dict[str, Any]) -> str:
    """Everything the invoicer wrote. This is untrusted: it is where an invoice would hide instructions."""
    detail, invoicer = invoice.get("detail") or {}, invoice.get("invoicer") or {}
    name = invoicer.get("name") or {}
    lines = [
        f"Invoice {detail.get('invoice_number') or invoice.get('id')} from "
        f"{invoicer.get('business_name') or ' '.join(filter(None, [name.get('given_name'), name.get('surname')]))} "
        f"<{invoicer.get('email_address') or 'unknown'}>",
    ]
    for label, value in (("Note", detail.get("note")), ("Terms", detail.get("terms_and_conditions")),
                         ("Memo", detail.get("memo")), ("Reference", detail.get("reference")),
                         ("Website", invoicer.get("website")), ("Additional notes", invoicer.get("additional_notes"))):
        if value:
            lines.append(f"{label}: {value}")
    lines.append("Items:")
    for raw in invoice.get("items") or []:
        unit = raw.get("unit_amount") or {}
        description = f" - {raw['description']}" if raw.get("description") else ""
        lines.append(f"- {raw.get('name')}{description} ({raw.get('quantity')} x {unit.get('value')} "
                     f"{unit.get('currency_code', '')})")
    due = invoice.get("due_amount") or invoice.get("amount") or {}
    lines.append(f"Amount due: {due.get('value')} {due.get('currency_code', '')}")
    return "\n".join(lines)[:MAX_TEXT]


def to_intent(invoice: dict[str, Any]) -> InvoiceIntentResponse:
    detail, invoicer = invoice.get("detail") or {}, invoice.get("invoicer") or {}
    due = invoice.get("due_amount") or invoice.get("amount") or {}
    status = str(invoice.get("status") or "UNKNOWN")
    name = invoicer.get("name") or {}
    proposal = Proposal(
        merchant_id=str(invoicer.get("email_address") or "unknown_invoicer").lower(),
        merchant_name=invoicer.get("business_name")
        or " ".join(filter(None, [name.get("given_name"), name.get("surname")])) or None,
        amount=str(due.get("value") or "0"),
        currency=str(due.get("currency_code") or detail.get("currency_code") or "USD"),
        items=_items(invoice),
    )
    context = SourceContext(
        ref=f"invoice:{invoice.get('id')}",
        kind="invoice",
        url=(detail.get("metadata") or {}).get("recipient_view_url"),
        text=_text(invoice),
    )
    return InvoiceIntentResponse(
        invoice_id=str(invoice.get("id")),
        invoice_number=detail.get("invoice_number"),
        status=status,
        payable=status in PAYABLE and Decimal(proposal.amount) > 0,
        proposal=proposal,
        source_contexts=[context],
    )


async def record_payment(
    settings: Settings, client: paypal.PayPal, invoice_id: str, req: InvoicePaymentRequest
) -> InvoicePaymentResponse | None:
    """Mark the invoice paid with the gateway's capture. None if PayPal has no such invoice."""
    invoice = await fetch(client, invoice_id)
    if invoice is None:
        return None
    transactions = (invoice.get("payments") or {}).get("transactions") or []
    if any(t.get("payment_id") == req.capture_id for t in transactions):
        return InvoicePaymentResponse(invoice_id=invoice_id, status="already_recorded", paypal_payment_id=req.capture_id)
    status = str(invoice.get("status"))
    if status not in PAYABLE:
        raise InvoiceConflict(f"invoice_not_payable: {status}")
    due = invoice.get("due_amount") or invoice.get("amount") or {}
    if due.get("currency_code") and due["currency_code"] != req.currency:
        raise InvoiceConflict(f"currency_mismatch: invoice {due['currency_code']}, payment {req.currency}")
    if due.get("value") is not None and Decimal(req.amount) > Decimal(str(due["value"])):
        raise InvoiceConflict(f"amount_exceeds_due: due {due['value']}, payment {req.amount}")

    paid_on = (req.paid_at or datetime.now(UTC)).astimezone(UTC).strftime("%Y-%m-%d")
    body = {
        "method": "PAYPAL",
        "payment_id": req.capture_id,
        "payment_date": paid_on,
        "amount": {"currency_code": req.currency, "value": req.amount},
        "note": f"Paid through AgentGuard, payment {req.payment_id}",
    }
    path = f"/v2/invoicing/invoices/{invoice_id}/payments"
    code, data = await client.call("POST", path, json_body=body)
    if code in (400, 422):  # some accounts refuse method PAYPAL for an external record: record it as OTHER
        body = {**body, "method": "OTHER", "note": f"{body['note']}, PayPal capture {req.capture_id}"}
        body.pop("payment_id")
        code, data = await client.call("POST", path, json_body=body)
    if code not in (200, 201):
        raise InvoiceConflict(f"paypal_refused_http_{code}: {data.get('name', '')}")
    result = InvoicePaymentResponse(invoice_id=invoice_id, status="recorded", paypal_payment_id=data.get("payment_id"))
    await ledger.append(
        settings, "invoice.paid", payment_id=req.payment_id, idempotency_key=f"invoice:{invoice_id}:{req.payment_id}",
        data={"invoice_id": invoice_id, "invoice_number": (invoice.get("detail") or {}).get("invoice_number"),
              "capture_id": req.capture_id, "amount": req.amount, "currency": req.currency,
              "paypal_payment_id": result.paypal_payment_id},
    )
    return result


async def create_demo(
    client: paypal.PayPal, *, to: str, amount: str, item: str, currency: str = "USD", note: str | None = None
) -> str:
    """Create and send a sandbox invoice from our own app's account. Returns the invoice id."""
    detail: dict[str, Any] = {"currency_code": currency}
    if note:
        detail["note"] = note
    body = {
        "detail": detail,
        "primary_recipients": [{"billing_info": {"email_address": to}}],
        "items": [{"name": item, "quantity": "1", "unit_amount": {"currency_code": currency, "value": amount}}],
    }
    code, data = await client.call("POST", "/v2/invoicing/invoices", json_body=body, representation=True)
    if code not in (200, 201):
        raise InvoiceConflict(f"create_failed_http_{code}: {data.get('name', '')} {data.get('message', '')}")
    invoice_id = data.get("id") or str(data.get("href", "")).rstrip("/").rsplit("/", 1)[-1]
    code, data = await client.call(
        "POST", f"/v2/invoicing/invoices/{invoice_id}/send", json_body={"send_to_invoicer": False}
    )
    if code not in (200, 202):
        raise InvoiceConflict(f"send_failed_http_{code}: {data.get('name', '')}")
    return invoice_id


def main() -> None:
    parser = argparse.ArgumentParser(prog="py -m intel.invoices", description="Sandbox invoice helpers for the demo.")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create", help="create and send an invoice from our sandbox account")
    create.add_argument("--to", required=True, help="recipient email (the buyer's sandbox account)")
    create.add_argument("--amount", required=True)
    create.add_argument("--item", required=True)
    create.add_argument("--currency", default="USD")
    create.add_argument("--note", help="invoice note; put an injection here to demo a poisoned invoice")
    show = sub.add_parser("show", help="print the payment proposal the gateway would receive")
    show.add_argument("invoice_id")
    args = parser.parse_args()
    client = paypal.get_client(get_settings())

    async def go() -> None:
        if args.command == "create":
            invoice_id = await create_demo(client, to=args.to, amount=args.amount, item=args.item,
                                           currency=args.currency, note=args.note)
            print(invoice_id)
        else:
            invoice = await fetch(client, args.invoice_id)
            if invoice is None:
                raise SystemExit("no such invoice")
            print(json.dumps(to_intent(invoice).model_dump(mode="json"), indent=2))

    asyncio.run(go())


if __name__ == "__main__":
    main()
