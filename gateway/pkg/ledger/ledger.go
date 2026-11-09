package ledger

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"strconv"
	"sync"
	"time"

	"github.com/google/uuid"
	_ "github.com/lib/pq"

	"gateway/pkg/models"
)

var (
	ErrDuplicateEvent       = errors.New("event with idempotency_key already recorded (conflict)")
	ErrNotFound             = errors.New("record not found in ledger")
	ErrTokenAlreadyConsumed = errors.New("step_up_token has already been consumed")
	ErrTokenExpired         = errors.New("step_up_token has expired")
	ErrTokenMismatch        = errors.New("step_up_token does not match")
	ErrBudgetExceeded       = errors.New("budget reservation exceeds mandate max_total cap")
)

// Ledger defines operations for the append-only event store and projections.
type Ledger interface {
	AppendEvent(ctx context.Context, event *models.LedgerEvent) error
	GetEvent(ctx context.Context, eventID string) (*models.LedgerEvent, error)
	GetEventsByPayment(ctx context.Context, paymentID string) ([]models.LedgerEvent, error)
	GetEventsByMandate(ctx context.Context, mandateID string) ([]models.LedgerEvent, error)
	ListEvents(ctx context.Context, limit int) ([]models.LedgerEvent, error)
	GetPaymentProjection(ctx context.Context, paymentID string) (*models.PaymentProjection, error)
	GetMandateProjection(ctx context.Context, mandateID string) (*models.MandateProjection, error)
	GetTotalSpent(mandateID string) (float64, error)
	GetDecisionByIdempotencyKey(ctx context.Context, idempKey string) (*models.DecisionResponse, error)
	SaveDecisionIdempotency(ctx context.Context, idempKey string, resp *models.DecisionResponse) error
	ConsumeStepUpToken(ctx context.Context, paymentID string, token string) error
	ReserveBudget(ctx context.Context, mandateID string, paymentID string, amount float64, maxTotal float64) error
	ReleaseBudgetReservation(ctx context.Context, mandateID string, paymentID string)
	CommitBudgetReservation(ctx context.Context, mandateID string, paymentID string)
	Replay(ctx context.Context) error
	Close() error
}

// -------------------------------------------------------------
// In-Memory Implementation (For Tests & Zero-Dependency Run)
// -------------------------------------------------------------

type MemoryLedger struct {
	mu            sync.RWMutex
	events        []models.LedgerEvent
	eventsByID    map[string]*models.LedgerEvent
	seenIdempKeys map[string]bool

	// Idempotency decision cache (Fix 5: recovery of original decision)
	cachedDecisions map[string]*models.DecisionResponse

	// Projections
	paymentProjections map[string]*models.PaymentProjection
	mandateProjections map[string]*models.MandateProjection

	// Atomic Budget Reservations: mandateID -> paymentID -> amount
	budgetReservations map[string]map[string]float64
}

func NewMemoryLedger() *MemoryLedger {
	return &MemoryLedger{
		eventsByID:         make(map[string]*models.LedgerEvent),
		seenIdempKeys:      make(map[string]bool),
		cachedDecisions:    make(map[string]*models.DecisionResponse),
		paymentProjections: make(map[string]*models.PaymentProjection),
		mandateProjections: make(map[string]*models.MandateProjection),
		budgetReservations: make(map[string]map[string]float64),
	}
}

func (m *MemoryLedger) AppendEvent(ctx context.Context, event *models.LedgerEvent) error {
	m.mu.Lock()
	defer m.mu.Unlock()

	if event.ID == "" {
		event.ID = "evt_" + uuid.New().String()[:8]
	}
	if event.CreatedAt.IsZero() {
		event.CreatedAt = time.Now().UTC()
	}

	// Unique check for idempotency key
	if event.IdempotencyKey != nil && *event.IdempotencyKey != "" {
		if m.seenIdempKeys[*event.IdempotencyKey] {
			return ErrDuplicateEvent
		}
		m.seenIdempKeys[*event.IdempotencyKey] = true
	}

	evtCopy := *event
	m.events = append(m.events, evtCopy)
	m.eventsByID[evtCopy.ID] = &evtCopy

	// Update projections immediately
	m.applyEventToProjections(&evtCopy)

	return nil
}

func (m *MemoryLedger) GetEvent(ctx context.Context, eventID string) (*models.LedgerEvent, error) {
	m.mu.RLock()
	defer m.mu.RUnlock()

	evt, ok := m.eventsByID[eventID]
	if !ok {
		return nil, ErrNotFound
	}
	res := *evt
	return &res, nil
}

func (m *MemoryLedger) GetEventsByPayment(ctx context.Context, paymentID string) ([]models.LedgerEvent, error) {
	m.mu.RLock()
	defer m.mu.RUnlock()

	var result []models.LedgerEvent
	for _, e := range m.events {
		if e.PaymentID != nil && *e.PaymentID == paymentID {
			result = append(result, e)
		}
	}
	return result, nil
}

