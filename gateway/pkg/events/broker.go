package events

import (
	"sync"

	"gateway/pkg/models"
)

// Subscriber represents an active SSE client connection.
type Subscriber struct {
	ch              chan models.LedgerEvent
	paymentIDFilter string
}

// Broker distributes ledger events to connected SSE listeners.
type Broker struct {
	mu          sync.RWMutex
	subscribers map[*Subscriber]struct{}
}

func NewBroker() *Broker {
	return &Broker{
		subscribers: make(map[*Subscriber]struct{}),
	}
}

// Subscribe registers a new listener, returning an event channel and an unsubscribe func.
func (b *Broker) Subscribe(paymentIDFilter string) (<-chan models.LedgerEvent, func()) {
	sub := &Subscriber{
		ch:              make(chan models.LedgerEvent, 100),
		paymentIDFilter: paymentIDFilter,
	}

	b.mu.Lock()
	b.subscribers[sub] = struct{}{}
	b.mu.Unlock()

	unsubscribe := func() {
		b.mu.Lock()
		defer b.mu.Unlock()
		if _, ok := b.subscribers[sub]; ok {
			delete(b.subscribers, sub)
			close(sub.ch)
		}
	}

	return sub.ch, unsubscribe
}

// Publish broadcasts an event to all interested subscribers.
func (b *Broker) Publish(event models.LedgerEvent) {
	b.mu.RLock()
	defer b.mu.RUnlock()

	for sub := range b.subscribers {
		// Filter by payment_id if requested
		if sub.paymentIDFilter != "" {
			if event.PaymentID == nil || *event.PaymentID != sub.paymentIDFilter {
				continue
			}
		}

		select {
		case sub.ch <- event:
		default:
			// Non-blocking drop if client is saturated
		}
	}
}
