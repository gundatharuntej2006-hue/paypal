package api

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"gateway/pkg/intelclient"
	"gateway/pkg/models"
	"gateway/pkg/statemachine"
)

func TestIntegrationRoutes_MandatesAndOrders(t *testing.T) {
	stubIntel := intelclient.NewStubIntelClient()
	srv, _, _, _ := setupTestServer(stubIntel)
	router := SetupRouter(srv)

	// 1. GET /v1/mandates - should return at least the pre-seeded demo mandate
	req := httptest.NewRequest(http.MethodGet, "/v1/mandates", nil)
	rec := httptest.NewRecorder()
	router.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("expected 200 from GET /v1/mandates, got %d", rec.Code)
	}
	var mandates []models.Mandate
	if err := json.NewDecoder(rec.Body).Decode(&mandates); err != nil {
		t.Fatalf("failed to decode mandates: %v", err)
	}
	if len(mandates) == 0 {
		t.Fatalf("expected pre-seeded mandates, got 0")
	}

	// 2. POST /v1/mandates - create new mandate
	newMandate := models.Mandate{
		MandateID:   "mdt_test_created",
		PrincipalID: "user_rikita",
		AgentID:     "agent_shopper",
		Task:        "Buy books",
		Currency:    "USD",
		MaxTotal:    "100.00",
		MaxPerTxn:   "50.00",
		PublicKey:   "mc4CAQAwBQYDK2VwBCIEIPTESTPUBLICKEY123",
	}
	mBody, _ := json.Marshal(newMandate)
	req = httptest.NewRequest(http.MethodPost, "/v1/mandates", bytes.NewReader(mBody))
	req.Header.Set("Content-Type", "application/json")
	rec = httptest.NewRecorder()
	router.ServeHTTP(rec, req)

	if rec.Code != http.StatusCreated {
		t.Fatalf("expected 201 from POST /v1/mandates, got %d", rec.Code)
	}

	// 3. POST /v1/mandates/{id}/order - create order for mandate
	orderDraft := map[string]interface{}{
		"mandate_id": "mdt_test_created",
		"amount":     35.00,
		"currency":   "USD",
	}
	odBody, _ := json.Marshal(orderDraft)
	req = httptest.NewRequest(http.MethodPost, "/v1/mandates/mdt_test_created/order", bytes.NewReader(odBody))
	req.Header.Set("Content-Type", "application/json")
	rec = httptest.NewRecorder()
	router.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("expected 200 from POST /v1/mandates/:id/order, got %d: %s", rec.Code, rec.Body.String())
	}
	var orderResp map[string]interface{}
	if err := json.NewDecoder(rec.Body).Decode(&orderResp); err != nil {
		t.Fatalf("failed to decode order response: %v", err)
	}
	if orderResp["id"] == "" {
		t.Fatalf("expected order id in response")
	}
}

func TestIntegrationRoutes_PaymentsAndApprovals(t *testing.T) {
	stubIntel := intelclient.NewStubIntelClient()
	stubIntel.RiskScoreVal = 0.45 // causes step_up

	srv, _, led, reg := setupTestServer(stubIntel)
	router := SetupRouter(srv)

	mandate, _ := createTestMandate(t)
	reg.RegisterPrincipal(mandate.PrincipalID, mandate.PublicKey)

	// Submit payment intent that triggers step-up
	intentReq := models.PaymentIntentRequest{
		Mandate:        mandate,
		AgentID:        mandate.AgentID,
		MerchantID:     "merchant_a",
		Amount:         "30.00", // > step_up_over 25.00
		Currency:       "USD",
		Category:       "electronics",
		Items:          []models.Item{{Name: "USB-C Hub", Qty: 1, UnitPrice: "30.00"}},
		AgentReasoning: "Restock necessary connector",
		IdempotencyKey: "idemp_test_stepup_1",
		DryRun:         true,
	}
	body, _ := json.Marshal(intentReq)
	req := httptest.NewRequest(http.MethodPost, "/v1/payments/intents", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	rec := httptest.NewRecorder()
	router.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", rec.Code, rec.Body.String())
	}
	var decResp models.DecisionResponse
	_ = json.NewDecoder(rec.Body).Decode(&decResp)
	if decResp.Decision != models.DecisionStepUp {
		t.Fatalf("expected decision step_up, got %s", decResp.Decision)
	}

	// 1. GET /v1/payments - should list this payment
	req = httptest.NewRequest(http.MethodGet, "/v1/payments", nil)
	rec = httptest.NewRecorder()
	router.ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("expected 200 from GET /v1/payments, got %d", rec.Code)
	}
	var payments []map[string]interface{}
	_ = json.NewDecoder(rec.Body).Decode(&payments)
	if len(payments) == 0 {
		t.Fatalf("expected at least 1 payment listed")
	}

	// 2. GET /v1/approvals - should list pending approval
	req = httptest.NewRequest(http.MethodGet, "/v1/approvals", nil)
	rec = httptest.NewRecorder()
	router.ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("expected 200 from GET /v1/approvals, got %d", rec.Code)
	}
	var approvals []map[string]interface{}
	_ = json.NewDecoder(rec.Body).Decode(&approvals)
	if len(approvals) == 0 {
		t.Fatalf("expected at least 1 approval listed")
	}
	found := false
	for _, app := range approvals {
		if app["payment_id"] == decResp.PaymentID {
			found = true
			break
		}
	}
	if !found {
		t.Fatalf("expected payment %s in approvals list", decResp.PaymentID)
	}

	// 3. POST /v1/payments/:id/approve - Dashboard approval omitting step_up_token, passing paypal_order_id
	approvePayload := map[string]interface{}{
		"paypal_order_id": "ORDER_DASHBOARD_APPROVED",
	}
	appBody, _ := json.Marshal(approvePayload)
	req = httptest.NewRequest(http.MethodPost, "/v1/payments/"+decResp.PaymentID+"/approve", bytes.NewReader(appBody))
	req.Header.Set("Content-Type", "application/json")
	rec = httptest.NewRecorder()
	router.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("expected 200 from dashboard approve, got %d: %s", rec.Code, rec.Body.String())
	}

	// Verify payment is now StateCaptured and not in approvals anymore
	state, _ := srv.stateMachine.Get(decResp.PaymentID)
	if state != statemachine.StateCaptured {
		t.Fatalf("expected StateCaptured, got %s", state)
	}

	req = httptest.NewRequest(http.MethodGet, "/v1/approvals", nil)
	rec = httptest.NewRecorder()
	router.ServeHTTP(rec, req)
	var approvalsAfter []map[string]interface{}
	_ = json.NewDecoder(rec.Body).Decode(&approvalsAfter)
	for _, app := range approvalsAfter {
		if app["payment_id"] == decResp.PaymentID {
			t.Fatalf("payment %s should no longer be pending approval", decResp.PaymentID)
		}
	}

	// Also test rejection on another payment
	intentReq.IdempotencyKey = "idemp_test_stepup_2"
	body2, _ := json.Marshal(intentReq)
	req = httptest.NewRequest(http.MethodPost, "/v1/payments/intents", bytes.NewReader(body2))
	rec = httptest.NewRecorder()
	router.ServeHTTP(rec, req)
	var decResp2 models.DecisionResponse
	_ = json.NewDecoder(rec.Body).Decode(&decResp2)

	rejectPayload := map[string]interface{}{
		"reason": "Not approved by finance lead",
	}
	rejBody, _ := json.Marshal(rejectPayload)
	req = httptest.NewRequest(http.MethodPost, "/v1/payments/"+decResp2.PaymentID+"/reject", bytes.NewReader(rejBody))
	rec = httptest.NewRecorder()
	router.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("expected 200 from dashboard reject, got %d: %s", rec.Code, rec.Body.String())
	}

	// Projection check
	p2, _ := led.GetPaymentProjection(context.Background(), decResp2.PaymentID)
	if p2 == nil {
		t.Fatalf("expected projection for payment 2")
	}
}

