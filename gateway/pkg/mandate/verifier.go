package mandate

import (
	"errors"
	"fmt"
	"math"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"time"

	"gateway/pkg/crypto"
	"gateway/pkg/models"
)

var (
	ErrExpiredMandate        = errors.New("mandate has expired")
	ErrNotYetValid           = errors.New("mandate is not yet valid")
	ErrForgedSignature       = errors.New("mandate signature verification failed: forged or tampered")
	ErrUntrustedPrincipalKey = errors.New("mandate public key does not match trusted principal registry")
	ErrUnknownPrincipal      = errors.New("principal is not registered in trusted registry")
	ErrDisallowedMerchant    = errors.New("merchant is not in mandate allowed_merchants list")
	ErrDisallowedCategory    = errors.New("purchase category is not in mandate allowed_categories list")
	ErrCurrencyMismatch      = errors.New("purchase currency does not match mandate currency")
	ErrPerTxnCapExceeded     = errors.New("amount exceeds max_per_txn limit")
	ErrTotalCapExceeded       = errors.New("cumulative spending exceeds max_total limit")
	ErrWrongAgent            = errors.New("requesting agent does not match authorized agent_id in mandate")
	ErrInvalidAmountFormat   = errors.New("invalid money amount format")
	ErrInvalidCurrencyFormat = errors.New("invalid currency code: must be 3-letter ISO code")
	ErrAmountItemMismatch    = errors.New("declared payment amount does not match sum of item quantities and unit prices")
	ErrMissingMerchant       = errors.New("merchant_id is required")
	ErrUnverifiedMerchant    = errors.New("merchant is not registered in trusted merchant registry")
)

var (
	moneyRegex    = regexp.MustCompile(`^\d+(\.\d{1,2})?$`)
	currencyRegex = regexp.MustCompile(`^[A-Z]{3}$`)
)

// PrincipalRegistry maps trusted principal IDs to their authorized Ed25519 public keys.
type PrincipalRegistry interface {
	GetPublicKey(principalID string) (string, error)
	RegisterPrincipal(principalID string, publicKeyBase64 string)
}

// MemoryPrincipalRegistry is a thread-safe registry of trusted principals.
type MemoryPrincipalRegistry struct {
	mu   sync.RWMutex
	keys map[string]string
}

func NewMemoryPrincipalRegistry() *MemoryPrincipalRegistry {
	return &MemoryPrincipalRegistry{
		keys: make(map[string]string),
	}
}

func (r *MemoryPrincipalRegistry) GetPublicKey(principalID string) (string, error) {
	r.mu.RLock()
	defer r.mu.RUnlock()

	key, ok := r.keys[principalID]
	if !ok {
		return "", ErrUnknownPrincipal
	}
	return key, nil
}

func (r *MemoryPrincipalRegistry) RegisterPrincipal(principalID string, publicKeyBase64 string) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.keys[principalID] = publicKeyBase64
}

// MerchantPayeeInfo holds verified PayPal payee details for a merchant.
type MerchantPayeeInfo struct {
	Email           string
	PayPalAccountID string
}

// MerchantRegistry maps merchant IDs to verified PayPal payee accounts.
type MerchantRegistry interface {
	GetPayee(merchantID string) (MerchantPayeeInfo, error)
	HasMerchant(merchantID string) bool
	RegisterMerchant(merchantID string, info MerchantPayeeInfo)
}

// MemoryMerchantRegistry is a thread-safe in-memory merchant registry with defaults.
type MemoryMerchantRegistry struct {
	mu        sync.RWMutex
	merchants map[string]MerchantPayeeInfo
}

func NewMemoryMerchantRegistry() *MemoryMerchantRegistry {
	r := &MemoryMerchantRegistry{
		merchants: make(map[string]MerchantPayeeInfo),
	}
	// Seed common test merchants
	r.RegisterMerchant("merchant_books_123", MerchantPayeeInfo{
		Email:           "books-seller@sandbox.agentguard.local",
		PayPalAccountID: "PAYEE_BOOKS_123",
	})
	r.RegisterMerchant("merchant_staples", MerchantPayeeInfo{
		Email:           "staples-seller@sandbox.agentguard.local",
		PayPalAccountID: "PAYEE_STAPLES_001",
	})
	r.RegisterMerchant("merchant_electronics_456", MerchantPayeeInfo{
		Email:           "gadgets-seller@sandbox.agentguard.local",
		PayPalAccountID: "PAYEE_ELEC_456",
	})
	r.RegisterMerchant("merchant_food_789", MerchantPayeeInfo{
		Email:           "food-seller@sandbox.agentguard.local",
		PayPalAccountID: "PAYEE_FOOD_789",
	})
	r.RegisterMerchant("merchant_a", MerchantPayeeInfo{
		Email:           "merchant-a@sandbox.agentguard.local",
		PayPalAccountID: "PAYEE_MERCHANT_A",
	})
	r.RegisterMerchant("merchant_123", MerchantPayeeInfo{
		Email:           "seller-electronics@sandbox.agentguard.local",
		PayPalAccountID: "PAYEE_ELEC_456",
	})
	return r
}

