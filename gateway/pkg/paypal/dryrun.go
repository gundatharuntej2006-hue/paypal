package paypal

import (
	"context"
	"fmt"
	"sync"
	"sync/atomic"

	"github.com/google/uuid"
)

// DryRunClient is structurally payment-incapable. It satisfies the Client interface
// without network access and without touching any PayPal accounts, guaranteed.
// It supports failure injection for comprehensive red-team resilience testing.
type DryRunClient struct {
	mu                sync.RWMutex
	InjectedOrderErr  error
	InjectedAuthErr   error
	InjectedCapErr    error
	InjectedCapStatus string // e.g. "DECLINED"
	InjectedVoidErr   error
	InjectedRefundErr error
}

func NewDryRunClient() *DryRunClient {
	return &DryRunClient{}
}

func (d *DryRunClient) InjectOrderError(err error) {
	d.mu.Lock()
	defer d.mu.Unlock()
	d.InjectedOrderErr = err
}

func (d *DryRunClient) InjectAuthorizeError(err error) {
	d.mu.Lock()
	defer d.mu.Unlock()
	d.InjectedAuthErr = err
}

func (d *DryRunClient) InjectCaptureError(err error) {
	d.mu.Lock()
	defer d.mu.Unlock()
	d.InjectedCapErr = err
}

func (d *DryRunClient) InjectCaptureStatus(status string) {
	d.mu.Lock()
	defer d.mu.Unlock()
	d.InjectedCapStatus = status
}

func (d *DryRunClient) CreateOrder(ctx context.Context, req OrderRequest) (*OrderResponse, error) {
	d.mu.RLock()
	defer d.mu.RUnlock()
	if d.InjectedOrderErr != nil {
		return nil, d.InjectedOrderErr
	}

	orderID := "DRYRUN_ORDER_" + uuid.New().String()[:8]
	return &OrderResponse{
		ID:          orderID,
		Status:      "CREATED",
		ApprovalURL: fmt.Sprintf("https://sandbox.agentguard.local/dryrun-approve?token=%s", orderID),
	}, nil
}

func (d *DryRunClient) AuthorizeOrder(ctx context.Context, orderID string, requestID string) (string, error) {
	d.mu.RLock()
	defer d.mu.RUnlock()
	if d.InjectedAuthErr != nil {
		return "", d.InjectedAuthErr
	}

	return "DRYRUN_AUTH_" + uuid.New().String()[:8], nil
}

func (d *DryRunClient) CaptureAuthorization(ctx context.Context, authID string, amount string, currency string, isFinal bool, requestID string) (*CaptureResponse, error) {
	d.mu.RLock()
	defer d.mu.RUnlock()
	if d.InjectedCapErr != nil {
		return nil, d.InjectedCapErr
	}

	status := "COMPLETED"
	if d.InjectedCapStatus != "" {
		status = d.InjectedCapStatus
	}

	return &CaptureResponse{
		ID:           "DRYRUN_CAP_" + uuid.New().String()[:8],
		Status:       status,
		Amount:       amount,
		Currency:     currency,
		FinalCapture: isFinal,
	}, nil
}

func (d *DryRunClient) VoidAuthorization(ctx context.Context, authID string, requestID string) (*VoidResponse, error) {
	d.mu.RLock()
	defer d.mu.RUnlock()
	if d.InjectedVoidErr != nil {
		return nil, d.InjectedVoidErr
	}

	return &VoidResponse{
		ID:     authID,
		Status: "VOIDED",
	}, nil
}

func (d *DryRunClient) RefundCapture(ctx context.Context, captureID string, amount string, currency string, note string, requestID string) (*RefundResponse, error) {
	d.mu.RLock()
	defer d.mu.RUnlock()
	if d.InjectedRefundErr != nil {
		return nil, d.InjectedRefundErr
	}

	return &RefundResponse{
		ID:       "DRYRUN_REF_" + uuid.New().String()[:8],
		Status:   "COMPLETED",
		Amount:   amount,
		Currency: currency,
	}, nil
}

// SpyClient wraps a Client and records every single method call for verification tests.
type SpyClient struct {
	mu                  sync.Mutex
	underlying          Client
	CreateOrderCalls    int64
	AuthorizeOrderCalls int64
	CaptureCalls        int64
	VoidCalls           int64
	RefundCalls         int64
}

func NewSpyClient(underlying Client) *SpyClient {
	if underlying == nil {
		underlying = NewMockClient()
	}
	return &SpyClient{underlying: underlying}
}

func (s *SpyClient) TotalCalls() int64 {
	return atomic.LoadInt64(&s.CreateOrderCalls) +
		atomic.LoadInt64(&s.AuthorizeOrderCalls) +
		atomic.LoadInt64(&s.CaptureCalls) +
		atomic.LoadInt64(&s.VoidCalls) +
		atomic.LoadInt64(&s.RefundCalls)
}

func (s *SpyClient) Reset() {
	atomic.StoreInt64(&s.CreateOrderCalls, 0)
	atomic.StoreInt64(&s.AuthorizeOrderCalls, 0)
	atomic.StoreInt64(&s.CaptureCalls, 0)
	atomic.StoreInt64(&s.VoidCalls, 0)
	atomic.StoreInt64(&s.RefundCalls, 0)
}

func (s *SpyClient) CreateOrder(ctx context.Context, req OrderRequest) (*OrderResponse, error) {
	atomic.AddInt64(&s.CreateOrderCalls, 1)
	return s.underlying.CreateOrder(ctx, req)
}

func (s *SpyClient) AuthorizeOrder(ctx context.Context, orderID string, requestID string) (string, error) {
	atomic.AddInt64(&s.AuthorizeOrderCalls, 1)
	return s.underlying.AuthorizeOrder(ctx, orderID, requestID)
}

func (s *SpyClient) CaptureAuthorization(ctx context.Context, authID string, amount string, currency string, isFinal bool, requestID string) (*CaptureResponse, error) {
	atomic.AddInt64(&s.CaptureCalls, 1)
	return s.underlying.CaptureAuthorization(ctx, authID, amount, currency, isFinal, requestID)
}

func (s *SpyClient) VoidAuthorization(ctx context.Context, authID string, requestID string) (*VoidResponse, error) {
	atomic.AddInt64(&s.VoidCalls, 1)
	return s.underlying.VoidAuthorization(ctx, authID, requestID)
}

func (s *SpyClient) RefundCapture(ctx context.Context, captureID string, amount string, currency string, note string, requestID string) (*RefundResponse, error) {
	atomic.AddInt64(&s.RefundCalls, 1)
	return s.underlying.RefundCapture(ctx, captureID, amount, currency, note, requestID)
}
