package paypal

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"sync"
	"time"
)

type Config struct {
	Env          string // "sandbox" or "live"
	ClientID     string
	ClientSecret string
	HTTPTimeout  time.Duration
}

type SandboxClient struct {
	config     Config
	httpClient *http.Client
	baseURL    string

	tokenMu sync.RWMutex
	token   *TokenCache
}

func NewSandboxClient(cfg Config) *SandboxClient {
	if cfg.HTTPTimeout <= 0 {
		cfg.HTTPTimeout = 10 * time.Second
	}
	baseURL := "https://api-m.sandbox.paypal.com"
	if cfg.Env == "live" {
		baseURL = "https://api-m.paypal.com"
	}
	return &SandboxClient{
		config: cfg,
		httpClient: &http.Client{
			Timeout: cfg.HTTPTimeout,
		},
		baseURL: baseURL,
	}
}

func (c *SandboxClient) getAccessToken(ctx context.Context) (string, error) {
	c.tokenMu.RLock()
	if c.token != nil && time.Now().Before(c.token.ExpiresAt.Add(-60*time.Second)) {
		tok := c.token.AccessToken
		c.tokenMu.RUnlock()
		return tok, nil
	}
	c.tokenMu.RUnlock()

	c.tokenMu.Lock()
	defer c.tokenMu.Unlock()

	// Double check
	if c.token != nil && time.Now().Before(c.token.ExpiresAt.Add(-60*time.Second)) {
		return c.token.AccessToken, nil
	}

	authStr := base64.StdEncoding.EncodeToString([]byte(fmt.Sprintf("%s:%s", c.config.ClientID, c.config.ClientSecret)))
	data := []byte("grant_type=client_credentials")

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.baseURL+"/v1/oauth2/token", bytes.NewBuffer(data))
	if err != nil {
		return "", err
	}
	req.Header.Set("Authorization", "Basic "+authStr)
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return "", fmt.Errorf("paypal oauth request error: %w", err)
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return "", err
	}

	if resp.StatusCode >= 300 {
		return "", fmt.Errorf("paypal oauth error (%d): %s", resp.StatusCode, string(body))
	}

	var tokenResp struct {
		AccessToken string `json:"access_token"`
		ExpiresIn   int    `json:"expires_in"`
	}
	if err := json.Unmarshal(body, &tokenResp); err != nil {
		return "", err
	}

	c.token = &TokenCache{
		AccessToken: tokenResp.AccessToken,
		ExpiresAt:   time.Now().Add(time.Duration(tokenResp.ExpiresIn) * time.Second),
	}
	return c.token.AccessToken, nil
}

func (c *SandboxClient) doRequestWithRetry(ctx context.Context, method, endpoint string, body []byte, requestID string) ([]byte, int, error) {
	token, err := c.getAccessToken(ctx)
	if err != nil {
		return nil, 0, err
	}

	maxRetries := 3
	backoff := 200 * time.Millisecond

	for attempt := 0; attempt < maxRetries; attempt++ {
		req, err := http.NewRequestWithContext(ctx, method, c.baseURL+endpoint, bytes.NewBuffer(body))
		if err != nil {
			return nil, 0, err
		}
		req.Header.Set("Authorization", "Bearer "+token)
		req.Header.Set("Content-Type", "application/json")
		if requestID != "" {
			req.Header.Set("PayPal-Request-Id", requestID)
		}

		resp, err := c.httpClient.Do(req)
		if err == nil {
			respBytes, readErr := io.ReadAll(resp.Body)
			resp.Body.Close()
			if readErr != nil {
				return nil, resp.StatusCode, readErr
			}

			// Transient 5xx retry
			if resp.StatusCode >= 500 && attempt < maxRetries-1 {
				time.Sleep(backoff)
				backoff *= 2
				continue
			}
			return respBytes, resp.StatusCode, nil
		}

		if attempt < maxRetries-1 {
			time.Sleep(backoff)
			backoff *= 2
			continue
		}
		return nil, 0, err
	}
	return nil, 0, errors.New("paypal request retries exhausted")
}

func (c *SandboxClient) CreateOrder(ctx context.Context, req OrderRequest) (*OrderResponse, error) {
	unit := map[string]interface{}{
		"custom_id":   req.PaymentID,
		"description": req.Description,
		"amount": map[string]interface{}{
			"currency_code": req.Currency,
			"value":         req.Amount,
		},
	}
	payee := map[string]interface{}{}
	if req.PayeeEmail != "" {
		payee["email_address"] = req.PayeeEmail
	}
	if req.PayeeMerchantID != "" {
		payee["merchant_id"] = req.PayeeMerchantID
	}
	if len(payee) > 0 {
		unit["payee"] = payee
	}

	payload := map[string]interface{}{
		"intent":         "AUTHORIZE",
		"purchase_units": []map[string]interface{}{unit},
	}
	data, _ := json.Marshal(payload)
	respBytes, code, err := c.doRequestWithRetry(ctx, http.MethodPost, "/v2/checkout/orders", data, req.RequestID)
	if err != nil {
		return nil, err
	}
	if code >= 300 {
		return nil, fmt.Errorf("paypal CreateOrder failed (%d): %s", code, string(respBytes))
	}

	var parsed struct {
		ID     string `json:"id"`
		Status string `json:"status"`
		Links  []struct {
			Href string `json:"href"`
			Rel  string `json:"rel"`
		} `json:"links"`
	}
	if err := json.Unmarshal(respBytes, &parsed); err != nil {
		return nil, err
	}

	res := &OrderResponse{
		ID:     parsed.ID,
		Status: parsed.Status,
	}
	for _, l := range parsed.Links {
		if l.Rel == "approve" || l.Rel == "payer-action" {
			res.ApprovalURL = l.Href
			break
		}
	}
	return res, nil
}