func (m *MemoryLedger) GetEventsByMandate(ctx context.Context, mandateID string) ([]models.LedgerEvent, error) {
	m.mu.RLock()
	defer m.mu.RUnlock()

	var result []models.LedgerEvent
	for _, e := range m.events {
		if e.MandateID != nil && *e.MandateID == mandateID {
			result = append(result, e)
		}
	}
	return result, nil
}

func (m *MemoryLedger) ListEvents(ctx context.Context, limit int) ([]models.LedgerEvent, error) {
	m.mu.RLock()
	defer m.mu.RUnlock()

	if limit <= 0 || limit > len(m.events) {
		limit = len(m.events)
	}

	start := len(m.events) - limit
	result := make([]models.LedgerEvent, limit)
	copy(result, m.events[start:])

	// Return descending order (newest first)
	for i, j := 0, len(result)-1; i < j; i, j = i+1, j-1 {
		result[i], result[j] = result[j], result[i]
	}
	return result, nil
}

func (m *MemoryLedger) GetPaymentProjection(ctx context.Context, paymentID string) (*models.PaymentProjection, error) {
	m.mu.RLock()
	defer m.mu.RUnlock()

	proj, ok := m.paymentProjections[paymentID]
	if !ok {
		return nil, ErrNotFound
	}
	res := *proj
	return &res, nil
}

func (m *MemoryLedger) GetMandateProjection(ctx context.Context, mandateID string) (*models.MandateProjection, error) {
	m.mu.RLock()
	defer m.mu.RUnlock()

	proj, ok := m.mandateProjections[mandateID]
	if !ok {
		return nil, ErrNotFound
	}
	res := *proj
	return &res, nil
}

func (m *MemoryLedger) GetTotalSpent(mandateID string) (float64, error) {
	m.mu.RLock()
	defer m.mu.RUnlock()

	proj, ok := m.mandateProjections[mandateID]
	if !ok {
		return 0.0, nil
	}
	spent, err := strconv.ParseFloat(proj.TotalSpent, 64)
	if err != nil {
		return 0.0, nil
	}
	return spent, nil
}

func (m *MemoryLedger) GetDecisionByIdempotencyKey(ctx context.Context, idempKey string) (*models.DecisionResponse, error) {
	m.mu.RLock()
	defer m.mu.RUnlock()

	dec, ok := m.cachedDecisions[idempKey]
	if !ok {
		return nil, ErrNotFound
	}
	decCopy := *dec
	return &decCopy, nil
}

func (m *MemoryLedger) SaveDecisionIdempotency(ctx context.Context, idempKey string, resp *models.DecisionResponse) error {
	m.mu.Lock()
	defer m.mu.Unlock()

	respCopy := *resp
	m.cachedDecisions[idempKey] = &respCopy
	return nil
}

// ConsumeStepUpToken atomically verifies and consumes a step-up token before execution.
func (m *MemoryLedger) ConsumeStepUpToken(ctx context.Context, paymentID string, token string) error {
	m.mu.Lock()
	defer m.mu.Unlock()

	proj, ok := m.paymentProjections[paymentID]
	if !ok {
		return ErrNotFound
	}
	if proj.StepUpConsumed {
		return ErrTokenAlreadyConsumed
	}
	if proj.StepUpToken != "" && proj.StepUpToken != token {
		return ErrTokenMismatch
	}
	if proj.StepUpExpiresAt != nil && time.Now().UTC().After(*proj.StepUpExpiresAt) {
		return ErrTokenExpired
	}
	proj.StepUpConsumed = true
	return nil
}

// ReserveBudget atomically reserves budget for a pending payment against the mandate max_total cap.
func (m *MemoryLedger) ReserveBudget(ctx context.Context, mandateID string, paymentID string, amount float64, maxTotal float64) error {
	m.mu.Lock()
	defer m.mu.Unlock()

	totalSpent := 0.0
	if mProj, ok := m.mandateProjections[mandateID]; ok {
		totalSpent, _ = strconv.ParseFloat(mProj.TotalSpent, 64)
	}

	activeReserved := 0.0
	if resMap, ok := m.budgetReservations[mandateID]; ok {
		for pid, amt := range resMap {
			if pid != paymentID {
				activeReserved += amt
			}
		}
	} else {
		m.budgetReservations[mandateID] = make(map[string]float64)
	}

	if totalSpent+activeReserved+amount > maxTotal {
		return ErrBudgetExceeded
	}

	m.budgetReservations[mandateID][paymentID] = amount
	return nil
}

func (m *MemoryLedger) ReleaseBudgetReservation(ctx context.Context, mandateID string, paymentID string) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if resMap, ok := m.budgetReservations[mandateID]; ok {
		delete(resMap, paymentID)
	}
}

func (m *MemoryLedger) CommitBudgetReservation(ctx context.Context, mandateID string, paymentID string) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if resMap, ok := m.budgetReservations[mandateID]; ok {
		delete(resMap, paymentID)
	}
}