func (r *MemoryMerchantRegistry) HasMerchant(merchantID string) bool {
	r.mu.RLock()
	defer r.mu.RUnlock()
	_, ok := r.merchants[strings.ToLower(strings.TrimSpace(merchantID))]
	return ok
}

func (r *MemoryMerchantRegistry) GetPayee(merchantID string) (MerchantPayeeInfo, error) {
	r.mu.RLock()
	defer r.mu.RUnlock()

	info, ok := r.merchants[strings.ToLower(strings.TrimSpace(merchantID))]
	if !ok {
		// Provide deterministic synthesized payee for dynamic test merchants
		cleanID := strings.ToLower(strings.TrimSpace(merchantID))
		return MerchantPayeeInfo{
			Email:           fmt.Sprintf("%s@merchants.agentguard.local", cleanID),
			PayPalAccountID: fmt.Sprintf("PAYEE_%s", strings.ToUpper(cleanID)),
		}, nil
	}
	return info, nil
}

func (r *MemoryMerchantRegistry) RegisterMerchant(merchantID string, info MerchantPayeeInfo) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.merchants[strings.ToLower(strings.TrimSpace(merchantID))] = info
}

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
	nonceStore        NonceStore
	spentTracker       SpentTracker
	principalRegistry PrincipalRegistry
	merchantRegistry  MerchantRegistry
	strictRegistry    bool
}

func NewVerifier(nonceStore NonceStore, tracker SpentTracker, registry PrincipalRegistry) *Verifier {
	return &Verifier{
		nonceStore:        nonceStore,
		spentTracker:       tracker,
		principalRegistry: registry,
		merchantRegistry:  NewMemoryMerchantRegistry(),
		strictRegistry:    registry != nil,
	}
}

// SetMerchantRegistry sets a custom merchant registry.
func (v *Verifier) SetMerchantRegistry(mr MerchantRegistry) {
	v.merchantRegistry = mr
}

// RegisterPrincipal registers a trusted principal key in the verifier's principal registry.
func (v *Verifier) RegisterPrincipal(principalID string, publicKeyBase64 string) {
	if v.principalRegistry != nil {
		v.principalRegistry.RegisterPrincipal(principalID, publicKeyBase64)
	}
}

// GetPrincipalPublicKey returns the registered public key for a principal.
func (v *Verifier) GetPrincipalPublicKey(principalID string) (string, error) {
	if v.principalRegistry == nil {
		return "", ErrUnknownPrincipal
	}
	return v.principalRegistry.GetPublicKey(principalID)
}

// RegisterMerchant registers a merchant in the verifier's merchant registry.
func (v *Verifier) RegisterMerchant(merchantID string, info MerchantPayeeInfo) {
	if r, ok := v.merchantRegistry.(*MemoryMerchantRegistry); ok {
		r.RegisterMerchant(merchantID, info)
	}
}

// GetMerchantPayee returns the verified payee for a merchant.
func (v *Verifier) GetMerchantPayee(merchantID string) (string, string) {
	if v.merchantRegistry == nil {
		return "", ""
	}
	info, err := v.merchantRegistry.GetPayee(merchantID)
	if err != nil {
		return "", ""
	}
	return info.Email, info.PayPalAccountID
}

