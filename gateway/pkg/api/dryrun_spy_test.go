package api

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

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

// setupSpyTestServer builds a test gateway server where live PayPal client is wrapped in a SpyClient.
func setupSpyTestServer(t *testing.T) (*Server, *paypal.SpyClient, *mandate.Verifier, ledger.Ledger, http.Handler) {
	cfg := &config.Config{
		Env:                 "development",
		RiskBlockThreshold:  0.65,
		RiskStepUpThreshold: 0.35,
	}

	led := ledger.NewMemoryLedger()
	nonceStore := mandate.NewMemoryNonceStore()
	principalRegistry := mandate.NewMemoryPrincipalRegistry()
	verifier := mandate.NewVerifier(nonceStore, led, principalRegistry)

	sm := statemachine.NewStateMachine()
	decConfig := policy.DecisionConfig{
		RiskBlockThreshold:  cfg.RiskBlockThreshold,
		RiskStepUpThreshold: cfg.RiskStepUpThreshold,
	}
	decEng := policy.NewDecisionEngine(decConfig)

	// Live mock PayPal client wrapped in a SpyClient
	mockPP := paypal.NewMockClient()
	spy := paypal.NewSpyClient(mockPP)

	intelStub := intelclient.NewStubIntelClient()
	broker := events.NewBroker()

	server := NewServer(cfg, led, verifier, sm, decEng, spy, intelStub, broker)
	handler := SetupRouter(server)

	return server, spy, verifier, led, handler
}

func helperCreateSignedMandate(t *testing.T, mutator func(*models.Mandate)) models.Mandate {
	pub, priv, err := crypto.GenerateKeyPair()
	if err != nil {
		t.Fatalf("GenerateKeyPair error: %v", err)
	}

	m := models.Mandate{
		MandateID:         "mdt_spy_test",
		PrincipalID:       "principal_user_1",
		AgentID:           "agent_shopper_1",
		Task:              "Office supplies under budget",
		Currency:          "USD",
		MaxTotal:          "150.00",
		MaxPerTxn:         "75.00",
		StepUpOver:        "40.00",
		AllowedMerchants:  []string{"merchant_staples", "merchant_books_123"},
		AllowedCategories: []string{"office_supplies", "books"},
		ValidFrom:         time.Now().Add(-1 * time.Hour),
		ExpiresAt:         time.Now().Add(24 * time.Hour),
		Nonce:             "nonce_" + time.Now().Format("20060102150405.000000"),
	}

	if mutator != nil {
		mutator(&m)
	}

	err = crypto.SignMandate(priv, &m)
	if err != nil {
		t.Fatalf("SignMandate error: %v", err)
	}
	m.PublicKey = pub
	return m
}