// Replay wipes all projections and re-applies all events sequentially.
func (m *MemoryLedger) Replay(ctx context.Context) error {
	m.mu.Lock()
	defer m.mu.Unlock()

	// Wipe projections
	m.paymentProjections = make(map[string]*models.PaymentProjection)
	m.mandateProjections = make(map[string]*models.MandateProjection)

	// Re-apply sequentially
	for i := range m.events {
		m.applyEventToProjections(&m.events[i])
	}
	return nil
}

func (m *MemoryLedger) Close() error {
	return nil
}

func (m *MemoryLedger) applyEventToProjections(e *models.LedgerEvent) {
	var data map[string]interface{}
	if err := json.Unmarshal(e.Data, &data); err != nil {
		return
	}

	paymentID := ""
	if e.PaymentID != nil {
		paymentID = *e.PaymentID
	}

	mandateID := ""
	if e.MandateID != nil {
		mandateID = *e.MandateID
	}

	if paymentID != "" {
		proj, exists := m.paymentProjections[paymentID]
		if !exists {
			proj = &models.PaymentProjection{
				PaymentID: paymentID,
				MandateID: mandateID,
				CreatedAt: e.CreatedAt,
			}
			m.paymentProjections[paymentID] = proj
		}
		proj.UpdatedAt = e.CreatedAt

		switch e.Type {
		case "intent.received":
			proj.State = "received"
			if val, ok := data["merchant_id"].(string); ok {
				proj.MerchantID = val
			}
			if val, ok := data["amount"].(string); ok {
				proj.Amount = val
			}
			if val, ok := data["currency"].(string); ok {
				proj.Currency = val
			}
			if isDry, ok := data["dry_run"].(bool); ok && isDry {
				proj.IsDryRun = true
			}
			if e.Source == "dry_run" {
				proj.IsDryRun = true
			}
		case "mandate.verified":
			proj.State = "verified"
		case "mandate.failed":
			proj.State = "rejected"
			proj.Decision = models.DecisionBlock
		case "scan.completed", "judge.completed", "risk.scored":
			if proj.State == "verified" {
				proj.State = "scanning"
			}
			if e.Type == "risk.scored" {
				if score, ok := data["score"].(float64); ok {
					proj.RiskScore = score
				}
			}
		case "decision.made":
			proj.State = "decided"
			if dec, ok := data["decision"].(string); ok {
				proj.Decision = models.DecisionType(dec)
				if proj.Decision == models.DecisionStepUp {
					proj.State = "step_up_pending"
					if token, ok := data["step_up_token"].(string); ok {
						proj.StepUpToken = token
					}
				} else if proj.Decision == models.DecisionBlock {
					proj.State = "rejected"
				}
			}
		case "payment.step_up_token_issued":
			if token, ok := data["step_up_token"].(string); ok {
				proj.StepUpToken = token
			}
			if expStr, ok := data["step_up_expires_at"].(string); ok {
				if t, err := time.Parse(time.RFC3339, expStr); err == nil {
					proj.StepUpExpiresAt = &t
				}
			}
		case "payment.step_up_token_consumed":
			proj.StepUpConsumed = true
		case "human.approved":
			proj.State = "decided"
			proj.Decision = models.DecisionAllow
		case "human.rejected":
			proj.State = "rejected"
			proj.Decision = models.DecisionBlock
		case "paypal.order_created":
			if orderID, ok := data["order_id"].(string); ok {
				proj.PayPalOrderID = orderID
			}
		case "paypal.authorized":
			proj.State = "authorized"
			if authID, ok := data["authorization_id"].(string); ok {
				proj.PayPalAuthID = authID
			}
		case "paypal.captured":
			proj.State = "captured"
			if capID, ok := data["capture_id"].(string); ok {
				proj.PayPalCaptureID = capID
			}
			// Update mandate spent ONLY if NOT a dry run!
			isDry := proj.IsDryRun || e.Source == "dry_run"
			if dVal, ok := data["dry_run"].(bool); ok && dVal {
				isDry = true
			}
			if !isDry && mandateID != "" && proj.Amount != "" {
				m.updateMandateSpent(mandateID, proj.Amount, proj.Currency, e.CreatedAt)
			}
		case "paypal.voided":
			proj.State = "voided"
		case "paypal.refunded":
			proj.State = "refunded"
		case "webhook.received":
			if status, ok := data["paypal_status"].(string); ok {
				if status == "captured" {
					proj.State = "captured"
				} else if status == "voided" {
					proj.State = "voided"
				} else if status == "refunded" {
					proj.State = "refunded"
				}
			}
		}
	}
}

func (m *MemoryLedger) updateMandateSpent(mandateID, amountStr, currency string, ts time.Time) {
	amount, err := strconv.ParseFloat(amountStr, 64)
	if err != nil {
		return
	}
	mProj, exists := m.mandateProjections[mandateID]
	if !exists {
		mProj = &models.MandateProjection{
			MandateID:   mandateID,
			TotalSpent:  "0.00",
			Currency:    currency,
			LastUpdated: ts,
		}
		m.mandateProjections[mandateID] = mProj
	}
	currentSpent, _ := strconv.ParseFloat(mProj.TotalSpent, 64)
	mProj.TotalSpent = fmt.Sprintf("%.2f", currentSpent+amount)
	mProj.LastUpdated = ts
}

