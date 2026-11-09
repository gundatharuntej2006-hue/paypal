package api

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"strconv"
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
	dryRunClient paypal.Client
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
		dryRunClient: paypal.NewDryRunClient(),
		intelClient:  intel,
		broker:       broker,
	}
}

// SetDryRunClient allows tests to inject an instrumented dry-run client.
func (s *Server) SetDryRunClient(c paypal.Client) {
	s.dryRunClient = c
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

	// Fix 1 & 5: Caller Authentication for Agents
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
	} else if s.cfg.Env == "production" {
		http.Error(w, "unauthorized agent caller: agent authentication token required in production", http.StatusUnauthorized)
		return
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

	// Fix 6: Agent identity derivation and verification against authenticated caller
	headerAgentID := strings.TrimSpace(r.Header.Get("X-Agent-ID"))
	if req.AgentID == "" {
		req.AgentID = headerAgentID
	} else if headerAgentID != "" && req.AgentID != headerAgentID {
		http.Error(w, fmt.Sprintf("agent identity mismatch: body agent '%s' != header agent '%s'", req.AgentID, headerAgentID), http.StatusBadRequest)
		return
	}

	isDryRun := req.DryRun || r.Header.Get("X-Dry-Run") == "true" || r.URL.Query().Get("dry_run") == "true"

	// Fix 5: Empty idempotency key rejection
	idempKey := strings.TrimSpace(req.IdempotencyKey)
	if idempKey == "" {
		http.Error(w, "idempotency_key is required and cannot be empty", http.StatusBadRequest)
		return
	}

	// Fix 3 & 4: Dedicated idempotency namespace for dry-run vs live requests
	modePrefix := "live:"
	if isDryRun {
		modePrefix = "dryrun:"
	}
	namespacedKey := modePrefix + idempKey

	// Fix 5: Reliable Idempotency Recovery - return original cached decision if seen
	if cachedDec, err := s.ledger.GetDecisionByIdempotencyKey(ctx, namespacedKey); err == nil && cachedDec != nil {
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

	// 1. Record intent.received with dry_run flag in event data
	intentEventKey := "intent:" + namespacedKey
	intentEvt, err := s.emit(ctx, "intent.received", &paymentID, &req.Mandate.MandateID, &intentEventKey, "gateway", map[string]interface{}{
		"merchant_id":     req.MerchantID,
		"merchant_name":   req.MerchantName,
		"amount":          req.Amount,
		"currency":        req.Currency,
		"items_count":     len(req.Items),
		"agent_reasoning": req.AgentReasoning,
		"contexts_count":  len(req.SourceContexts),
		"dry_run":         isDryRun,
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
			DryRun:       isDryRun,
		}

		// Cache decision for idempotent retry recovery under namespaced key
		_ = s.ledger.SaveDecisionIdempotency(ctx, namespacedKey, &decision)

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

	// Atul Point 3 & Tharun Isolation: Query previous payments under this MANDATE
	// Must exclude current paymentID, and exclude dry-run transactions from live risk history
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

			var d map[string]interface{}
			_ = json.Unmarshal(e.Data, &d)

			// Exclude dry-run payments from live risk history to prevent pollution
			if !isDryRun {
				if dDryRun, ok := d["dry_run"].(bool); ok && dDryRun {
					continue
				}
				if e.Source == "dry_run" {
					continue
				}
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

	// Resolve verified merchant payee binding (Fix 8)
	payeeEmail, payeeMerchantID := s.verifier.GetMerchantPayee(req.MerchantID)

	reqAmtFloat, _ := strconv.ParseFloat(req.Amount, 64)
	maxTotalFloat, _ := strconv.ParseFloat(req.Mandate.MaxTotal, 64)

	// 5. Execute decision (Tharun requirement: separate payment-client interface for DryRun vs Live)
	if isDryRun {
		switch decision.Decision {
		case models.DecisionAllow:
			// Fully isolated dry-run order, authorization and capture via dryRunClient
			orderResp, _ := s.dryRunClient.CreateOrder(ctx, paypal.OrderRequest{
				PaymentID:       paymentID,
				Amount:          req.Amount,
				Currency:        req.Currency,
				Description:     fmt.Sprintf("DryRun AgentGuard Payment: %s", req.Mandate.Task),
				RequestID:       "dryrun_order:" + paymentID,
				PayeeEmail:      payeeEmail,
				PayeeMerchantID: payeeMerchantID,
			})
			authID, _ := s.dryRunClient.AuthorizeOrder(ctx, orderResp.ID, "dryrun_auth:"+paymentID)
			capResp, capErr := s.dryRunClient.CaptureAuthorization(ctx, authID, req.Amount, req.Currency, true, "dryrun_cap:"+paymentID)

			if capErr != nil || capResp == nil || capResp.Status != "COMPLETED" {
				_ = s.stateMachine.Transition(paymentID, statemachine.StateRejected)
				_, _ = s.emit(ctx, "paypal.capture_failed", &paymentID, &req.Mandate.MandateID, nil, "dry_run", map[string]interface{}{
					"error": fmt.Sprintf("capture failed: %v", capErr),
				})
				http.Error(w, "dry-run payment capture failed", http.StatusBadGateway)
				return
			}

			_ = s.stateMachine.Transition(paymentID, statemachine.StateAuthorized)
			_ = s.stateMachine.Transition(paymentID, statemachine.StateCaptured)

			_, _ = s.emit(ctx, "paypal.order_created", &paymentID, &req.Mandate.MandateID, nil, "dry_run", orderResp)
			_, _ = s.emit(ctx, "paypal.authorized", &paymentID, &req.Mandate.MandateID, nil, "dry_run", map[string]interface{}{
				"order_id":         orderResp.ID,
				"authorization_id": authID,
				"dry_run":          true,
			})
			_, _ = s.emit(ctx, "paypal.captured", &paymentID, &req.Mandate.MandateID, nil, "dry_run", map[string]interface{}{
				"capture_id": capResp.ID,
				"dry_run":    true,
				"amount":     req.Amount,
				"currency":   req.Currency,
			})

			decision.PayPal = &models.PayPalDetails{
				OrderID:         orderResp.ID,
				AuthorizationID: authID,
				CaptureID:       capResp.ID,
				ApprovalURL:     orderResp.ApprovalURL,
			}

		case models.DecisionStepUp:
			_ = s.stateMachine.Transition(paymentID, statemachine.StateStepUpPending)
			stepUpToken := "stepup_dryrun_" + uuid.New().String()[:12]
			expiresAt := time.Now().UTC().Add(15 * time.Minute)
			decision.StepUpToken = stepUpToken

			orderResp, _ := s.dryRunClient.CreateOrder(ctx, paypal.OrderRequest{
				PaymentID:       paymentID,
				Amount:          req.Amount,
				Currency:        req.Currency,
				Description:     fmt.Sprintf("DryRun Step-Up Required: %s", req.Mandate.Task),
				RequestID:       "dryrun_stepup_order:" + paymentID,
				PayeeEmail:      payeeEmail,
				PayeeMerchantID: payeeMerchantID,
			})

			decision.PayPal = &models.PayPalDetails{
				OrderID:     orderResp.ID,
				ApprovalURL: orderResp.ApprovalURL,
			}
			_, _ = s.emit(ctx, "paypal.order_created", &paymentID, &req.Mandate.MandateID, nil, "dry_run", orderResp)
			_, _ = s.emit(ctx, "payment.step_up_token_issued", &paymentID, &req.Mandate.MandateID, nil, "dry_run", map[string]interface{}{
				"step_up_token":      stepUpToken,
				"step_up_expires_at": expiresAt.Format(time.RFC3339),
				"dry_run":            true,
			})

		case models.DecisionBlock:
			_ = s.stateMachine.Transition(paymentID, statemachine.StateRejected)
			decision.PayPal = nil
		}
	} else {
		// Live payment execution via live paypalClient
		switch decision.Decision {
		case models.DecisionAllow:
			// Atomic budget reservation before calling PayPal
			if maxTotalFloat > 0 {
				if err := s.ledger.ReserveBudget(ctx, req.Mandate.MandateID, paymentID, reqAmtFloat, maxTotalFloat); err != nil {
					_ = s.stateMachine.Transition(paymentID, statemachine.StateRejected)
					decision.Decision = models.DecisionBlock
					decision.Reasons = append(decision.Reasons, "concurrent_budget_cap_exceeded")
					_ = s.ledger.SaveDecisionIdempotency(ctx, namespacedKey, &decision)
					w.Header().Set("Content-Type", "application/json")
					w.WriteHeader(http.StatusOK)
					json.NewEncoder(w).Encode(decision)
					return
				}
			}

			orderID := req.Mandate.PayPalOrderID
			approvalURL := ""

			// If no pre-approved order attached to mandate, create an order with intent=AUTHORIZE & bound payee
			if orderID == "" {
				orderResp, err := s.paypalClient.CreateOrder(ctx, paypal.OrderRequest{
					PaymentID:       paymentID,
					Amount:          req.Amount,
					Currency:        req.Currency,
					Description:     fmt.Sprintf("AgentGuard Payment: %s", req.Mandate.Task),
					RequestID:       "order:" + paymentID,
					PayeeEmail:      payeeEmail,
					PayeeMerchantID: payeeMerchantID,
				})
				if err != nil {
					s.ledger.ReleaseBudgetReservation(ctx, req.Mandate.MandateID, paymentID)
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
				s.ledger.ReleaseBudgetReservation(ctx, req.Mandate.MandateID, paymentID)
				log.Printf("[gateway] paypal AuthorizeOrder error: %v", err)
				http.Error(w, fmt.Sprintf("paypal authorize error: %v", err), http.StatusBadGateway)
				return
			}
			_ = s.stateMachine.Transition(paymentID, statemachine.StateAuthorized)
			_, _ = s.emit(ctx, "paypal.authorized", &paymentID, &req.Mandate.MandateID, nil, "paypal", map[string]interface{}{
				"order_id":         orderID,
				"authorization_id": authID,
			})

			// Capture authorization for approved amount & verify status
			captureResp, err := s.paypalClient.CaptureAuthorization(ctx, authID, req.Amount, req.Currency, true, "cap:"+paymentID)
			if err != nil || captureResp == nil || captureResp.Status != "COMPLETED" {
				s.ledger.ReleaseBudgetReservation(ctx, req.Mandate.MandateID, paymentID)
				log.Printf("[gateway] paypal CaptureAuthorization error: %v, resp: %+v", err, captureResp)
				_ = s.stateMachine.Transition(paymentID, statemachine.StateRejected)
				_, _ = s.emit(ctx, "paypal.capture_failed", &paymentID, &req.Mandate.MandateID, nil, "paypal", map[string]interface{}{
					"error": fmt.Sprintf("%v", err),
				})
				http.Error(w, fmt.Sprintf("paypal capture failed: %v", err), http.StatusBadGateway)
				return
			}

			s.ledger.CommitBudgetReservation(ctx, req.Mandate.MandateID, paymentID)
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
				PaymentID:       paymentID,
				Amount:          req.Amount,
				Currency:        req.Currency,
				Description:     fmt.Sprintf("Human Step-Up Approval Required: %s", req.Mandate.Task),
				RequestID:       "stepup_order:" + paymentID,
				PayeeEmail:      payeeEmail,
				PayeeMerchantID: payeeMerchantID,
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

	// Fix 5: Cache the final decision under the namespaced idempotency key for safe replay
	_ = s.ledger.SaveDecisionIdempotency(ctx, namespacedKey, &decision)

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(decision)
}

// Fix 4 & 7: Hardened Human Step-Up Approval with dry-run isolation & atomic single-use token consumption
func (s *Server) handleApprovePayment(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	paymentID := r.PathValue("id")
	if paymentID == "" {
		http.Error(w, "missing payment id", http.StatusBadRequest)
		return
	}

	// Caller Authentication for Human approvals
	if s.cfg.HumanAuthToken != "" {
		authHeader := r.Header.Get("Authorization")
		if authHeader != "Bearer "+s.cfg.HumanAuthToken {
			http.Error(w, "unauthorized human approver: invalid token", http.StatusUnauthorized)
			return
		}
	} else if s.cfg.Env == "production" {
		http.Error(w, "unauthorized human approver: token required in production", http.StatusUnauthorized)
		return
	}

	var approvalReq models.StepUpApprovalRequest
	if r.Body != nil {
		_ = json.NewDecoder(r.Body).Decode(&approvalReq)
	}

	sentToken := approvalReq.StepUpToken
	if sentToken == "" {
		sentToken = r.Header.Get("X-Step-Up-Token")
	}

	// Point 2: ATOMIC token consumption before executing approval (prevents TOCTOU concurrency race)
	if err := s.ledger.ConsumeStepUpToken(ctx, paymentID, sentToken); err != nil {
		if errors.Is(err, ledger.ErrTokenAlreadyConsumed) {
			http.Error(w, "step_up_token has already been consumed", http.StatusConflict)
			return
		}
		if errors.Is(err, ledger.ErrTokenExpired) {
			http.Error(w, "step_up_token has expired (15-minute validity window)", http.StatusForbidden)
			return
		}
		if errors.Is(err, ledger.ErrTokenMismatch) {
			http.Error(w, "invalid or missing step_up_token for human approval", http.StatusForbidden)
			return
		}
		http.Error(w, fmt.Sprintf("token verification error: %v", err), http.StatusBadRequest)
		return
	}

	state, err := s.stateMachine.Get(paymentID)
	if err != nil || state != statemachine.StateStepUpPending {
		http.Error(w, fmt.Sprintf("payment cannot be approved in state '%s' (must be 'step_up_pending')", state), http.StatusConflict)
		return
	}

	proj, err := s.ledger.GetPaymentProjection(ctx, paymentID)
	if err != nil {
		http.Error(w, "payment projection not found", http.StatusNotFound)
		return
	}

	// Point 1 & 4: Dry-Run Isolation on step-up approval route
	if proj.IsDryRun {
		authID := proj.PayPalAuthID
		if authID == "" && proj.PayPalOrderID != "" {
			authID, _ = s.dryRunClient.AuthorizeOrder(ctx, proj.PayPalOrderID, "dryrun_auth_stepup:"+paymentID)
		}
		if authID == "" {
			authID = "DRYRUN_AUTH_STEPUP_" + paymentID
		}
		_ = s.stateMachine.Transition(paymentID, statemachine.StateAuthorized)

		capResp, capErr := s.dryRunClient.CaptureAuthorization(ctx, authID, proj.Amount, proj.Currency, true, "dryrun_cap_stepup:"+paymentID)
		if capErr != nil || capResp == nil || capResp.Status != "COMPLETED" {
			_ = s.stateMachine.Transition(paymentID, statemachine.StateRejected)
			_, _ = s.emit(ctx, "paypal.capture_failed", &paymentID, &proj.MandateID, nil, "dry_run", map[string]interface{}{
				"error": fmt.Sprintf("dry-run capture failed: %v", capErr),
			})
			http.Error(w, "dry-run payment capture failed", http.StatusBadGateway)
			return
		}

		_ = s.stateMachine.Transition(paymentID, statemachine.StateCaptured)

		// Record dry-run approval
		_, _ = s.emit(ctx, "payment.step_up_token_consumed", &paymentID, &proj.MandateID, nil, "dry_run", map[string]interface{}{
			"payment_id": paymentID,
		})
		_, _ = s.emit(ctx, "human.approved", &paymentID, &proj.MandateID, nil, "dry_run", map[string]interface{}{
			"approved_by": "human_principal",
			"payment_id":  paymentID,
			"dry_run":     true,
		})
		_, _ = s.emit(ctx, "paypal.captured", &paymentID, &proj.MandateID, nil, "dry_run", map[string]interface{}{
			"capture_id": capResp.ID,
			"amount":     proj.Amount,
			"currency":   proj.Currency,
			"dry_run":    true,
		})

		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]interface{}{
			"payment_id": paymentID,
			"status":     "approved",
			"decision":   "allow",
			"capture_id": capResp.ID,
			"dry_run":    true,
		})
		return
	}

	// Live Route: Authorize order if not yet authorized
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

	// Fix 4: CRITICAL - Only report approved if CaptureAuthorization actually succeeds with status COMPLETED!
	capResp, capErr := s.paypalClient.CaptureAuthorization(ctx, authID, proj.Amount, proj.Currency, true, "cap_stepup:"+paymentID)
	if capErr != nil || capResp == nil || capResp.Status != "COMPLETED" {
		log.Printf("[gateway] step-up capture failed: %v, resp: %+v", capErr, capResp)
		_ = s.stateMachine.Transition(paymentID, statemachine.StateRejected)
		_, _ = s.emit(ctx, "paypal.capture_failed", &paymentID, &proj.MandateID, nil, "paypal", map[string]interface{}{
			"error": fmt.Sprintf("%v", capErr),
		})
		http.Error(w, fmt.Sprintf("paypal capture failed: %v", capErr), http.StatusBadGateway)
		return
	}

	// All succeeded
	_ = s.stateMachine.Transition(paymentID, statemachine.StateCaptured)
	_, _ = s.emit(ctx, "payment.step_up_token_consumed", &paymentID, &proj.MandateID, nil, "human", map[string]interface{}{
		"payment_id": paymentID,
	})
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

	// Caller Authentication for Human rejection
	if s.cfg.HumanAuthToken != "" {
		authHeader := r.Header.Get("Authorization")
		if authHeader != "Bearer "+s.cfg.HumanAuthToken {
			http.Error(w, "unauthorized human approver: invalid token", http.StatusUnauthorized)
			return
		}
	} else if s.cfg.Env == "production" {
		http.Error(w, "unauthorized human approver: token required in production", http.StatusUnauthorized)
		return
	}

	proj, err := s.ledger.GetPaymentProjection(ctx, paymentID)
	if err != nil {
		http.Error(w, "payment projection not found", http.StatusNotFound)
		return
	}

	_ = s.stateMachine.Transition(paymentID, statemachine.StateRejected)

	if proj.IsDryRun {
		// Dry run rejection
		_, _ = s.emit(ctx, "payment.step_up_token_consumed", &paymentID, &proj.MandateID, nil, "dry_run", map[string]interface{}{
			"payment_id": paymentID,
		})
		_, _ = s.emit(ctx, "human.rejected", &paymentID, &proj.MandateID, nil, "dry_run", map[string]interface{}{
			"rejected_by": "human_principal",
			"payment_id":  paymentID,
			"dry_run":     true,
		})

		if proj.PayPalAuthID != "" {
			_, _ = s.dryRunClient.VoidAuthorization(ctx, proj.PayPalAuthID, "dryrun_void_stepup:"+paymentID)
			_ = s.stateMachine.Transition(paymentID, statemachine.StateVoided)
			_, _ = s.emit(ctx, "paypal.voided", &paymentID, &proj.MandateID, nil, "dry_run", map[string]interface{}{
				"authorization_id": proj.PayPalAuthID,
				"dry_run":          true,
			})
		}

		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]interface{}{
			"payment_id": paymentID,
			"status":     "rejected",
			"decision":   "block",
			"dry_run":    true,
		})
		return
	}

	// Live Route
	_, _ = s.emit(ctx, "payment.step_up_token_consumed", &paymentID, &proj.MandateID, nil, "human", map[string]interface{}{
		"payment_id": paymentID,
	})
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
