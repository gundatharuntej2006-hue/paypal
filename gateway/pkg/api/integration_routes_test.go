package api

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"gateway/pkg/crypto"
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
	mandate2, _ := createTestMandate(t)
	reg.RegisterPrincipal(mandate2.PrincipalID, mandate2.PublicKey)
	intentReq.Mandate = mandate2
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

	// 4. Verify GET /v1/payments/:id/events returns events array
	req = httptest.NewRequest(http.MethodGet, "/v1/payments/"+decResp2.PaymentID+"/events", nil)
	rec = httptest.NewRecorder()
	router.ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("expected 200 from GET /v1/payments/:id/events, got %d", rec.Code)
	}
	var pEvents []models.LedgerEvent
	if err := json.NewDecoder(rec.Body).Decode(&pEvents); err != nil {
		t.Fatalf("failed to decode payment events: %v", err)
	}
	if len(pEvents) == 0 {
		t.Fatalf("expected at least 1 event for payment %s", decResp2.PaymentID)
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

// ---------------------------------------------------------------------------
// Tests specifically addressing Tharun's independent security audit findings
// ---------------------------------------------------------------------------

func TestAuditIssue1_MandateRegistrationSecurity(t *testing.T) {
	srv, _, _, _ := setupTestServer(nil)
	srv.cfg.HumanAuthToken = "secret_human_token"
	router := SetupRouter(srv)

	mandate := models.Mandate{
		MandateID:   "mdt_audit_1",
		PrincipalID: "user_audit_principal",
		PublicKey:   "mc4CAQAwBQYDK2VwBCIEIPTESTPUBLICKEY_AUDIT_1",
	}
	body, _ := json.Marshal(mandate)

	// 1. Unauthenticated registration must be rejected with 401
	req := httptest.NewRequest(http.MethodPost, "/v1/mandates", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	rec := httptest.NewRecorder()
	router.ServeHTTP(rec, req)
	if rec.Code != http.StatusUnauthorized {
		t.Fatalf("expected 401 Unauthorized for unauthenticated mandate registration, got %d", rec.Code)
	}

	// 2. Authenticated registration succeeds
	req = httptest.NewRequest(http.MethodPost, "/v1/mandates", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer secret_human_token")
	rec = httptest.NewRecorder()
	router.ServeHTTP(rec, req)
	if rec.Code != http.StatusCreated {
		t.Fatalf("expected 201 Created for authenticated registration, got %d: %s", rec.Code, rec.Body.String())
	}

	// 3. Attempting to overwrite existing principal's public key must be rejected with 409 Conflict
	tamperedMandate := models.Mandate{
		MandateID:   "mdt_audit_tampered",
		PrincipalID: "user_audit_principal",
		PublicKey:   "mc4CAQAwBQYDK2VwBCIEIPATTACKER_OVERWRITE_KEY",
	}
	tamperedBody, _ := json.Marshal(tamperedMandate)
	req = httptest.NewRequest(http.MethodPost, "/v1/mandates", bytes.NewReader(tamperedBody))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer secret_human_token")
	rec = httptest.NewRecorder()
	router.ServeHTTP(rec, req)
	if rec.Code != http.StatusConflict {
		t.Fatalf("expected 409 Conflict on principal public key replacement, got %d", rec.Code)
	}
}

func TestAuditIssue2_MandateOrderSecurityAndDryRun(t *testing.T) {
	srv, _, _, _ := setupTestServer(nil)
	srv.cfg.HumanAuthToken = "secret_human_token"
	srv.cfg.AgentAuthToken = "secret_agent_token"
	router := SetupRouter(srv)

	// Register mandate
	mandate := models.Mandate{
		MandateID:   "mdt_audit_order",
		PrincipalID: "user_principal_a",
		AgentID:     "agent_shopper_a",
		Currency:    "USD",
	}
	srv.mandates[mandate.MandateID] = mandate

	draft := map[string]interface{}{
		"mandate_id":   "mdt_audit_order",
		"principal_id": "user_principal_a",
		"agent_id":     "agent_shopper_a",
		"amount":       25.00,
		"dry_run":      true,
	}
	draftBody, _ := json.Marshal(draft)

	// 1. Unauthenticated request must return 401
	req := httptest.NewRequest(http.MethodPost, "/v1/mandates/mdt_audit_order/order", bytes.NewReader(draftBody))
	req.Header.Set("Content-Type", "application/json")
	rec := httptest.NewRecorder()
	router.ServeHTTP(rec, req)
	if rec.Code != http.StatusUnauthorized {
		t.Fatalf("expected 401, got %d", rec.Code)
	}

	// 2. Non-existent mandate must return 404
	req = httptest.NewRequest(http.MethodPost, "/v1/mandates/non_existent/order", bytes.NewReader(draftBody))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer secret_human_token")
	rec = httptest.NewRecorder()
	router.ServeHTTP(rec, req)
	if rec.Code != http.StatusNotFound {
		t.Fatalf("expected 404, got %d", rec.Code)
	}

	// 3. Ownership mismatch must return 403
	badOwnerDraft := map[string]interface{}{
		"principal_id": "user_attacker",
		"amount":       25.00,
	}
	badBody, _ := json.Marshal(badOwnerDraft)
	req = httptest.NewRequest(http.MethodPost, "/v1/mandates/mdt_audit_order/order", bytes.NewReader(badBody))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer secret_human_token")
	rec = httptest.NewRecorder()
	router.ServeHTTP(rec, req)
	if rec.Code != http.StatusForbidden {
		t.Fatalf("expected 403 on ownership mismatch, got %d", rec.Code)
	}

	// 4. Authenticated dry-run request succeeds and indicates dry_run
	req = httptest.NewRequest(http.MethodPost, "/v1/mandates/mdt_audit_order/order", bytes.NewReader(draftBody))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer secret_agent_token")
	rec = httptest.NewRecorder()
	router.ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", rec.Code, rec.Body.String())
	}
	var res map[string]interface{}
	_ = json.NewDecoder(rec.Body).Decode(&res)
	if res["dry_run"] != true {
		t.Fatalf("expected dry_run: true in response")
	}
}

func TestAuditIssue3_ApprovalsAuthenticationAndRedaction(t *testing.T) {
	stubIntel := intelclient.NewStubIntelClient()
	stubIntel.RiskScoreVal = 0.45 // causes step_up

	srv, _, _, reg := setupTestServer(stubIntel)
	srv.cfg.HumanAuthToken = "secret_human_approver"
	router := SetupRouter(srv)

	mandate, _ := createTestMandate(t)
	reg.RegisterPrincipal(mandate.PrincipalID, mandate.PublicKey)
	srv.mandates[mandate.MandateID] = mandate

	// Submit payment intent that triggers step-up
	intentReq := models.PaymentIntentRequest{
		Mandate:        mandate,
		AgentID:        mandate.AgentID,
		MerchantID:     "merchant_a",
		Amount:         "30.00",
		Currency:       "USD",
		Category:       "electronics",
		Items:          []models.Item{{Name: "USB-C Hub", Qty: 1, UnitPrice: "30.00"}},
		AgentReasoning: "Restock necessary connector",
		IdempotencyKey: "idemp_su_leak_test",
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

	// 1. Unauthenticated GET /v1/approvals must return 401
	req = httptest.NewRequest(http.MethodGet, "/v1/approvals", nil)
	rec = httptest.NewRecorder()
	router.ServeHTTP(rec, req)
	if rec.Code != http.StatusUnauthorized {
		t.Fatalf("expected 401 Unauthorized, got %d", rec.Code)
	}

	// 2. Authenticated GET /v1/approvals returns redacted token (empty string)
	req = httptest.NewRequest(http.MethodGet, "/v1/approvals", nil)
	req.Header.Set("Authorization", "Bearer secret_human_approver")
	rec = httptest.NewRecorder()
	router.ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", rec.Code)
	}
	var approvals []map[string]interface{}
	_ = json.NewDecoder(rec.Body).Decode(&approvals)
	if len(approvals) == 0 {
		t.Fatalf("expected at least 1 approval")
	}
	for _, app := range approvals {
		if app["step_up_token"] != "" && app["step_up_token"] != nil {
			t.Fatalf("step_up_token must be redacted in list approvals, got: %v", app["step_up_token"])
		}
	}
}

func TestAuditIssue4_DemoRunGateAndSyntheticTagging(t *testing.T) {
	srv, _, _, _ := setupTestServer(nil)
	router := SetupRouter(srv)

	demoReq := models.DemoRunRequest{Scenario: "happy_path"}
	body, _ := json.Marshal(demoReq)

	// 1. In production, demo run is strictly blocked
	srv.cfg.Env = "production"
	req := httptest.NewRequest(http.MethodPost, "/v1/demo/run", bytes.NewReader(body))
	rec := httptest.NewRecorder()
	router.ServeHTTP(rec, req)
	if rec.Code != http.StatusForbidden {
		t.Fatalf("expected 403 Forbidden in production, got %d", rec.Code)
	}

	// 2. In non-production, runs successfully and returns is_synthetic: true
	srv.cfg.Env = "development"
	req = httptest.NewRequest(http.MethodPost, "/v1/demo/run", bytes.NewReader(body))
	rec = httptest.NewRecorder()
	router.ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("expected 200 in development, got %d", rec.Code)
	}
	var res map[string]interface{}
	_ = json.NewDecoder(rec.Body).Decode(&res)
	if res["is_synthetic"] != true {
		t.Fatalf("expected is_synthetic: true in response")
	}
}

func TestAuditIssue6_DryRunCumulativeSpendingEnforcement(t *testing.T) {
	stubIntel := intelclient.NewStubIntelClient()
	srv, _, led, reg := setupTestServer(stubIntel)
	router := SetupRouter(srv)

	mandate, privKey := createTestMandate(t)
	mandate.MandateID = "mdt_dryrun_spending_test"
	mandate.MaxTotal = "50.00"
	mandate.MaxPerTxn = "35.00"
	mandate.StepUpOver = "40.00"
	if err := crypto.SignMandate(privKey, &mandate); err != nil {
		t.Fatalf("failed to sign mandate: %v", err)
	}
	reg.RegisterPrincipal(mandate.PrincipalID, mandate.PublicKey)
	srv.mandates[mandate.MandateID] = mandate

	// First payment of $30.00 in dry-run mode -> allowed
	req1 := models.PaymentIntentRequest{
		Mandate:        mandate,
		AgentID:        mandate.AgentID,
		MerchantID:     "merchant_a",
		Amount:         "30.00",
		Currency:       "USD",
		Category:       "electronics",
		Items:          []models.Item{{Name: "Connector", Qty: 1, UnitPrice: "30.00"}},
		IdempotencyKey: "idemp_dryrun_spend_1",
		DryRun:         true,
	}
	b1, _ := json.Marshal(req1)
	r1 := httptest.NewRequest(http.MethodPost, "/v1/payments/intents", bytes.NewReader(b1))
	rec1 := httptest.NewRecorder()
	router.ServeHTTP(rec1, r1)
	if rec1.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", rec1.Code, rec1.Body.String())
	}
	var dec1 models.DecisionResponse
	_ = json.NewDecoder(rec1.Body).Decode(&dec1)
	if dec1.Decision != models.DecisionAllow {
		t.Fatalf("expected allow for first payment, got %s", dec1.Decision)
	}

	// Second payment of $30.00 under same mandate in dry-run mode -> cumulative 60.00 > 50.00 max_total -> BLOCKED
	req2 := models.PaymentIntentRequest{
		Mandate:        mandate,
		AgentID:        mandate.AgentID,
		MerchantID:     "merchant_a",
		Amount:         "30.00",
		Currency:       "USD",
		Category:       "electronics",
		Items:          []models.Item{{Name: "Connector 2", Qty: 1, UnitPrice: "30.00"}},
		IdempotencyKey: "idemp_dryrun_spend_2",
		DryRun:         true,
	}
	b2, _ := json.Marshal(req2)
	r2 := httptest.NewRequest(http.MethodPost, "/v1/payments/intents", bytes.NewReader(b2))
	rec2 := httptest.NewRecorder()
	router.ServeHTTP(rec2, r2)
	if rec2.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", rec2.Code)
	}
	var dec2 models.DecisionResponse
	_ = json.NewDecoder(rec2.Body).Decode(&dec2)
	if dec2.Decision != models.DecisionBlock {
		t.Fatalf("expected block for exceeding dry-run cumulative max_total, got %s", dec2.Decision)
	}

	// Verify live spending projection on this mandate remains 0.00 (no contamination)
	liveSpent, _ := led.GetTotalSpent(mandate.MandateID)
	if liveSpent != 0.0 {
		t.Fatalf("expected live spent to remain 0.00, got %.2f", liveSpent)
	}
}