// -------------------------------------------------------------
// PostgreSQL Implementation
// -------------------------------------------------------------

type PostgresLedger struct {
	db *sql.DB
}

func NewPostgresLedger(connString string) (*PostgresLedger, error) {
	db, err := sql.Open("postgres", connString)
	if err != nil {
		return nil, fmt.Errorf("failed to open postgres connection: %w", err)
	}

	if err := db.Ping(); err != nil {
		return nil, fmt.Errorf("failed to ping postgres: %w", err)
	}

	pl := &PostgresLedger{db: db}
	if err := pl.migrate(); err != nil {
		return nil, fmt.Errorf("failed to run postgres migrations: %w", err)
	}

	return pl, nil
}

func (p *PostgresLedger) migrate() error {
	schema := `
	CREATE TABLE IF NOT EXISTS events (
		id TEXT PRIMARY KEY,
		type TEXT NOT NULL,
		payment_id TEXT,
		mandate_id TEXT,
		idempotency_key TEXT UNIQUE,
		source TEXT NOT NULL,
		data JSONB NOT NULL,
		created_at TIMESTAMP WITH TIME ZONE NOT NULL
	);

	CREATE INDEX IF NOT EXISTS idx_events_payment_id ON events(payment_id);
	CREATE INDEX IF NOT EXISTS idx_events_mandate_id ON events(mandate_id);
	CREATE INDEX IF NOT EXISTS idx_events_created_at ON events(created_at);

	CREATE TABLE IF NOT EXISTS idempotency_decisions (
		idempotency_key TEXT PRIMARY KEY,
		decision JSONB NOT NULL,
		created_at TIMESTAMP WITH TIME ZONE NOT NULL
	);

	CREATE TABLE IF NOT EXISTS projections_payments (
		payment_id TEXT PRIMARY KEY,
		mandate_id TEXT,
		merchant_id TEXT,
		amount TEXT,
		currency TEXT,
		state TEXT,
		decision TEXT,
		risk_score REAL,
		step_up_token TEXT,
		step_up_expires_at TIMESTAMP WITH TIME ZONE,
		step_up_consumed BOOLEAN NOT NULL DEFAULT FALSE,
		is_dry_run BOOLEAN NOT NULL DEFAULT FALSE,
		paypal_order_id TEXT,
		paypal_auth_id TEXT,
		paypal_capture_id TEXT,
		created_at TIMESTAMP WITH TIME ZONE NOT NULL,
		updated_at TIMESTAMP WITH TIME ZONE NOT NULL
	);

	ALTER TABLE projections_payments ADD COLUMN IF NOT EXISTS step_up_expires_at TIMESTAMP WITH TIME ZONE;
	ALTER TABLE projections_payments ADD COLUMN IF NOT EXISTS step_up_consumed BOOLEAN NOT NULL DEFAULT FALSE;
	ALTER TABLE projections_payments ADD COLUMN IF NOT EXISTS is_dry_run BOOLEAN NOT NULL DEFAULT FALSE;

	CREATE TABLE IF NOT EXISTS projections_mandates (
		mandate_id TEXT PRIMARY KEY,
		principal_id TEXT,
		total_spent TEXT NOT NULL,
		currency TEXT NOT NULL,
		last_updated TIMESTAMP WITH TIME ZONE NOT NULL
	);

	CREATE TABLE IF NOT EXISTS projections_budget_reservations (
		mandate_id TEXT NOT NULL,
		payment_id TEXT PRIMARY KEY,
		amount NUMERIC NOT NULL,
		created_at TIMESTAMP WITH TIME ZONE NOT NULL
	);
	CREATE INDEX IF NOT EXISTS idx_budget_res_mandate ON projections_budget_reservations(mandate_id);
	`
	_, err := p.db.Exec(schema)
	return err
}

func (p *PostgresLedger) AppendEvent(ctx context.Context, event *models.LedgerEvent) error {
	if event.ID == "" {
		event.ID = "evt_" + uuid.New().String()[:8]
	}
	if event.CreatedAt.IsZero() {
		event.CreatedAt = time.Now().UTC()
	}

	tx, err := p.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()

	query := `
		INSERT INTO events (id, type, payment_id, mandate_id, idempotency_key, source, data, created_at)
		VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
	`
	_, err = tx.ExecContext(ctx, query,
		event.ID,
		event.Type,
		event.PaymentID,
		event.MandateID,
		event.IdempotencyKey,
		event.Source,
		string(event.Data),
		event.CreatedAt,
	)
	if err != nil {
		if isUniqueViolation(err) {
			return ErrDuplicateEvent
		}
		return fmt.Errorf("failed to insert event: %w", err)
	}

	if err := p.applyEventToProjectionsTx(ctx, tx, event); err != nil {
		return fmt.Errorf("failed to apply event projection in postgres: %w", err)
	}

	return tx.Commit()
}

