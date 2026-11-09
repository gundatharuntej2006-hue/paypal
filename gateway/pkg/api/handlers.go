package api

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"strings"
	"time"

	"github.com/google/uuid"

	"gateway/pkg/config"
	"gateway/pkg/events"
	"gateway/pkg/intelclient"
	"gateway/pkg/ledger"
	"gateway/pkg/mandate"
	"gateway/pkg/models"
	"gateway/pkg/paypal"
	"gateway/pkg/policy"
	"gateway/pkg/statemachine"
)

type Server struct {
	cfg          *config.Config
	ledger       ledger.Ledger
	verifier     *mandate.Verifier
	stateMachine *statemachine.StateMachine
	decisionEng  *policy.DecisionEngine
	paypalClient paypal.Client
	intelClient  intelclient.IntelClient
	broker       *events.Broker
}

func NewServer(
	cfg *config.Config,
	led ledger.Ledger,
	ver *mandate.Verifier,
	sm *statemachine.StateMachine,
	de *policy.DecisionEngine,
	pp paypal.Client,
	intel intelclient.IntelClient,
	broker *events.Broker,
) *Server {
	return &Server{
		cfg:          cfg,
		ledger:       led,
		verifier:     ver,
		stateMachine: sm,
		decisionEng:  de,
		paypalClient: pp,
		intelClient:  intel,
		broker:       broker,
	}
}

// emit records an event in the ledger and broadcasts it to the SSE broker.
func (s *Server) emit(ctx context.Context, evtType string, paymentID *string, mandateID *string, idempKey *string, source string, data interface{}) (*models.LedgerEvent, error) {
	dataBytes, err := json.Marshal(data)
	if err != nil {
		return nil, err
	}

	event := &models.LedgerEvent{
		ID:             "evt_" + uuid.New().String()[:8],
		Type:           evtType,
		PaymentID:      paymentID,
		MandateID:      mandateID,
		IdempotencyKey: idempKey,
		Source:         source,
		Data:           dataBytes,
		CreatedAt:      time.Now().UTC(),
	}

	if err := s.ledger.AppendEvent(ctx, event); err != nil {
		return nil, err
	}

	s.broker.Publish(*event)
	return event, nil
}

func (s *Server) handleHealth(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(map[string]interface{}{
		"status":    "healthy",
		"service":   "agentguard-gateway",
		"version":   "0.1.0",
		"env":       s.cfg.Env,
		"mock_mode": s.cfg.PayPalMockMode,
	})
}

