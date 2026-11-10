package crypto

import (
	"crypto/ed25519"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"time"

	"gateway/pkg/models"
)

var (
	ErrInvalidSignature = errors.New("invalid mandate ed25519 signature")
	ErrInvalidPublicKey = errors.New("invalid base64 ed25519 public key")
)

// GenerateKeyPair generates a new Ed25519 keypair encoded in standard Base64.
func GenerateKeyPair() (pubKeyBase64 string, privKeyBase64 string, err error) {
	pub, priv, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		return "", "", fmt.Errorf("failed to generate ed25519 key: %w", err)
	}
	return base64.StdEncoding.EncodeToString(pub), base64.StdEncoding.EncodeToString(priv), nil
}

// CanonicalMandatePayload creates a deterministic map with sorted keys,
// omitting signature, and formatting timestamps canonically in RFC 3339 UTC.
func CanonicalMandatePayload(m *models.Mandate) map[string]interface{} {
	payload := map[string]interface{}{
		"mandate_id":   m.MandateID,
		"principal_id": m.PrincipalID,
		"agent_id":     m.AgentID,
		"task":         m.Task,
		"currency":     m.Currency,
		"max_total":    m.MaxTotal,
		"max_per_txn":  m.MaxPerTxn,
		"nonce":        m.Nonce,
		"public_key":   m.PublicKey,
		"valid_from":   m.ValidFrom.UTC().Format(time.RFC3339),
		"expires_at":   m.ExpiresAt.UTC().Format(time.RFC3339),
	}

	if m.StepUpOver != "" {
		payload["step_up_over"] = m.StepUpOver
	}
	if len(m.AllowedMerchants) > 0 {
		merchants := append([]string(nil), m.AllowedMerchants...)
		sort.Strings(merchants)
		payload["allowed_merchants"] = merchants
	}
	if len(m.AllowedCategories) > 0 {
		categories := append([]string(nil), m.AllowedCategories...)
		sort.Strings(categories)
		payload["allowed_categories"] = categories
	}
	if m.PayPalOrderID != "" {
		payload["paypal_order_id"] = m.PayPalOrderID
	}

	return payload
}

// CanonicalJSON recursively serializes maps and slices into canonical sorted JSON bytes.
func CanonicalJSON(v interface{}) ([]byte, error) {
	switch val := v.(type) {
	case map[string]interface{}:
		keys := make([]string, 0, len(val))
		for k := range val {
			keys = append(keys, k)
		}
		sort.Strings(keys)

		buf := []byte("{")
		for i, k := range keys {
			if i > 0 {
				buf = append(buf, ',')
			}
			keyBytes, err := json.Marshal(k)
			if err != nil {
				return nil, err
			}
			buf = append(buf, keyBytes...)
			buf = append(buf, ':')

			valBytes, err := CanonicalJSON(val[k])
			if err != nil {
				return nil, err
			}
			buf = append(buf, valBytes...)
		}
		buf = append(buf, '}')
		return buf, nil

	case []interface{}:
		buf := []byte("[")
		for i, item := range val {
			if i > 0 {
				buf = append(buf, ',')
			}
			itemBytes, err := CanonicalJSON(item)
			if err != nil {
				return nil, err
			}
			buf = append(buf, itemBytes...)
		}
		buf = append(buf, ']')
		return buf, nil

	default:
		return json.Marshal(v)
	}
}

// CanonicalMandateBytes returns the canonical deterministic byte slice for a mandate.
func CanonicalMandateBytes(m *models.Mandate) ([]byte, error) {
	payload := CanonicalMandatePayload(m)
	return CanonicalJSON(payload)
}

// SignMandate signs the canonical mandate bytes with the given base64 private key.
func SignMandate(privKeyBase64 string, m *models.Mandate) error {
	privBytes, err := base64.StdEncoding.DecodeString(privKeyBase64)
	if err != nil {
		return fmt.Errorf("invalid base64 private key: %w", err)
	}
	if len(privBytes) != ed25519.PrivateKeySize {
		return fmt.Errorf("invalid ed25519 private key size: expected %d, got %d", ed25519.PrivateKeySize, len(privBytes))
	}

	privKey := ed25519.PrivateKey(privBytes)
	pubKey := privKey.Public().(ed25519.PublicKey)
	m.PublicKey = base64.StdEncoding.EncodeToString(pubKey)

	canonicalBytes, err := CanonicalMandateBytes(m)
	if err != nil {
		return fmt.Errorf("failed to canonicalize mandate: %w", err)
	}

	sig := ed25519.Sign(privKey, canonicalBytes)
	m.Signature = base64.StdEncoding.EncodeToString(sig)
	return nil
}

// VerifyMandateSignature verifies the Ed25519 signature of a mandate against its public key.
func VerifyMandateSignature(m *models.Mandate) (bool, error) {
	if m.Signature == "" {
		return false, errors.New("mandate signature is empty")
	}
	if m.PublicKey == "" {
		return false, errors.New("mandate public key is empty")
	}

	pubBytes, err := base64.StdEncoding.DecodeString(m.PublicKey)
	if err != nil {
		return false, ErrInvalidPublicKey
	}
	if len(pubBytes) != ed25519.PublicKeySize {
		return false, fmt.Errorf("%w: expected %d bytes, got %d", ErrInvalidPublicKey, ed25519.PublicKeySize, len(pubBytes))
	}

	sigBytes, err := base64.StdEncoding.DecodeString(m.Signature)
	if err != nil {
		return false, fmt.Errorf("invalid base64 signature: %w", err)
	}
	if len(sigBytes) != ed25519.SignatureSize {
		return false, fmt.Errorf("invalid ed25519 signature size: expected %d, got %d", ed25519.SignatureSize, len(sigBytes))
	}

	canonicalBytes, err := CanonicalMandateBytes(m)
	if err != nil {
		return false, fmt.Errorf("failed to canonicalize mandate: %w", err)
	}

	valid := ed25519.Verify(pubBytes, canonicalBytes, sigBytes)
	if !valid {
		return false, ErrInvalidSignature
	}
	return true, nil
}
