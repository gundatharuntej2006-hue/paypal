package api

import (
	"net/http"
)

// SetupRouter creates and returns the configured HTTP handler with all routes.
func SetupRouter(s *Server) http.Handler {
	mux := http.NewServeMux()

	// Health check
	mux.HandleFunc("GET /health", s.handleHealth)

	// Payment Intent Gateway (shopping agents)
	mux.HandleFunc("POST /v1/payments/intents", s.handlePaymentIntent)

	// Mandates (Rikita's dashboard)
	mux.HandleFunc("GET /v1/mandates", s.handleListMandates)
	mux.HandleFunc("POST /v1/mandates", s.handleCreateMandate)
	mux.HandleFunc("POST /v1/mandates/{id}/order", s.handleCreateMandateOrder)

	// Human Step-up Approvals (Rikita's dashboard)
	mux.HandleFunc("GET /v1/approvals", s.handleListApprovals)
	mux.HandleFunc("POST /v1/payments/{id}/approve", s.handleApprovePayment)
	mux.HandleFunc("POST /v1/payments/{id}/reject", s.handleRejectPayment)

	// Payments listing, details & timeline
	mux.HandleFunc("GET /v1/payments", s.handleListPayments)
	mux.HandleFunc("GET /v1/payments/{id}", s.handleGetPayment)
	mux.HandleFunc("GET /v1/payments/{id}/events", s.handleGetPaymentEvents)

	// Demo scenarios runner (happy_path, poisoned_page, budget_splitting)
	mux.HandleFunc("POST /v1/demo/run", s.handleRunDemoScenario)

	// Benchmark report
	mux.HandleFunc("GET /v1/benchmark/report", s.handleGetBenchmarkReport)

	// Server-Sent Events stream (AG Grid live updates)
	mux.HandleFunc("GET /v1/events", s.handleSSEEvents)

	// Ledger inspection
	mux.HandleFunc("GET /v1/ledger/events", s.handleListEvents)

	// Internal Ledger Hook (Atul's webhooks & reconciliation)
	mux.HandleFunc("POST /internal/ledger/events", s.handleInternalLedgerHook)

	return withCORS(mux)
}

// withCORS adds permissive cross-origin headers so the React dashboard can connect cleanly.
func withCORS(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Access-Control-Allow-Origin", "*")
		w.Header().Set("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
		w.Header().Set("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Internal-Token, X-Step-Up-Token, X-Agent-Key, PayPal-Request-Id, Last-Event-ID")

		if r.Method == http.MethodOptions {
			w.WriteHeader(http.StatusNoContent)
			return
		}

		next.ServeHTTP(w, r)
	})
}