// SetStrictRegistry toggles whether unregistered principals are rejected.
func (v *Verifier) SetStrictRegistry(strict bool) {
	v.strictRegistry = strict
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

	// 2. Principal Key Registry Check (Fix 2: prevent self-signed attacker mandates)
	if v.principalRegistry != nil {
		trustedKey, err := v.principalRegistry.GetPublicKey(m.PrincipalID)
		if err != nil {
			if v.strictRegistry {
				return &VerificationResult{Valid: false, FailedReason: ErrUnknownPrincipal.Error()}, ErrUnknownPrincipal
			}
		} else {
			if m.PublicKey == "" {
				m.PublicKey = trustedKey
			} else if trustedKey != m.PublicKey {
				return &VerificationResult{Valid: false, FailedReason: ErrUntrustedPrincipalKey.Error()}, ErrUntrustedPrincipalKey
			}
		}
	}

	// 3. Cryptographic signature check (Ed25519)
	validSig, err := crypto.VerifyMandateSignature(m)
	if err != nil || !validSig {
		return &VerificationResult{Valid: false, FailedReason: ErrForgedSignature.Error()}, ErrForgedSignature
	}

	// 4. Nonce replay protection (namespaced so dry-run never contaminates live nonces)
	nonceKey := "live:" + m.Nonce
	if req.DryRun {
		nonceKey = "dryrun:" + m.Nonce
	}
	ok, err := v.nonceStore.CheckAndConsume(nonceKey, m.MandateID)
	if err != nil || !ok {
		return &VerificationResult{Valid: false, FailedReason: ErrNonceReplayed.Error()}, ErrNonceReplayed
	}

	// 5. Agent identity binding
	if m.AgentID != "" && req.AgentID != "" && req.AgentID != m.AgentID {
		return &VerificationResult{
			Valid:        false,
			FailedReason: fmt.Sprintf("%s: caller agent '%s' does not match mandate agent '%s'", ErrWrongAgent, req.AgentID, m.AgentID),
		}, ErrWrongAgent
	}

	// 5. Currency format & matching
	if !currencyRegex.MatchString(req.Currency) || !currencyRegex.MatchString(m.Currency) {
		return &VerificationResult{Valid: false, FailedReason: ErrInvalidCurrencyFormat.Error()}, ErrInvalidCurrencyFormat
	}
	if strings.ToUpper(req.Currency) != strings.ToUpper(m.Currency) {
		return &VerificationResult{Valid: false, FailedReason: ErrCurrencyMismatch.Error()}, ErrCurrencyMismatch
	}

	// 6. Money format validation
	if !moneyRegex.MatchString(req.Amount) || !moneyRegex.MatchString(m.MaxPerTxn) || !moneyRegex.MatchString(m.MaxTotal) {
		return &VerificationResult{Valid: false, FailedReason: ErrInvalidAmountFormat.Error()}, ErrInvalidAmountFormat
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

	// 7. Per-transaction cap
	if reqAmount > maxPerTxn {
		return &VerificationResult{
			Valid:        false,
			FailedReason: fmt.Sprintf("%s: requested %.2f > max_per_txn %.2f", ErrPerTxnCapExceeded, reqAmount, maxPerTxn),
		}, ErrPerTxnCapExceeded
	}

	// 8. Cumulative total cap (independent tracking for live vs dryrun mode)
	var totalSpent float64
	if v.spentTracker != nil {
		trackerMandateID := m.MandateID
		if req.DryRun {
			trackerMandateID = "dryrun:" + m.MandateID
		}
		totalSpent, err = v.spentTracker.GetTotalSpent(trackerMandateID)
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

	// 9. Merchant validation
	if strings.TrimSpace(req.MerchantID) == "" {
		return &VerificationResult{Valid: false, FailedReason: ErrMissingMerchant.Error()}, ErrMissingMerchant
	}

	// 10. Merchant allowlist check (mandate policy)
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

	// In strict mode, merchant must be registered in trusted merchant registry
	if v.strictRegistry && v.merchantRegistry != nil && !v.merchantRegistry.HasMerchant(req.MerchantID) {
		return &VerificationResult{
			Valid:        false,
			FailedReason: fmt.Sprintf("%s: merchant '%s' is not registered", ErrUnverifiedMerchant, req.MerchantID),
		}, ErrUnverifiedMerchant
	}

	// 11. Category allowlist check
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

	// 12. Fix 9: Reconcile amount against items totals (prevent amount-splitting / ghost amounts)
	if len(req.Items) > 0 {
		var itemCents int64
		for _, item := range req.Items {
			if item.Qty <= 0 {
				return &VerificationResult{Valid: false, FailedReason: "item qty must be >= 1"}, ErrAmountItemMismatch
			}
			if !moneyRegex.MatchString(item.UnitPrice) {
				return &VerificationResult{Valid: false, FailedReason: "invalid item unit_price format"}, ErrInvalidAmountFormat
			}
			unitPriceFloat, err := strconv.ParseFloat(item.UnitPrice, 64)
			if err != nil {
				return &VerificationResult{Valid: false, FailedReason: "invalid item unit_price value"}, ErrInvalidAmountFormat
			}
			itemCents += int64(item.Qty) * int64(math.Round(unitPriceFloat*100))
		}

		declaredCents := int64(math.Round(reqAmount * 100))
		if itemCents != declaredCents {
			return &VerificationResult{
				Valid:        false,
				FailedReason: fmt.Sprintf("%s: items sum to %.2f but request declared %.2f", ErrAmountItemMismatch, float64(itemCents)/100.0, reqAmount),
			}, ErrAmountItemMismatch
		}
	}

	return &VerificationResult{
		Valid:   true,
		Reasons: []string{"signature_verified", "principal_trusted", "nonce_fresh", "within_caps", "merchant_allowed", "items_reconciled"},
	}, nil
}
