package paypal

import (
	"context"
	"testing"
)

func TestPayPalClientAcceptance(t *testing.T) {
	ctx := context.Background()
	client := NewMockClient()

	// 1. Create order
	reqID := "req_test_001"
	order, err := client.CreateOrder(ctx, OrderRequest{
		PaymentID:   "pay_123",
		Amount:      "29.99",
		Currency:    "USD",
		Description: "USB-C hub purchase",
		RequestID:   reqID,
	})
	if err != nil {
		t.Fatalf("CreateOrder failed: %v", err)
	}
	if order.ID == "" {
		t.Fatal("expected order ID")
	}

	// ACCEPTANCE: Running CreateOrder twice with same request id returns exact same order, no duplicates
	order2, err := client.CreateOrder(ctx, OrderRequest{
		PaymentID:   "pay_123",
		Amount:      "29.99",
		Currency:    "USD",
		Description: "USB-C hub purchase",
		RequestID:   reqID,
	})
	if err != nil {
		t.Fatalf("repeated CreateOrder failed: %v", err)
	}
	if order.ID != order2.ID {
		t.Fatalf("expected duplicate request to return identical order id %s, got %s", order.ID, order2.ID)
	}

	// 2. Authorize
	authID, err := client.AuthorizeOrder(ctx, order.ID, "auth_req_001")
	if err != nil {
		t.Fatalf("AuthorizeOrder failed: %v", err)
	}
	if authID == "" {
		t.Fatal("expected auth ID")
	}

	// 3. Partial Capture
	capture, err := client.CaptureAuthorization(ctx, authID, "15.00", "USD", false, "cap_req_001")
	if err != nil {
		t.Fatalf("CaptureAuthorization failed: %v", err)
	}
	if capture.Amount != "15.00" || capture.FinalCapture != false {
		t.Fatalf("partial capture unexpected response: %+v", capture)
	}

	// Repeated capture with same request ID returns identical capture
	capture2, err := client.CaptureAuthorization(ctx, authID, "15.00", "USD", false, "cap_req_001")
	if err != nil {
		t.Fatalf("repeated capture failed: %v", err)
	}
	if capture.ID != capture2.ID {
		t.Fatalf("expected identical capture ID for idempotent request")
	}

	// 4. Refund
	refund, err := client.RefundCapture(ctx, capture.ID, "15.00", "USD", "return item", "ref_req_001")
	if err != nil {
		t.Fatalf("RefundCapture failed: %v", err)
	}
	if refund.ID == "" {
		t.Fatal("expected refund ID")
	}

	// 5. Void on another authorization
	orderForVoid, _ := client.CreateOrder(ctx, OrderRequest{
		PaymentID: "pay_void_test",
		Amount:    "40.00",
		Currency:  "USD",
		RequestID: "req_void_order",
	})
	authVoid, _ := client.AuthorizeOrder(ctx, orderForVoid.ID, "auth_req_void")
	voidResp, err := client.VoidAuthorization(ctx, authVoid, "req_void_001")
	if err != nil {
		t.Fatalf("VoidAuthorization failed: %v", err)
	}
	if voidResp.Status != "VOIDED" {
		t.Fatalf("expected VOIDED status, got %s", voidResp.Status)
	}

	// Trying to capture a voided authorization fails
	_, err = client.CaptureAuthorization(ctx, authVoid, "40.00", "USD", true, "cap_after_void")
	if err == nil {
		t.Fatal("expected error capturing voided authorization!")
	}
}
