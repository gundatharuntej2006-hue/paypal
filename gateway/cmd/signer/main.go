package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"log"
	"time"

	"gateway/pkg/crypto"
	"gateway/pkg/models"
)

func main() {
	mode := flag.String("mode", "generate", "Command mode: 'generate' or 'sample'")
	flag.Parse()

	switch *mode {
	case "vector":
		priv := "AQIDBAUGBwgJCgsMDQ4PEBESExQVFhcYGRobHB0eHyB5tVYuj+ZU+UB4sRLoqYunkB+FOuaVvtfg45ELrQSWZA=="
		pub := "ebVWLo/mVPlAeLES6KmLp5AfhTrmlb7X4OORC60ElmQ="

		validFrom, _ := time.Parse(time.RFC3339, "2026-11-11T00:00:00Z")
		expiresAt, _ := time.Parse(time.RFC3339, "2026-11-20T00:00:00Z")

		mandate := models.Mandate{
			MandateID:         "mdt_test_clean_001",
			PrincipalID:       "principal_cross_lang",
			AgentID:           "agent_shopper_lang",
			Task:              "Procure test equipment under policy",
			Currency:          "USD",
			MaxTotal:          "250.00",
			MaxPerTxn:         "100.00",
			StepUpOver:        "50.00",
			AllowedMerchants:  []string{"merchant_a", "merchant_staples"},
			AllowedCategories: []string{"books", "electronics", "office_supplies"},
			ValidFrom:         validFrom,
			ExpiresAt:         expiresAt,
			Nonce:             "nonce_bench_fresh_001",
			PublicKey:         pub,
		}

		err := crypto.SignMandate(priv, &mandate)
		if err != nil {
			log.Fatalf("failed to sign vector mandate: %v", err)
		}

		canonicalJSON, _ := crypto.CanonicalMandateBytes(&mandate)
		out, _ := json.MarshalIndent(mandate, "", "  ")
		fmt.Println("📋 Generated Fresh Signed Test Mandate:")
		fmt.Println(string(out))
		fmt.Printf("\nCanonical JSON: %s\n", string(canonicalJSON))
		fmt.Printf("Signature (Base64): %s\n", mandate.Signature)

	case "generate":
		pub, priv, err := crypto.GenerateKeyPair()
		if err != nil {
			log.Fatalf("failed to generate key pair: %v", err)
		}
		fmt.Println("🔑 Generated New Ed25519 Keypair:")
		fmt.Printf("Public Key (Base64):  %s\n", pub)
		fmt.Printf("Private Key (Base64): %s\n", priv)

	case "sample":
		pub, priv, err := crypto.GenerateKeyPair()
		if err != nil {
			log.Fatalf("failed to generate key pair: %v", err)
		}

		mandate := models.Mandate{
			MandateID:         "mdt_sample_001",
			PrincipalID:       "user_vivek",
			AgentID:           "agent_shopper",
			Task:              "Buy one USB-C hub under 40 USD for the office",
			Currency:          "USD",
			MaxTotal:          "60.00",
			MaxPerTxn:         "40.00",
			StepUpOver:        "25.00",
			AllowedMerchants:  []string{"merchant_a", "merchant_b"},
			AllowedCategories: []string{"electronics_accessories"},
			ValidFrom:         time.Now().UTC(),
			ExpiresAt:         time.Now().UTC().Add(48 * time.Hour),
			Nonce:             "b1f0c29a",
		}

		err = crypto.SignMandate(priv, &mandate)
		if err != nil {
			log.Fatalf("failed to sign sample mandate: %v", err)
		}

		out, _ := json.MarshalIndent(mandate, "", "  ")
		fmt.Println("📋 Generated Valid Signed Sample Mandate:")
		fmt.Println(string(out))
		fmt.Printf("\nPublic Key: %s\nPrivate Key: %s\n", pub, priv)
	}
}