func (s *Server) handlePaymentIntent(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()

	// Fix 1: Caller Authentication for Agents (if AGENT_AUTH_TOKEN is configured)
	if s.cfg.AgentAuthToken != "" {
		authHeader := r.Header.Get("Authorization")
		apiKeyHeader := r.Header.Get("X-Agent-Key")
		token := ""
		if strings.HasPrefix(authHeader, "Bearer ") {
			token = strings.TrimPrefix(authHeader, "Bearer ")
		} else if apiKeyHeader != "" {
			token = apiKeyHeader
		}

		if token != s.cfg.AgentAuthToken {
			http.Error(w, "unauthorized agent caller: invalid or missing authentication token", http.StatusUnauthorized)
			return
		}
	}

	// Read body with 2MB limit
	r.Body = http.MaxBytesReader(w, r.Body, 2*1024*1024)
	body, err := io.ReadAll(r.Body)
	if err != nil {
		http.Error(w, "request payload exceeds maximum size limit (2MB)", http.StatusRequestEntityTooLarge)
		return
	}

	var req models.PaymentIntentRequest
	if err := json.Unmarshal(body, &req); err != nil {
		http.Error(w, fmt.Sprintf("invalid json payload: %v", err), http.StatusBadRequest)
		return
	}

	if req.AgentID == "" {
		req.AgentID = r.Header.Get("X-Agent-ID")
	}
	isDryRun := req.DryRun || r.Header.Get("X-Dry-Run") == "true" || r.URL.Query().Get("dry_run") == "true"

	// Fix 5: Empty idempotency key rejection
	idempKey := strings.TrimSpace(req.IdempotencyKey)
	if idempKey == "" {
		http.Error(w, "idempotency_key is required and cannot be empty", http.StatusBadRequest)
		return
	}

	// Fix 5: Reliable Idempotency Recovery - return original cached decision if seen
	if cachedDec, err := s.ledger.GetDecisionByIdempotencyKey(ctx, idempKey); err == nil && cachedDec != nil {
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("X-Cache-Lookup", "HIT")
		w.WriteHeader(http.StatusOK)
		json.NewEncoder(w).Encode(cachedDec)
		return
	}

	// Fix 8: Strict Validation & Source Context Limits
	if len(req.SourceContexts) > 50 {
		http.Error(w, "source_contexts limit exceeded (maximum 50 contexts allowed)", http.StatusBadRequest)
		return
	}
	for i, sc := range req.SourceContexts {
		if len(sc.Text) > 200_000 {
			http.Error(w, fmt.Sprintf("source_context[%d] text exceeds maximum length of 200,000 characters", i), http.StatusBadRequest)
			return
		}
		if len(sc.HTML) > 1_000_000 {
			http.Error(w, fmt.Sprintf("source_context[%d] html exceeds maximum length of 1,000,000 characters", i), http.StatusBadRequest)
			return
		}
	}

	paymentID := "pay_" + uuid.New().String()[:8]
	if err := s.stateMachine.Init(paymentID); err != nil {
		http.Error(w, "failed to initialize payment state", http.StatusInternalServerError)
		return
	}

	// 1. Record intent.received
	intentEventKey := "intent:" + idempKey
	intentEvt, err := s.emit(ctx, "intent.received", &paymentID, &req.Mandate.MandateID, &intentEventKey, "gateway", map[string]interface{}{
		"merchant_id":     req.MerchantID,
		"merchant_name":   req.MerchantName,
		"amount":          req.Amount,
		"currency":        req.Currency,
		"items_count":     len(req.Items),
		"agent_reasoning": req.AgentReasoning,
		"contexts_count":  len(req.SourceContexts),
	})
	if err != nil {
		if errors.Is(err, ledger.ErrDuplicateEvent) {
			http.Error(w, "duplicate intent request in-flight", http.StatusConflict)
			return
		}
		http.Error(w, fmt.Sprintf("ledger error: %v", err), http.StatusInternalServerError)
		return
	}

	// 2. Deterministic Mandate Verification (includes Fix 2: principal key registry, Fix 8: formats, Fix 9: item reconciliation)
	now := time.Now().UTC()
	vRes, vErr := s.verifier.VerifyMandate(&req, now)
	if vErr != nil || !vRes.Valid {
		_ = s.stateMachine.Transition(paymentID, statemachine.StateRejected)
		failedReason := "mandate_invalid"
		if vRes != nil && vRes.FailedReason != "" {
			failedReason = vRes.FailedReason
		}

		failEvt, _ := s.emit(ctx, "mandate.failed", &paymentID, &req.Mandate.MandateID, nil, "gateway", map[string]interface{}{
			"reason": failedReason,
		})

		auditID := intentEvt.ID
		if failEvt != nil {
			auditID = failEvt.ID
		}

		decision := models.DecisionResponse{
			PaymentID: paymentID,
			Decision:  models.DecisionBlock,
			RiskScore: 1.0,
			Reasons:   []string{failedReason},
			Checks: models.ChecksResult{
				Mandate:   "fail",
				Injection: "unknown",
				Intent:    "unknown",
				Risk:      "unknown",
			},
			AuditEventID: auditID,
		}

		// Cache decision for idempotent retry recovery
		_ = s.ledger.SaveDecisionIdempotency(ctx, idempKey, &decision)

		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		json.NewEncoder(w).Encode(decision)
		return
	}

	_ = s.stateMachine.Transition(paymentID, statemachine.StateVerified)
	_, _ = s.emit(ctx, "mandate.verified", &paymentID, &req.Mandate.MandateID, nil, "gateway", map[string]interface{}{
		"mandate_id": req.Mandate.MandateID,
		"reasons":    vRes.Reasons,
	})

	// 3. Scanning state & Intelligence calls
	_ = s.stateMachine.Transition(paymentID, statemachine.StateScanning)

	// Atul Point 3: Query previous payments under this MANDATE (excluding current paymentID)
	// Must include merchant_id, amount, currency, ts, and decision (allow, step_up, block, or pending)
	riskHistory := []models.RiskHistoryItem{}
	mandateEvents, err := s.ledger.GetEventsByMandate(ctx, req.Mandate.MandateID)
	if err == nil {
		type paymentSummary struct {
			merchantID string
			amount     string
			currency   string
			ts         time.Time
			decision   string
		}
		summaryByPID := make(map[string]*paymentSummary)

		for _, e := range mandateEvents {
			if e.PaymentID == nil || *e.PaymentID == paymentID {
				continue // skip current payment
			}
			pid := *e.PaymentID
			summary, exists := summaryByPID[pid]
			if !exists {
				summary = &paymentSummary{
					ts:       e.CreatedAt,
					decision: "pending",
				}
				summaryByPID[pid] = summary
			}

			var d map[string]interface{}
			_ = json.Unmarshal(e.Data, &d)

			switch e.Type {
			case "intent.received":
				if mID, ok := d["merchant_id"].(string); ok {
					summary.merchantID = mID
				}
				if amt, ok := d["amount"].(string); ok {
					summary.amount = amt
				}
				if cur, ok := d["currency"].(string); ok {
					summary.currency = cur
				}
			case "decision.made":
				if dec, ok := d["decision"].(string); ok {
					summary.decision = dec
				}
			case "mandate.failed", "human.rejected":
				summary.decision = "block"
			case "paypal.captured", "human.approved":
				summary.decision = "allow"
			}
		}

		for pid, sum := range summaryByPID {
			if sum.amount != "" {
				pIDCopy := pid
				riskHistory = append(riskHistory, models.RiskHistoryItem{
					PaymentID:  &pIDCopy,
					MerchantID: sum.merchantID,
					Amount:     sum.amount,
					Currency:   sum.currency,
					TS:         sum.ts,
					Decision:   sum.decision,
				})
			}
		}
	}

	// Call intelligence service in parallel
	scanResp, judgeResp, riskResp := s.intelClient.CallAllParallel(ctx, &req, riskHistory)

	// Emit intelligence events
	if scanResp != nil {
		_, _ = s.emit(ctx, "scan.completed", &paymentID, &req.Mandate.MandateID, nil, "intel", scanResp)
	}
	if judgeResp != nil {
		_, _ = s.emit(ctx, "judge.completed", &paymentID, &req.Mandate.MandateID, nil, "intel", judgeResp)
	}
	if riskResp != nil {
		_, _ = s.emit(ctx, "risk.scored", &paymentID, &req.Mandate.MandateID, nil, "intel", riskResp)
	}

	// 4. Decision Engine (Fix 7: Strict fail-closed verification)
	_ = s.stateMachine.Transition(paymentID, statemachine.StateDecided)
	sig := policy.Signals{
		MandateValid:    true,
		Scan:            scanResp,
		Judge:           judgeResp,
		Risk:            riskResp,
		RequestedAmount: req.Amount,
		StepUpOver:      req.Mandate.StepUpOver,
	}

	decision := s.decisionEng.Evaluate(paymentID, sig)
	decision.DryRun = isDryRun

	decEvt, _ := s.emit(ctx, "decision.made", &paymentID, &req.Mandate.MandateID, nil, "gateway", map[string]interface{}{
		"decision":   decision.Decision,
		"risk_score": decision.RiskScore,
		"reasons":    decision.Reasons,
		"checks":     decision.Checks,
		"dry_run":    isDryRun,
	})
	if decEvt != nil {
		decision.AuditEventID = decEvt.ID
	}

	// 5. Execute decision with PayPal (Fix 3: Payer consent verification / order linkage, with DryRun support)
	if isDryRun {
		switch decision.Decision {
		case models.DecisionAllow:
			_ = s.stateMachine.Transition(paymentID, statemachine.StateAuthorized)
			_ = s.stateMachine.Transition(paymentID, statemachine.StateCaptured)
			_, _ = s.emit(ctx, "paypal.captured", &paymentID, &req.Mandate.MandateID, nil, "dry_run", map[string]interface{}{
				"dry_run":  true,
				"amount":   req.Amount,
				"currency": req.Currency,
			})
			decision.PayPal = &models.PayPalDetails{
				OrderID:         "DRY_RUN_ORDER_" + paymentID,
				AuthorizationID: "DRY_RUN_AUTH_" + paymentID,
				CaptureID:       "DRY_RUN_CAP_" + paymentID,
				ApprovalURL:     "https://sandbox.agentguard.local/dryrun-approve",
			}

		case models.DecisionStepUp:
			_ = s.stateMachine.Transition(paymentID, statemachine.StateStepUpPending)
			stepUpToken := "stepup_dryrun_" + uuid.New().String()[:12]
			expiresAt := time.Now().UTC().Add(15 * time.Minute)
			decision.StepUpToken = stepUpToken
			decision.PayPal = &models.PayPalDetails{
				OrderID:     "DRY_RUN_ORDER_" + paymentID,
				ApprovalURL: "https://sandbox.agentguard.local/dryrun-stepup?token=" + stepUpToken,
			}
			_, _ = s.emit(ctx, "payment.step_up_token_issued", &paymentID, &req.Mandate.MandateID, nil, "dry_run", map[string]interface{}{
				"step_up_token":      stepUpToken,
				"step_up_expires_at": expiresAt.Format(time.RFC3339),
			})

		case models.DecisionBlock:
			_ = s.stateMachine.Transition(paymentID, statemachine.StateRejected)
			decision.PayPal = nil
		}
	} else {
		switch decision.Decision {
		case models.DecisionAllow:
			orderID := req.Mandate.PayPalOrderID
			approvalURL := ""

			// If no pre-approved order attached to mandate, create an order with intent=AUTHORIZE
			if orderID == "" {
				orderResp, err := s.paypalClient.CreateOrder(ctx, paypal.OrderRequest{
					PaymentID:   paymentID,
					Amount:      req.Amount,
					Currency:    req.Currency,
					Description: fmt.Sprintf("AgentGuard Payment: %s", req.Mandate.Task),
					RequestID:   "order:" + paymentID,
				})
				if err != nil {
					log.Printf("[gateway] paypal CreateOrder error: %v", err)
					http.Error(w, fmt.Sprintf("paypal error: %v", err), http.StatusBadGateway)
					return
				}
				orderID = orderResp.ID
				approvalURL = orderResp.ApprovalURL
				_, _ = s.emit(ctx, "paypal.order_created", &paymentID, &req.Mandate.MandateID, nil, "paypal", orderResp)
			}

			// Authorize order
			authID, err := s.paypalClient.AuthorizeOrder(ctx, orderID, "auth:"+paymentID)
			if err != nil {
				log.Printf("[gateway] paypal AuthorizeOrder error: %v", err)
				http.Error(w, fmt.Sprintf("paypal authorize error: %v", err), http.StatusBadGateway)
				return
			}
			_ = s.stateMachine.Transition(paymentID, statemachine.StateAuthorized)
			_, _ = s.emit(ctx, "paypal.authorized", &paymentID, &req.Mandate.MandateID, nil, "paypal", map[string]interface{}{
				"order_id":         orderID,
				"authorization_id": authID,
			})

			// Capture authorization for approved amount
			captureResp, err := s.paypalClient.CaptureAuthorization(ctx, authID, req.Amount, req.Currency, true, "cap:"+paymentID)
			if err != nil {
				log.Printf("[gateway] paypal CaptureAuthorization error: %v", err)
				http.Error(w, fmt.Sprintf("paypal capture error: %v", err), http.StatusBadGateway)
				return
			}
			_ = s.stateMachine.Transition(paymentID, statemachine.StateCaptured)
			_, _ = s.emit(ctx, "paypal.captured", &paymentID, &req.Mandate.MandateID, nil, "paypal", captureResp)

			decision.PayPal = &models.PayPalDetails{
				OrderID:         orderID,
				AuthorizationID: authID,
				CaptureID:       captureResp.ID,
				ApprovalURL:     approvalURL,
			}

		case models.DecisionStepUp:
			_ = s.stateMachine.Transition(paymentID, statemachine.StateStepUpPending)
			stepUpToken := "stepup_" + uuid.New().String()[:12]
			expiresAt := time.Now().UTC().Add(15 * time.Minute)
			decision.StepUpToken = stepUpToken

			orderResp, err := s.paypalClient.CreateOrder(ctx, paypal.OrderRequest{
				PaymentID:   paymentID,
				Amount:      req.Amount,
				Currency:    req.Currency,
				Description: fmt.Sprintf("Human Step-Up Approval Required: %s", req.Mandate.Task),
				RequestID:   "stepup_order:" + paymentID,
			})
			approvalURL := ""
			orderID := ""
			if err == nil && orderResp != nil {
				approvalURL = orderResp.ApprovalURL
				orderID = orderResp.ID
				_, _ = s.emit(ctx, "paypal.order_created", &paymentID, &req.Mandate.MandateID, nil, "paypal", orderResp)
			}

			decision.PayPal = &models.PayPalDetails{
				OrderID:     orderID,
				ApprovalURL: approvalURL,
			}

			// Update projection with step_up_token and expiration
			_, _ = s.emit(ctx, "payment.step_up_token_issued", &paymentID, &req.Mandate.MandateID, nil, "gateway", map[string]interface{}{
				"step_up_token":      stepUpToken,
				"step_up_expires_at": expiresAt.Format(time.RFC3339),
			})

		case models.DecisionBlock:
			_ = s.stateMachine.Transition(paymentID, statemachine.StateRejected)
			decision.PayPal = nil
		}
	}

	// Fix 5: Cache the final decision under the idempotency key for safe replay
	_ = s.ledger.SaveDecisionIdempotency(ctx, idempKey, &decision)

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(decision)
}