// Test 1: DryRun Allow Path asserts 0 live PayPal calls and 0 mandate spending
func TestDryRunSpy_AllowPathZeroCalls(t *testing.T) {
	_, spy, verifier, led, handler := setupSpyTestServer(t)

	m := helperCreateSignedMandate(t, nil)
	verifier.RegisterPrincipal(m.PrincipalID, m.PublicKey)

	reqPayload := models.PaymentIntentRequest{
		Mandate:        m,
		AgentID:        m.AgentID,
		MerchantID:     "merchant_staples",
		MerchantName:   "Staples Office Store",
		Amount:         "29.98",
		Currency:       "USD",
		Category:       "office_supplies",
		Items:          []models.Item{{Name: "Paper Ream", Qty: 2, UnitPrice: "14.99"}},
		AgentReasoning: "Best price office paper for task",
		IdempotencyKey: "dryrun_allow_idemp_001",
		DryRun:         true,
	}

	body, _ := json.Marshal(reqPayload)
	req := httptest.NewRequest(http.MethodPost, "/v1/payments/intents", bytes.NewBuffer(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()

	handler.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200 OK, got %d: %s", w.Code, w.Body.String())
	}

	var dec models.DecisionResponse
	_ = json.Unmarshal(w.Body.Bytes(), &dec)

	if dec.Decision != models.DecisionAllow {
		t.Fatalf("expected allow, got %s (reasons: %v)", dec.Decision, dec.Reasons)
	}
	if !dec.DryRun {
		t.Fatalf("expected DryRun=true in decision response")
	}

	// ASSERTION: Zero calls to live PayPal client across all operations
	if spy.TotalCalls() != 0 {
		t.Fatalf("VIOLATION: spy recorded %d live calls (expected 0 calls in dry-run mode)", spy.TotalCalls())
	}

	// ASSERTION: Mandate total spent must remain 0.00
	totalSpent, err := led.GetTotalSpent(m.MandateID)
	if err != nil {
		t.Fatalf("GetTotalSpent error: %v", err)
	}
	if totalSpent != 0.00 {
		t.Fatalf("VIOLATION: dry-run payment altered mandate spending to %.2f (expected 0.00)", totalSpent)
	}
}

// Test 2: DryRun Step-Up and Human Approval asserts ZERO live PayPal calls throughout the full lifecycle
func TestDryRunSpy_StepUpAndApprovalZeroCalls(t *testing.T) {
	_, spy, verifier, led, handler := setupSpyTestServer(t)

	m := helperCreateSignedMandate(t, nil)
	verifier.RegisterPrincipal(m.PrincipalID, m.PublicKey)

	// Amount 49.98 exceeds StepUpOver ($40.00) -> triggers step_up
	reqPayload := models.PaymentIntentRequest{
		Mandate:        m,
		AgentID:        m.AgentID,
		MerchantID:     "merchant_staples",
		Amount:         "49.98",
		Currency:       "USD",
		Category:       "office_supplies",
		Items:          []models.Item{{Name: "Desk Lamp", Qty: 2, UnitPrice: "24.99"}},
		AgentReasoning: "Desk lamp requested",
		IdempotencyKey: "dryrun_stepup_idemp_002",
		DryRun:         true,
	}

	body, _ := json.Marshal(reqPayload)
	req := httptest.NewRequest(http.MethodPost, "/v1/payments/intents", bytes.NewBuffer(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()

	handler.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200 OK, got %d: %s", w.Code, w.Body.String())
	}

	var dec models.DecisionResponse
	_ = json.Unmarshal(w.Body.Bytes(), &dec)

	if dec.Decision != models.DecisionStepUp {
		t.Fatalf("expected step_up, got %s", dec.Decision)
	}
	if dec.StepUpToken == "" {
		t.Fatalf("expected step_up_token in dry-run step-up response")
	}

	// ASSERTION: Zero calls during intent step-up
	if spy.TotalCalls() != 0 {
		t.Fatalf("VIOLATION: spy recorded %d calls during step-up creation", spy.TotalCalls())
	}

	// Now call POST /v1/payments/{id}/approve (Tharun Finding 1: must NOT touch live PayPal capture)
	apprBody, _ := json.Marshal(models.StepUpApprovalRequest{StepUpToken: dec.StepUpToken})
	apprReq := httptest.NewRequest(http.MethodPost, "/v1/payments/"+dec.PaymentID+"/approve", bytes.NewBuffer(apprBody))
	apprReq.Header.Set("Content-Type", "application/json")
	apprW := httptest.NewRecorder()

	handler.ServeHTTP(apprW, apprReq)

	if apprW.Code != http.StatusOK {
		t.Fatalf("expected 200 OK on approve, got %d: %s", apprW.Code, apprW.Body.String())
	}

	var apprResp map[string]interface{}
	_ = json.Unmarshal(apprW.Body.Bytes(), &apprResp)

	if apprResp["status"] != "approved" || apprResp["decision"] != "allow" {
		t.Fatalf("unexpected approve response: %+v", apprResp)
	}

	// CRITICAL ASSERTION: Zero calls to live PayPal during human approval of dry-run payment!
	if spy.TotalCalls() != 0 {
		t.Fatalf("CRITICAL SECURITY VIOLATION: live PayPal client was invoked %d times during dry-run approval!", spy.TotalCalls())
	}

	// ASSERTION: Mandate total spent must remain 0.00
	totalSpent, _ := led.GetTotalSpent(m.MandateID)
	if totalSpent != 0.00 {
		t.Fatalf("VIOLATION: dry-run approval altered mandate spending to %.2f (expected 0.00)", totalSpent)
	}

	// Verify projection state
	proj, err := led.GetPaymentProjection(context.Background(), dec.PaymentID)
	if err != nil {
		t.Fatalf("GetPaymentProjection error: %v", err)
	}
	if !proj.IsDryRun {
		t.Fatalf("expected proj.IsDryRun=true")
	}
	if !proj.StepUpConsumed {
		t.Fatalf("expected proj.StepUpConsumed=true after approval")
	}

	// Re-approval attempt with consumed token must return 409 Conflict
	reApprW := httptest.NewRecorder()
	reApprReq := httptest.NewRequest(http.MethodPost, "/v1/payments/"+dec.PaymentID+"/approve", bytes.NewBuffer(apprBody))
	reApprReq.Header.Set("Content-Type", "application/json")
	handler.ServeHTTP(reApprW, reApprReq)

	if reApprW.Code != http.StatusConflict && reApprW.Code != http.StatusBadRequest {
		t.Fatalf("expected 409 Conflict on consumed token replay, got %d: %s", reApprW.Code, reApprW.Body.String())
	}
}

// Test 3: DryRun Step-Up Rejection asserts zero live PayPal calls
func TestDryRunSpy_StepUpAndRejectionZeroCalls(t *testing.T) {
	_, spy, verifier, _, handler := setupSpyTestServer(t)

	m := helperCreateSignedMandate(t, nil)
	verifier.RegisterPrincipal(m.PrincipalID, m.PublicKey)

	reqPayload := models.PaymentIntentRequest{
		Mandate:        m,
		AgentID:        m.AgentID,
		MerchantID:     "merchant_staples",
		Amount:         "49.98",
		Currency:       "USD",
		Category:       "office_supplies",
		Items:          []models.Item{{Name: "Desk Lamp", Qty: 2, UnitPrice: "24.99"}},
		AgentReasoning: "Desk lamp requested",
		IdempotencyKey: "dryrun_reject_idemp_003",
		DryRun:         true,
	}

	body, _ := json.Marshal(reqPayload)
	req := httptest.NewRequest(http.MethodPost, "/v1/payments/intents", bytes.NewBuffer(body))
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, req)

	var dec models.DecisionResponse
	_ = json.Unmarshal(w.Body.Bytes(), &dec)

	// Call POST /v1/payments/{id}/reject
	rejReq := httptest.NewRequest(http.MethodPost, "/v1/payments/"+dec.PaymentID+"/reject", nil)
	rejW := httptest.NewRecorder()
	handler.ServeHTTP(rejW, rejReq)

	if rejW.Code != http.StatusOK {
		t.Fatalf("expected 200 OK on reject, got %d: %s", rejW.Code, rejW.Body.String())
	}

	// ASSERTION: Zero calls to live PayPal
	if spy.TotalCalls() != 0 {
		t.Fatalf("VIOLATION: spy recorded %d calls during dry-run rejection", spy.TotalCalls())
	}
}

// Test 4: Idempotency Partitioning: dryrun and live namespaces do not collide
func TestDryRunSpy_IdempotencyNamespaceIsolation(t *testing.T) {
	_, spy, verifier, _, handler := setupSpyTestServer(t)

	m := helperCreateSignedMandate(t, nil)
	verifier.RegisterPrincipal(m.PrincipalID, m.PublicKey)

	sharedKey := "shared_idemp_key_999"

	// 1. Submit as DryRun
	reqDryRun := models.PaymentIntentRequest{
		Mandate:        m,
		AgentID:        m.AgentID,
		MerchantID:     "merchant_staples",
		Amount:         "29.98",
		Currency:       "USD",
		Category:       "office_supplies",
		Items:          []models.Item{{Name: "Paper Ream", Qty: 2, UnitPrice: "14.99"}},
		AgentReasoning: "Dry run test",
		IdempotencyKey: sharedKey,
		DryRun:         true,
	}
	bodyDry, _ := json.Marshal(reqDryRun)
	wDry := httptest.NewRecorder()
	handler.ServeHTTP(wDry, httptest.NewRequest(http.MethodPost, "/v1/payments/intents", bytes.NewBuffer(bodyDry)))

	var decDry models.DecisionResponse
	_ = json.Unmarshal(wDry.Body.Bytes(), &decDry)
	if !decDry.DryRun {
		t.Fatalf("expected decDry.DryRun=true")
	}
	if spy.TotalCalls() != 0 {
		t.Fatalf("expected 0 spy calls on dry run")
	}

	// 2. Submit same idempotency key as LIVE
	// Need fresh nonce for mandate
	mLive := helperCreateSignedMandate(t, func(m *models.Mandate) {
		m.Nonce = "nonce_live_fresh_" + time.Now().Format("150405.000000")
	})
	verifier.RegisterPrincipal(mLive.PrincipalID, mLive.PublicKey)

	reqLive := models.PaymentIntentRequest{
		Mandate:        mLive,
		AgentID:        mLive.AgentID,
		MerchantID:     "merchant_staples",
		Amount:         "29.98",
		Currency:       "USD",
		Category:       "office_supplies",
		Items:          []models.Item{{Name: "Paper Ream", Qty: 2, UnitPrice: "14.99"}},
		AgentReasoning: "Live execution with same idempotency key",
		IdempotencyKey: sharedKey,
		DryRun:         false,
	}
	bodyLive, _ := json.Marshal(reqLive)
	wLive := httptest.NewRecorder()
	handler.ServeHTTP(wLive, httptest.NewRequest(http.MethodPost, "/v1/payments/intents", bytes.NewBuffer(bodyLive)))

	var decLive models.DecisionResponse
	_ = json.Unmarshal(wLive.Body.Bytes(), &decLive)

	// Live request must NOT have retrieved the dry-run cached decision
	if decLive.DryRun {
		t.Fatalf("VIOLATION: live request retrieved cached dry-run decision across namespaces!")
	}
	if decLive.PaymentID == decDry.PaymentID {
		t.Fatalf("VIOLATION: live and dry-run payments shared payment ID %s", decLive.PaymentID)
	}

	// In live mode, spy should have recorded live PayPal operations (CreateOrder, AuthorizeOrder, Capture)
	if spy.TotalCalls() == 0 {
		t.Fatalf("expected live request to invoke paypal operations, got 0")
	}
}

// Test 5: Merchant Payee Binding in Live Mode
func TestMerchantPayeeBindingLiveMode(t *testing.T) {
	_, spy, verifier, _, handler := setupSpyTestServer(t)

	m := helperCreateSignedMandate(t, nil)
	verifier.RegisterPrincipal(m.PrincipalID, m.PublicKey)

	reqLive := models.PaymentIntentRequest{
		Mandate:        m,
		AgentID:        m.AgentID,
		MerchantID:     "merchant_books_123",
		Amount:         "19.99",
		Currency:       "USD",
		Category:       "books",
		Items:          []models.Item{{Name: "Go Programming Book", Qty: 1, UnitPrice: "19.99"}},
		AgentReasoning: "Required textbook",
		IdempotencyKey: "payee_binding_live_001",
		DryRun:         false,
	}

	body, _ := json.Marshal(reqLive)
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, httptest.NewRequest(http.MethodPost, "/v1/payments/intents", bytes.NewBuffer(body)))

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200 OK, got %d: %s", w.Code, w.Body.String())
	}

	// Verify that live paypal calls were made
	if spy.CreateOrderCalls != 1 || spy.AuthorizeOrderCalls != 1 || spy.CaptureCalls != 1 {
		t.Fatalf("expected 1 CreateOrder, 1 Authorize, 1 Capture, got: Create=%d, Auth=%d, Cap=%d",
			spy.CreateOrderCalls, spy.AuthorizeOrderCalls, spy.CaptureCalls)
	}
}

