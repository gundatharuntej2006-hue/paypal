package intelclient

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"sync"
	"time"

	"gateway/pkg/models"
)

type IntelClient interface {
	ScanContent(ctx context.Context, contexts []models.SourceContext) (*models.ScanResponse, error)
	JudgeIntent(ctx context.Context, req models.JudgeRequest) (*models.JudgeResponse, error)
	RiskScore(ctx context.Context, req models.RiskRequest) (*models.RiskResponse, error)
	CallAllParallel(ctx context.Context, req *models.PaymentIntentRequest, history []models.RiskHistoryItem) (*models.ScanResponse, *models.JudgeResponse, *models.RiskResponse)
}

type HTTPIntelClient struct {
	baseURL     string
	token       string
	timeout     time.Duration
	httpClient  *http.Client
}

func NewHTTPIntelClient(baseURL string, token string, timeout time.Duration) *HTTPIntelClient {
	if timeout <= 0 {
		timeout = 3 * time.Second
	}
	return &HTTPIntelClient{
		baseURL: baseURL,
		token:   token,
		timeout: timeout,
		httpClient: &http.Client{
			Timeout: timeout,
		},
	}
}

func (c *HTTPIntelClient) ScanContent(ctx context.Context, contexts []models.SourceContext) (*models.ScanResponse, error) {
	if contexts == nil {
		contexts = []models.SourceContext{}
	}
	payload := models.ScanRequest{Contexts: contexts}
	data, err := json.Marshal(payload)
	if err != nil {
		return nil, err
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.baseURL+"/scan/content", bytes.NewBuffer(data))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	if c.token != "" {
		req.Header.Set("X-Internal-Token", c.token)
	}

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("scan returned status %d: %s", resp.StatusCode, string(body))
	}

	var scanResp models.ScanResponse
	if err := json.Unmarshal(body, &scanResp); err != nil {
		return nil, fmt.Errorf("failed to decode scan response: %w", err)
	}
	if err := ValidateScanResponse(&scanResp); err != nil {
		reason := fmt.Sprintf("schema_validation_failed: %v", err)
		scanResp.Verdict = "unknown"
		scanResp.Reason = &reason
	}
	return &scanResp, nil
}

func (c *HTTPIntelClient) JudgeIntent(ctx context.Context, req models.JudgeRequest) (*models.JudgeResponse, error) {
	data, err := json.Marshal(req)
	if err != nil {
		return nil, err
	}

	httpReq, err := http.NewRequestWithContext(ctx, http.MethodPost, c.baseURL+"/judge/intent", bytes.NewBuffer(data))
	if err != nil {
		return nil, err
	}
	httpReq.Header.Set("Content-Type", "application/json")
	if c.token != "" {
		httpReq.Header.Set("X-Internal-Token", c.token)
	}

	resp, err := c.httpClient.Do(httpReq)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("judge returned status %d: %s", resp.StatusCode, string(body))
	}

	var judgeResp models.JudgeResponse
	if err := json.Unmarshal(body, &judgeResp); err != nil {
		return nil, fmt.Errorf("failed to decode judge response: %w", err)
	}
	if err := ValidateJudgeResponse(&judgeResp); err != nil {
		reason := fmt.Sprintf("schema_validation_failed: %v", err)
		judgeResp.Verdict = "unknown"
		judgeResp.Reason = &reason
	}
	return &judgeResp, nil
}

func (c *HTTPIntelClient) RiskScore(ctx context.Context, req models.RiskRequest) (*models.RiskResponse, error) {
	data, err := json.Marshal(req)
	if err != nil {
		return nil, err
	}

	httpReq, err := http.NewRequestWithContext(ctx, http.MethodPost, c.baseURL+"/risk/score", bytes.NewBuffer(data))
	if err != nil {
		return nil, err
	}
	httpReq.Header.Set("Content-Type", "application/json")
	if c.token != "" {
		httpReq.Header.Set("X-Internal-Token", c.token)
	}

	resp, err := c.httpClient.Do(httpReq)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("risk returned status %d: %s", resp.StatusCode, string(body))
	}

	var riskResp models.RiskResponse
	if err := json.Unmarshal(body, &riskResp); err != nil {
		return nil, fmt.Errorf("failed to decode risk response: %w", err)
	}
	if err := ValidateRiskResponse(&riskResp); err != nil {
		reason := fmt.Sprintf("schema_validation_failed: %v", err)
		riskResp.Status = "unknown"
		riskResp.Reason = &reason
		riskResp.Score = nil
	}
	return &riskResp, nil
}