func (p *PostgresLedger) applyEventToProjectionsTx(ctx context.Context, tx *sql.Tx, e *models.LedgerEvent) error {
	var data map[string]interface{}
	if err := json.Unmarshal(e.Data, &data); err != nil {
		return nil
	}

	paymentID := ""
	if e.PaymentID != nil {
		paymentID = *e.PaymentID
	}
	mandateID := ""
	if e.MandateID != nil {
		mandateID = *e.MandateID
	}

	if paymentID == "" {
		return nil
	}

	// Ensure record in projections_payments
	_, err := tx.ExecContext(ctx, `
		INSERT INTO projections_payments (payment_id, mandate_id, state, created_at, updated_at)
		VALUES ($1, $2, 'received', $3, $3)
		ON CONFLICT (payment_id) DO NOTHING
	`, paymentID, mandateID, e.CreatedAt)
	if err != nil {
		return err
	}

	switch e.Type {
	case "intent.received":
		merchantID, _ := data["merchant_id"].(string)
		amount, _ := data["amount"].(string)
		currency, _ := data["currency"].(string)
		isDryRun, _ := data["dry_run"].(bool)
		if e.Source == "dry_run" {
			isDryRun = true
		}
		_, err = tx.ExecContext(ctx, `
			UPDATE projections_payments 
			SET state = 'received', merchant_id = $2, amount = $3, currency = $4, is_dry_run = $5, updated_at = $6
			WHERE payment_id = $1
		`, paymentID, merchantID, amount, currency, isDryRun, e.CreatedAt)

	case "mandate.verified":
		_, err = tx.ExecContext(ctx, `UPDATE projections_payments SET state = 'verified', updated_at = $2 WHERE payment_id = $1`, paymentID, e.CreatedAt)

	case "mandate.failed":
		_, err = tx.ExecContext(ctx, `UPDATE projections_payments SET state = 'rejected', decision = 'block', updated_at = $2 WHERE payment_id = $1`, paymentID, e.CreatedAt)

	case "scan.completed", "judge.completed", "risk.scored":
		if e.Type == "risk.scored" {
			if score, ok := data["score"].(float64); ok {
				_, err = tx.ExecContext(ctx, `UPDATE projections_payments SET risk_score = $2, updated_at = $3 WHERE payment_id = $1`, paymentID, score, e.CreatedAt)
			}
		}

	case "decision.made":
		dec, _ := data["decision"].(string)
		stepUpToken, _ := data["step_up_token"].(string)
		state := "decided"
		if dec == "step_up" {
			state = "step_up_pending"
		} else if dec == "block" {
			state = "rejected"
		}
		_, err = tx.ExecContext(ctx, `
			UPDATE projections_payments 
			SET state = $2, decision = $3, step_up_token = COALESCE(NULLIF($4, ''), step_up_token), updated_at = $5
			WHERE payment_id = $1
		`, paymentID, state, dec, stepUpToken, e.CreatedAt)

	case "payment.step_up_token_issued":
		token, _ := data["step_up_token"].(string)
		expStr, _ := data["step_up_expires_at"].(string)
		var expTime *time.Time
		if t, parseErr := time.Parse(time.RFC3339, expStr); parseErr == nil {
			expTime = &t
		}
		_, err = tx.ExecContext(ctx, `
			UPDATE projections_payments 
			SET step_up_token = $2, step_up_expires_at = $3, updated_at = $4
			WHERE payment_id = $1
		`, paymentID, token, expTime, e.CreatedAt)

	case "payment.step_up_token_consumed":
		_, err = tx.ExecContext(ctx, `
			UPDATE projections_payments 
			SET step_up_consumed = TRUE, updated_at = $2
			WHERE payment_id = $1
		`, paymentID, e.CreatedAt)

	case "human.approved":
		_, err = tx.ExecContext(ctx, `
			UPDATE projections_payments 
			SET state = 'decided', decision = 'allow', updated_at = $2
			WHERE payment_id = $1
		`, paymentID, e.CreatedAt)

	case "human.rejected":
		_, err = tx.ExecContext(ctx, `
			UPDATE projections_payments 
			SET state = 'rejected', decision = 'block', updated_at = $2
			WHERE payment_id = $1
		`, paymentID, e.CreatedAt)

	case "paypal.order_created":
		orderID, _ := data["order_id"].(string)
		_, err = tx.ExecContext(ctx, `
			UPDATE projections_payments 
			SET paypal_order_id = $2, updated_at = $3
			WHERE payment_id = $1
		`, paymentID, orderID, e.CreatedAt)

	case "paypal.authorized":
		authID, _ := data["authorization_id"].(string)
		_, err = tx.ExecContext(ctx, `
			UPDATE projections_payments 
			SET state = 'authorized', paypal_auth_id = $2, updated_at = $3
			WHERE payment_id = $1
		`, paymentID, authID, e.CreatedAt)

	case "paypal.captured":
		capID, _ := data["capture_id"].(string)
		_, err = tx.ExecContext(ctx, `
			UPDATE projections_payments 
			SET state = 'captured', paypal_capture_id = $2, updated_at = $3
			WHERE payment_id = $1
		`, paymentID, capID, e.CreatedAt)
		if err != nil {
			return err
		}

		// Update mandate spent ONLY if NOT a dry run!
		var isDryRun bool
		var amountStr, currency string
		row := tx.QueryRowContext(ctx, "SELECT is_dry_run, amount, currency FROM projections_payments WHERE payment_id = $1", paymentID)
		_ = row.Scan(&isDryRun, &amountStr, &currency)

		if e.Source == "dry_run" {
			isDryRun = true
		}
		if dVal, ok := data["dry_run"].(bool); ok && dVal {
			isDryRun = true
		}

		if !isDryRun && mandateID != "" && amountStr != "" {
			amt, _ := strconv.ParseFloat(amountStr, 64)
			if amt > 0 {
				_, err = tx.ExecContext(ctx, `
					INSERT INTO projections_mandates (mandate_id, principal_id, total_spent, currency, last_updated)
					VALUES ($1, '', $2, $3, $4)
					ON CONFLICT (mandate_id) DO UPDATE SET 
						total_spent = (CAST(projections_mandates.total_spent AS NUMERIC) + CAST(EXCLUDED.total_spent AS NUMERIC))::TEXT,
						last_updated = EXCLUDED.last_updated
				`, mandateID, fmt.Sprintf("%.2f", amt), currency, e.CreatedAt)
			}
		}

	case "paypal.voided":
		_, err = tx.ExecContext(ctx, `UPDATE projections_payments SET state = 'voided', updated_at = $2 WHERE payment_id = $1`, paymentID, e.CreatedAt)

	case "paypal.refunded":
		_, err = tx.ExecContext(ctx, `UPDATE projections_payments SET state = 'refunded', updated_at = $2 WHERE payment_id = $1`, paymentID, e.CreatedAt)
	}

	return err
}

