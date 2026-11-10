package mandate

import (
	"testing"
	"time"

	"gateway/pkg/crypto"
	"gateway/pkg/models"
)

func createValidSignedMandate(t *testing.T, mutator func(*models.Mandate)) (models.Mandate, string) {
	pub, priv, err := crypto.GenerateKeyPair()
	if err != nil {
		t.Fatalf("crypto.GenerateKeyPair error: %v", err)
	}

	m := models.Mandate{
		MandateID:         "mdt_100",
		PrincipalID:       "user_vivek",
		AgentID:           "agent_shopper",
		Task:              "Purchase office supplies under budget",
		Currency:          "USD",
		MaxTotal:          "100.00",
		MaxPerTxn:         "50.00",
		StepUpOver:        "30.00",
		AllowedMerchants:  []string{"merchant_staples", "merchant_bestbuy"},
		AllowedCategories: []string{"office_supplies", "electronics"},
		ValidFrom:         time.Now().Add(-1 * time.Hour),
		ExpiresAt:         time.Now().Add(24 * time.Hour),
		Nonce:             "nonce_" + time.Now().Format("20060102150405.000000"),
	}

	if mutator != nil {
		mutator(&m)
	}

	err = crypto.SignMandate(priv, &m)
	if err != nil {
		t.Fatalf("crypto.SignMandate error: %v", err)
	}

	if m.PublicKey != pub {
		t.Fatalf("public key mismatch")
	}

	return m, priv
}