// Test 6: Failure Injection in Dry-Run Mode asserts proper error return and zero live calls
func TestDryRunSpy_FailureInjection(t *testing.T) {
	server, spy, verifier, _, handler := setupSpyTestServer(t)

	// Inject error in dry-run client
	dryClient := paypal.NewDryRunClient()
	dryClient.InjectCaptureError(errors.New("simulated payment network outage"))
	server.SetDryRunClient(dryClient)

	m := helperCreateSignedMandate(t, nil)
	verifier.RegisterPrincipal(m.PrincipalID, m.PublicKey)

	reqPayload := models.PaymentIntentRequest{
		Mandate:        m,
		AgentID:        m.AgentID,
		MerchantID:     "merchant_staples",
		Amount:         "29.98",
		Currency:       "USD",
		Category:       "office_supplies",
		Items:          []models.Item{{Name: "Paper Ream", Qty: 2, UnitPrice: "14.99"}},
		AgentReasoning: "Failure injection test",
		IdempotencyKey: "dryrun_fail_injection_001",
		DryRun:         true,
	}

	body, _ := json.Marshal(reqPayload)
	req := httptest.NewRequest(http.MethodPost, "/v1/payments/intents", bytes.NewBuffer(body))
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, req)

	// Should report 502 Bad Gateway
	if w.Code != http.StatusBadGateway {
		t.Fatalf("expected 502 Bad Gateway on injected failure, got %d", w.Code)
	}

	// CRITICAL: Live PayPal client must still have 0 calls
	if spy.TotalCalls() != 0 {
		t.Fatalf("VIOLATION: spy recorded %d calls on injected dry-run failure", spy.TotalCalls())
	}
}

