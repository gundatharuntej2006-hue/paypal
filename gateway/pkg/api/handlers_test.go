package api

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/google/uuid"

	"gateway/pkg/config"
	"gateway/pkg/crypto"
	"gateway/pkg/events"
	"gateway/pkg/intelclient"
	"gateway/pkg/ledger"
	"gateway/pkg/mandate"
	"gateway/pkg/models"
	"gateway/pkg/paypal"
	"gateway/pkg/policy"
	"gateway/pkg/statemachine"
)

func setupTestServer(intel intelclient.IntelClient) (*Server, *paypal.MockClient, *ledger.MemoryLedger, *mandate.MemoryPrincipalRegistry) {
	cfg := &config.Config{
		InternalLedgerToken: "test-token",
		RiskBlockThreshold:  0.65,
		RiskStepUpThreshold: 0.35,
	}

	led := ledger.NewMemoryLedger()
	nonceStore := mandate.NewMemoryNonceStore()
	tracker := mandate.NewMemorySpentTracker()
	registry := mandate.NewMemoryPrincipalRegistry()
	verifier := mandate.NewVerifier(nonceStore, tracker, registry)
	sm := statemachine.NewStateMachine()
	de := policy.NewDecisionEngine(policy.DefaultDecisionConfig())
	pp := paypal.NewMockClient()
	broker := events.NewBroker()

	srv := NewServer(cfg, led, verifier, sm, de, pp, intel, broker)
	return srv, pp, led, registry
}

func createTestMandate(t *testing.T) (models.Mandate, string) {
	pub, priv, err := crypto.GenerateKeyPair()
	if err != nil {
		t.Fatalf("failed to generate key: %v", err)
	}

	m := models.Mandate{
		MandateID:         "mdt_test_api_" + uuid.New().String()[:8],
		PrincipalID:       "user_vivek",
		AgentID:           "agent_shopper",
		Task:              "Buy one USB-C hub under 40 USD",
		Currency:          "USD",
		MaxTotal:          "60.00",
		MaxPerTxn:         "40.00",
		StepUpOver:        "25.00",
		AllowedMerchants:  []string{"merchant_a"},
		AllowedCategories: []string{"electronics"},
		ValidFrom:         time.Now().Add(-1 * time.Hour),
		ExpiresAt:         time.Now().Add(24 * time.Hour),
		Nonce:             "nonce_api_" + uuid.New().String(),
	}

	if err := crypto.SignMandate(priv, &m); err != nil {
		t.Fatalf("failed to sign: %v", err)
	}
	if m.PublicKey != pub {
		t.Fatal("pub key mismatch")
	}
	return m, priv
}

func TestHealthEndpoint(t *testing.T) {
	srv, _, _, _ := setupTestServer(intelclient.NewStubIntelClient())
	handler := SetupRouter(srv)

	req := httptest.NewRequest(http.MethodGet, "/health", nil)
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", rec.Code)
	}
}

