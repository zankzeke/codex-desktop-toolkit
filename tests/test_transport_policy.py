from __future__ import annotations

import time
from transport_policy import CircuitState, TransportCircuitBreaker


def test_circuit_breaker_closed_initial_state():
    cb = TransportCircuitBreaker(threshold=3, cooldown_seconds=60)
    assert cb.state == CircuitState.CLOSED
    assert cb.should_allow_websocket("auto") is True
    assert cb.should_allow_websocket("websocket") is True
    assert cb.should_allow_websocket("http") is False


def test_circuit_breaker_transitions_to_open_after_threshold_failures():
    cb = TransportCircuitBreaker(threshold=3, cooldown_seconds=60)
    cb.record_failure("test 1")
    assert cb.state == CircuitState.CLOSED
    assert cb.should_allow_websocket("auto") is True

    cb.record_failure("test 2")
    assert cb.state == CircuitState.CLOSED
    assert cb.should_allow_websocket("auto") is True

    cb.record_failure("test 3")
    assert cb.state == CircuitState.OPEN
    # Auto mode should disallow websocket when OPEN
    assert cb.should_allow_websocket("auto") is False
    # Manual websocket mode is never blocked
    assert cb.should_allow_websocket("websocket") is True
    # Cooldown remaining should be positive
    assert cb.cooldown_remaining_seconds() > 0


def test_circuit_breaker_cooldown_and_half_open():
    # Set a tiny cooldown for testing
    cb = TransportCircuitBreaker(threshold=2, cooldown_seconds=1)
    cb.record_failure("f1")
    cb.record_failure("f2")
    assert cb.state == CircuitState.OPEN

    # Wait for cooldown to expire
    time.sleep(1.05)
    assert cb.state == CircuitState.HALF_OPEN
    assert cb.should_allow_websocket("auto") is True

    # Trial success in half-open state closes circuit
    cb.record_success()
    assert cb.state == CircuitState.CLOSED
    assert cb.should_allow_websocket("auto") is True


def test_circuit_breaker_half_open_failure_reopens():
    cb = TransportCircuitBreaker(threshold=2, cooldown_seconds=1)
    cb.record_failure("f1")
    cb.record_failure("f2")
    assert cb.state == CircuitState.OPEN

    time.sleep(1.05)
    assert cb.state == CircuitState.HALF_OPEN

    # Trial failure in half-open state reopens immediately
    cb.record_failure("trial fail")
    assert cb.state == CircuitState.OPEN
    assert cb.should_allow_websocket("auto") is False


def test_circuit_breaker_snapshot_and_status_message():
    cb = TransportCircuitBreaker(threshold=3, cooldown_seconds=900)
    cb.record_failure("err1")
    cb.record_failure("err2")
    cb.record_failure("err3")

    snap = cb.snapshot("auto")
    assert snap["state"] == "OPEN"
    assert snap["consecutive_failures"] == 3
    assert "已临时熔断" in snap["status_text"]

    cb.reset()
    assert cb.state == CircuitState.CLOSED
    assert cb.snapshot("auto")["consecutive_failures"] == 0