// Test 7: Concurrent Approval Attempts: Atomic step-up token consumption allows exactly 1 approval
func TestDryRunSpy_ConcurrentApprovals(t *testing.T) {
	_, spy, verifier, _, handler := setupSpyTestServer(t)

	m := helperCreateSignedMandate(t, nil)
	verifier.RegisterPrincipal(m.PrincipalID, m.PublicKey)

	// Create step-up payment
	reqPayload := models.PaymentIntentRequest{
		Mandate:        m,
		AgentID:        m.AgentID,
		MerchantID:     "merchant_staples",
		Amount:         "49.98",
		Currency:       "USD",
		Category:       "office_supplies",
		Items:          []models.Item{{Name: "Desk Lamp", Qty: 2, UnitPrice: "24.99"}},
		AgentReasoning: "Step-up concurrent test",
		IdempotencyKey: "concurrent_stepup_idemp",
		DryRun:         true,
	}

	body, _ := json.Marshal(reqPayload)
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, httptest.NewRequest(http.MethodPost, "/v1/payments/intents", bytes.NewBuffer(body)))

	var dec models.DecisionResponse
	_ = json.Unmarshal(w.Body.Bytes(), &dec)
	if dec.StepUpToken == "" {
		t.Fatalf("expected step_up_token")
	}

	// Fire 10 concurrent approval requests simultaneously
	concurrency := 10
	results := make(chan int, concurrency)
	for i := 0; i < concurrency; i++ {
		go func() {
			apprBody, _ := json.Marshal(models.StepUpApprovalRequest{StepUpToken: dec.StepUpToken})
			apprReq := httptest.NewRequest(http.MethodPost, "/v1/payments/"+dec.PaymentID+"/approve", bytes.NewBuffer(apprBody))
			apprW := httptest.NewRecorder()
			handler.ServeHTTP(apprW, apprReq)
			results <- apprW.Code
		}()
	}

	successCount := 0
	conflictCount := 0
	for i := 0; i < concurrency; i++ {
		code := <-results
		if code == http.StatusOK {
			successCount++
		} else if code == http.StatusConflict {
			conflictCount++
		}
	}

	// Exactly 1 must succeed, exactly 9 must receive 409 Conflict
	if successCount != 1 {
		t.Fatalf("expected exactly 1 successful approval, got %d", successCount)
	}
	if conflictCount != concurrency-1 {
		t.Fatalf("expected %d conflicts, got %d", concurrency-1, conflictCount)
	}

	// Live PayPal client calls must be 0
	if spy.TotalCalls() != 0 {
		t.Fatalf("expected 0 live calls, got %d", spy.TotalCalls())
	}
}

