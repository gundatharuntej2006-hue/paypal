package mandate

import (
	"errors"
	"fmt"
	"strconv"
	"strings"
	"time"

	"gateway/pkg/crypto"
	"gateway/pkg/models"
)

var (
	ErrExpiredMandate        = errors.New("mandate has expired")
	ErrNotYetValid           = errors.New("mandate is not yet valid")
	ErrForgedSignature       = errors.New("mandate signature verification failed: forged or tampered")
	ErrDisallowedMerchant    = errors.New("merchant is not in mandate allowed_merchants list")
	ErrDisallowedCategory    = errors.New("purchase category is not in mandate allowed_categories list")
	ErrCurrencyMismatch      = errors.New("purchase currency does not match mandate currency")
	ErrPerTxnCapExceeded     = errors.New("amount exceeds max_per_txn limit")
	ErrTotalCapExceeded       = errors.New("cumulative spending exceeds max_total limit")
	ErrWrongAgent            = errors.New("requesting agent does not match authorized agent_id in mandate")
	ErrInvalidAmountFormat   = errors.New("invalid money amount format")
)

// SpentTracker looks up total cumulative spent amount for a mandate.
type SpentTracker interface {
	GetTotalSpent(mandateID string) (float64, error)
}

// MemorySpentTracker tracks spending in memory for tests/local mode.
type MemorySpentTracker struct {
	spent map[string]float64
}

func NewMemorySpentTracker() *MemorySpentTracker {
	return &MemorySpentTracker{spent: make(map[string]float64)}
}

func (m *MemorySpentTracker) GetTotalSpent(mandateID string) (float64, error) {
	return m.spent[mandateID], nil
}

func (m *MemorySpentTracker) AddSpent(mandateID string, amount float64) {
	m.spent[mandateID] += amount
}

// Verifier handles deterministic gate policy verification on mandates.
type Verifier struct {
	nonceStore   NonceStore
	spentTracker SpentTracker
}

func NewVerifier(nonceStore NonceStore, tracker SpentTracker) *Verifier {
	return &Verifier{
		nonceStore:   nonceStore,
		spentTracker: tracker,
	}
}

// VerificationResult describes the outcome of deterministic mandate checks.
type VerificationResult struct {
	Valid        bool     `json:"valid"`
	FailedReason string   `json:"failed_reason,omitempty"`
	Reasons      []string `json:"reasons"`
}

// VerifyMandate runs all deterministic mandate rules against a payment intent.
func (v *Verifier) VerifyMandate(req *models.PaymentIntentRequest, now time.Time) (*VerificationResult, error) {
	m := &req.Mandate

	// 1. Time validity window
	if !m.ExpiresAt.IsZero() && now.After(m.ExpiresAt) {
		return &VerificationResult{Valid: false, FailedReason: ErrExpiredMandate.Error()}, ErrExpiredMandate
	}
	if !m.ValidFrom.IsZero() && now.Before(m.ValidFrom) {
		return &VerificationResult{Valid: false, FailedReason: ErrNotYetValid.Error()}, ErrNotYetValid
	}

	// 2. Cryptographic signature check (Ed25519)
	validSig, err := crypto.VerifyMandateSignature(m)
	if err != nil || !validSig {
		return &VerificationResult{Valid: false, FailedReason: ErrForgedSignature.Error()}, ErrForgedSignature
	}

	// 3. Nonce replay protection
	ok, err := v.nonceStore.CheckAndConsume(m.Nonce, m.MandateID)
	if err != nil || !ok {
		return &VerificationResult{Valid: false, FailedReason: ErrNonceReplayed.Error()}, ErrNonceReplayed
	}

	// 4. Currency matching
	if strings.ToUpper(req.Currency) != strings.ToUpper(m.Currency) {
		return &VerificationResult{Valid: false, FailedReason: ErrCurrencyMismatch.Error()}, ErrCurrencyMismatch
	}

	// Parse amounts safely
	reqAmount, err := strconv.ParseFloat(req.Amount, 64)
	if err != nil || reqAmount <= 0 {
		return &VerificationResult{Valid: false, FailedReason: ErrInvalidAmountFormat.Error()}, ErrInvalidAmountFormat
	}

	maxPerTxn, err := strconv.ParseFloat(m.MaxPerTxn, 64)
	if err != nil {
		return &VerificationResult{Valid: false, FailedReason: "invalid max_per_txn format in mandate"}, ErrInvalidAmountFormat
	}

	maxTotal, err := strconv.ParseFloat(m.MaxTotal, 64)
	if err != nil {
		return &VerificationResult{Valid: false, FailedReason: "invalid max_total format in mandate"}, ErrInvalidAmountFormat
	}

	// 5. Per-transaction cap
	if reqAmount > maxPerTxn {
		return &VerificationResult{
			Valid:        false,
			FailedReason: fmt.Sprintf("%s: requested %.2f > max_per_txn %.2f", ErrPerTxnCapExceeded, reqAmount, maxPerTxn),
		}, ErrPerTxnCapExceeded
	}

	// 6. Cumulative total cap
	var totalSpent float64
	if v.spentTracker != nil {
		totalSpent, err = v.spentTracker.GetTotalSpent(m.MandateID)
		if err != nil {
			return nil, fmt.Errorf("failed to query total spent: %w", err)
		}
	}
	if totalSpent+reqAmount > maxTotal {
		return &VerificationResult{
			Valid:        false,
			FailedReason: fmt.Sprintf("%s: %.2f + %.2f > max_total %.2f", ErrTotalCapExceeded, totalSpent, reqAmount, maxTotal),
		}, ErrTotalCapExceeded
	}

	// 7. Merchant allowlist check
	if len(m.AllowedMerchants) > 0 {
		merchantAllowed := false
		for _, allowed := range m.AllowedMerchants {
			if strings.EqualFold(strings.TrimSpace(allowed), strings.TrimSpace(req.MerchantID)) {
				merchantAllowed = true
				break
			}
		}
		if !merchantAllowed {
			return &VerificationResult{Valid: false, FailedReason: ErrDisallowedMerchant.Error()}, ErrDisallowedMerchant
		}
	}

	// 8. Category allowlist check
	if len(m.AllowedCategories) > 0 {
		categoryAllowed := false
		if req.Category != "" {
			for _, allowed := range m.AllowedCategories {
				if strings.EqualFold(strings.TrimSpace(allowed), strings.TrimSpace(req.Category)) {
					categoryAllowed = true
					break
				}
			}
		}
		// Also check item-level categories if top-level category not matched
		if !categoryAllowed {
			for _, item := range req.Items {
				if item.Category != "" {
					for _, allowed := range m.AllowedCategories {
						if strings.EqualFold(strings.TrimSpace(allowed), strings.TrimSpace(item.Category)) {
							categoryAllowed = true
							break
						}
					}
				}
			}
		}
		if !categoryAllowed {
			return &VerificationResult{Valid: false, FailedReason: ErrDisallowedCategory.Error()}, ErrDisallowedCategory
		}
	}

	return &VerificationResult{
		Valid:   true,
		Reasons: []string{"signature_verified", "nonce_fresh", "within_caps", "merchant_allowed"},
	}, nil
}
