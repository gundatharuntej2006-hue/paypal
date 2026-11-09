package crypto

import (
	"crypto/ed25519"
	"encoding/base64"
	"encoding/json"
	"os"
	"testing"
	"time"

	"gateway/pkg/models"
)

type MandateSigningVector struct {
	Description      string         `json:"description"`
	PrivateKeyBase64 string         `json:"private_key_base64"`
	PublicKeyBase64  string         `json:"public_key_base64"`
	Mandate          models.Mandate `json:"mandate"`
	CanonicalJSON    string         `json:"canonical_json"`
	SignatureBase64  string         `json:"signature_base64"`
}

func TestCrossLanguageSigningVectors(t *testing.T) {
	// Deterministic seed for reproducible cross-language testing
	seed := make([]byte, 32)
	for i := range seed {
		seed[i] = byte(i + 1)
	}

	privKey := ed25519.NewKeyFromSeed(seed)
	pubKey := privKey.Public().(ed25519.PublicKey)

	privB64 := base64.StdEncoding.EncodeToString(privKey)
	pubB64 := base64.StdEncoding.EncodeToString(pubKey)

	validFrom, _ := time.Parse(time.RFC3339, "2026-11-09T10:00:00Z")
	expiresAt, _ := time.Parse(time.RFC3339, "2026-11-10T10:00:00Z")

	m := models.Mandate{
		MandateID:         "mdt_test_vector_001",
		PrincipalID:       "principal_cross_lang",
		AgentID:           "agent_shopper_lang",
		Task:              "Procure test equipment",
		Currency:          "USD",
		MaxTotal:          "250.00",
		MaxPerTxn:         "100.00",
		StepUpOver:        "50.00",
		AllowedMerchants:  []string{"merchant_staples", "merchant_books_123"},
		AllowedCategories: []string{"books", "office_supplies"},
		ValidFrom:         validFrom,
		ExpiresAt:         expiresAt,
		Nonce:             "nonce_fixed_vector_999",
		PublicKey:         pubB64,
	}

	// 1. Check Canonical JSON generation
	canonicalBytes, err := CanonicalMandateBytes(&m)
	if err != nil {
		t.Fatalf("CanonicalMandateBytes error: %v", err)
	}
	canonicalStr := string(canonicalBytes)

	// Verify key sorting in canonical JSON
	expectedPrefix := `{"agent_id":"agent_shopper_lang","allowed_categories":["books","office_supplies"],"allowed_merchants":["merchant_books_123","merchant_staples"]`
	if len(canonicalStr) < len(expectedPrefix) || canonicalStr[:len(expectedPrefix)] != expectedPrefix {
		t.Fatalf("canonical JSON mismatch, got: %s", canonicalStr)
	}

	// 2. Sign mandate
	err = SignMandate(privB64, &m)
	if err != nil {
		t.Fatalf("SignMandate error: %v", err)
	}

	if m.Signature == "" {
		t.Fatalf("signature is empty after signing")
	}

	// 3. Verify signature
	valid, err := VerifyMandateSignature(&m)
	if err != nil || !valid {
		t.Fatalf("expected valid signature, got valid=%v, err=%v", valid, err)
	}

	// 4. Tampering detection: changing 1 cent in max_total breaks signature
	tampered := m
	tampered.MaxTotal = "250.01"
	validTampered, _ := VerifyMandateSignature(&tampered)
	if validTampered {
		t.Fatalf("security violation: tampered mandate verified successfully")
	}

	// 5. Export vector to contracts/test_vectors for Python SDK consumption
	vector := MandateSigningVector{
		Description:      "Standard RFC 8785 Ed25519 Mandate Signing Vector for cross-language validation (Go <-> Python)",
		PrivateKeyBase64: privB64,
		PublicKeyBase64:  pubB64,
		Mandate:          m,
		CanonicalJSON:    canonicalStr,
		SignatureBase64:  m.Signature,
	}

	vecData, err := json.MarshalIndent(vector, "", "  ")
	if err == nil {
		_ = os.MkdirAll("../../../contracts/test_vectors", 0755)
		_ = os.WriteFile("../../../contracts/test_vectors/mandate_signing_vector.json", vecData, 0644)
	}
}