func TestPaymentIntentEndToEndAllowAndIdempotencyRecovery(t *testing.T) {
	stubIntel := intelclient.NewStubIntelClient()
	srv, ppMock, led, reg := setupTestServer(stubIntel)
	handler := SetupRouter(srv)

	m, _ := createTestMandate(t)
	reg.RegisterPrincipal("user_vivek", m.PublicKey)

	intentReq := models.PaymentIntentRequest{
		Mandate:        m,
		MerchantID:     "merchant_a",
		Amount:         "20.00", // Reconciles with items: 1 * 20.00
		Currency:       "USD",
		Category:       "electronics",
		Items:          []models.Item{{Name: "USB-C hub", Qty: 1, UnitPrice: "20.00"}},
		AgentReasoning: "Best hub under budget",
		IdempotencyKey: "idemp_test_allow_1",
	}

	body, _ := json.Marshal(intentReq)
	req := httptest.NewRequest(http.MethodPost, "/v1/payments/intents", bytes.NewBuffer(body))
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d (body: %s)", rec.Code, rec.Body.String())
	}

	var resp models.DecisionResponse
	if err := json.Unmarshal(rec.Body.Bytes(), &resp); err != nil {
		t.Fatalf("failed to unmarshal response: %v", err)
	}

	if resp.Decision != models.DecisionAllow {
		t.Fatalf("expected allow decision, got %s (reasons: %v)", resp.Decision, resp.Reasons)
	}
	if resp.PayPal == nil || resp.PayPal.CaptureID == "" {
		t.Fatalf("expected PayPal capture details on allow decision: %+v", resp.PayPal)
	}

	// Fix 5: SPEC ACCEPTANCE TEST - Retry with same idempotency key returns cached original DecisionResponse (HTTP 200)
	reqRetry := httptest.NewRequest(http.MethodPost, "/v1/payments/intents", bytes.NewBuffer(body))
	recRetry := httptest.NewRecorder()
	handler.ServeHTTP(recRetry, reqRetry)

	if recRetry.Code != http.StatusOK {
		t.Fatalf("expected 200 on idempotent retry, got %d", recRetry.Code)
	}
	var respRetry models.DecisionResponse
	_ = json.Unmarshal(recRetry.Body.Bytes(), &respRetry)
	if respRetry.PaymentID != resp.PaymentID || respRetry.PayPal.CaptureID != resp.PayPal.CaptureID {
		t.Fatalf("idempotent retry did not recover original decision: got %+v, want %+v", respRetry, resp)
	}

	// Verify events were written into the ledger
	eventsList, err := led.GetEventsByPayment(context.Background(), resp.PaymentID)
	if err != nil || len(eventsList) < 5 {
		t.Fatalf("expected event chain recorded in ledger, got %d events: %+v", len(eventsList), eventsList)
	}

	// Verify state machine ended in captured
	state, err := srv.stateMachine.Get(resp.PaymentID)
	if err != nil || state != statemachine.StateCaptured {
		t.Fatalf("expected state machine in captured state, got %s", state)
	}

	// Verify PayPal mock recorded the capture
	if _, ok := ppMock.Captures[resp.PayPal.CaptureID]; !ok {
		t.Fatal("capture was not recorded in PayPal client")
	}
}

func TestEmptyIdempotencyKeyRejected(t *testing.T) {
	srv, _, _, _ := setupTestServer(intelclient.NewStubIntelClient())
	handler := SetupRouter(srv)

	m, _ := createTestMandate(t)
	intentReq := models.PaymentIntentRequest{
		Mandate:        m,
		MerchantID:     "merchant_a",
		Amount:         "20.00",
		Currency:       "USD",
		IdempotencyKey: "", // Empty!
	}

	body, _ := json.Marshal(intentReq)
	req := httptest.NewRequest(http.MethodPost, "/v1/payments/intents", bytes.NewBuffer(body))
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)

	if rec.Code != http.StatusBadRequest {
		t.Fatalf("expected 400 for empty idempotency key, got %d", rec.Code)
	}
}

func TestItemAmountMismatchBlocked(t *testing.T) {
	stubIntel := intelclient.NewStubIntelClient()
	srv, _, _, reg := setupTestServer(stubIntel)
	handler := SetupRouter(srv)

	m, _ := createTestMandate(t)
	reg.RegisterPrincipal("user_vivek", m.PublicKey)

	intentReq := models.PaymentIntentRequest{
		Mandate:        m,
		MerchantID:     "merchant_a",
		Amount:         "50.00", // Mismatch: declared 50.00, items sum to 20.00
		Currency:       "USD",
		Category:       "electronics",
		Items:          []models.Item{{Name: "USB-C hub", Qty: 1, UnitPrice: "20.00"}},
		IdempotencyKey: "idemp_test_mismatch",
	}

	body, _ := json.Marshal(intentReq)
	req := httptest.NewRequest(http.MethodPost, "/v1/payments/intents", bytes.NewBuffer(body))
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)

	var resp models.DecisionResponse
	_ = json.Unmarshal(rec.Body.Bytes(), &resp)

	if resp.Decision != models.DecisionBlock {
		t.Fatalf("expected block on item amount mismatch, got %s", resp.Decision)
	}
}

