package policy

import (
	"testing"

	"gateway/pkg/models"
)

func floatPtr(f float64) *float64 {
	return &f
}

func TestDecisionEngineTableDrivenCombinations(t *testing.T) {
	cfg := DecisionConfig{
		RiskBlockThreshold:  0.65,
		RiskStepUpThreshold: 0.35,
	}
	engine := NewDecisionEngine(cfg)

	cleanScan := &models.ScanResponse{Verdict: "clean", Confidence: 0.95}
	injectionScan := &models.ScanResponse{Verdict: "injection", Confidence: 0.99}
	unknownScan := &models.ScanResponse{Verdict: "unknown", Reason: stringPtr("timeout")}

	alignedJudge := &models.JudgeResponse{Verdict: "aligned", Confidence: 0.90}
	misalignedJudge := &models.JudgeResponse{Verdict: "misaligned", Confidence: 0.92}
	unknownJudge := &models.JudgeResponse{Verdict: "unknown", Reason: stringPtr("timeout")}

	lowRisk := &models.RiskResponse{Status: "ok", Score: floatPtr(0.15)}
	elevatedRisk := &models.RiskResponse{Status: "ok", Score: floatPtr(0.45)}
	highRisk := &models.RiskResponse{Status: "ok", Score: floatPtr(0.80)}
	unknownRisk := &models.RiskResponse{Status: "unknown", Reason: stringPtr("service_unavailable")}

	tests := []struct {
		name             string
		signals          Signals
		expectedDecision models.DecisionType
		expectedMandate  string
		expectedInj      string
		expectedJudge    string
		expectedRisk     string
	}{
		{
			name: "Happy path all green -> allow",
			signals: Signals{
				MandateValid:    true,
				Scan:            cleanScan,
				Judge:           alignedJudge,
				Risk:            lowRisk,
				RequestedAmount: "20.00",
				StepUpOver:      "50.00",
			},
			expectedDecision: models.DecisionAllow,
			expectedMandate:  "pass",
			expectedInj:      "pass",
			expectedJudge:    "pass",
			expectedRisk:     "pass",
		},
		{
			name: "Mandate invalid -> block",
			signals: Signals{
				MandateValid:  false,
				MandateReason: "signature_verification_failed",
				Scan:          cleanScan,
				Judge:         alignedJudge,
				Risk:          lowRisk,
			},
			expectedDecision: models.DecisionBlock,
			expectedMandate:  "fail",
			expectedInj:      "unknown",
			expectedJudge:    "unknown",
			expectedRisk:     "unknown",
		},
		{
			name: "Prompt injection detected -> block",
			signals: Signals{
				MandateValid: true,
				Scan:         injectionScan,
				Judge:        alignedJudge,
				Risk:         lowRisk,
			},
			expectedDecision: models.DecisionBlock,
			expectedMandate:  "pass",
			expectedInj:      "flagged",
			expectedJudge:    "unknown",
			expectedRisk:     "unknown",
		},
		{
			name: "Intent misaligned (drift) -> block",
			signals: Signals{
				MandateValid: true,
				Scan:         cleanScan,
				Judge:        misalignedJudge,
				Risk:         lowRisk,
			},
			expectedDecision: models.DecisionBlock,
			expectedMandate:  "pass",
			expectedInj:      "pass",
			expectedJudge:    "flagged",
			expectedRisk:     "unknown",
		},
		{
			name: "High risk score (above 0.65) -> block",
			signals: Signals{
				MandateValid: true,
				Scan:         cleanScan,
				Judge:        alignedJudge,
				Risk:         highRisk,
			},
			expectedDecision: models.DecisionBlock,
			expectedMandate:  "pass",
			expectedInj:      "pass",
			expectedJudge:    "pass",
			expectedRisk:     "flagged",
		},
		{
			name: "Scan unknown/timeout (fail-closed) -> step_up (NEVER allow)",
			signals: Signals{
				MandateValid:    true,
				Scan:            unknownScan,
				Judge:           alignedJudge,
				Risk:            lowRisk,
				RequestedAmount: "20.00",
			},
			expectedDecision: models.DecisionStepUp,
			expectedMandate:  "pass",
			expectedInj:      "unknown",
			expectedJudge:    "pass",
			expectedRisk:     "pass",
		},
		{
			name: "Judge unknown/timeout (fail-closed) -> step_up (NEVER allow)",
			signals: Signals{
				MandateValid:    true,
				Scan:            cleanScan,
				Judge:           unknownJudge,
				Risk:            lowRisk,
				RequestedAmount: "20.00",
			},
			expectedDecision: models.DecisionStepUp,
			expectedMandate:  "pass",
			expectedInj:      "pass",
			expectedJudge:    "unknown",
			expectedRisk:     "pass",
		},
		{
			name: "Risk unknown/timeout (fail-closed) -> step_up (NEVER allow)",
			signals: Signals{
				MandateValid:    true,
				Scan:            cleanScan,
				Judge:           alignedJudge,
				Risk:            unknownRisk,
				RequestedAmount: "20.00",
			},
			expectedDecision: models.DecisionStepUp,
			expectedMandate:  "pass",
			expectedInj:      "pass",
			expectedJudge:    "pass",
			expectedRisk:     "unknown",
		},
		{
			name: "Elevated risk score (0.45 between 0.35 and 0.65) -> step_up",
			signals: Signals{
				MandateValid:    true,
				Scan:            cleanScan,
				Judge:           alignedJudge,
				Risk:            elevatedRisk,
				RequestedAmount: "20.00",
			},
			expectedDecision: models.DecisionStepUp,
			expectedMandate:  "pass",
			expectedInj:      "pass",
			expectedJudge:    "pass",
			expectedRisk:     "flagged",
		},
		{
			name: "Amount exceeds step_up_over limit -> step_up",
			signals: Signals{
				MandateValid:    true,
				Scan:            cleanScan,
				Judge:           alignedJudge,
				Risk:            lowRisk,
				RequestedAmount: "35.00",
				StepUpOver:      "25.00",
			},
			expectedDecision: models.DecisionStepUp,
			expectedMandate:  "pass",
			expectedInj:      "pass",
			expectedJudge:    "pass",
			expectedRisk:     "pass",
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			res := engine.Evaluate("pay_test", tc.signals)
			if res.Decision != tc.expectedDecision {
				t.Fatalf("expected decision %s, got %s (reasons: %v)", tc.expectedDecision, res.Decision, res.Reasons)
			}
			if res.Checks.Mandate != tc.expectedMandate {
				t.Errorf("expected checks.mandate %s, got %s", tc.expectedMandate, res.Checks.Mandate)
			}
			if res.Checks.Injection != tc.expectedInj {
				t.Errorf("expected checks.injection %s, got %s", tc.expectedInj, res.Checks.Injection)
			}
			if res.Checks.Intent != tc.expectedJudge {
				t.Errorf("expected checks.intent %s, got %s", tc.expectedJudge, res.Checks.Intent)
			}
			if res.Checks.Risk != tc.expectedRisk {
				t.Errorf("expected checks.risk %s, got %s", tc.expectedRisk, res.Checks.Risk)
			}
		})
	}
}

func stringPtr(s string) *string {
	return &s
}