func (c *SandboxClient) AuthorizeOrder(ctx context.Context, orderID string, requestID string) (string, error) {
	respBytes, code, err := c.doRequestWithRetry(ctx, http.MethodPost, fmt.Sprintf("/v2/checkout/orders/%s/authorize", orderID), nil, requestID)
	if err != nil {
		return "", err
	}
	if code >= 300 {
		return "", fmt.Errorf("paypal AuthorizeOrder failed (%d): %s", code, string(respBytes))
	}

	var parsed struct {
		PurchaseUnits []struct {
			Payments struct {
				Authorizations []struct {
					ID     string `json:"id"`
					Status string `json:"status"`
				} `json:"authorizations"`
			} `json:"payments"`
		} `json:"purchase_units"`
	}
	if err := json.Unmarshal(respBytes, &parsed); err != nil {
		return "", err
	}

	for _, pu := range parsed.PurchaseUnits {
		if len(pu.Payments.Authorizations) > 0 {
			return pu.Payments.Authorizations[0].ID, nil
		}
	}
	return "", fmt.Errorf("no authorization id in response: %s", string(respBytes))
}

func (c *SandboxClient) CaptureAuthorization(ctx context.Context, authID string, amount string, currency string, isFinal bool, requestID string) (*CaptureResponse, error) {
	payload := map[string]interface{}{
		"final_capture": isFinal,
		"amount": map[string]interface{}{
			"value":         amount,
			"currency_code": currency,
		},
	}
	data, _ := json.Marshal(payload)
	respBytes, code, err := c.doRequestWithRetry(ctx, http.MethodPost, fmt.Sprintf("/v2/payments/authorizations/%s/capture", authID), data, requestID)
	if err != nil {
		return nil, err
	}
	if code >= 300 {
		return nil, fmt.Errorf("paypal CaptureAuthorization failed (%d): %s", code, string(respBytes))
	}

	var parsed struct {
		ID     string `json:"id"`
		Status string `json:"status"`
		Amount struct {
			Value        string `json:"value"`
			CurrencyCode string `json:"currency_code"`
		} `json:"amount"`
		FinalCapture bool `json:"final_capture"`
	}
	if err := json.Unmarshal(respBytes, &parsed); err != nil {
		return nil, err
	}

	return &CaptureResponse{
		ID:           parsed.ID,
		Status:       parsed.Status,
		Amount:       parsed.Amount.Value,
		Currency:     parsed.Amount.CurrencyCode,
		FinalCapture: parsed.FinalCapture,
	}, nil
}

func (c *SandboxClient) VoidAuthorization(ctx context.Context, authID string, requestID string) (*VoidResponse, error) {
	respBytes, code, err := c.doRequestWithRetry(ctx, http.MethodPost, fmt.Sprintf("/v2/payments/authorizations/%s/void", authID), nil, requestID)
	if err != nil {
		return nil, err
	}
	if code >= 300 && code != http.StatusNoContent {
		return nil, fmt.Errorf("paypal VoidAuthorization failed (%d): %s", code, string(respBytes))
	}

	return &VoidResponse{
		ID:     authID,
		Status: "VOIDED",
	}, nil
}

func (c *SandboxClient) RefundCapture(ctx context.Context, captureID string, amount string, currency string, note string, requestID string) (*RefundResponse, error) {
	payload := map[string]interface{}{}
	if amount != "" && currency != "" {
		payload["amount"] = map[string]interface{}{
			"value":         amount,
			"currency_code": currency,
		}
	}
	if note != "" {
		payload["note_to_payer"] = note
	}

	data, _ := json.Marshal(payload)
	respBytes, code, err := c.doRequestWithRetry(ctx, http.MethodPost, fmt.Sprintf("/v2/payments/captures/%s/refund", captureID), data, requestID)
	if err != nil {
		return nil, err
	}
	if code >= 300 {
		return nil, fmt.Errorf("paypal RefundCapture failed (%d): %s", code, string(respBytes))
	}

	var parsed struct {
		ID     string `json:"id"`
		Status string `json:"status"`
		Amount struct {
			Value        string `json:"value"`
			CurrencyCode string `json:"currency_code"`
		} `json:"amount"`
	}
	if err := json.Unmarshal(respBytes, &parsed); err != nil {
		return nil, err
	}

	return &RefundResponse{
		ID:       parsed.ID,
		Status:   parsed.Status,
		Amount:   parsed.Amount.Value,
		Currency: parsed.Amount.CurrencyCode,
	}, nil
}