// Fix 4: Hardened Human Step-Up Approval
func (s *Server) handleApprovePayment(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	paymentID := r.PathValue("id")
	if paymentID == "" {
		http.Error(w, "missing payment id", http.StatusBadRequest)
		return
	}

	// Caller Authentication for Human approvals (if HUMAN_AUTH_TOKEN configured)
	if s.cfg.HumanAuthToken != "" {
		authHeader := r.Header.Get("Authorization")
		if authHeader != "Bearer "+s.cfg.HumanAuthToken {
			http.Error(w, "unauthorized human approver", http.StatusUnauthorized)
			return
		}
	}

	var approvalReq models.StepUpApprovalRequest
	if r.Body != nil {
		_ = json.NewDecoder(r.Body).Decode(&approvalReq)
	}

	state, err := s.stateMachine.Get(paymentID)
	if err != nil || state != statemachine.StateStepUpPending {
		http.Error(w, fmt.Sprintf("payment cannot be approved in state '%s' (must be 'step_up_pending')", state), http.StatusBadRequest)
		return
	}

	proj, err := s.ledger.GetPaymentProjection(ctx, paymentID)
	if err != nil {
		http.Error(w, "payment projection not found", http.StatusNotFound)
		return
	}

	// Fix 4: Validate step_up_token
	if proj.StepUpToken != "" {
		sentToken := approvalReq.StepUpToken
		if sentToken == "" {
			sentToken = r.Header.Get("X-Step-Up-Token")
		}
		if sentToken != proj.StepUpToken {
			http.Error(w, "invalid or missing step_up_token for human approval", http.StatusForbidden)
			return
		}
		if proj.StepUpExpiresAt != nil && time.Now().After(*proj.StepUpExpiresAt) {
			http.Error(w, "step_up_token has expired (15-minute validity window)", http.StatusForbidden)
			return
		}
	}

	// Authorize order if not yet authorized
	authID := proj.PayPalAuthID
	if authID == "" && proj.PayPalOrderID != "" {
		authorizedID, authErr := s.paypalClient.AuthorizeOrder(ctx, proj.PayPalOrderID, "auth_stepup:"+paymentID)
		if authErr != nil {
			log.Printf("[gateway] step-up authorize failed: %v", authErr)
			http.Error(w, fmt.Sprintf("paypal authorization failed: %v", authErr), http.StatusBadGateway)
			return
		}
		authID = authorizedID
	}
	if authID == "" {
		authID = "AUTH_STEPUP_" + paymentID
	}
	_ = s.stateMachine.Transition(paymentID, statemachine.StateAuthorized)

	// Fix 4: CRITICAL - Only report approved if CaptureAuthorization actually succeeds!
	capResp, capErr := s.paypalClient.CaptureAuthorization(ctx, authID, proj.Amount, proj.Currency, true, "cap_stepup:"+paymentID)
	if capErr != nil || capResp == nil {
		log.Printf("[gateway] step-up capture failed: %v", capErr)
		_ = s.stateMachine.Transition(paymentID, statemachine.StateRejected)
		_, _ = s.emit(ctx, "paypal.capture_failed", &paymentID, &proj.MandateID, nil, "paypal", map[string]interface{}{
			"error": fmt.Sprintf("%v", capErr),
		})
		http.Error(w, fmt.Sprintf("paypal capture failed: %v", capErr), http.StatusBadGateway)
		return
	}

	// All succeeded
	_ = s.stateMachine.Transition(paymentID, statemachine.StateCaptured)
	_, _ = s.emit(ctx, "human.approved", &paymentID, &proj.MandateID, nil, "human", map[string]interface{}{
		"approved_by": "human_principal",
		"payment_id":  paymentID,
	})
	_, _ = s.emit(ctx, "paypal.captured", &paymentID, &proj.MandateID, nil, "paypal", capResp)

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"payment_id": paymentID,
		"status":     "approved",
		"decision":   "allow",
		"capture_id": capResp.ID,
	})
}

