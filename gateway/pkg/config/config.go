package config

import (
	"os"
	"strconv"
	"strings"
	"time"
)

type Config struct {
	Port                string
	Env                 string
	DBDriver            string
	DatabaseURL         string
	IntelServiceURL     string
	IntelTimeout        time.Duration
	IntelAPIToken       string
	InternalLedgerToken string
	AgentAuthToken      string
	HumanAuthToken      string
	RiskBlockThreshold  float64
	RiskStepUpThreshold float64

	GatewayMode        string // "dryrun", "mock", "live"
	PayPalEnv          string
	PayPalClientID     string
	PayPalClientSecret string
	PayPalMockMode     bool
}

func Load() *Config {
	port := getEnv("PORT", "8080")
	env := getEnv("ENV", "development")
	dbDriver := getEnv("DB_DRIVER", "memory")
	dbURL := os.Getenv("DATABASE_URL")
	if dbURL != "" && dbDriver == "memory" {
		dbDriver = "postgres"
	}

	intelURL := getEnv("INTEL_SERVICE_URL", "http://localhost:8000")
	// Atul Point 2: If Render gives a bare hostport (e.g. agentguard-intel:8000), add http:// prefix
	if intelURL != "" && !strings.HasPrefix(intelURL, "http://") && !strings.HasPrefix(intelURL, "https://") {
		intelURL = "http://" + intelURL
	}

	intelTimeoutMS, _ := strconv.Atoi(getEnv("INTEL_TIMEOUT_MS", "3000"))
	if intelTimeoutMS <= 0 {
		intelTimeoutMS = 3000
	}

	blockThresh, err := strconv.ParseFloat(getEnv("RISK_THRESHOLD_BLOCK", "0.65"), 64)
	if err != nil {
		blockThresh = 0.65
	}
	stepUpThresh, err := strconv.ParseFloat(getEnv("RISK_THRESHOLD_STEPUP", "0.35"), 64)
	if err != nil {
		stepUpThresh = 0.35
	}

	clientID := os.Getenv("PAYPAL_CLIENT_ID")
	clientSecret := os.Getenv("PAYPAL_CLIENT_SECRET")
	mockMode := true
	mockEnv := os.Getenv("PAYPAL_MOCK_MODE")
	if mockEnv == "false" && clientID != "" && clientSecret != "" {
		mockMode = false
	}

	// Determine guaranteed deployment mode
	gatewayMode := strings.ToLower(strings.TrimSpace(os.Getenv("GATEWAY_MODE")))
	if gatewayMode == "" {
		if os.Getenv("DRY_RUN_MODE") == "true" || strings.ToLower(os.Getenv("PAYPAL_MODE")) == "dryrun" {
			gatewayMode = "dryrun"
		} else if mockMode {
			gatewayMode = "mock"
		} else {
			gatewayMode = "live"
		}
	}

	return &Config{
		Port:                port,
		Env:                 env,
		DBDriver:            dbDriver,
		DatabaseURL:         dbURL,
		IntelServiceURL:     intelURL,
		IntelTimeout:        time.Duration(intelTimeoutMS) * time.Millisecond,
		IntelAPIToken:       os.Getenv("INTEL_API_TOKEN"),
		InternalLedgerToken: getEnv("INTERNAL_LEDGER_TOKEN", "secret-gateway-token-123"),
		AgentAuthToken:      getEnv("AGENT_AUTH_TOKEN", "agentguard-agent-token-xyz"),
		HumanAuthToken:      getEnv("HUMAN_AUTH_TOKEN", "agentguard-human-token-xyz"),
		RiskBlockThreshold:  blockThresh,
		RiskStepUpThreshold: stepUpThresh,
		GatewayMode:         gatewayMode,
		PayPalEnv:          getEnv("PAYPAL_ENV", "sandbox"),
		PayPalClientID:     clientID,
		PayPalClientSecret: clientSecret,
		PayPalMockMode:     mockMode,
	}
}

func getEnv(key, fallback string) string {
	val := os.Getenv(key)
	if val == "" {
		return fallback
	}
	return val
}
