"""PayPal Payouts: send money to a payee's PayPal email after the gateway has decided 'allow'.

The gateway's payment id is the idempotency key: it becomes the PayPal-Request-Id and the sender_batch_id
(so a retry returns the same batch instead of paying twice) and the item's sender_item_id (so payout
webhooks link back to the payment). PayPal settles payouts later: the batch goes PENDING, then SUCCESS.
"""

from __future__ import annotations

from typing import Any

from intel import ledger, paypal
from intel.config import Settings
from intel.models import PayoutRequest, PayoutResponse

BATCH_PREFIX = "ag-"


class PayoutRejected(Exception):
    """PayPal refused the payout, e.g. Payouts not enabled on the app or not enough sandbox balance."""

    def __init__(self, status: int, body: dict[str, Any]):
        self.status, self.name = status, str(body.get("name") or f"http_{status}")
        super().__init__(f"{self.name}: {body.get('message', '')}".strip())


def batch_id_for(payment_id: str) -> str:
    return f"{BATCH_PREFIX}{payment_id}"[:127]


def payment_id_from_batch(sender_batch_id: str | None) -> str | None:
    if sender_batch_id and sender_batch_id.startswith(BATCH_PREFIX):
        return sender_batch_id[len(BATCH_PREFIX):]
    return None


async def create(settings: Settings, client: paypal.PayPal, req: PayoutRequest) -> PayoutResponse:
    sender_batch_id = batch_id_for(req.payment_id)
    note = req.note or "Payment sent through AgentGuard"
    body = {
        "sender_batch_header": {
            "sender_batch_id": sender_batch_id,
            "email_subject": "You have received a payment",
            "email_message": note,
        },
        "items": [{
            "recipient_type": "EMAIL",
            "receiver": req.receiver,
            "amount": {"value": req.amount, "currency": req.currency},
            "sender_item_id": req.payment_id,
            "note": note,
        }],
    }
    status, data = await client.call("POST", "/v1/payments/payouts", json_body=body, request_id=sender_batch_id)
    if status not in (200, 201):
        raise PayoutRejected(status, data)
    header = data.get("batch_header") or {}
    result = PayoutResponse(
        payment_id=req.payment_id,
        batch_id=str(header.get("payout_batch_id")),
        batch_status=str(header.get("batch_status") or "PENDING"),
    )
    await ledger.append(
        settings, "payout.created", payment_id=req.payment_id, idempotency_key=f"payout:{result.batch_id}",
        data={"payout_batch_id": result.batch_id, "batch_status": result.batch_status, "receiver": req.receiver,
              "amount": req.amount, "currency": req.currency, "sender_batch_id": sender_batch_id},
    )
    return result


async def status(settings: Settings, client: paypal.PayPal, batch_id: str) -> PayoutResponse | None:
    """Current batch and item status from PayPal. Records each new status once. None if PayPal has no batch."""
    code, data = await client.call("GET", f"/v1/payments/payouts/{batch_id}")
    if code == 404:
        return None
    if code != 200:
        raise PayoutRejected(code, data)
    header = data.get("batch_header") or {}
    items = data.get("items") or []
    item = items[0] if items else {}
    payment_id = (item.get("payout_item") or {}).get("sender_item_id") or payment_id_from_batch(
        (header.get("sender_batch_header") or {}).get("sender_batch_id"))
    result = PayoutResponse(
        payment_id=payment_id,
        batch_id=str(header.get("payout_batch_id") or batch_id),
        batch_status=str(header.get("batch_status") or "UNKNOWN"),
        item_status=item.get("transaction_status"),
    )
    await ledger.append(
        settings, "payout.updated", payment_id=payment_id,
        idempotency_key=f"payout:{result.batch_id}:{result.batch_status}:{result.item_status}",
        data=result.model_dump(mode="json"),
    )
    return result