func TestMandateVerifierAcceptance(t *testing.T) {
	nonceStore := NewMemoryNonceStore()
	tracker := NewMemorySpentTracker()
	registry := NewMemoryPrincipalRegistry()
	verifier := NewVerifier(nonceStore, tracker, registry)

	now := time.Now()

	// 1. Valid mandate with registered principal
	validMandate, _ := createValidSignedMandate(t, nil)
	registry.RegisterPrincipal("user_vivek", validMandate.PublicKey)

	req := &models.PaymentIntentRequest{
		Mandate:        validMandate,
		MerchantID:     "merchant_staples",
		Amount:         "29.98",
		Currency:       "USD",
		Category:       "office_supplies",
		Items:          []models.Item{{Name: "Paper Reams", Qty: 2, UnitPrice: "14.99"}},
		AgentReasoning: "Best price for paper",
	}

	res, err := verifier.VerifyMandate(req, now)
	if err != nil || !res.Valid {
		t.Fatalf("expected valid mandate to pass, got: %v, res: %+v", err, res)
	}

	// 2. Untrusted Principal Key (Attacker creates their own keypair and signs a mandate for user_vivek)
	attackerMandate, _ := createValidSignedMandate(t, func(m *models.Mandate) {
		m.Nonce = "nonce_attacker_1"
	})
	reqAttacker := &models.PaymentIntentRequest{
		Mandate:    attackerMandate, // Has attacker's public key inside, not user_vivek's registered key!
		MerchantID: "merchant_staples",
		Amount:     "14.99",
		Currency:   "USD",
		Items:      []models.Item{{Name: "Paper Reams", Qty: 1, UnitPrice: "14.99"}},
	}
	resAttacker, errAttacker := verifier.VerifyMandate(reqAttacker, now)
	if errAttacker != ErrUntrustedPrincipalKey || (resAttacker != nil && resAttacker.Valid) {
		t.Fatalf("expected ErrUntrustedPrincipalKey, got: %v", errAttacker)
	}

	// 3. Item amount mismatch (Declared amount 50.00, but items total 14.99)
	reqMismatch := &models.PaymentIntentRequest{
		Mandate:    validMandate,
		MerchantID: "merchant_staples",
		Amount:     "50.00",
		Currency:   "USD",
		Category:   "office_supplies",
		Items:      []models.Item{{Name: "Paper Reams", Qty: 1, UnitPrice: "14.99"}},
	}
	// Use fresh nonce for validMandate test
	validMandateMismatch, _ := createValidSignedMandate(t, func(m *models.Mandate) {
		m.Nonce = "nonce_mismatch_1"
	})
	registry.RegisterPrincipal("user_vivek", validMandateMismatch.PublicKey)
	reqMismatch.Mandate = validMandateMismatch
	resMismatch, errMismatch := verifier.VerifyMandate(reqMismatch, now)
	if errMismatch != ErrAmountItemMismatch || (resMismatch != nil && resMismatch.Valid) {
		t.Fatalf("expected ErrAmountItemMismatch, got: %v", errMismatch)
	}

	// 4. Replay attack with same nonce
	registry.RegisterPrincipal("user_vivek", validMandate.PublicKey)
	resReplay, errReplay := verifier.VerifyMandate(req, now)
	if errReplay != ErrNonceReplayed || (resReplay != nil && resReplay.Valid) {
		t.Fatalf("expected replay attack error, got: %v", errReplay)
	}

	// 5. Expired mandate
	expiredMandate, _ := createValidSignedMandate(t, func(m *models.Mandate) {
		m.ValidFrom = now.Add(-48 * time.Hour)
		m.ExpiresAt = now.Add(-1 * time.Hour)
		m.Nonce = "nonce_expired_1"
	})
	registry.RegisterPrincipal("user_vivek", expiredMandate.PublicKey)
	reqExpired := &models.PaymentIntentRequest{
		Mandate:    expiredMandate,
		MerchantID: "merchant_staples",
		Amount:     "20.00",
		Currency:   "USD",
	}
	resExpired, errExpired := verifier.VerifyMandate(reqExpired, now)
	if errExpired != ErrExpiredMandate || (resExpired != nil && resExpired.Valid) {
		t.Fatalf("expected ErrExpiredMandate, got: %v", errExpired)
	}

	// 6. Forged / Tampered signature (tampering max_total after signing)
	tamperedMandate, _ := createValidSignedMandate(t, func(m *models.Mandate) {
		m.Nonce = "nonce_tamper_1"
	})
	registry.RegisterPrincipal("user_vivek", tamperedMandate.PublicKey)
	tamperedMandate.MaxTotal = "999.00" // Tamper amount!
	reqTampered := &models.PaymentIntentRequest{
		Mandate:    tamperedMandate,
		MerchantID: "merchant_staples",
		Amount:     "20.00",
		Currency:   "USD",
	}
	resTampered, errTampered := verifier.VerifyMandate(reqTampered, now)
	if errTampered != ErrForgedSignature || (resTampered != nil && resTampered.Valid) {
		t.Fatalf("expected ErrForgedSignature for tampered mandate, got: %v", errTampered)
	}

	// 7. Per-transaction cap exceeded
	capMandate, _ := createValidSignedMandate(t, func(m *models.Mandate) {
		m.MaxPerTxn = "25.00"
		m.Nonce = "nonce_cap_1"
	})
	registry.RegisterPrincipal("user_vivek", capMandate.PublicKey)
	reqOverCap := &models.PaymentIntentRequest{
		Mandate:    capMandate,
		MerchantID: "merchant_staples",
		Amount:     "25.01",
		Currency:   "USD",
	}
	resOverCap, errOverCap := verifier.VerifyMandate(reqOverCap, now)
	if errOverCap != ErrPerTxnCapExceeded || (resOverCap != nil && resOverCap.Valid) {
		t.Fatalf("expected ErrPerTxnCapExceeded, got: %v", errOverCap)
	}

	// 8. Cumulative cap exceeded
	cumMandate, _ := createValidSignedMandate(t, func(m *models.Mandate) {
		m.MandateID = "mdt_cumulative"
		m.MaxTotal = "50.00"
		m.MaxPerTxn = "50.00"
		m.Nonce = "nonce_cum_1"
	})
	registry.RegisterPrincipal("user_vivek", cumMandate.PublicKey)
	tracker.AddSpent("mdt_cumulative", 40.00) // already spent 40
	reqOverTotal := &models.PaymentIntentRequest{
		Mandate:    cumMandate,
		MerchantID: "merchant_staples",
		Amount:     "15.00", // 40 + 15 = 55 > 50!
		Currency:   "USD",
	}
	resOverTotal, errOverTotal := verifier.VerifyMandate(reqOverTotal, now)
	if errOverTotal != ErrTotalCapExceeded || (resOverTotal != nil && resOverTotal.Valid) {
		t.Fatalf("expected ErrTotalCapExceeded, got: %v", errOverTotal)
	}

	// 9. Disallowed merchant
	merchantMandate, _ := createValidSignedMandate(t, func(m *models.Mandate) {
		m.AllowedMerchants = []string{"legit_merchant"}
		m.Nonce = "nonce_merchant_1"
	})
	registry.RegisterPrincipal("user_vivek", merchantMandate.PublicKey)
	reqBadMerchant := &models.PaymentIntentRequest{
		Mandate:    merchantMandate,
		MerchantID: "rogue_seller_99",
		Amount:     "10.00",
		Currency:   "USD",
	}
	resBadMerchant, errBadMerchant := verifier.VerifyMandate(reqBadMerchant, now)
	if errBadMerchant != ErrDisallowedMerchant || (resBadMerchant != nil && resBadMerchant.Valid) {
		t.Fatalf("expected ErrDisallowedMerchant, got: %v", errBadMerchant)
	}
}