var (
	ErrInvalidVerdict       = errors.New("invalid intelligence verdict")
	ErrConfidenceOutOfRange = errors.New("confidence must be between 0.0 and 1.0")
	ErrInvalidRiskStatus    = errors.New("invalid risk status")
	ErrRiskScoreOutOfRange  = errors.New("risk score must be between 0.0 and 1.0")
)

func ValidateScanResponse(resp *models.ScanResponse) error {
	if resp == nil {
		return errors.New("scan response is nil")
	}
	switch resp.Verdict {
	case "clean", "injection", "unknown":
	default:
		return fmt.Errorf("%w: '%s'", ErrInvalidVerdict, resp.Verdict)
	}
	if resp.Confidence < 0.0 || resp.Confidence > 1.0 {
		return fmt.Errorf("%w: %f", ErrConfidenceOutOfRange, resp.Confidence)
	}
	return nil
}

func ValidateJudgeResponse(resp *models.JudgeResponse) error {
	if resp == nil {
		return errors.New("judge response is nil")
	}
	switch resp.Verdict {
	case "aligned", "misaligned", "unknown":
	default:
		return fmt.Errorf("%w: '%s'", ErrInvalidVerdict, resp.Verdict)
	}
	if resp.Confidence < 0.0 || resp.Confidence > 1.0 {
		return fmt.Errorf("%w: %f", ErrConfidenceOutOfRange, resp.Confidence)
	}
	return nil
}

func ValidateRiskResponse(resp *models.RiskResponse) error {
	if resp == nil {
		return errors.New("risk response is nil")
	}
	switch resp.Status {
	case "ok", "unknown":
	default:
		return fmt.Errorf("%w: '%s'", ErrInvalidRiskStatus, resp.Status)
	}
	if resp.Status == "ok" && resp.Score != nil {
		if *resp.Score < 0.0 || *resp.Score > 1.0 {
			return fmt.Errorf("%w: %f", ErrRiskScoreOutOfRange, *resp.Score)
		}
	}
	return nil
}

// CallAllParallel runs the three AI checks concurrently within the timeout budget,
// applying fail-closed defaults if any endpoint fails or times out.
func (c *HTTPIntelClient) CallAllParallel(
	ctx context.Context,
	req *models.PaymentIntentRequest,
	history []models.RiskHistoryItem,
) (*models.ScanResponse, *models.JudgeResponse, *models.RiskResponse) {
	ctx, cancel := context.WithTimeout(ctx, c.timeout)
	defer cancel()

	var (
		scanResp  *models.ScanResponse
		judgeResp *models.JudgeResponse
		riskResp  *models.RiskResponse
		wg        sync.WaitGroup
	)

	wg.Add(3)

	// 1. Scan content
	go func() {
		defer wg.Done()
		res, err := c.ScanContent(ctx, req.SourceContexts)
		if err != nil {
			reason := fmt.Sprintf("scan_error: %v", err)
			scanResp = &models.ScanResponse{
				Verdict: "unknown",
				Stage:   "none",
				Reason:  &reason,
				Version: "client_failclosed",
			}
		} else {
			scanResp = res
		}
	}()

	// 2. Judge intent
	go func() {
		defer wg.Done()
		proposal := models.Proposal{
			MerchantID:   req.MerchantID,
			MerchantName: req.MerchantName,
			Amount:       req.Amount,
			Currency:     req.Currency,
			Category:     req.Category,
			Items:        req.Items,
		}
		judgeReq := models.JudgeRequest{
			Task:           req.Mandate.Task,
			Mandate:        req.Mandate,
			Proposal:       proposal,
			Reasoning:      req.AgentReasoning,
			SourceContexts: req.SourceContexts,
		}
		res, err := c.JudgeIntent(ctx, judgeReq)
		if err != nil {
			reason := fmt.Sprintf("judge_error: %v", err)
			judgeResp = &models.JudgeResponse{
				Verdict: "unknown",
				Reason:  &reason,
				Version: "client_failclosed",
			}
		} else {
			judgeResp = res
		}
	}()

	// 3. Risk score
	go func() {
		defer wg.Done()
		proposal := models.Proposal{
			MerchantID:   req.MerchantID,
			MerchantName: req.MerchantName,
			Amount:       req.Amount,
			Currency:     req.Currency,
			Category:     req.Category,
			Items:        req.Items,
		}
		now := time.Now().UTC()
		riskReq := models.RiskRequest{
			Mandate:  req.Mandate,
			Proposal: proposal,
			History:  history,
			Now:      &now,
		}
		res, err := c.RiskScore(ctx, riskReq)
		if err != nil {
			reason := fmt.Sprintf("risk_error: %v", err)
			riskResp = &models.RiskResponse{
				Status:  "unknown",
				Reason:  &reason,
				Version: "client_failclosed",
			}
		} else {
			riskResp = res
		}
	}()

	wg.Wait()
	return scanResp, judgeResp, riskResp
}