func (s *Server) handleRejectPayment(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	paymentID := r.PathValue("id")
	if paymentID == "" {
		http.Error(w, "missing payment id", http.StatusBadRequest)
		return
	}

	proj, err := s.ledger.GetPaymentProjection(ctx, paymentID)
	if err != nil {
		http.Error(w, "payment projection not found", http.StatusNotFound)
		return
	}

	_ = s.stateMachine.Transition(paymentID, statemachine.StateRejected)
	_, _ = s.emit(ctx, "human.rejected", &paymentID, &proj.MandateID, nil, "human", map[string]interface{}{
		"rejected_by": "human_principal",
		"payment_id":  paymentID,
	})

	if proj.PayPalAuthID != "" {
		_, _ = s.paypalClient.VoidAuthorization(ctx, proj.PayPalAuthID, "void_stepup:"+paymentID)
		_ = s.stateMachine.Transition(paymentID, statemachine.StateVoided)
		_, _ = s.emit(ctx, "paypal.voided", &paymentID, &proj.MandateID, nil, "paypal", map[string]interface{}{
			"authorization_id": proj.PayPalAuthID,
		})
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"payment_id": paymentID,
		"status":     "rejected",
		"decision":   "block",
	})
}

// handleInternalLedgerHook handles POST /internal/ledger/events (Atul's webhook receiver hook).
func (s *Server) handleInternalLedgerHook(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()

	// Verify token if configured
	if s.cfg.InternalLedgerToken != "" {
		sentToken := r.Header.Get("X-Internal-Token")
		if sentToken != s.cfg.InternalLedgerToken {
			http.Error(w, "unauthorized internal token", http.StatusUnauthorized)
			return
		}
	}

	var req models.HookEventRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "invalid json payload", http.StatusBadRequest)
		return
	}

	dataBytes, err := json.Marshal(req.Data)
	if err != nil {
		http.Error(w, "failed to marshal event data", http.StatusBadRequest)
		return
	}

	source := req.Source
	if source == "" {
		source = "intel"
	}

	event := &models.LedgerEvent{
		ID:             "evt_" + uuid.New().String()[:8],
		Type:           req.Type,
		PaymentID:      req.PaymentID,
		IdempotencyKey: req.IdempotencyKey,
		Source:         source,
		Data:           dataBytes,
		CreatedAt:      time.Now().UTC(),
	}

	err = s.ledger.AppendEvent(ctx, event)
	if err != nil {
		if errors.Is(err, ledger.ErrDuplicateEvent) {
			http.Error(w, "duplicate idempotency_key", http.StatusConflict)
			return
		}
		http.Error(w, fmt.Sprintf("ledger error: %v", err), http.StatusInternalServerError)
		return
	}

	s.broker.Publish(*event)
	w.WriteHeader(http.StatusCreated)
	json.NewEncoder(w).Encode(map[string]interface{}{
		"status":   "recorded",
		"event_id": event.ID,
	})
}