func TestAuditIssue7_IdempotencyOwnershipIsolation(t *testing.T) {
	stubIntel := intelclient.NewStubIntelClient()
	srv, _, _, reg := setupTestServer(stubIntel)
	router := SetupRouter(srv)

	mandateA, _ := createTestMandate(t)
	mandateA.MandateID = "mdt_agent_a"
	mandateA.AgentID = "agent_shopper_a"
	reg.RegisterPrincipal(mandateA.PrincipalID, mandateA.PublicKey)
	srv.mandates[mandateA.MandateID] = mandateA

	// Agent A submits with idempotency key "idemp_shared_key"
	reqA := models.PaymentIntentRequest{
		Mandate:        mandateA,
		AgentID:        mandateA.AgentID,
		MerchantID:     "merchant_a",
		Amount:         "10.00",
		Currency:       "USD",
		Category:       "electronics",
		Items:          []models.Item{{Name: "Item A", Qty: 1, UnitPrice: "10.00"}},
		IdempotencyKey: "idemp_shared_key",
		DryRun:         true,
	}
	bA, _ := json.Marshal(reqA)
	rA := httptest.NewRequest(http.MethodPost, "/v1/payments/intents", bytes.NewReader(bA))
	recA := httptest.NewRecorder()
	router.ServeHTTP(recA, rA)
	if recA.Code != http.StatusOK {
		t.Fatalf("expected 200 for agent A, got %d", recA.Code)
	}

	// Agent B submits with same idempotency key "idemp_shared_key" under different mandate
	mandateB, _ := createTestMandate(t)
	mandateB.MandateID = "mdt_agent_b"
	mandateB.AgentID = "agent_shopper_b"
	reg.RegisterPrincipal(mandateB.PrincipalID, mandateB.PublicKey)
	srv.mandates[mandateB.MandateID] = mandateB

	reqB := models.PaymentIntentRequest{
		Mandate:        mandateB,
		AgentID:        mandateB.AgentID,
		MerchantID:     "merchant_a",
		Amount:         "15.00",
		Currency:       "USD",
		Category:       "electronics",
		Items:          []models.Item{{Name: "Item B", Qty: 1, UnitPrice: "15.00"}},
		IdempotencyKey: "idemp_shared_key",
		DryRun:         true,
	}
	bB, _ := json.Marshal(reqB)
	rB := httptest.NewRequest(http.MethodPost, "/v1/payments/intents", bytes.NewReader(bB))
	recB := httptest.NewRecorder()
	router.ServeHTTP(recB, rB)

	// Must NOT be a cache hit for Agent A's decision!
	if recB.Header().Get("X-Cache-Lookup") == "HIT" {
		t.Fatalf("idempotency key must not be shared across different mandates/agents!")
	}
}

