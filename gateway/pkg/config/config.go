package config

import (
	"errors"
	"net"
	"os"
	"strconv"
	"strings"
	"time"
)

type Config struct {
	Port                string
	BindAddr            string
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
	DemoMode           bool
	PayPalEnv          string
	PayPalClientID     string
	PayPalClientSecret string
	PayPalMockMode     bool

	// Pre-registered Principals for secure bootstrap/test execution
	BootstrapPrincipals    string
	TestPrincipalID        string
	TestPrincipalPublicKey string
}

func Load() *Config {
	port := getEnv("PORT", "8080")
	bindAddr := os.Getenv("GATEWAY_BIND_ADDR")
	env := getEnv("ENV", "development")
	dbDriver := getEnv("DB_DRIVER", "memory")
	dbURL := os.Getenv("DATABASE_URL")
	if dbURL != "" && dbDriver == "memory" {
		dbDriver = "postgres"
	}

	intelURL := getEnv("INTEL_SERVICE_URL", "http://localhost:8000")
	// If Render gives a bare hostport (e.g. agentguard-intel:8000), add http:// prefix
	if intelURL != "" && !strings.HasPrefix(intelURL, "http://") && !strings.HasPrefix(intelURL, "https://") {
		intelURL = "http://" + intelURL
	}

	intelTimeoutMS, _ := strconv.Atoi(getEnv("INTEL_TIMEOUT_MS", "7000"))
	if intelTimeoutMS <= 0 {
		intelTimeoutMS = 7000
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

	demoMode := os.Getenv("DEMO_MODE") == "true" || os.Getenv("DEMO_SANDBOX") == "1" || os.Getenv("ENABLE_DEMO") == "true"

	return &Config{
		Port:                port,
		BindAddr:            bindAddr,
		Env:                 env,
		DBDriver:            dbDriver,
		DatabaseURL:         dbURL,
		IntelServiceURL:     intelURL,
		IntelTimeout:        time.Duration(intelTimeoutMS) * time.Millisecond,
		IntelAPIToken:       os.Getenv("INTEL_API_TOKEN"),
		InternalLedgerToken: os.Getenv("INTERNAL_LEDGER_TOKEN"),
		AgentAuthToken:      os.Getenv("AGENT_AUTH_TOKEN"),
		HumanAuthToken:      os.Getenv("HUMAN_AUTH_TOKEN"),
		RiskBlockThreshold:  blockThresh,
		RiskStepUpThreshold: stepUpThresh,
		GatewayMode:         gatewayMode,
		DemoMode:            demoMode,
		PayPalEnv:           getEnv("PAYPAL_ENV", "sandbox"),
		PayPalClientID:      clientID,
		PayPalClientSecret:  clientSecret,
		PayPalMockMode:      mockMode,

		BootstrapPrincipals:    os.Getenv("BOOTSTRAP_PRINCIPALS"),
		TestPrincipalID:        os.Getenv("TEST_PRINCIPAL_ID"),
		TestPrincipalPublicKey: os.Getenv("TEST_PRINCIPAL_PUBLIC_KEY"),
	}
}

// Validate checks that all required security tokens and credentials are present,
// failing immediately on missing secrets in production or live execution mode.
func (c *Config) Validate() error {
	if c.Env == "production" {
		if c.AgentAuthToken == "" {
			return errors.New("AGENT_AUTH_TOKEN is required in production: missing secret")
		}
		if c.HumanAuthToken == "" {
			return errors.New("HUMAN_AUTH_TOKEN is required in production: missing secret")
		}
		if c.InternalLedgerToken == "" {
			return errors.New("INTERNAL_LEDGER_TOKEN is required in production: missing secret")
		}
		if c.IntelAPIToken == "" {
			return errors.New("INTEL_API_TOKEN is required in production: missing secret")
		}
		if c.GatewayMode == "live" && (c.PayPalClientID == "" || c.PayPalClientSecret == "") {
			return errors.New("PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET are required in live production mode")
		}
	}
	return nil
}

func getEnv(key, fallback string) string {
	val := os.Getenv(key)
	if val == "" {
		return fallback
	}
	return val
}

// ListenAddr returns the combined host:port string for binding HTTP listeners.
// If BindAddr is configured (e.g. "127.0.0.1"), it produces "127.0.0.1:<Port>".
// If BindAddr is empty, it returns ":<Port>" which binds across all available interfaces.
func (c *Config) ListenAddr() string {
	cleanPort := strings.TrimPrefix(strings.TrimSpace(c.Port), ":")
	if cleanPort == "" {
		cleanPort = "8080"
	}
	trimmedBind := strings.TrimSpace(c.BindAddr)
	if trimmedBind != "" {
		return net.JoinHostPort(trimmedBind, cleanPort)
	}
	return ":" + cleanPort
}
