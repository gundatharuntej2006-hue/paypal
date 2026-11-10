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