func TestAuditIssue1_PrincipalKeyEnrollmentRoute(t *testing.T) {
	srv, _, _, _ := setupTestServer(nil)
	srv.cfg.HumanAuthToken = "secret_human_admin"
	router := SetupRouter(srv)

	pubBytes := make([]byte, 32)
	for i := range pubBytes {
		pubBytes[i] = byte(i + 1)
	}
	validKey := base64.StdEncoding.EncodeToString(pubBytes)

	// 1. Missing auth token -> 401
	payload := map[string]string{"principal_id": "usr_alice", "public_key": validKey}
	b, _ := json.Marshal(payload)
	req := httptest.NewRequest(http.MethodPost, "/v1/principals/keys", bytes.NewReader(b))
	rec := httptest.NewRecorder()
	router.ServeHTTP(rec, req)
	if rec.Code != http.StatusUnauthorized {
		t.Fatalf("expected 401 unauthorized, got %d", rec.Code)
	}

	// 2. Valid enrollment -> 201 Created
	req = httptest.NewRequest(http.MethodPost, "/v1/principals/keys", bytes.NewReader(b))
	req.Header.Set("Authorization", "Bearer secret_human_admin")
	rec = httptest.NewRecorder()
	router.ServeHTTP(rec, req)
	if rec.Code != http.StatusCreated {
		t.Fatalf("expected 201 created, got %d: %s", rec.Code, rec.Body.String())
	}

	// 3. Immutability: Attempt overwrite with different key -> 409 Conflict
	diffBytes := make([]byte, 32)
	diffBytes[0] = 99
	diffKey := base64.StdEncoding.EncodeToString(diffBytes)
	payloadDiff := map[string]string{"principal_id": "usr_alice", "public_key": diffKey}
	bDiff, _ := json.Marshal(payloadDiff)
	req = httptest.NewRequest(http.MethodPost, "/v1/principals/keys", bytes.NewReader(bDiff))
	req.Header.Set("Authorization", "Bearer secret_human_admin")
	rec = httptest.NewRecorder()
	router.ServeHTTP(rec, req)
	if rec.Code != http.StatusConflict {
		t.Fatalf("expected 409 conflict on key overwrite, got %d", rec.Code)
	}
}