// handleSSEEvents handles GET /v1/events (Server-Sent Events).
func (s *Server) handleSSEEvents(w http.ResponseWriter, r *http.Request) {
	flusher, ok := w.(http.Flusher)
	if !ok {
		http.Error(w, "streaming unsupported", http.StatusInternalServerError)
		return
	}

	paymentIDFilter := r.URL.Query().Get("payment_id")

	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")
	w.Header().Set("Access-Control-Allow-Origin", "*")

	eventCh, unsubscribe := s.broker.Subscribe(paymentIDFilter)
	defer unsubscribe()

	fmt.Fprintf(w, ": connection established\n\n")
	flusher.Flush()

	ctx := r.Context()
	for {
		select {
		case <-ctx.Done():
			return
		case evt, ok := <-eventCh:
			if !ok {
				return
			}
			data, err := json.Marshal(evt)
			if err != nil {
				continue
			}
			fmt.Fprintf(w, "event: %s\nid: %s\ndata: %s\n\n", evt.Type, evt.ID, string(data))
			flusher.Flush()
		}
	}
}

func (s *Server) handleListEvents(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	eventsList, err := s.ledger.ListEvents(ctx, 100)
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(eventsList)
}

func (s *Server) handleGetPayment(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	paymentID := r.PathValue("id")
	if paymentID == "" {
		http.Error(w, "missing payment id", http.StatusBadRequest)
		return
	}

	proj, err := s.ledger.GetPaymentProjection(ctx, paymentID)
	if err != nil {
		http.Error(w, "payment not found", http.StatusNotFound)
		return
	}

	eventsList, _ := s.ledger.GetEventsByPayment(ctx, paymentID)

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"payment":  proj,
		"timeline": eventsList,
	})
}
