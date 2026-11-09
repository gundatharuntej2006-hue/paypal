package ledger

import (
	"context"
	"encoding/json"
	"testing"
	"time"

	"gateway/pkg/models"
)

func TestLedgerAppendAndReplayAcceptance(t *testing.T) {
	ctx := context.Background()
	l := NewMemoryLedger()

	paymentID := "pay_test_ledger_1"
	mandateID := "mdt_test_ledger_1"
	idempKey := "idemp_test_abc"

	// 1. Append intent.received
	data1, _ := json.Marshal(map[string]interface{}{
		"merchant_id": "merchant_a",
		"amount":      "29.99",
		"currency":    "USD",
	})
	evt1 := &models.LedgerEvent{
		ID:             "evt_01",
		Type:           "intent.received",
		PaymentID:      &paymentID,
		MandateID:      &mandateID,
		IdempotencyKey: &idempKey,
		Source:         "gateway",
		Data:           data1,
		CreatedAt:      time.Now().Add(-10 * time.Second),
	}
	if err := l.AppendEvent(ctx, evt1); err != nil {
		t.Fatalf("failed to append evt1: %v", err)
	}

	// 2. Acceptance test: deduplication under idempotency key
	errDup := l.AppendEvent(ctx, evt1)
	if errDup != ErrDuplicateEvent {
		t.Fatalf("expected ErrDuplicateEvent for duplicate idempotency key, got: %v", errDup)
	}

	// 3. Append paypal.captured
	data2, _ := json.Marshal(map[string]interface{}{
		"capture_id": "CAP_9999",
		"amount":     "29.99",
	})
	evt2 := &models.LedgerEvent{
		ID:        "evt_02",
		Type:      "paypal.captured",
		PaymentID: &paymentID,
		MandateID: &mandateID,
		Source:    "gateway",
		Data:      data2,
		CreatedAt: time.Now().Add(-5 * time.Second),
	}
	if err := l.AppendEvent(ctx, evt2); err != nil {
		t.Fatalf("failed to append evt2: %v", err)
	}

	// Verify projections before replay
	projBefore, err := l.GetPaymentProjection(ctx, paymentID)
	if err != nil {
		t.Fatalf("failed to get payment projection: %v", err)
	}
	if projBefore.State != "captured" || projBefore.PayPalCaptureID != "CAP_9999" {
		t.Fatalf("projection state incorrect: %+v", projBefore)
	}

	spentBefore, _ := l.GetTotalSpent(mandateID)
	if spentBefore != 29.99 {
		t.Fatalf("expected spent 29.99, got %.2f", spentBefore)
	}

	// 4. SPEC ACCEPTANCE TEST: Wipe projections and replay
	if err := l.Replay(ctx); err != nil {
		t.Fatalf("replay failed: %v", err)
	}

	projAfter, err := l.GetPaymentProjection(ctx, paymentID)
	if err != nil {
		t.Fatalf("failed to get payment projection after replay: %v", err)
	}
	if projAfter.State != projBefore.State || projAfter.PayPalCaptureID != projBefore.PayPalCaptureID {
		t.Fatalf("replayed projection does not match original: before=%+v, after=%+v", projBefore, projAfter)
	}

	spentAfter, _ := l.GetTotalSpent(mandateID)
	if spentAfter != spentBefore {
		t.Fatalf("spent after replay %.2f does not match before %.2f", spentAfter, spentBefore)
	}
}