func (p *PostgresLedger) GetEvent(ctx context.Context, eventID string) (*models.LedgerEvent, error) {
	row := p.db.QueryRowContext(ctx,
		"SELECT id, type, payment_id, mandate_id, idempotency_key, source, data, created_at FROM events WHERE id = $1",
		eventID,
	)
	var e models.LedgerEvent
	var dataStr string
	err := row.Scan(&e.ID, &e.Type, &e.PaymentID, &e.MandateID, &e.IdempotencyKey, &e.Source, &dataStr, &e.CreatedAt)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return nil, ErrNotFound
		}
		return nil, err
	}
	e.Data = json.RawMessage(dataStr)
	return &e, nil
}

func (p *PostgresLedger) GetEventsByPayment(ctx context.Context, paymentID string) ([]models.LedgerEvent, error) {
	rows, err := p.db.QueryContext(ctx,
		"SELECT id, type, payment_id, mandate_id, idempotency_key, source, data, created_at FROM events WHERE payment_id = $1 ORDER BY created_at ASC",
		paymentID,
	)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var events []models.LedgerEvent
	for rows.Next() {
		var e models.LedgerEvent
		var dataStr string
		if err := rows.Scan(&e.ID, &e.Type, &e.PaymentID, &e.MandateID, &e.IdempotencyKey, &e.Source, &dataStr, &e.CreatedAt); err != nil {
			return nil, err
		}
		e.Data = json.RawMessage(dataStr)
		events = append(events, e)
	}
	return events, nil
}

func (p *PostgresLedger) GetEventsByMandate(ctx context.Context, mandateID string) ([]models.LedgerEvent, error) {
	rows, err := p.db.QueryContext(ctx,
		"SELECT id, type, payment_id, mandate_id, idempotency_key, source, data, created_at FROM events WHERE mandate_id = $1 ORDER BY created_at ASC",
		mandateID,
	)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var events []models.LedgerEvent
	for rows.Next() {
		var e models.LedgerEvent
		var dataStr string
		if err := rows.Scan(&e.ID, &e.Type, &e.PaymentID, &e.MandateID, &e.IdempotencyKey, &e.Source, &dataStr, &e.CreatedAt); err != nil {
			return nil, err
		}
		e.Data = json.RawMessage(dataStr)
		events = append(events, e)
	}
	return events, nil
}

func (p *PostgresLedger) ListEvents(ctx context.Context, limit int) ([]models.LedgerEvent, error) {
	if limit <= 0 {
		limit = 100
	}
	rows, err := p.db.QueryContext(ctx,
		"SELECT id, type, payment_id, mandate_id, idempotency_key, source, data, created_at FROM events ORDER BY created_at DESC LIMIT $1",
		limit,
	)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var events []models.LedgerEvent
	for rows.Next() {
		var e models.LedgerEvent
		var dataStr string
		if err := rows.Scan(&e.ID, &e.Type, &e.PaymentID, &e.MandateID, &e.IdempotencyKey, &e.Source, &dataStr, &e.CreatedAt); err != nil {
			return nil, err
		}
		e.Data = json.RawMessage(dataStr)
		events = append(events, e)
	}
	return events, nil
}

