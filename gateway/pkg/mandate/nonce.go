package mandate

import (
	"errors"
	"sync"
	"time"
)

var ErrNonceReplayed = errors.New("mandate nonce already consumed: replay attack detected")

// NonceStore tracks consumed nonces to prevent replay attacks.
type NonceStore interface {
	CheckAndConsume(nonce, mandateID string) (bool, error)
}

// MemoryNonceStore provides thread-safe in-memory nonce tracking.
type MemoryNonceStore struct {
	mu     sync.RWMutex
	nonces map[string]nonceRecord
}

type nonceRecord struct {
	mandateID string
	usedAt    time.Time
}

func NewMemoryNonceStore() *MemoryNonceStore {
	return &MemoryNonceStore{
		nonces: make(map[string]nonceRecord),
	}
}

func (s *MemoryNonceStore) CheckAndConsume(nonce, mandateID string) (bool, error) {
	if nonce == "" {
		return false, errors.New("empty nonce")
	}

	s.mu.Lock()
	defer s.mu.Unlock()

	if _, exists := s.nonces[nonce]; exists {
		return false, ErrNonceReplayed
	}

	s.nonces[nonce] = nonceRecord{
		mandateID: mandateID,
		usedAt:    time.Now(),
	}
	return true, nil
}