// Test 8: Replay & Recovery Consistency in Dry-Run Mode
func TestDryRunSpy_ReplayAndRecoveryConsistency(t *testing.T) {
	_, _, verifier, led, handler := setupSpyTestServer(t)

	m := helperCreateSignedMandate(t, nil)
	verifier.RegisterPrincipal(m.PrincipalID, m.PublicKey)

	reqPayload := models.PaymentIntentRequest{
		Mandate:        m,
		AgentID:        m.AgentID,
		MerchantID:     "merchant_staples",
		Amount:         "29.98",
		Currency:       "USD",
		Category:       "office_supplies",
		Items:          []models.Item{{Name: "Paper Ream", Qty: 2, UnitPrice: "14.99"}},
		AgentReasoning: "Replay test",
		IdempotencyKey: "replay_test_idemp_001",
		DryRun:         true,
	}

	body, _ := json.Marshal(reqPayload)
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, httptest.NewRequest(http.MethodPost, "/v1/payments/intents", bytes.NewBuffer(body)))

	var dec models.DecisionResponse
	_ = json.Unmarshal(w.Body.Bytes(), &dec)

	// Run full replay on ledger
	if err := led.Replay(context.Background()); err != nil {
		t.Fatalf("ledger.Replay error: %v", err)
	}

	// Verify projection after replay
	proj, err := led.GetPaymentProjection(context.Background(), dec.PaymentID)
	if err != nil {
		t.Fatalf("failed to retrieve projection after replay: %v", err)
	}

	if !proj.IsDryRun {
		t.Fatalf("expected proj.IsDryRun=true after replay")
	}
	if proj.State != "captured" {
		t.Fatalf("expected proj.State=captured after replay, got: %s", proj.State)
	}

	// Mandate total spent must STILL be 0.00 after replay
	totalSpent, _ := led.GetTotalSpent(m.MandateID)
	if totalSpent != 0.00 {
		t.Fatalf("expected 0.00 spent after replay, got %.2f", totalSpent)
	}
}

