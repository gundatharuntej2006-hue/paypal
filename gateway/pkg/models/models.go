package models

import (
	"encoding/json"
	"time"
)

// Mandate represents a cryptographically signed spending mandate from a human principal.
type Mandate struct {
	MandateID         string     `json:"mandate_id"`
	PrincipalID       string     `json:"principal_id"`
	AgentID           string     `json:"agent_id"`
	Task              string     `json:"task"`
	Currency          string     `json:"currency"`
	MaxTotal          string     `json:"max_total"`
	MaxPerTxn         string     `json:"max_per_txn"`
	StepUpOver        string     `json:"step_up_over,omitempty"`
	AllowedMerchants  []string   `json:"allowed_merchants,omitempty"`
	AllowedCategories []string   `json:"allowed_categories,omitempty"`
	ValidFrom         time.Time  `json:"valid_from"`
	ExpiresAt         time.Time  `json:"expires_at"`
	Nonce             string     `json:"nonce"`
	PayPalOrderID     string     `json:"paypal_order_id,omitempty"`
	PublicKey         string     `json:"public_key"`
	Signature         string     `json:"signature"`
}

// Item represents a single line item in an intent proposal.
type Item struct {
	Name      string `json:"name"`
	Qty       int    `json:"qty"`
	UnitPrice string `json:"unit_price"`
	Category  string `json:"category,omitempty"`
}

// SourceContext represents web content or context the agent inspected.
type SourceContext struct {
	Ref  string `json:"ref"`
	Kind string `json:"kind,omitempty"` // html, text, review, email, invoice, tool_output
	URL  string `json:"url,omitempty"`
	Text string `json:"text"`
	HTML string `json:"html,omitempty"`
}

// Proposal represents the agent's proposed purchase details.
type Proposal struct {
	MerchantID   string `json:"merchant_id"`
	MerchantName string `json:"merchant_name,omitempty"`
	Amount       string `json:"amount"`
	Currency     string `json:"currency"`
	Category     string `json:"category,omitempty"`
	Items        []Item `json:"items"`
}

// PaymentIntentRequest is sent by shopping agents to the gateway.
type PaymentIntentRequest struct {
	Mandate        Mandate         `json:"mandate"`
	MerchantID     string          `json:"merchant_id"`
	MerchantName   string          `json:"merchant_name,omitempty"`
	Amount         string          `json:"amount"`
	Currency       string          `json:"currency"`
	Category       string          `json:"category,omitempty"`
	Items          []Item          `json:"items"`
	AgentReasoning string          `json:"agent_reasoning"`
	SourceContexts []SourceContext `json:"source_contexts,omitempty"`
	IdempotencyKey string          `json:"idempotency_key"`
}

// DecisionType represents allow, step_up, or block.
type DecisionType string

const (
	DecisionAllow  DecisionType = "allow"
	DecisionStepUp DecisionType = "step_up"
	DecisionBlock  DecisionType = "block"
)

// ChecksResult records individual verdicts for each firewall layer.
type ChecksResult struct {
	Mandate   string `json:"mandate"`   // pass, fail
	Injection string `json:"injection"` // pass, flagged, unknown
	Intent    string `json:"intent"`    // pass, flagged, unknown
	Risk      string `json:"risk"`      // pass, flagged, unknown
}

// PayPalDetails holds IDs resulting from PayPal operations.
type PayPalDetails struct {
	OrderID         string `json:"order_id,omitempty"`
	AuthorizationID string `json:"authorization_id,omitempty"`
	CaptureID       string `json:"capture_id,omitempty"`
	RefundID        string `json:"refund_id,omitempty"`
	ApprovalURL     string `json:"approval_url,omitempty"`
}

// DecisionResponse is returned to the agent and UI.
type DecisionResponse struct {
	PaymentID    string         `json:"payment_id"`
	Decision     DecisionType   `json:"decision"`
	RiskScore    float64        `json:"risk_score"`
	Reasons      []string       `json:"reasons"`
	Checks       ChecksResult   `json:"checks"`
	PayPal       *PayPalDetails `json:"paypal,omitempty"`
	StepUpToken  string         `json:"step_up_token,omitempty"`
	AuditEventID string         `json:"audit_event_id"`
}

