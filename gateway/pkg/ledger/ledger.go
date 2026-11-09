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
	ErrDuplicateEvent = errors.New("event with idempotency_key already recorded (conflict)")
	ErrNotFound       = errors.New("record not found in ledger")
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
}

func NewMemoryLedger() *MemoryLedger {
	return &MemoryLedger{
		eventsByID:         make(map[string]*models.LedgerEvent),
		seenIdempKeys:      make(map[string]bool),
		cachedDecisions:    make(map[string]*models.DecisionResponse),
		paymentProjections: make(map[string]*models.PaymentProjection),
		mandateProjections: make(map[string]*models.MandateProjection),
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
			// Update mandate spent
			if mandateID != "" && proj.Amount != "" {
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
		paypal_order_id TEXT,
		paypal_auth_id TEXT,
		paypal_capture_id TEXT,
		created_at TIMESTAMP WITH TIME ZONE NOT NULL,
		updated_at TIMESTAMP WITH TIME ZONE NOT NULL
	);

	CREATE TABLE IF NOT EXISTS projections_mandates (
		mandate_id TEXT PRIMARY KEY,
		principal_id TEXT,
		total_spent TEXT NOT NULL,
		currency TEXT NOT NULL,
		last_updated TIMESTAMP WITH TIME ZONE NOT NULL
	);
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

	query := `
		INSERT INTO events (id, type, payment_id, mandate_id, idempotency_key, source, data, created_at)
		VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
	`
	_, err := p.db.ExecContext(ctx, query,
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

	return nil
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
		"SELECT payment_id, mandate_id, merchant_id, amount, currency, state, decision, risk_score, COALESCE(step_up_token, ''), paypal_order_id, paypal_auth_id, paypal_capture_id, created_at, updated_at FROM projections_payments WHERE payment_id = $1",
		paymentID,
	)
	var pr models.PaymentProjection
	err := row.Scan(&pr.PaymentID, &pr.MandateID, &pr.MerchantID, &pr.Amount, &pr.Currency, &pr.State, &pr.Decision, &pr.RiskScore, &pr.StepUpToken, &pr.PayPalOrderID, &pr.PayPalAuthID, &pr.PayPalCaptureID, &pr.CreatedAt, &pr.UpdatedAt)
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
			`INSERT INTO projections_payments (payment_id, mandate_id, merchant_id, amount, currency, state, decision, risk_score, step_up_token, paypal_order_id, paypal_auth_id, paypal_capture_id, created_at, updated_at)
			 VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)`,
			pr.PaymentID, pr.MandateID, pr.MerchantID, pr.Amount, pr.Currency, pr.State, pr.Decision, pr.RiskScore, pr.StepUpToken, pr.PayPalOrderID, pr.PayPalAuthID, pr.PayPalCaptureID, pr.CreatedAt, pr.UpdatedAt,
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