// Test 9: GATEWAY_MODE=dryrun forces dry-run provenance even when request omits dry_run flag
func TestDryRunSpy_GatewayModeDryRunForcesProvenance(t *testing.T) {
	spy := paypal.NewSpyClient(paypal.NewMockClient())
	cfg := &config.Config{
		Port:                "8080",
		Env:                 "development",
		GatewayMode:         "dryrun", // Deployment level dryrun mode
		RiskBlockThreshold:  0.65,
		RiskStepUpThreshold: 0.35,
	}
	nonceStore := mandate.NewMemoryNonceStore()
	led := ledger.NewMemoryLedger()
	registry := mandate.NewMemoryPrincipalRegistry()
	verifier := mandate.NewVerifier(nonceStore, led, registry)
	sm := statemachine.NewStateMachine()
	de := policy.NewDecisionEngine(policy.DefaultDecisionConfig())
	broker := events.NewBroker()

	srv := NewServer(cfg, led, verifier, sm, de, spy, intelclient.NewStubIntelClient(), broker)
	handler := SetupRouter(srv)

	m := helperCreateSignedMandate(t, nil)
	verifier.RegisterPrincipal(m.PrincipalID, m.PublicKey)

	reqPayload := models.PaymentIntentRequest{
		Mandate:        m,
		AgentID:        m.AgentID,
		MerchantID:     "merchant_staples",
		Amount:         "14.99",
		Currency:       "USD",
		Category:       "office_supplies",
		Items:          []models.Item{{Name: "Paper Ream", Qty: 1, UnitPrice: "14.99"}},
		AgentReasoning: "Testing deployment-level dryrun provenance forcing",
		IdempotencyKey: "force_dryrun_prov_001",
		DryRun:         false, // Request explicitly says false!
	}

	body, _ := json.Marshal(reqPayload)
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, httptest.NewRequest(http.MethodPost, "/v1/payments/intents", bytes.NewBuffer(body)))

	var dec models.DecisionResponse
	_ = json.Unmarshal(w.Body.Bytes(), &dec)

	// Decision must have DryRun forced to true
	if !dec.DryRun {
		t.Fatalf("expected dec.DryRun=true when GATEWAY_MODE=dryrun, got false")
	}

	// Projection must have IsDryRun forced to true
	proj, err := led.GetPaymentProjection(context.Background(), dec.PaymentID)
	if err != nil || !proj.IsDryRun {
		t.Fatalf("expected projection IsDryRun=true")
	}

	// Live PayPal client calls must be strictly 0
	if spy.TotalCalls() != 0 {
		t.Fatalf("expected 0 calls, got %d", spy.TotalCalls())
	}
}