// LedgerEvent is the append-only ledger audit record.
type LedgerEvent struct {
	ID             string          `json:"id"`
	Type           string          `json:"type"`
	PaymentID      *string         `json:"payment_id,omitempty"`
	MandateID      *string         `json:"mandate_id,omitempty"`
	IdempotencyKey *string         `json:"idempotency_key,omitempty"`
	Source         string          `json:"source"`
	Data           json.RawMessage `json:"data"`
	CreatedAt      time.Time       `json:"created_at"`
}

// HookEventRequest is the payload received from internal services (e.g. Atul's webhook handler).
type HookEventRequest struct {
	Type           string                 `json:"type"`
	PaymentID      *string                `json:"payment_id,omitempty"`
	IdempotencyKey *string                `json:"idempotency_key,omitempty"`
	Source         string                 `json:"source"`
	Data           map[string]interface{} `json:"data"`
}

// PaymentProjection tracks current status of a payment in projections table.
type PaymentProjection struct {
	PaymentID       string       `json:"payment_id"`
	MandateID       string       `json:"mandate_id"`
	MerchantID      string       `json:"merchant_id"`
	Amount          string       `json:"amount"`
	Currency        string       `json:"currency"`
	State           string       `json:"state"`
	Decision        DecisionType `json:"decision"`
	RiskScore       float64      `json:"risk_score"`
	PayPalOrderID   string       `json:"paypal_order_id,omitempty"`
	PayPalAuthID    string       `json:"paypal_auth_id,omitempty"`
	PayPalCaptureID string       `json:"paypal_capture_id,omitempty"`
	CreatedAt       time.Time    `json:"created_at"`
	UpdatedAt       time.Time    `json:"updated_at"`
}

// MandateProjection tracks spent total for a mandate.
type MandateProjection struct {
	MandateID   string    `json:"mandate_id"`
	PrincipalID string    `json:"principal_id"`
	TotalSpent  string    `json:"total_spent"`
	Currency    string    `json:"currency"`
	LastUpdated time.Time `json:"last_updated"`
}

// Intelligence service model bindings (matching contracts/intel/)

type ScanSnippet struct {
	Ref  string `json:"ref"`
	Text string `json:"text"`
	Rule string `json:"rule"`
}

type ScanRequest struct {
	Contexts []SourceContext `json:"contexts"`
}

type ScanResponse struct {
	Version    string        `json:"version"`
	LatencyMS  int           `json:"latency_ms"`
	Reason     *string       `json:"reason"`
	Verdict    string        `json:"verdict"` // clean, injection, unknown
	Confidence float64       `json:"confidence"`
	Snippets   []ScanSnippet `json:"snippets"`
	Stage      string        `json:"stage"`
}

type JudgeInfluence struct {
	ReasoningQuote string  `json:"reasoning_quote"`
	SourceRef      *string `json:"source_ref"`
}

type JudgeRequest struct {
	Task           string          `json:"task,omitempty"`
	Mandate        Mandate         `json:"mandate"`
	Proposal       Proposal        `json:"proposal"`
	Reasoning      string          `json:"reasoning"`
	SourceContexts []SourceContext `json:"source_contexts,omitempty"`
}

type JudgeResponse struct {
	Version              string           `json:"version"`
	LatencyMS            int              `json:"latency_ms"`
	Reason               *string          `json:"reason"`
	Verdict              string           `json:"verdict"` // aligned, misaligned, unknown
	Confidence           float64          `json:"confidence"`
	Rationale            string           `json:"rationale"`
	InfluencedByExternal []JudgeInfluence `json:"influenced_by_external"`
}

type RiskHistoryItem struct {
	PaymentID  *string   `json:"payment_id,omitempty"`
	MerchantID string    `json:"merchant_id"`
	Amount     string    `json:"amount"`
	Currency   string    `json:"currency"`
	Category   *string   `json:"category,omitempty"`
	TS         time.Time `json:"ts"`
	Decision   string    `json:"decision"`
}

type RiskFactor struct {
	Name         string  `json:"name"`
	Contribution float64 `json:"contribution"`
	Detail       string  `json:"detail"`
}

type RiskRequest struct {
	Mandate  Mandate           `json:"mandate"`
	Proposal Proposal          `json:"proposal"`
	History  []RiskHistoryItem `json:"history"`
	Now      *time.Time        `json:"now,omitempty"`
}

type RiskResponse struct {
	Version   string       `json:"version"`
	LatencyMS int          `json:"latency_ms"`
	Reason    *string      `json:"reason"`
	Status    string       `json:"status"` // ok, unknown
	Score     *float64     `json:"score"`
	Factors   []RiskFactor `json:"factors"`
}