func TestStepUpTokenValidation(t *testing.T) {
	stubIntel := intelclient.NewStubIntelClient()
	srv, _, _, reg := setupTestServer(stubIntel)
	handler := SetupRouter(srv)

	m, _ := createTestMandate(t)
	reg.RegisterPrincipal("user_vivek", m.PublicKey)

	// Amount 35.00 > step_up_over 25.00
	intentReq := models.PaymentIntentRequest{
		Mandate:        m,
		MerchantID:     "merchant_a",
		Amount:         "35.00",
		Currency:       "USD",
		Category:       "electronics",
		Items:          []models.Item{{Name: "USB-C hub", Qty: 1, UnitPrice: "35.00"}},
		IdempotencyKey: "idemp_test_stepup",
	}

	body, _ := json.Marshal(intentReq)
	req := httptest.NewRequest(http.MethodPost, "/v1/payments/intents", bytes.NewBuffer(body))
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)

	var resp models.DecisionResponse
	_ = json.Unmarshal(rec.Body.Bytes(), &resp)

	if resp.Decision != models.DecisionStepUp || resp.StepUpToken == "" {
		t.Fatalf("expected step_up with step_up_token, got %+v", resp)
	}

	// 1. Approve with wrong step_up_token -> 403 Forbidden
	wrongApproveBody, _ := json.Marshal(models.StepUpApprovalRequest{StepUpToken: "fake_token"})
	reqWrong := httptest.NewRequest(http.MethodPost, "/v1/payments/"+resp.PaymentID+"/approve", bytes.NewBuffer(wrongApproveBody))
	recWrong := httptest.NewRecorder()
	handler.ServeHTTP(recWrong, reqWrong)
	if recWrong.Code != http.StatusForbidden {
		t.Fatalf("expected 403 for wrong step_up_token, got %d", recWrong.Code)
	}

	// 2. Approve with correct step_up_token -> 200 OK
	correctApproveBody, _ := json.Marshal(models.StepUpApprovalRequest{StepUpToken: resp.StepUpToken})
	reqCorrect := httptest.NewRequest(http.MethodPost, "/v1/payments/"+resp.PaymentID+"/approve", bytes.NewBuffer(correctApproveBody))
	recCorrect := httptest.NewRecorder()
	handler.ServeHTTP(recCorrect, reqCorrect)
	if recCorrect.Code != http.StatusOK {
		t.Fatalf("expected 200 for correct step_up_token, got %d (%s)", recCorrect.Code, recCorrect.Body.String())
	}
}

func TestPayloadSizeLimitsEnforced(t *testing.T) {
	srv, _, _, _ := setupTestServer(intelclient.NewStubIntelClient())
	handler := SetupRouter(srv)

	m, _ := createTestMandate(t)
	// Create context exceeding 200,000 chars
	hugeText := string(make([]byte, 250_000))
	intentReq := models.PaymentIntentRequest{
		Mandate:        m,
		MerchantID:     "merchant_a",
		Amount:         "20.00",
		Currency:       "USD",
		SourceContexts: []models.SourceContext{{Ref: "huge", Text: hugeText}},
		IdempotencyKey: "idemp_huge",
	}

	body, _ := json.Marshal(intentReq)
	req := httptest.NewRequest(http.MethodPost, "/v1/payments/intents", bytes.NewBuffer(body))
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)

	if rec.Code != http.StatusBadRequest {
		t.Fatalf("expected 400 for payload limit exceedance, got %d", rec.Code)
	}
}