// Test 10: Nonce Isolation between Dry-Run and Live Mode
func TestDryRunSpy_NonceIsolationBetweenDryRunAndLive(t *testing.T) {
	_, _, verifier, _, handler := setupSpyTestServer(t)

	// 1. Create a mandate with a specific fixed nonce
	m := helperCreateSignedMandate(t, func(m *models.Mandate) {
		m.Nonce = "isolated_test_nonce_12345"
	})
	verifier.RegisterPrincipal(m.PrincipalID, m.PublicKey)

	// Submit in DRY-RUN mode
	dryPayload := models.PaymentIntentRequest{
		Mandate:        m,
		AgentID:        m.AgentID,
		MerchantID:     "merchant_staples",
		Amount:         "10.00",
		Currency:       "USD",
		Category:       "office_supplies",
		Items:          []models.Item{{Name: "Pen", Qty: 1, UnitPrice: "10.00"}},
		AgentReasoning: "Dry-run nonce burn test",
		IdempotencyKey: "dryrun_nonce_idemp_1",
		DryRun:         true,
	}
	body, _ := json.Marshal(dryPayload)
	wDry := httptest.NewRecorder()
	handler.ServeHTTP(wDry, httptest.NewRequest(http.MethodPost, "/v1/payments/intents", bytes.NewBuffer(body)))

	var decDry models.DecisionResponse
	_ = json.Unmarshal(wDry.Body.Bytes(), &decDry)
	if decDry.Decision != models.DecisionAllow {
		t.Fatalf("expected dry-run allow, got %s (reasons: %v)", decDry.Decision, decDry.Reasons)
	}

	// Re-submitting the exact same nonce in DRY-RUN mode must be REJECTED (replay protection within namespace)
	dryPayload.IdempotencyKey = "dryrun_nonce_idemp_2"
	bodyDryReplay, _ := json.Marshal(dryPayload)
	wDryReplay := httptest.NewRecorder()
	handler.ServeHTTP(wDryReplay, httptest.NewRequest(http.MethodPost, "/v1/payments/intents", bytes.NewBuffer(bodyDryReplay)))
	var decDryReplay models.DecisionResponse
	_ = json.Unmarshal(wDryReplay.Body.Bytes(), &decDryReplay)
	if decDryReplay.Decision != models.DecisionBlock {
		t.Fatalf("expected replay rejection in dry-run mode, got %s", decDryReplay.Decision)
	}

	// Submitting the SAME mandate and nonce in LIVE mode MUST SUCCEED (dry-run did not burn live nonce!)
	livePayload := models.PaymentIntentRequest{
		Mandate:        m,
		AgentID:        m.AgentID,
		MerchantID:     "merchant_staples",
		Amount:         "10.00",
		Currency:       "USD",
		Category:       "office_supplies",
		Items:          []models.Item{{Name: "Pen", Qty: 1, UnitPrice: "10.00"}},
		AgentReasoning: "Live submission with same mandate",
		IdempotencyKey: "live_nonce_idemp_1",
		DryRun:         false, // LIVE MODE
	}
	bodyLive, _ := json.Marshal(livePayload)
	wLive := httptest.NewRecorder()
	handler.ServeHTTP(wLive, httptest.NewRequest(http.MethodPost, "/v1/payments/intents", bytes.NewBuffer(bodyLive)))

	var decLive models.DecisionResponse
	_ = json.Unmarshal(wLive.Body.Bytes(), &decLive)
	if decLive.Decision != models.DecisionAllow {
		t.Fatalf("expected live submission to succeed with unburned live nonce, got %s (reasons: %v)", decLive.Decision, decLive.Reasons)
	}
}

