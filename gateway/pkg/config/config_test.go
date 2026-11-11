package config_test

import (
	"net"
	"os"
	"testing"

	"gateway/pkg/config"
)

func TestConfig_ListenAddr(t *testing.T) {
	tests := []struct {
		name     string
		port     string
		bindAddr string
		expected string
	}{
		{
			name:     "default empty bind addr binds to all interfaces",
			port:     "8080",
			bindAddr: "",
			expected: ":8080",
		},
		{
			name:     "explicit loopback bind addr",
			port:     "8080",
			bindAddr: "127.0.0.1",
			expected: "127.0.0.1:8080",
		},
		{
			name:     "custom port with loopback",
			port:     "9090",
			bindAddr: "127.0.0.1",
			expected: "127.0.0.1:9090",
		},
		{
			name:     "port with leading colon handled defensively",
			port:     ":8085",
			bindAddr: "127.0.0.1",
			expected: "127.0.0.1:8085",
		},
		{
			name:     "explicit 0.0.0.0 bind addr",
			port:     "8080",
			bindAddr: "0.0.0.0",
			expected: "0.0.0.0:8080",
		},
		{
			name:     "ipv6 loopback bind addr",
			port:     "8080",
			bindAddr: "::1",
			expected: "[::1]:8080",
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			cfg := &config.Config{
				Port:     tc.port,
				BindAddr: tc.bindAddr,
			}
			addr := cfg.ListenAddr()
			if addr != tc.expected {
				t.Fatalf("expected listen addr %q, got %q", tc.expected, addr)
			}
		})
	}
}

func TestConfig_LoadFromEnvironment(t *testing.T) {
	os.Setenv("PORT", "7777")
	os.Setenv("GATEWAY_BIND_ADDR", "127.0.0.1")
	os.Setenv("GATEWAY_MODE", "dryrun")
	os.Setenv("TEST_PRINCIPAL_ID", "bench_p1")
	os.Setenv("TEST_PRINCIPAL_PUBLIC_KEY", "pubkey_bench_p1")
	defer func() {
		os.Unsetenv("PORT")
		os.Unsetenv("GATEWAY_BIND_ADDR")
		os.Unsetenv("GATEWAY_MODE")
		os.Unsetenv("TEST_PRINCIPAL_ID")
		os.Unsetenv("TEST_PRINCIPAL_PUBLIC_KEY")
	}()

	cfg := config.Load()
	if cfg.Port != "7777" {
		t.Fatalf("expected port 7777, got %s", cfg.Port)
	}
	if cfg.BindAddr != "127.0.0.1" {
		t.Fatalf("expected bind addr 127.0.0.1, got %s", cfg.BindAddr)
	}
	if cfg.ListenAddr() != "127.0.0.1:7777" {
		t.Fatalf("expected listen addr 127.0.0.1:7777, got %s", cfg.ListenAddr())
	}
	if cfg.TestPrincipalID != "bench_p1" || cfg.TestPrincipalPublicKey != "pubkey_bench_p1" {
		t.Fatalf("test principal fields not loaded properly")
	}
}

func TestListener_LoopbackBindingVerification(t *testing.T) {
	cfg := &config.Config{
		Port:     "0", // Allocate random available port
		BindAddr: "127.0.0.1",
	}

	addr := cfg.ListenAddr()
	ln, err := net.Listen("tcp", addr)
	if err != nil {
		t.Fatalf("failed to bind to loopback: %v", err)
	}
	defer ln.Close()

	tcpAddr, ok := ln.Addr().(*net.TCPAddr)
	if !ok {
		t.Fatalf("expected *net.TCPAddr, got %T", ln.Addr())
	}

	// Verify listener is strictly bound to IPv4 127.0.0.1
	if !tcpAddr.IP.Equal(net.ParseIP("127.0.0.1")) {
		t.Fatalf("expected listener bound to 127.0.0.1, got %s", tcpAddr.IP.String())
	}
	if tcpAddr.Port <= 0 {
		t.Fatalf("expected valid allocated port, got %d", tcpAddr.Port)
	}
}

func TestListener_InvalidBindFailsClearly(t *testing.T) {
	// Attempt to bind to a non-existent host/interface address (e.g. 192.0.2.1 - RFC 5737 TEST-NET-1)
	cfg := &config.Config{
		Port:     "8080",
		BindAddr: "192.0.2.1",
	}

	addr := cfg.ListenAddr()
	ln, err := net.Listen("tcp", addr)
	if err == nil {
		ln.Close()
		t.Fatalf("expected net.Listen on unassignable address %s to fail, but it succeeded", addr)
	}
	t.Logf("correctly caught bind failure on invalid address: %v", err)
}