func TestAuditIssue2_IdempotencyPayloadBindingAndConflict(t *testing.T) {
	stubIntel := intelclient.NewStubIntelClient()
	srv, _, _, reg := setupTestServer(stubIntel)
	router := SetupRouter(srv)

	mandate, _ := createTestMandate(t)
	reg.RegisterPrincipal(mandate.PrincipalID, mandate.PublicKey)

	intentReq := models.PaymentIntentRequest{
		Mandate:        mandate,
		AgentID:        mandate.AgentID,
		MerchantID:     "merchant_a",
		Amount:         "10.00",
		Currency:       "USD",
		Category:       "electronics",
		Items:          []models.Item{{Name: "Item 1", Qty: 1, UnitPrice: "10.00"}},
		IdempotencyKey: "idemp_test_payload_binding",
		DryRun:         true,
	}

	// First execution -> 200 OK
	b, _ := json.Marshal(intentReq)
	req := httptest.NewRequest(http.MethodPost, "/v1/payments/intents", bytes.NewReader(b))
	rec := httptest.NewRecorder()
	router.ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", rec.Code, rec.Body.String())
	}

	// Second execution with SAME payload -> 200 OK with X-Cache-Lookup: HIT
	req = httptest.NewRequest(http.MethodPost, "/v1/payments/intents", bytes.NewReader(b))
	rec = httptest.NewRecorder()
	router.ServeHTTP(rec, req)
	if rec.Code != http.StatusOK || rec.Header().Get("X-Cache-Lookup") != "HIT" {
		t.Fatalf("expected 200 HIT, got %d (lookup: %s)", rec.Code, rec.Header().Get("X-Cache-Lookup"))
	}

	// Third execution with SAME key but DIFFERENT payload (Amount changed to 15.00) -> 409 Conflict!
	intentReqModified := intentReq
	intentReqModified.Amount = "15.00"
	intentReqModified.Items = []models.Item{{Name: "Item 1", Qty: 1, UnitPrice: "15.00"}}
	bMod, _ := json.Marshal(intentReqModified)
	req = httptest.NewRequest(http.MethodPost, "/v1/payments/intents", bytes.NewReader(bMod))
	rec = httptest.NewRecorder()
	router.ServeHTTP(rec, req)
	if rec.Code != http.StatusConflict {
		t.Fatalf("expected 409 Conflict on payload tampering for same idempotency key, got %d: %s", rec.Code, rec.Body.String())
	}
}

