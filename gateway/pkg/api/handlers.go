package api

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
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
	var req models.PaymentIntentRequest

	body, err := io.ReadAll(r.Body)
	if err != nil {
		http.Error(w, "failed to read body", http.StatusBadRequest)
		return
	}
	if err := json.Unmarshal(body, &req); err != nil {
		http.Error(w, fmt.Sprintf("invalid json payload: %v", err), http.StatusBadRequest)
		return
	}

	paymentID := "pay_" + uuid.New().String()[:8]
	if err := s.stateMachine.Init(paymentID); err != nil {
		http.Error(w, "failed to initialize payment state", http.StatusInternalServerError)
		return
	}

	// 1. Record intent.received
	intentIdemp := req.IdempotencyKey
	var idempPtr *string
	if intentIdemp != "" {
		key := "intent:" + intentIdemp
		idempPtr = &key
	}
	intentEvt, err := s.emit(ctx, "intent.received", &paymentID, &req.Mandate.MandateID, idempPtr, "gateway", map[string]interface{}{
		"merchant_id":      req.MerchantID,
		"merchant_name":    req.MerchantName,
		"amount":           req.Amount,
		"currency":         req.Currency,
		"items_count":      len(req.Items),
		"agent_reasoning":  req.AgentReasoning,
		"contexts_count":   len(req.SourceContexts),
	})
	if err != nil {
		if errors.Is(err, ledger.ErrDuplicateEvent) {
			http.Error(w, "duplicate intent request (idempotency key conflict)", http.StatusConflict)
			return
		}
		http.Error(w, fmt.Sprintf("ledger error: %v", err), http.StatusInternalServerError)
		return
	}

	// 2. Deterministic Mandate Verification
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

	// Fetch history from projections for risk scoring
	var riskHistory []models.RiskHistoryItem
	eventsList, err := s.ledger.GetEventsByPayment(ctx, paymentID)
	if err == nil {
		for _, e := range eventsList {
			if e.Type == "paypal.captured" {
				var d map[string]interface{}
				_ = json.Unmarshal(e.Data, &d)
				riskHistory = append(riskHistory, models.RiskHistoryItem{
					MerchantID: req.MerchantID,
					Amount:     req.Amount,
					Currency:   req.Currency,
					TS:         e.CreatedAt,
					Decision:   "allow",
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

	// 4. Decision Engine
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

	decEvt, _ := s.emit(ctx, "decision.made", &paymentID, &req.Mandate.MandateID, nil, "gateway", map[string]interface{}{
		"decision":   decision.Decision,
		"risk_score": decision.RiskScore,
		"reasons":    decision.Reasons,
		"checks":     decision.Checks,
	})
	if decEvt != nil {
		decision.AuditEventID = decEvt.ID
	}

	// 5. Execute decision with PayPal
	switch decision.Decision {
	case models.DecisionAllow:
		// Create order with intent=AUTHORIZE
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
		_, _ = s.emit(ctx, "paypal.order_created", &paymentID, &req.Mandate.MandateID, nil, "paypal", orderResp)

		// Authorize order
		authID, err := s.paypalClient.AuthorizeOrder(ctx, orderResp.ID, "auth:"+paymentID)
		if err != nil {
			log.Printf("[gateway] paypal AuthorizeOrder error: %v", err)
			http.Error(w, fmt.Sprintf("paypal authorize error: %v", err), http.StatusBadGateway)
			return
		}
		_ = s.stateMachine.Transition(paymentID, statemachine.StateAuthorized)
		_, _ = s.emit(ctx, "paypal.authorized", &paymentID, &req.Mandate.MandateID, nil, "paypal", map[string]interface{}{
			"order_id":         orderResp.ID,
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
			OrderID:         orderResp.ID,
			AuthorizationID: authID,
			CaptureID:       captureResp.ID,
			ApprovalURL:     orderResp.ApprovalURL,
		}

	case models.DecisionStepUp:
		_ = s.stateMachine.Transition(paymentID, statemachine.StateStepUpPending)
		// Create PayPal order so human has an approval URL
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

		decision.StepUpToken = "stepup_" + uuid.New().String()[:12]
		decision.PayPal = &models.PayPalDetails{
			OrderID:     orderID,
			ApprovalURL: approvalURL,
		}

	case models.DecisionBlock:
		_ = s.stateMachine.Transition(paymentID, statemachine.StateRejected)
		// Void if order exists, or record block
		decision.PayPal = nil
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(decision)
}

func (s *Server) handleApprovePayment(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	paymentID := r.PathValue("id")
	if paymentID == "" {
		http.Error(w, "missing payment id", http.StatusBadRequest)
		return
	}

	state, err := s.stateMachine.Get(paymentID)
	if err != nil || state != statemachine.StateStepUpPending {
		http.Error(w, fmt.Sprintf("payment cannot be approved in state %s", state), http.StatusBadRequest)
		return
	}

	proj, err := s.ledger.GetPaymentProjection(ctx, paymentID)
	if err != nil {
		http.Error(w, "payment projection not found", http.StatusNotFound)
		return
	}

	_, _ = s.emit(ctx, "human.approved", &paymentID, &proj.MandateID, nil, "human", map[string]interface{}{
		"approved_by": "human_principal",
		"payment_id":  paymentID,
	})

	// Authorize & capture
	authID, err := s.paypalClient.AuthorizeOrder(ctx, proj.PayPalOrderID, "auth_stepup:"+paymentID)
	if err != nil {
		authID = "AUTH_STEPUP_" + paymentID
	}
	_ = s.stateMachine.Transition(paymentID, statemachine.StateAuthorized)

	capResp, err := s.paypalClient.CaptureAuthorization(ctx, authID, proj.Amount, proj.Currency, true, "cap_stepup:"+paymentID)
	if err == nil && capResp != nil {
		_ = s.stateMachine.Transition(paymentID, statemachine.StateCaptured)
		_, _ = s.emit(ctx, "paypal.captured", &paymentID, &proj.MandateID, nil, "paypal", capResp)
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"payment_id": paymentID,
		"status":     "approved",
		"decision":   "allow",
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
			// Atul's contract: return 409 when idempotency_key already exists
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

	// Initial heartbeat comment
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
