package statemachine

import (
	"errors"
	"testing"
)

func TestStateMachineAcceptance(t *testing.T) {
	sm := NewStateMachine()
	paymentID := "pay_test_state_1"

	err := sm.Init(paymentID)
	if err != nil {
		t.Fatalf("failed to init state machine: %v", err)
	}

	state, _ := sm.Get(paymentID)
	if state != StateReceived {
		t.Fatalf("expected state received, got %s", state)
	}

	// Legal flow: received -> verified -> scanning -> decided -> authorized -> voided
	if err := sm.Transition(paymentID, StateVerified); err != nil {
		t.Fatalf("failed transition to verified: %v", err)
	}
	if err := sm.Transition(paymentID, StateScanning); err != nil {
		t.Fatalf("failed transition to scanning: %v", err)
	}
	if err := sm.Transition(paymentID, StateDecided); err != nil {
		t.Fatalf("failed transition to decided: %v", err)
	}
	if err := sm.Transition(paymentID, StateAuthorized); err != nil {
		t.Fatalf("failed transition to authorized: %v", err)
	}
	if err := sm.Transition(paymentID, StateVoided); err != nil {
		t.Fatalf("failed transition to voided: %v", err)
	}

	// SPEC ACCEPTANCE TEST: Capturing a voided payment is rejected
	err = sm.Transition(paymentID, StateCaptured)
	if err == nil {
		t.Fatal("expected error capturing a voided payment, but transition succeeded!")
	}
	if !errors.Is(err, ErrIllegalTransition) {
		t.Fatalf("expected ErrIllegalTransition, got %v", err)
	}

	// Idempotent transition to voided
	if err := sm.Transition(paymentID, StateVoided); err != nil {
		t.Fatalf("idempotent transition to same state should succeed, got: %v", err)
	}
}

func TestHappyPathCaptureAndRefund(t *testing.T) {
	sm := NewStateMachine()
	paymentID := "pay_test_capture_refund"

	if err := sm.Init(paymentID); err != nil {
		t.Fatal(err)
	}
	if err := sm.Transition(paymentID, StateVerified); err != nil {
		t.Fatal(err)
	}
	if err := sm.Transition(paymentID, StateScanning); err != nil {
		t.Fatal(err)
	}
	if err := sm.Transition(paymentID, StateDecided); err != nil {
		t.Fatal(err)
	}
	if err := sm.Transition(paymentID, StateAuthorized); err != nil {
		t.Fatal(err)
	}
	if err := sm.Transition(paymentID, StateCaptured); err != nil {
		t.Fatal(err)
	}
	if err := sm.Transition(paymentID, StateRefunded); err != nil {
		t.Fatal(err)
	}

	// Cannot void an already refunded payment
	err := sm.Transition(paymentID, StateVoided)
	if err == nil || !errors.Is(err, ErrIllegalTransition) {
		t.Fatalf("expected ErrIllegalTransition when voiding refunded payment, got: %v", err)
	}
}