func TestIntegrationRoutes_DemoRunAndBenchmark(t *testing.T) {
	srv, _, _, _ := setupTestServer(nil)
	router := SetupRouter(srv)

	// 1. POST /v1/demo/run with happy_path
	demoReq := models.DemoRunRequest{
		Scenario:  "happy_path",
		MandateID: "mdt_demo_office",
	}
	body, _ := json.Marshal(demoReq)
	req := httptest.NewRequest(http.MethodPost, "/v1/demo/run", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	rec := httptest.NewRecorder()
	router.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("expected 200 from POST /v1/demo/run, got %d: %s", rec.Code, rec.Body.String())
	}
	var demoResp map[string]interface{}
	_ = json.NewDecoder(rec.Body).Decode(&demoResp)
	if demoResp["status"] != "completed" {
		t.Fatalf("expected status completed, got %v", demoResp["status"])
	}

	// 2. POST /v1/demo/run with poisoned_page
	demoReq.Scenario = "poisoned_page"
	body, _ = json.Marshal(demoReq)
	req = httptest.NewRequest(http.MethodPost, "/v1/demo/run", bytes.NewReader(body))
	rec = httptest.NewRecorder()
	router.ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("expected 200 from poisoned_page, got %d", rec.Code)
	}

	// 3. POST /v1/demo/run with budget_splitting
	demoReq.Scenario = "budget_splitting"
	body, _ = json.Marshal(demoReq)
	req = httptest.NewRequest(http.MethodPost, "/v1/demo/run", bytes.NewReader(body))
	rec = httptest.NewRecorder()
	router.ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("expected 200 from budget_splitting, got %d", rec.Code)
	}

	// 4. GET /v1/benchmark/report
	req = httptest.NewRequest(http.MethodGet, "/v1/benchmark/report", nil)
	rec = httptest.NewRecorder()
	router.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("expected 200 from GET /v1/benchmark/report, got %d", rec.Code)
	}
	var benchResp map[string]interface{}
	if err := json.NewDecoder(rec.Body).Decode(&benchResp); err != nil {
		t.Fatalf("failed to decode benchmark report: %v", err)
	}
	if benchResp["run_id"] == nil {
		t.Fatalf("expected run_id in benchmark report")
	}
}

func TestIntegrationRoutes_SSEEnvelopeSchema(t *testing.T) {
	srv, _, _, _ := setupTestServer(nil)
	broker := srv.broker

	payID := "pay_test_sse_1"
	mandID := "mdt_test_sse_1"

	subCh, unsub := broker.Subscribe("")
	defer unsub()

	// Emit an event
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	_, err := srv.emit(ctx, "payment.created", &payID, &mandID, nil, "test", map[string]string{
		"info": "unit test event",
	})
	if err != nil {
		t.Fatalf("emit failed: %v", err)
	}

	select {
	case evt := <-subCh:
		if evt.Type != "payment.created" {
			t.Fatalf("expected payment.created, got %s", evt.Type)
		}
		if evt.PaymentID == nil || *evt.PaymentID != payID {
			t.Fatalf("expected payment ID %s", payID)
		}
	case <-time.After(1 * time.Second):
		t.Fatalf("timeout waiting for broker event")
	}
}