func TestInternalLedgerHookAcceptance(t *testing.T) {
	srv, _, _, _ := setupTestServer(intelclient.NewStubIntelClient())
	handler := SetupRouter(srv)

	paymentID := "pay_test_hook"
	idempKey := "paypal:WH-123456"

	hookPayload := models.HookEventRequest{
		Type:           "webhook.received",
		PaymentID:      &paymentID,
		IdempotencyKey: &idempKey,
		Source:         "intel",
		Data: map[string]interface{}{
			"paypal_event_id":   "WH-123456",
			"paypal_event_type": "PAYMENT.CAPTURE.COMPLETED",
			"paypal_status":     "captured",
			"amount":            "29.99",
			"currency":          "USD",
		},
	}

	body, _ := json.Marshal(hookPayload)

	// 1. Without internal token -> 401 Unauthorized
	reqUnauthorized := httptest.NewRequest(http.MethodPost, "/internal/ledger/events", bytes.NewBuffer(body))
	recUnauthorized := httptest.NewRecorder()
	handler.ServeHTTP(recUnauthorized, reqUnauthorized)
	if recUnauthorized.Code != http.StatusUnauthorized {
		t.Fatalf("expected 401 unauthorized, got %d", recUnauthorized.Code)
	}

	// 2. With valid internal token -> 201 Created
	reqValid := httptest.NewRequest(http.MethodPost, "/internal/ledger/events", bytes.NewBuffer(body))
	reqValid.Header.Set("X-Internal-Token", "test-token")
	recValid := httptest.NewRecorder()
	handler.ServeHTTP(recValid, reqValid)
	if recValid.Code != http.StatusCreated {
		t.Fatalf("expected 201 created, got %d (%s)", recValid.Code, recValid.Body.String())
	}

	// 3. Repeat with same idempotency key -> 409 Conflict (SPEC ACCEPTANCE REQUIREMENT)
	reqDup := httptest.NewRequest(http.MethodPost, "/internal/ledger/events", bytes.NewBuffer(body))
	reqDup.Header.Set("X-Internal-Token", "test-token")
	recDup := httptest.NewRecorder()
	handler.ServeHTTP(recDup, reqDup)
	if recDup.Code != http.StatusConflict {
		t.Fatalf("expected 409 conflict on duplicate idempotency key, got %d", recDup.Code)
	}
}

func TestDryRunModeDoesNotCallPayPal(t *testing.T) {
	stubIntel := intelclient.NewStubIntelClient()
	srv, ppMock, _, reg := setupTestServer(stubIntel)
	handler := SetupRouter(srv)

	m, _ := createTestMandate(t)
	reg.RegisterPrincipal("user_vivek", m.PublicKey)

	intentReq := models.PaymentIntentRequest{
		Mandate:        m,
		MerchantID:     "merchant_a",
		Amount:         "20.00",
		Currency:       "USD",
		Category:       "electronics",
		Items:          []models.Item{{Name: "USB-C hub", Qty: 1, UnitPrice: "20.00"}},
		AgentReasoning: "Benchmark scenario test",
		IdempotencyKey: "idemp_dryrun_test",
		DryRun:         true, // Tharun request: safe benchmark evaluation
	}

	body, _ := json.Marshal(intentReq)
	req := httptest.NewRequest(http.MethodPost, "/v1/payments/intents", bytes.NewBuffer(body))
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", rec.Code)
	}

	var resp models.DecisionResponse
	_ = json.Unmarshal(rec.Body.Bytes(), &resp)

	if !resp.DryRun {
		t.Fatal("expected dry_run flag in decision response")
	}
	if resp.Decision != models.DecisionAllow {
		t.Fatalf("expected allow decision, got %s", resp.Decision)
	}
	// PayPal mock should NOT have any real order created
	if len(ppMock.Orders) > 0 {
		t.Fatalf("dry run must not create PayPal orders, but found %d orders", len(ppMock.Orders))
	}
	if len(ppMock.Captures) > 0 {
		t.Fatalf("dry run must not capture PayPal funds, but found %d captures", len(ppMock.Captures))
	}
}

func TestWrongAgentBindingRejected(t *testing.T) {
	stubIntel := intelclient.NewStubIntelClient()
	srv, _, _, reg := setupTestServer(stubIntel)
	handler := SetupRouter(srv)

	m, _ := createTestMandate(t) // Mandate has AgentID: "agent_shopper"
	reg.RegisterPrincipal("user_vivek", m.PublicKey)

	intentReq := models.PaymentIntentRequest{
		Mandate:        m,
		AgentID:        "rogue_agent_99", // Mismatched agent!
		MerchantID:     "merchant_a",
		Amount:         "20.00",
		Currency:       "USD",
		Category:       "electronics",
		Items:          []models.Item{{Name: "USB-C hub", Qty: 1, UnitPrice: "20.00"}},
		IdempotencyKey: "idemp_agent_mismatch",
	}

	body, _ := json.Marshal(intentReq)
	req := httptest.NewRequest(http.MethodPost, "/v1/payments/intents", bytes.NewBuffer(body))
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)

	var resp models.DecisionResponse
	_ = json.Unmarshal(rec.Body.Bytes(), &resp)

	if resp.Decision != models.DecisionBlock {
		t.Fatalf("expected block on wrong agent binding, got %s", resp.Decision)
	}
}
