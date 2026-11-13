package statemachine

import (
	"errors"
	"fmt"
	"sync"
)

// PaymentState represents explicit stages in a payment lifecycle.
type PaymentState string

const (
	StateReceived       PaymentState = "received"
	StateVerified       PaymentState = "verified"
	StateScanning       PaymentState = "scanning"
	StateDecided        PaymentState = "decided"
	StateStepUpPending  PaymentState = "step_up_pending"
	StateAuthorized     PaymentState = "authorized"
	StateCaptured       PaymentState = "captured"
	StateVoided         PaymentState = "voided"
	StateRefunded       PaymentState = "refunded"
	StateRejected       PaymentState = "rejected"
)

var (
	ErrIllegalTransition = errors.New("illegal payment state transition")
	ErrPaymentNotFound   = errors.New("payment not found in state machine")
)

// AllowedTransitions specifies the valid directed graph of state movements.
var AllowedTransitions = map[PaymentState][]PaymentState{
	StateReceived: {
		StateVerified,
		StateRejected,
	},
	StateVerified: {
		StateScanning,
		StateRejected,
	},
	StateScanning: {
		StateDecided,
		StateRejected,
	},
	StateDecided: {
		StateAuthorized,
		StateStepUpPending,
		StateVoided,
		StateRejected,
	},
	StateStepUpPending: {
		StateAuthorized,
		StateVoided,
		StateRejected,
	},
	StateAuthorized: {
		StateCaptured,
		StateVoided,
	},
	StateCaptured: {
		StateRefunded,
	},
	StateVoided: {
		// Terminal state: capturing or authorizing a voided payment is strictly rejected
	},
	StateRefunded: {
		// Terminal state
	},
	StateRejected: {
		// Terminal state
	},
}

// StateMachine manages valid state movements for payments.
type StateMachine struct {
	mu     sync.RWMutex
	states map[string]PaymentState
}

func NewStateMachine() *StateMachine {
	return &StateMachine{
		states: make(map[string]PaymentState),
	}
}

// Init initializes a new payment in received state.
func (sm *StateMachine) Init(paymentID string) error {
	sm.mu.Lock()
	defer sm.mu.Unlock()

	if _, exists := sm.states[paymentID]; exists {
		return fmt.Errorf("payment %s already exists in state machine", paymentID)
	}
	sm.states[paymentID] = StateReceived
	return nil
}

// SetState directly sets the state of a payment without transition checks (for seeding and demo runs).
func (sm *StateMachine) SetState(paymentID string, state PaymentState) {
	sm.mu.Lock()
	defer sm.mu.Unlock()
	sm.states[paymentID] = state
}

// Get returns the current state of a payment.
func (sm *StateMachine) Get(paymentID string) (PaymentState, error) {
	sm.mu.RLock()
	defer sm.mu.RUnlock()

	state, ok := sm.states[paymentID]
	if !ok {
		return "", ErrPaymentNotFound
	}
	return state, nil
}

// Transition moves a payment to a new state if the transition is legal.
func (sm *StateMachine) Transition(paymentID string, to PaymentState) error {
	sm.mu.Lock()
	defer sm.mu.Unlock()

	current, ok := sm.states[paymentID]
	if !ok {
		return ErrPaymentNotFound
	}

	if current == to {
		return nil // Idempotent no-op
	}

	validNextStates := AllowedTransitions[current]
	allowed := false
	for _, target := range validNextStates {
		if target == to {
			allowed = true
			break
		}
	}

	if !allowed {
		return fmt.Errorf("%w: cannot transition payment %s from %s to %s", ErrIllegalTransition, paymentID, current, to)
	}

	sm.states[paymentID] = to
	return nil
}