func (p *PostgresLedger) GetPaymentProjection(ctx context.Context, paymentID string) (*models.PaymentProjection, error) {
	row := p.db.QueryRowContext(ctx,
		`SELECT payment_id, mandate_id, COALESCE(merchant_id, ''), COALESCE(amount, ''), COALESCE(currency, ''), 
		        COALESCE(state, ''), COALESCE(decision, ''), COALESCE(risk_score, 0), COALESCE(step_up_token, ''), 
		        step_up_expires_at, COALESCE(step_up_consumed, FALSE), COALESCE(is_dry_run, FALSE),
		        COALESCE(paypal_order_id, ''), COALESCE(paypal_auth_id, ''), COALESCE(paypal_capture_id, ''), 
		        created_at, updated_at 
		 FROM projections_payments WHERE payment_id = $1`,
		paymentID,
	)
	var pr models.PaymentProjection
	err := row.Scan(
		&pr.PaymentID, &pr.MandateID, &pr.MerchantID, &pr.Amount, &pr.Currency,
		&pr.State, &pr.Decision, &pr.RiskScore, &pr.StepUpToken,
		&pr.StepUpExpiresAt, &pr.StepUpConsumed, &pr.IsDryRun,
		&pr.PayPalOrderID, &pr.PayPalAuthID, &pr.PayPalCaptureID,
		&pr.CreatedAt, &pr.UpdatedAt,
	)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return nil, ErrNotFound
		}
		return nil, err
	}
	return &pr, nil
}

func (p *PostgresLedger) GetMandateProjection(ctx context.Context, mandateID string) (*models.MandateProjection, error) {
	row := p.db.QueryRowContext(ctx,
		"SELECT mandate_id, principal_id, total_spent, currency, last_updated FROM projections_mandates WHERE mandate_id = $1",
		mandateID,
	)
	var mp models.MandateProjection
	err := row.Scan(&mp.MandateID, &mp.PrincipalID, &mp.TotalSpent, &mp.Currency, &mp.LastUpdated)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return nil, ErrNotFound
		}
		return nil, err
	}
	return &mp, nil
}

func (p *PostgresLedger) GetTotalSpent(mandateID string) (float64, error) {
	proj, err := p.GetMandateProjection(context.Background(), mandateID)
	if err != nil {
		if errors.Is(err, ErrNotFound) {
			return 0.0, nil
		}
		return 0.0, err
	}
	return strconv.ParseFloat(proj.TotalSpent, 64)
}

func (p *PostgresLedger) GetDecisionByIdempotencyKey(ctx context.Context, idempKey string) (*models.DecisionResponse, error) {
	row := p.db.QueryRowContext(ctx,
		"SELECT decision FROM idempotency_decisions WHERE idempotency_key = $1",
		idempKey,
	)
	var dataStr string
	err := row.Scan(&dataStr)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return nil, ErrNotFound
		}
		return nil, err
	}
	var dec models.DecisionResponse
	if err := json.Unmarshal([]byte(dataStr), &dec); err != nil {
		return nil, err
	}
	return &dec, nil
}

func (p *PostgresLedger) SaveDecisionIdempotency(ctx context.Context, idempKey string, resp *models.DecisionResponse) error {
	dataBytes, err := json.Marshal(resp)
	if err != nil {
		return err
	}
	_, err = p.db.ExecContext(ctx,
		"INSERT INTO idempotency_decisions (idempotency_key, decision, created_at) VALUES ($1, $2, $3) ON CONFLICT (idempotency_key) DO NOTHING",
		idempKey, string(dataBytes), time.Now().UTC(),
	)
	return err
}

func (p *PostgresLedger) ConsumeStepUpToken(ctx context.Context, paymentID string, token string) error {
	tx, err := p.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()

	var stepUpToken string
	var stepUpConsumed bool
	var stepUpExpiresAt *time.Time

	row := tx.QueryRowContext(ctx, `
		SELECT COALESCE(step_up_token, ''), step_up_consumed, step_up_expires_at 
		FROM projections_payments 
		WHERE payment_id = $1 
		FOR UPDATE
	`, paymentID)
	err = row.Scan(&stepUpToken, &stepUpConsumed, &stepUpExpiresAt)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return ErrNotFound
		}
		return err
	}

	if stepUpConsumed {
		return ErrTokenAlreadyConsumed
	}
	if stepUpToken != "" && stepUpToken != token {
		return ErrTokenMismatch
	}
	if stepUpExpiresAt != nil && time.Now().UTC().After(*stepUpExpiresAt) {
		return ErrTokenExpired
	}

	_, err = tx.ExecContext(ctx, `UPDATE projections_payments SET step_up_consumed = TRUE, updated_at = $2 WHERE payment_id = $1`, paymentID, time.Now().UTC())
	if err != nil {
		return err
	}

	return tx.Commit()
}

