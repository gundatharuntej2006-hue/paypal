package main

import (
	"context"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"gateway/pkg/api"
	"gateway/pkg/config"
	"gateway/pkg/events"
	"gateway/pkg/intelclient"
	"gateway/pkg/ledger"
	"gateway/pkg/mandate"
	"gateway/pkg/paypal"
	"gateway/pkg/policy"
	"gateway/pkg/statemachine"
)

func main() {
	cfg := config.Load()
	if err := cfg.Validate(); err != nil {
		log.Fatalf("[gateway] configuration startup error: %v", err)
	}
	log.Printf("[gateway] starting AgentGuard Gateway on port %s (env: %s, mode: %s)", cfg.Port, cfg.Env, cfg.GatewayMode)

	// 1. Initialize Ledger
	var led ledger.Ledger
	var err error
	if cfg.DBDriver == "postgres" && cfg.DatabaseURL != "" {
		log.Printf("[gateway] connecting to PostgreSQL ledger...")
		led, err = ledger.NewPostgresLedger(cfg.DatabaseURL)
		if err != nil {
			log.Fatalf("[gateway] failed to connect to PostgreSQL: %v", err)
		}
	} else {
		log.Printf("[gateway] using In-Memory Ledger (development mode)")
		led = ledger.NewMemoryLedger()
	}
	defer led.Close()

	// 2. Initialize Mandate Verifier, Nonce Store & Principal Registry (Fix 2)
	nonceStore := mandate.NewMemoryNonceStore()
	principalRegistry := mandate.NewMemoryPrincipalRegistry()

	// Pre-register bootstrap principals from environment configuration
	if cfg.BootstrapPrincipals != "" {
		pairs := strings.Split(cfg.BootstrapPrincipals, ",")
		for _, pair := range pairs {
			parts := strings.Split(strings.TrimSpace(pair), ":")
			if len(parts) == 2 && parts[0] != "" && parts[1] != "" {
				principalRegistry.RegisterPrincipal(parts[0], parts[1])
				log.Printf("[gateway] 🔑 pre-registered bootstrap principal '%s'", parts[0])
			}
		}
	}
	if cfg.TestPrincipalID != "" && cfg.TestPrincipalPublicKey != "" {
		principalRegistry.RegisterPrincipal(cfg.TestPrincipalID, cfg.TestPrincipalPublicKey)
		log.Printf("[gateway] 🔑 pre-registered test principal '%s'", cfg.TestPrincipalID)
	}
	// In development/dry-run mode, also pre-seed standard test vector principals
	if cfg.Env != "production" {
		principalRegistry.RegisterPrincipal("principal_cross_lang", "ebVWLo/mVPlAeLES6KmLp5AfhTrmlb7X4OORC60ElmQ=")
		principalRegistry.RegisterPrincipal("user_vivek", "+QrsGHy80RVVR5btBnZ26hkL+aJ9ZbqWJVAffz7v5gE=")
	}

	verifier := mandate.NewVerifier(nonceStore, led, principalRegistry)
	verifier.SetStrictRegistry(cfg.Env == "production")

	// 3. Initialize State Machine
	sm := statemachine.NewStateMachine()

	// 4. Initialize Policy Decision Engine
	decConfig := policy.DecisionConfig{
		RiskBlockThreshold:  cfg.RiskBlockThreshold,
		RiskStepUpThreshold: cfg.RiskStepUpThreshold,
	}
	decisionEngine := policy.NewDecisionEngine(decConfig)

	// 5. Initialize PayPal Client (with guaranteed payment-incapable mode support)
	var ppClient paypal.Client
	if cfg.GatewayMode == "dryrun" {
		log.Printf("[gateway] 🔒 GATEWAY MODE: DRY-RUN (payment-incapable, zero funds movement guaranteed)")
		ppClient = paypal.NewDryRunClient()
	} else if cfg.PayPalMockMode {
		log.Printf("[gateway] PayPal running in MOCK mode (no live sandbox credentials needed)")
		ppClient = paypal.NewMockClient()
	} else {
		log.Printf("[gateway] PayPal running in live SANDBOX mode with Client ID: %s", cfg.PayPalClientID)
		ppClient = paypal.NewSandboxClient(paypal.Config{
			Env:          cfg.PayPalEnv,
			ClientID:     cfg.PayPalClientID,
			ClientSecret: cfg.PayPalClientSecret,
			HTTPTimeout:  10 * time.Second,
		})
	}

	// 6. Initialize Intelligence Client (Atul's service)
	var intel intelclient.IntelClient
	if cfg.IntelServiceURL != "" {
		log.Printf("[gateway] connecting to Intelligence Service at %s (timeout: %v)", cfg.IntelServiceURL, cfg.IntelTimeout)
		intel = intelclient.NewHTTPIntelClient(cfg.IntelServiceURL, cfg.IntelAPIToken, cfg.IntelTimeout)
	} else {
		log.Printf("[gateway] using Stub Intelligence Client")
		intel = intelclient.NewStubIntelClient()
	}

	// 7. Initialize SSE Broker
	broker := events.NewBroker()

	// 8. Build Router & Server
	server := api.NewServer(cfg, led, verifier, sm, decisionEngine, ppClient, intel, broker)
	handler := api.SetupRouter(server)

	httpServer := &http.Server{
		Addr:         ":" + cfg.Port,
		Handler:      handler,
		ReadTimeout:  15 * time.Second,
		WriteTimeout: 15 * time.Second,
	}

	// Graceful shutdown handling
	stop := make(chan os.Signal, 1)
	signal.Notify(stop, os.Interrupt, syscall.SIGTERM)

	go func() {
		fmt.Printf("\n🛡️  AgentGuard Payment Firewall Gateway listening on http://localhost:%s\n", cfg.Port)
		fmt.Printf("   - Health check:     GET  /health\n")
		fmt.Printf("   - Payment intents:  POST /v1/payments/intents\n")
		fmt.Printf("   - SSE event stream: GET  /v1/events\n")
		fmt.Printf("   - Ledger hook:      POST /internal/ledger/events\n\n")

		if err := httpServer.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			log.Fatalf("[gateway] server listen error: %v", err)
		}
	}()

	<-stop
	log.Printf("[gateway] shutting down gracefully...")
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	if err := httpServer.Shutdown(ctx); err != nil {
		log.Printf("[gateway] server shutdown error: %v", err)
	}
	log.Printf("[gateway] gateway stopped.")
}
