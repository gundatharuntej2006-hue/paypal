package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"os"
	"time"

	"gateway/pkg/ledger"
)

func main() {
	dbURL := flag.String("db", os.Getenv("DATABASE_URL"), "PostgreSQL database connection URL")
	flag.Parse()

	if *dbURL == "" {
		log.Fatal("DATABASE_URL is required to run the replay command. Use -db flag or set DATABASE_URL environment variable.")
	}

	fmt.Println("🔄 AgentGuard Ledger Replay Tool")
	fmt.Printf("Connecting to database: %s\n", *dbURL)

	pl, err := ledger.NewPostgresLedger(*dbURL)
	if err != nil {
		log.Fatalf("Failed to connect to postgres: %v", err)
	}
	defer pl.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Minute)
	defer cancel()

	start := time.Now()
	fmt.Println("Wiping projection tables and sequentially replaying all ledger events...")

	if err := pl.Replay(ctx); err != nil {
		log.Fatalf("Replay failed: %v", err)
	}

	duration := time.Since(start)
	fmt.Printf("✅ Replay completed successfully in %v! All projection tables reconstructed.\n", duration)
}