// -------------------------------------------------------------
// Stub Intel Client (For Local Mode & Fast Integration Tests)
// -------------------------------------------------------------

type StubIntelClient struct {
	ScanVerdict  string
	JudgeVerdict string
	RiskScoreVal float64
	ForceTimeout bool
}

func NewStubIntelClient() *StubIntelClient {
	return &StubIntelClient{
		ScanVerdict:  "clean",
		JudgeVerdict: "aligned",
		RiskScoreVal: 0.12,
	}
}

func (s *StubIntelClient) ScanContent(ctx context.Context, contexts []models.SourceContext) (*models.ScanResponse, error) {
	if s.ForceTimeout {
		time.Sleep(50 * time.Millisecond)
		return nil, context.DeadlineExceeded
	}
	return &models.ScanResponse{
		Verdict:    s.ScanVerdict,
		Confidence: 0.95,
		Stage:      "heuristic",
		Version:    "stub",
	}, nil
}

func (s *StubIntelClient) JudgeIntent(ctx context.Context, req models.JudgeRequest) (*models.JudgeResponse, error) {
	if s.ForceTimeout {
		time.Sleep(50 * time.Millisecond)
		return nil, context.DeadlineExceeded
	}
	return &models.JudgeResponse{
		Verdict:    s.JudgeVerdict,
		Confidence: 0.90,
		Rationale:  "Stub: purchase strictly aligns with mandate task.",
		Version:    "stub",
	}, nil
}

func (s *StubIntelClient) RiskScore(ctx context.Context, req models.RiskRequest) (*models.RiskResponse, error) {
	if s.ForceTimeout {
		time.Sleep(50 * time.Millisecond)
		return nil, context.DeadlineExceeded
	}
	score := s.RiskScoreVal
	return &models.RiskResponse{
		Status:  "ok",
		Score:   &score,
		Version: "stub",
		Factors: []models.RiskFactor{
			{Name: "amount_vs_cap", Contribution: 0.10, Detail: "well within cap"},
		},
	}, nil
}

func (s *StubIntelClient) CallAllParallel(
	ctx context.Context,
	req *models.PaymentIntentRequest,
	history []models.RiskHistoryItem,
) (*models.ScanResponse, *models.JudgeResponse, *models.RiskResponse) {
	scan, _ := s.ScanContent(ctx, req.SourceContexts)
	proposal := models.Proposal{
		MerchantID:   req.MerchantID,
		MerchantName: req.MerchantName,
		Amount:       req.Amount,
		Currency:     req.Currency,
		Category:     req.Category,
		Items:        req.Items,
	}
	judge, _ := s.JudgeIntent(ctx, models.JudgeRequest{
		Task:      req.Mandate.Task,
		Mandate:   req.Mandate,
		Proposal:  proposal,
		Reasoning: req.AgentReasoning,
	})
	risk, _ := s.RiskScore(ctx, models.RiskRequest{
		Mandate:  req.Mandate,
		Proposal: proposal,
		History:  history,
	})
	return scan, judge, risk
}
