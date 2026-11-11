package api_test

import (
	"context"
	"fmt"
	"io"
	"net"
	"net/http"
	"testing"
	"time"

	"gateway/pkg/api"
	"gateway/pkg/config"
	"gateway/pkg/crypto"
	"gateway/pkg/events"
	"gateway/pkg/intelclient"
	"gateway/pkg/ledger"
	"gateway/pkg/mandate"
	"gateway/pkg/models"
	"gateway/pkg/paypal"
	"gateway/pkg/policy"
	"gateway/pkg/statemachine"
)

// TestGatewayListener_LoopbackHTTPExecution proves the server exclusively listens on 127.0.0.1
// and successfully responds to loopback HTTP requests.
func TestGatewayListener_LoopbackHTTPExecution(t *testing.T) {
	cfg := &config.Config{
		Port:           "0", // random free port
		BindAddr:       "127.0.0.1",
		Env:            "development",
		GatewayMode:    "dryrun",
		PayPalMockMode: true,
	}

	led := ledger.NewMemoryLedger()
	nonceStore := mandate.NewMemoryNonceStore()
	registry := mandate.NewMemoryPrincipalRegistry()
	verifier := mandate.NewVerifier(nonceStore, led, registry)
	sm := statemachine.NewStateMachine()
	engine := policy.NewDecisionEngine(policy.DecisionConfig{
		RiskBlockThreshold:  0.65,
		RiskStepUpThreshold: 0.35,
	})
	ppClient := paypal.NewDryRunClient()
	intel := intelclient.NewStubIntelClient()
	broker := events.NewBroker()

	server := api.NewServer(cfg, led, verifier, sm, engine, ppClient, intel, broker)
	handler := api.SetupRouter(server)

	listenAddr := cfg.ListenAddr()
	ln, err := net.Listen("tcp", listenAddr)
	if err != nil {
		t.Fatalf("failed to listen on %s: %v", listenAddr, err)
	}
	defer ln.Close()

	tcpAddr := ln.Addr().(*net.TCPAddr)
	if !tcpAddr.IP.Equal(net.ParseIP("127.0.0.1")) {
		t.Fatalf("expected listener bound to 127.0.0.1, got %s", tcpAddr.IP.String())
	}

	httpSrv := &http.Server{
		Handler: handler,
	}
	go func() {
		_ = httpSrv.Serve(ln)
	}()
	defer func() {
		ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		defer cancel()
		_ = httpSrv.Shutdown(ctx)
	}()

	// Query /health over loopback
	url := fmt.Sprintf("http://127.0.0.1:%d/health", tcpAddr.Port)
	client := &http.Client{Timeout: 3 * time.Second}
	resp, err := client.Get(url)
	if err != nil {
		t.Fatalf("failed to query loopback server: %v", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(resp.Body)
		t.Fatalf("expected 200 OK, got %d: %s", resp.StatusCode, string(body))
	}
}

// TestBootstrapPrincipals_ProductionDryRun proves that TEST_PRINCIPAL_ID and TEST_PRINCIPAL_PUBLIC_KEY
// work properly in strict registry mode (ENV=production, GATEWAY_MODE=dryrun).
func TestBootstrapPrincipals_ProductionDryRun(t *testing.T) {
	pub, priv, err := crypto.GenerateKeyPair()
	if err != nil {
		t.Fatalf("failed to generate keypair: %v", err)
	}

	testPrincipalID := "usr_production_dryrun_bench"
	cfg := &config.Config{
		Env:                    "production",
		GatewayMode:            "dryrun",
		TestPrincipalID:        testPrincipalID,
		TestPrincipalPublicKey: pub,
	}

	led := ledger.NewMemoryLedger()
	nonceStore := mandate.NewMemoryNonceStore()
	registry := mandate.NewMemoryPrincipalRegistry()

	// Register bootstrap test principal exactly as main.go does
	if cfg.TestPrincipalID != "" && cfg.TestPrincipalPublicKey != "" {
		registry.RegisterPrincipal(cfg.TestPrincipalID, cfg.TestPrincipalPublicKey)
	}

	verifier := mandate.NewVerifier(nonceStore, led, registry)
	// In production, verifier is set to strict mode (auto-registration is rejected)
	verifier.SetStrictRegistry(cfg.Env == "production")

	// Create and sign mandate with this test principal
	m := &models.Mandate{
		MandateID:         "mdt_prod_dryrun_001",
		PrincipalID:       testPrincipalID,
		AgentID:           "agent_shopper_bench",
		Task:              "Bench test procure",
		Currency:          "USD",
		MaxTotal:          "100.00",
		MaxPerTxn:         "50.00",
		StepUpOver:        "25.00",
		AllowedMerchants:  []string{"merchant_staples"},
		AllowedCategories: []string{"electronics"},
		ValidFrom:         time.Now().Add(-1 * time.Hour),
		ExpiresAt:         time.Now().Add(24 * time.Hour),
		Nonce:             "nonce_prod_dryrun_001",
		PublicKey:         pub,
	}

	if err := crypto.SignMandate(priv, m); err != nil {
		t.Fatalf("failed to sign mandate: %v", err)
	}

	// Verify mandate against strict verifier
	req := &models.PaymentIntentRequest{
		Mandate:    *m,
		Amount:     "20.00",
		Currency:   "USD",
		Category:   "electronics",
		AgentID:    "agent_shopper_bench",
		MerchantID: "merchant_staples",
	}

	res, err := verifier.VerifyMandate(req, time.Now())
	if err != nil || !res.Valid {
		t.Fatalf("mandate verification failed in strict production dryrun mode: err=%v, res=%+v", err, res)
	}
}
