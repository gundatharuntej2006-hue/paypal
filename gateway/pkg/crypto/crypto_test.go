package crypto

import (
	"testing"
	"time"

	"gateway/pkg/models"
)

func TestMandateSigningAndVerification(t *testing.T) {
	pub, priv, err := GenerateKeyPair()
	if err != nil {
		t.Fatalf("failed to generate key pair: %v", err)
	}

	mandate := models.Mandate{
		MandateID:         "mdt_test_001",
		PrincipalID:       "user_vivek",
		AgentID:           "agent_shopper",
		Task:              "Buy one USB-C hub under 40 USD",
		Currency:          "USD",
		MaxTotal:          "60.00",
		MaxPerTxn:         "40.00",
		StepUpOver:        "25.00",
		AllowedMerchants:  []string{"merchant_b", "merchant_a"},
		AllowedCategories: []string{"electronics"},
		ValidFrom:         time.Now().Add(-1 * time.Hour),
		ExpiresAt:         time.Now().Add(24 * time.Hour),
		Nonce:             "random_nonce_12345",
	}

	err = SignMandate(priv, &mandate)
	if err != nil {
		t.Fatalf("failed to sign mandate: %v", err)
	}

	if mandate.PublicKey != pub {
		t.Errorf("expected public key %s, got %s", pub, mandate.PublicKey)
	}
	if mandate.Signature == "" {
		t.Fatal("signature was empty")
	}

	// Verify valid signature
	valid, err := VerifyMandateSignature(&mandate)
	if err != nil || !valid {
		t.Fatalf("verification failed on untouched mandate: %v", err)
	}

	// Tamper with max_per_txn (e.g. agent tries to raise their budget limit)
	tampered := mandate
	tampered.MaxPerTxn = "99.00"
	valid, err = VerifyMandateSignature(&tampered)
	if valid || err == nil {
		t.Fatal("verification should have failed on tampered max_per_txn!")
	}

	// Tamper with allowed_merchants
	tamperedMerchant := mandate
	tamperedMerchant.AllowedMerchants = []string{"evil_merchant"}
	valid, err = VerifyMandateSignature(&tamperedMerchant)
	if valid || err == nil {
		t.Fatal("verification should have failed on tampered allowed_merchants!")
	}
}