func TestAuditIssue3_DryRunStepUpFailClosedCapExceeded(t *testing.T) {
	stubIntel := intelclient.NewStubIntelClient()
	stubIntel.RiskScoreVal = 0.45 // triggers step_up
	srv, _, led, reg := setupTestServer(stubIntel)
	router := SetupRouter(srv)

	mandate, _ := createTestMandate(t)
	mandate.MaxTotal = "50.00"
	mandate.MaxPerTxn = "50.00"
	reg.RegisterPrincipal(mandate.PrincipalID, mandate.PublicKey)

	// Pre-consume $40 under dryrun budget
	_ = led.ReserveBudget(context.Background(), "dryrun:"+mandate.MandateID, "pay_existing", 40.00, 50.00)
	led.CommitBudgetReservation(context.Background(), "dryrun:"+mandate.MandateID, "pay_existing")

	// Now try to request $20 (> remaining $10) with step-up
	intentReq := models.PaymentIntentRequest{
		Mandate:        mandate,
		AgentID:        mandate.AgentID,
		MerchantID:     "merchant_a",
		Amount:         "20.00",
		Currency:       "USD",
		Category:       "electronics",
		Items:          []models.Item{{Name: "Connector", Qty: 1, UnitPrice: "20.00"}},
		IdempotencyKey: "idemp_stepup_fail_closed",
		DryRun:         true,
	}
	b, _ := json.Marshal(intentReq)
	req := httptest.NewRequest(http.MethodPost, "/v1/payments/intents", bytes.NewReader(b))
	rec := httptest.NewRecorder()
	router.ServeHTTP(rec, req)

	var dec models.DecisionResponse
	_ = json.NewDecoder(rec.Body).Decode(&dec)
	if dec.Decision != models.DecisionBlock {
		t.Fatalf("expected step-up to fail-closed to block when budget cap exceeded, got %s", dec.Decision)
	}
}