func (p *PostgresLedger) ReserveBudget(ctx context.Context, mandateID string, paymentID string, amount float64, maxTotal float64) error {
	tx, err := p.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()

	var totalSpentStr string
	row := tx.QueryRowContext(ctx, `SELECT total_spent FROM projections_mandates WHERE mandate_id = $1 FOR UPDATE`, mandateID)
	err = row.Scan(&totalSpentStr)
	totalSpent := 0.0
	if err == nil {
		totalSpent, _ = strconv.ParseFloat(totalSpentStr, 64)
	}

	var activeReserved sql.NullFloat64
	resRow := tx.QueryRowContext(ctx, `SELECT SUM(amount) FROM projections_budget_reservations WHERE mandate_id = $1 AND payment_id != $2`, mandateID, paymentID)
	_ = resRow.Scan(&activeReserved)

	reservedSum := 0.0
	if activeReserved.Valid {
		reservedSum = activeReserved.Float64
	}

	if totalSpent+reservedSum+amount > maxTotal {
		return ErrBudgetExceeded
	}

	_, err = tx.ExecContext(ctx, `
		INSERT INTO projections_budget_reservations (mandate_id, payment_id, amount, created_at)
		VALUES ($1, $2, $3, $4)
		ON CONFLICT (payment_id) DO UPDATE SET amount = EXCLUDED.amount, created_at = EXCLUDED.created_at
	`, mandateID, paymentID, amount, time.Now().UTC())
	if err != nil {
		return err
	}

	return tx.Commit()
}

func (p *PostgresLedger) ReleaseBudgetReservation(ctx context.Context, mandateID string, paymentID string) {
	_, _ = p.db.ExecContext(ctx, `DELETE FROM projections_budget_reservations WHERE payment_id = $1`, paymentID)
}

func (p *PostgresLedger) CommitBudgetReservation(ctx context.Context, mandateID string, paymentID string) {
	_, _ = p.db.ExecContext(ctx, `DELETE FROM projections_budget_reservations WHERE payment_id = $1`, paymentID)
}

func (p *PostgresLedger) Replay(ctx context.Context) error {
	tx, err := p.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()

	// Wipe projections
	if _, err := tx.ExecContext(ctx, "DELETE FROM projections_payments; DELETE FROM projections_mandates;"); err != nil {
		return err
	}

	// Fetch all events
	rows, err := tx.QueryContext(ctx, "SELECT id, type, payment_id, mandate_id, idempotency_key, source, data, created_at FROM events ORDER BY created_at ASC")
	if err != nil {
		return err
	}
	defer rows.Close()

	// Build in-memory projection during replay, then insert
	mem := NewMemoryLedger()
	for rows.Next() {
		var e models.LedgerEvent
		var dataStr string
		if err := rows.Scan(&e.ID, &e.Type, &e.PaymentID, &e.MandateID, &e.IdempotencyKey, &e.Source, &dataStr, &e.CreatedAt); err != nil {
			return err
		}
		e.Data = json.RawMessage(dataStr)
		mem.applyEventToProjections(&e)
	}

	for _, pr := range mem.paymentProjections {
		_, err := tx.ExecContext(ctx,
			`INSERT INTO projections_payments (payment_id, mandate_id, merchant_id, amount, currency, state, decision, risk_score, step_up_token, step_up_expires_at, step_up_consumed, is_dry_run, paypal_order_id, paypal_auth_id, paypal_capture_id, created_at, updated_at)
			 VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17)`,
			pr.PaymentID, pr.MandateID, pr.MerchantID, pr.Amount, pr.Currency, pr.State, pr.Decision, pr.RiskScore, pr.StepUpToken, pr.StepUpExpiresAt, pr.StepUpConsumed, pr.IsDryRun, pr.PayPalOrderID, pr.PayPalAuthID, pr.PayPalCaptureID, pr.CreatedAt, pr.UpdatedAt,
		)
		if err != nil {
			return err
		}
	}

	for _, mp := range mem.mandateProjections {
		_, err := tx.ExecContext(ctx,
			`INSERT INTO projections_mandates (mandate_id, principal_id, total_spent, currency, last_updated)
			 VALUES ($1, $2, $3, $4, $5)`,
			mp.MandateID, mp.PrincipalID, mp.TotalSpent, mp.Currency, mp.LastUpdated,
		)
		if err != nil {
			return err
		}
	}

	return tx.Commit()
}

func (p *PostgresLedger) Close() error {
	return p.db.Close()
}

func isUniqueViolation(err error) bool {
	return err != nil && (fmt.Sprintf("%v", err) == "pq: duplicate key value violates unique constraint \"events_idempotency_key_key\"" ||
		errors.Is(err, sql.ErrNoRows) == false && fmt.Sprintf("%v", err) != "")
}
