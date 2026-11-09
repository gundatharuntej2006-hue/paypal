"""Internal PayPal operations the gateway calls: reconciliation, payouts and invoices.

Errors: 503 when PayPal or the ledger can't be reached (retry later), 404 when PayPal has no such object,
409 when PayPal or the invoice state refuses the operation. Nothing here decides whether money may move:
the gateway calls payouts and invoice payments only after its own 'allow' decision.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException

from intel import invoices, ledger, paypal, payouts, recon
from intel.auth import require_token
from intel.config import Settings, get_settings
from intel.models import (
    InvoiceIntentResponse,
    InvoicePaymentRequest,
    InvoicePaymentResponse,
    PayoutRequest,
    PayoutResponse,
    ReconRequest,
    ReconResponse,
)

router = APIRouter(dependencies=[Depends(require_token)], tags=["paypal operations"])
UNAVAILABLE = (paypal.PayPalError, ledger.LedgerError)


def _unavailable(error: Exception) -> HTTPException:
    return HTTPException(503, f"unavailable, retry later: {error}")


@router.post("/recon/run", response_model=ReconResponse)
async def run_reconciliation(
    req: ReconRequest = Body(default_factory=ReconRequest), settings: Settings = Depends(get_settings)
) -> ReconResponse:
    try:
        return await recon.run(settings, req, paypal.get_client(settings))
    except UNAVAILABLE as error:
        raise _unavailable(error) from None


@router.post("/payouts", response_model=PayoutResponse, status_code=201)
async def create_payout(req: PayoutRequest, settings: Settings = Depends(get_settings)) -> PayoutResponse:
    try:
        return await payouts.create(settings, paypal.get_client(settings), req)
    except payouts.PayoutRejected as error:
        raise HTTPException(409, f"paypal refused the payout: {error}") from None
    except UNAVAILABLE as error:
        raise _unavailable(error) from None


@router.get("/payouts/{batch_id}", response_model=PayoutResponse)
async def payout_status(batch_id: str, settings: Settings = Depends(get_settings)) -> PayoutResponse:
    try:
        result = await payouts.status(settings, paypal.get_client(settings), batch_id)
    except payouts.PayoutRejected as error:
        raise HTTPException(409, str(error)) from None
    except UNAVAILABLE as error:
        raise _unavailable(error) from None
    if result is None:
        raise HTTPException(404, "no such payout batch")
    return result


@router.post("/invoices/{invoice_id}/intent", response_model=InvoiceIntentResponse)
async def invoice_intent(invoice_id: str, settings: Settings = Depends(get_settings)) -> InvoiceIntentResponse:
    try:
        invoice = await invoices.fetch(paypal.get_client(settings), invoice_id)
    except UNAVAILABLE as error:
        raise _unavailable(error) from None
    if invoice is None:
        raise HTTPException(404, "no such invoice")
    try:
        return invoices.to_intent(invoice)
    except ValueError as error:
        raise HTTPException(409, f"invoice can't be turned into a payment: {error}") from None


@router.post("/invoices/{invoice_id}/payments", response_model=InvoicePaymentResponse)
async def invoice_payment(
    invoice_id: str, req: InvoicePaymentRequest, settings: Settings = Depends(get_settings)
) -> InvoicePaymentResponse:
    try:
        result = await invoices.record_payment(settings, paypal.get_client(settings), invoice_id, req)
    except invoices.InvoiceConflict as error:
        raise HTTPException(409, str(error)) from None
    except UNAVAILABLE as error:
        raise _unavailable(error) from None
    if result is None:
        raise HTTPException(404, "no such invoice")
    return result