func TestAuditIssue5_BenchmarkReportContractValidation(t *testing.T) {
	srv, _, _, _ := setupTestServer(nil)
	router := SetupRouter(srv)

	// GET /v1/benchmark/report should find test vector and validate contract
	req := httptest.NewRequest(http.MethodGet, "/v1/benchmark/report", nil)
	rec := httptest.NewRecorder()
	router.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("expected 200 from valid benchmark report, got %d: %s", rec.Code, rec.Body.String())
	}
	var rep map[string]interface{}
	if err := json.NewDecoder(rec.Body).Decode(&rep); err != nil {
		t.Fatalf("expected valid JSON report: %v", err)
	}
	if rep["run_id"] == nil || rep["provenance"] == nil {
		t.Fatalf("missing required contract fields in report")
	}

	// Test validation function directly on invalid payloads
	if err := validateBenchmarkReport([]byte(`{"run_id": "test"}`)); err == nil {
		t.Fatalf("expected error on missing provenance and classes")
	}
	if err := validateBenchmarkReport([]byte(`{"provenance": "invalid_val", "run_id": "r1"}`)); err == nil {
		t.Fatalf("expected error on invalid provenance")
	}
}

func TestAudit_ApprovalRejectionMutualExclusion(t *testing.T) {
	stubIntel := intelclient.NewStubIntelClient()
	stubIntel.RiskScoreVal = 0.45 // step up
	srv, _, _, reg := setupTestServer(stubIntel)
	router := SetupRouter(srv)

	mandate, _ := createTestMandate(t)
	reg.RegisterPrincipal(mandate.PrincipalID, mandate.PublicKey)

	intentReq := models.PaymentIntentRequest{
		Mandate:        mandate,
		AgentID:        mandate.AgentID,
		MerchantID:     "merchant_a",
		Amount:         "30.00",
		Currency:       "USD",
		Category:       "electronics",
		Items:          []models.Item{{Name: "Item", Qty: 1, UnitPrice: "30.00"}},
		IdempotencyKey: "idemp_mutual_exclusion",
		DryRun:         true,
	}
	b, _ := json.Marshal(intentReq)
	req := httptest.NewRequest(http.MethodPost, "/v1/payments/intents", bytes.NewReader(b))
	rec := httptest.NewRecorder()
	router.ServeHTTP(rec, req)

	var dec models.DecisionResponse
	_ = json.NewDecoder(rec.Body).Decode(&dec)

	// 1. Reject payment
	rejReq := httptest.NewRequest(http.MethodPost, "/v1/payments/"+dec.PaymentID+"/reject", bytes.NewReader([]byte(`{"reason":"declined"}`)))
	rejRec := httptest.NewRecorder()
	router.ServeHTTP(rejRec, rejReq)
	if rejRec.Code != http.StatusOK {
		t.Fatalf("expected 200 from reject, got %d", rejRec.Code)
	}

	// 2. Reject again is idempotent 200 OK
	rejReq2 := httptest.NewRequest(http.MethodPost, "/v1/payments/"+dec.PaymentID+"/reject", bytes.NewReader([]byte(`{"reason":"declined"}`)))
	rejRec2 := httptest.NewRecorder()
	router.ServeHTTP(rejRec2, rejReq2)
	if rejRec2.Code != http.StatusOK {
		t.Fatalf("expected 200 idempotent from second reject, got %d", rejRec2.Code)
	}

	// 3. Attempting to approve a rejected payment MUST fail with 409 Conflict
	appReq := httptest.NewRequest(http.MethodPost, "/v1/payments/"+dec.PaymentID+"/approve", bytes.NewReader([]byte(`{"paypal_order_id":"ORD_1"}`)))
	appRec := httptest.NewRecorder()
	router.ServeHTTP(appRec, appReq)
	if appRec.Code != http.StatusConflict {
		t.Fatalf("expected 409 conflict when approving an already rejected payment, got %d", appRec.Code)
	}
}

