package paypal

import (
	"context"
	"time"
)

// OrderRequest parameters for creating an order with intent=AUTHORIZE.
type OrderRequest struct {
	PaymentID   string   `json:"payment_id"`
	Amount      string   `json:"amount"`
	Currency    string   `json:"currency"`
	Description string   `json:"description,omitempty"`
	RequestID   string   `json:"request_id"`
}

// OrderResponse returned by PayPal Orders API.
type OrderResponse struct {
	ID              string `json:"id"`
	Status          string `json:"status"` // CREATED, SAVED, APPROVED, VOIDED, COMPLETED
	AuthorizationID string `json:"authorization_id,omitempty"`
	ApprovalURL     string `json:"approval_url,omitempty"`
}

// CaptureResponse returned by PayPal Capture API.
type CaptureResponse struct {
	ID        string `json:"id"`
	Status    string `json:"status"` // COMPLETED, PENDING, DECLINED
	Amount    string `json:"amount"`
	Currency  string `json:"currency"`
	FinalCapture bool `json:"final_capture"`
}

// VoidResponse returned by PayPal Void API.
type VoidResponse struct {
	ID     string `json:"id"`
	Status string `json:"status"` // VOIDED
}

// RefundResponse returned by PayPal Refund API.
type RefundResponse struct {
	ID       string `json:"id"`
	Status   string `json:"status"` // COMPLETED, PENDING
	Amount   string `json:"amount"`
	Currency string `json:"currency"`
}

// Client defines the interface for interacting with PayPal.
type Client interface {
	CreateOrder(ctx context.Context, req OrderRequest) (*OrderResponse, error)
	AuthorizeOrder(ctx context.Context, orderID string, requestID string) (string, error)
	CaptureAuthorization(ctx context.Context, authID string, amount string, currency string, isFinal bool, requestID string) (*CaptureResponse, error)
	VoidAuthorization(ctx context.Context, authID string, requestID string) (*VoidResponse, error)
	RefundCapture(ctx context.Context, captureID string, amount string, currency string, note string, requestID string) (*RefundResponse, error)
}

// TokenCache caches the PayPal OAuth bearer token.
type TokenCache struct {
	AccessToken string
	ExpiresAt   time.Time
}
