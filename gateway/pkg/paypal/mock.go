package paypal

import (
	"context"
	"fmt"
	"sync"

	"github.com/google/uuid"
)

// MockClient simulates the PayPal Sandbox API in-memory.
type MockClient struct {
	mu sync.Mutex

	// RequestIdempotencyCache: requestID -> response object
	idempOrders   map[string]*OrderResponse
	idempAuths    map[string]string
	idempCaptures map[string]*CaptureResponse
	idempVoids    map[string]*VoidResponse
	idempRefunds  map[string]*RefundResponse

	// Storage of entities
	Orders         map[string]*OrderResponse
	Authorizations map[string]string // authID -> status ("AUTHORIZED", "CAPTURED", "VOIDED")
	Captures       map[string]*CaptureResponse
}

func NewMockClient() *MockClient {
	return &MockClient{
		idempOrders:    make(map[string]*OrderResponse),
		idempAuths:     make(map[string]string),
		idempCaptures:  make(map[string]*CaptureResponse),
		idempVoids:     make(map[string]*VoidResponse),
		idempRefunds:   make(map[string]*RefundResponse),
		Orders:         make(map[string]*OrderResponse),
		Authorizations: make(map[string]string),
		Captures:       make(map[string]*CaptureResponse),
	}
}

func (m *MockClient) CreateOrder(ctx context.Context, req OrderRequest) (*OrderResponse, error) {
	m.mu.Lock()
	defer m.mu.Unlock()

	// Idempotency check
	if req.RequestID != "" {
		if cached, exists := m.idempOrders[req.RequestID]; exists {
			return cached, nil
		}
	}

	orderID := "ORDER_" + uuid.New().String()[:8]
	resp := &OrderResponse{
		ID:          orderID,
		Status:      "CREATED",
		ApprovalURL: fmt.Sprintf("https://www.sandbox.paypal.com/checkoutnow?token=%s", orderID),
	}

	m.Orders[orderID] = resp
	if req.RequestID != "" {
		m.idempOrders[req.RequestID] = resp
	}
	return resp, nil
}

func (m *MockClient) AuthorizeOrder(ctx context.Context, orderID string, requestID string) (string, error) {
	m.mu.Lock()
	defer m.mu.Unlock()

	if requestID != "" {
		if cachedAuthID, exists := m.idempAuths[requestID]; exists {
			return cachedAuthID, nil
		}
	}

	authID := "AUTH_" + uuid.New().String()[:8]
	m.Authorizations[authID] = "AUTHORIZED"

	if order, exists := m.Orders[orderID]; exists {
		order.Status = "COMPLETED"
		order.AuthorizationID = authID
	}

	if requestID != "" {
		m.idempAuths[requestID] = authID
	}
	return authID, nil
}

func (m *MockClient) CaptureAuthorization(ctx context.Context, authID string, amount string, currency string, isFinal bool, requestID string) (*CaptureResponse, error) {
	m.mu.Lock()
	defer m.mu.Unlock()

	if requestID != "" {
		if cached, exists := m.idempCaptures[requestID]; exists {
			return cached, nil
		}
	}

	status, exists := m.Authorizations[authID]
	if !exists {
		// Auto-register for convenience in tests if created via stub
		m.Authorizations[authID] = "AUTHORIZED"
		status = "AUTHORIZED"
	}
	if status == "VOIDED" {
		return nil, fmt.Errorf("cannot capture voided authorization %s", authID)
	}

	m.Authorizations[authID] = "CAPTURED"
	captureID := "CAP_" + uuid.New().String()[:8]
	resp := &CaptureResponse{
		ID:           captureID,
		Status:       "COMPLETED",
		Amount:       amount,
		Currency:     currency,
		FinalCapture: isFinal,
	}

	m.Captures[captureID] = resp
	if requestID != "" {
		m.idempCaptures[requestID] = resp
	}
	return resp, nil
}

func (m *MockClient) VoidAuthorization(ctx context.Context, authID string, requestID string) (*VoidResponse, error) {
	m.mu.Lock()
	defer m.mu.Unlock()

	if requestID != "" {
		if cached, exists := m.idempVoids[requestID]; exists {
			return cached, nil
		}
	}

	m.Authorizations[authID] = "VOIDED"
	resp := &VoidResponse{
		ID:     authID,
		Status: "VOIDED",
	}

	if requestID != "" {
		m.idempVoids[requestID] = resp
	}
	return resp, nil
}

func (m *MockClient) RefundCapture(ctx context.Context, captureID string, amount string, currency string, note string, requestID string) (*RefundResponse, error) {
	m.mu.Lock()
	defer m.mu.Unlock()

	if requestID != "" {
		if cached, exists := m.idempRefunds[requestID]; exists {
			return cached, nil
		}
	}

	refundID := "REF_" + uuid.New().String()[:8]
	resp := &RefundResponse{
		ID:       refundID,
		Status:   "COMPLETED",
		Amount:   amount,
		Currency: currency,
	}

	if requestID != "" {
		m.idempRefunds[requestID] = resp
	}
	return resp, nil
}