// Test 11: Crash Recovery and Atomic Step-Up Token Consumption
func TestDryRunSpy_CrashRecoveryIdempotentApproval(t *testing.T) {
	_, spy, verifier, led, handler := setupSpyTestServer(t)

	m := helperCreateSignedMandate(t, func(m *models.Mandate) {
		m.StepUpOver = "10.00"
		m.MaxPerTxn = "50.00"
		m.MaxTotal = "100.00"
	})
	verifier.RegisterPrincipal(m.PrincipalID, m.PublicKey)

	reqPayload := models.PaymentIntentRequest{
		Mandate:        m,
		AgentID:        m.AgentID,
		MerchantID:     "merchant_staples",
		Amount:         "25.00",
		Currency:       "USD",
		Category:       "office_supplies",
		Items:          []models.Item{{Name: "Paper Ream", Qty: 1, UnitPrice: "25.00"}},
		AgentReasoning: "Step up approval idempotency test",
		IdempotencyKey: "crash_recovery_stepup_idemp",
		DryRun:         true,
	}

	body, _ := json.Marshal(reqPayload)
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, httptest.NewRequest(http.MethodPost, "/v1/payments/intents", bytes.NewBuffer(body)))

	var dec models.DecisionResponse
	_ = json.Unmarshal(w.Body.Bytes(), &dec)
	if dec.StepUpToken == "" {
		t.Fatalf("expected step_up_token, got: %+v", dec)
	}

	// 1. First approval call: succeeds
	apprBody, _ := json.Marshal(models.StepUpApprovalRequest{StepUpToken: dec.StepUpToken})
	wAppr1 := httptest.NewRecorder()
	handler.ServeHTTP(wAppr1, httptest.NewRequest(http.MethodPost, "/v1/payments/"+dec.PaymentID+"/approve", bytes.NewBuffer(apprBody)))
	if wAppr1.Code != http.StatusOK {
		t.Fatalf("expected 200 OK on first approval, got %d: %s", wAppr1.Code, wAppr1.Body.String())
	}
	var res1 map[string]interface{}
	_ = json.Unmarshal(wAppr1.Body.Bytes(), &res1)
	capID1, _ := res1["capture_id"].(string)
	if capID1 == "" {
		t.Fatalf("expected non-empty capture_id on approval")
	}

	// 2. Simulated retry with same consumed token: must receive 409 Conflict
	wAppr2 := httptest.NewRecorder()
	handler.ServeHTTP(wAppr2, httptest.NewRequest(http.MethodPost, "/v1/payments/"+dec.PaymentID+"/approve", bytes.NewBuffer(apprBody)))
	if wAppr2.Code != http.StatusConflict {
		t.Fatalf("expected 409 Conflict on consumed token replay, got %d: %s", wAppr2.Code, wAppr2.Body.String())
	}

	// 3. Projection in ledger must remain captured with same capture ID
	proj, err := led.GetPaymentProjection(context.Background(), dec.PaymentID)
	if err != nil || proj.State != "captured" || proj.PayPalCaptureID != capID1 {
		t.Fatalf("expected projection to remain captured with %s, got %+v", capID1, proj)
	}

	// 4. Live calls must be strictly 0
	if spy.TotalCalls() != 0 {
		t.Fatalf("expected 0 live calls, got %d", spy.TotalCalls())
	}
}

// Test 12: Intelligence Response Validation - Incomplete Schemas
func TestIntelligenceResponseValidation_IncompleteSchemas(t *testing.T) {
	// Missing version
	scanMissingVersion := &models.ScanResponse{
		Verdict:    "clean",
		Confidence: 0.9,
		Stage:      "heuristic",
	}
	if err := intelclient.ValidateScanResponse(scanMissingVersion); err == nil {
		t.Fatalf("expected error for scan response missing version")
	}

	// Invalid confidence
	scanBadConf := &models.ScanResponse{
		Version:    "scan_v1",
		Verdict:    "clean",
		Confidence: 1.5,
		Stage:      "heuristic",
	}
	if err := intelclient.ValidateScanResponse(scanBadConf); err == nil {
		t.Fatalf("expected error for scan response confidence > 1.0")
	}

	// Missing reason on unknown verdict
	scanUnknownNoReason := &models.ScanResponse{
		Version: "scan_v1",
		Verdict: "unknown",
	}
	if err := intelclient.ValidateScanResponse(scanUnknownNoReason); err == nil {
		t.Fatalf("expected error for unknown scan without reason")
	}

	// Risk ok status but missing score
	riskOkNoScore := &models.RiskResponse{
		Version: "risk_v1",
		Status:  "ok",
		Score:   nil,
	}
	if err := intelclient.ValidateRiskResponse(riskOkNoScore); err == nil {
		t.Fatalf("expected error for risk ok status with null score")
	}

	// Risk unknown status but with score
	score := 0.2
	riskUnknownWithScore := &models.RiskResponse{
		Version: "risk_v1",
		Status:  "unknown",
		Score:   &score,
	}
	if err := intelclient.ValidateRiskResponse(riskUnknownWithScore); err == nil {
		t.Fatalf("expected error for risk unknown status with non-null score")
	}
}
