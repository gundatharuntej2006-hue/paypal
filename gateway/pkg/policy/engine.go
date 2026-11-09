package policy

import (
	"strconv"

	"gateway/pkg/models"
)

// DecisionConfig defines policy engine thresholds.
type DecisionConfig struct {
	RiskBlockThreshold  float64 // e.g. 0.65
	RiskStepUpThreshold float64 // e.g. 0.35
}

func DefaultDecisionConfig() DecisionConfig {
	return DecisionConfig{
		RiskBlockThreshold:  0.65,
		RiskStepUpThreshold: 0.35,
	}
}

// DecisionEngine combines deterministic mandate checks and AI intelligence signals.
type DecisionEngine struct {
	config DecisionConfig
}

func NewDecisionEngine(cfg DecisionConfig) *DecisionEngine {
	if cfg.RiskBlockThreshold <= 0 {
		cfg.RiskBlockThreshold = 0.65
	}
	if cfg.RiskStepUpThreshold <= 0 {
		cfg.RiskStepUpThreshold = 0.35
	}
	return &DecisionEngine{config: cfg}
}

type Signals struct {
	MandateValid    bool
	MandateReason   string
	Scan            *models.ScanResponse
	Judge           *models.JudgeResponse
	Risk            *models.RiskResponse
	RequestedAmount string
	StepUpOver      string
}

// Evaluate evaluates all signals and computes the final DecisionResponse.
func (de *DecisionEngine) Evaluate(paymentID string, sig Signals) models.DecisionResponse {
	resp := models.DecisionResponse{
		PaymentID: paymentID,
		Checks: models.ChecksResult{
			Mandate:   "fail",
			Injection: "unknown",
			Intent:    "unknown",
			Risk:      "unknown",
		},
		Reasons: []string{},
	}

	// 1. Mandate deterministic check
	if !sig.MandateValid {
		resp.Decision = models.DecisionBlock
		resp.Checks.Mandate = "fail"
		reason := sig.MandateReason
		if reason == "" {
			reason = "mandate_invalid"
		}
		resp.Reasons = append(resp.Reasons, reason)
		return resp
	}
	resp.Checks.Mandate = "pass"

	// 2. Scan Signal
	hasUnknown := false
	if sig.Scan == nil || sig.Scan.Verdict == "unknown" {
		resp.Checks.Injection = "unknown"
		resp.Reasons = append(resp.Reasons, "injection_scan_unknown")
		hasUnknown = true
	} else if sig.Scan.Verdict == "injection" {
		resp.Checks.Injection = "flagged"
		resp.Decision = models.DecisionBlock
		resp.Reasons = append(resp.Reasons, "prompt_injection_detected")
		return resp
	} else {
		resp.Checks.Injection = "pass"
	}

	// 3. Judge Signal
	if sig.Judge == nil || sig.Judge.Verdict == "unknown" {
		resp.Checks.Intent = "unknown"
		resp.Reasons = append(resp.Reasons, "intent_judge_unknown")
		hasUnknown = true
	} else if sig.Judge.Verdict == "misaligned" {
		resp.Checks.Intent = "flagged"
		resp.Decision = models.DecisionBlock
		resp.Reasons = append(resp.Reasons, "intent_drift_detected")
		return resp
	} else {
		resp.Checks.Intent = "pass"
	}

	// 4. Risk Signal
	var riskScore float64
	if sig.Risk == nil || sig.Risk.Status == "unknown" || sig.Risk.Score == nil {
		resp.Checks.Risk = "unknown"
		resp.Reasons = append(resp.Reasons, "risk_score_unknown")
		hasUnknown = true
	} else {
		riskScore = *sig.Risk.Score
		resp.RiskScore = riskScore
		if riskScore >= de.config.RiskBlockThreshold {
			resp.Checks.Risk = "flagged"
			resp.Decision = models.DecisionBlock
			resp.Reasons = append(resp.Reasons, "risk_score_exceeds_block_threshold")
			return resp
		} else if riskScore >= de.config.RiskStepUpThreshold {
			resp.Checks.Risk = "flagged"
			resp.Reasons = append(resp.Reasons, "risk_score_exceeds_step_up_threshold")
		} else {
			resp.Checks.Risk = "pass"
		}
	}

	// 5. Fail-Closed rule: Any unknown / timed-out AI signal mandates step-up, NEVER allow
	if hasUnknown {
		resp.Decision = models.DecisionStepUp
		resp.Reasons = append(resp.Reasons, "fail_closed_step_up_due_to_unknown_signal")
		return resp
	}

	// 6. Mandate step_up_over threshold check
	if sig.StepUpOver != "" && sig.RequestedAmount != "" {
		reqAmt, err1 := strconv.ParseFloat(sig.RequestedAmount, 64)
		stepUpLimit, err2 := strconv.ParseFloat(sig.StepUpOver, 64)
		if err1 == nil && err2 == nil && reqAmt > stepUpLimit {
			resp.Decision = models.DecisionStepUp
			resp.Reasons = append(resp.Reasons, "amount_exceeds_mandate_step_up_threshold")
			return resp
		}
	}

	// 7. Check if risk triggered step-up
	if resp.Checks.Risk == "flagged" {
		resp.Decision = models.DecisionStepUp
		return resp
	}

	// 8. All checks passed cleanly -> Allow!
	resp.Decision = models.DecisionAllow
	resp.Reasons = append(resp.Reasons, "mandate_valid", "intent_aligned", "no_injection", "risk_acceptable")
	return resp
}
