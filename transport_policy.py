"""transport_policy.py — Transport policy and WebSocket circuit breaker.

Controls transport modes ('auto', 'websocket', 'http') and manages the
three-state circuit breaker (CLOSED -> OPEN -> HALF_OPEN -> CLOSED) for
Codex WebSocket connections.
"""
from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any


class CircuitState(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


@dataclass
class CircuitBreakerSnapshot:
    state: str
    consecutive_failures: int
    threshold: int
    cooldown_seconds: int
    cooldown_remaining_seconds: int
    enabled: bool
    action_mode: str
    status_text: str
    last_failure_reason: str | None = None
    last_failure_at: float | None = None
    last_success_at: float | None = None
    circuit_opened_at: float | None = None


class TransportCircuitBreaker:
    """Thread-safe circuit breaker for WebSocket upstream stability."""

    def __init__(
        self,
        threshold: int = 3,
        cooldown_seconds: int = 15 * 60,
        enabled: bool = True,
        action_mode: str = "auto_switch",
    ) -> None:
        self._lock = threading.RLock()
        self.threshold = max(1, int(threshold))
        self.cooldown_seconds = max(1, int(cooldown_seconds))
        self.enabled = bool(enabled)
        self.action_mode = "auto_switch" if action_mode == "auto_switch" else "notify_only"

        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._last_failure_reason: str | None = None
        self._last_failure_at: float | None = None
        self._last_success_at: float | None = None
        self._circuit_opened_at: float | None = None
        self._half_open_probe_in_flight = False

    @property
    def state(self) -> CircuitState:
        with self._lock:
            return self._evaluate_state_locked()

    def _evaluate_state_locked(self) -> CircuitState:
        if self._state == CircuitState.OPEN and self._circuit_opened_at:
            elapsed = time.time() - self._circuit_opened_at
            if elapsed >= self.cooldown_seconds:
                self._state = CircuitState.HALF_OPEN
                self._half_open_probe_in_flight = False
        return self._state

    def record_success(self) -> CircuitState:
        """Record a successful WS connection/handshake."""
        with self._lock:
            self._consecutive_failures = 0
            self._last_success_at = time.time()
            self._state = CircuitState.CLOSED
            self._circuit_opened_at = None
            self._half_open_probe_in_flight = False
            return self._state

    def record_failure(self, reason: str = "") -> CircuitState:
        """Record a failed WS connection or handshake error."""
        with self._lock:
            self._consecutive_failures += 1
            self._last_failure_at = time.time()
            self._last_failure_reason = str(reason or "websocket failure")

            if self._state == CircuitState.HALF_OPEN:
                # A failed half-open trial immediately reopens the circuit.
                self._state = CircuitState.OPEN
                self._circuit_opened_at = time.time()
                self._half_open_probe_in_flight = False
            elif self.enabled and self._consecutive_failures >= self.threshold:
                self._state = CircuitState.OPEN
                self._circuit_opened_at = time.time()
                self._half_open_probe_in_flight = False

            return self._state

    def should_allow_websocket(self, transport_mode: str = "auto") -> bool:
        """Decide whether a new WebSocket request should proceed."""
        mode = (transport_mode or "auto").lower().strip()
        if mode == "websocket":
            # Manual forced WebSocket mode: never break or downgrade
            return True
        if mode == "http":
            # Manual forced HTTP mode: never allow WebSocket
            return False

        # Auto mode:
        if not self.enabled:
            return True

        with self._lock:
            current = self._evaluate_state_locked()
            # notify_only tracks degradation but must never change transport.
            if self.action_mode == "notify_only":
                return True
            if current == CircuitState.OPEN:
                return False
            if current == CircuitState.HALF_OPEN:
                # Permit exactly one trial connection. Parallel requests wait
                # for that trial to resolve rather than stampeding upstream.
                if self._half_open_probe_in_flight:
                    return False
                self._half_open_probe_in_flight = True
            return True

    def cooldown_remaining_seconds(self) -> int:
        with self._lock:
            if self._state == CircuitState.OPEN and self._circuit_opened_at:
                remaining = self.cooldown_seconds - (time.time() - self._circuit_opened_at)
                return max(0, int(remaining))
            return 0

    def status_message(self, transport_mode: str = "auto") -> str:
        mode = (transport_mode or "auto").lower().strip()
        if mode == "websocket":
            return "WebSocket (强制保持)"
        if mode == "http":
            return "强制 HTTP 模式 (已禁用 WebSocket)"

        with self._lock:
            current = self._evaluate_state_locked()
            if not self.enabled:
                return "自动模式 (熔断器已停用)"
            if current == CircuitState.OPEN:
                rem_sec = self.cooldown_remaining_seconds()
                rem_min = max(1, (rem_sec + 59) // 60)
                action_desc = "已触发自动降级策略" if self.action_mode == "auto_switch" else "仅提示，不改变传输"
                return f"WebSocket 已临时熔断 | {action_desc} | {rem_min} 分钟后重新探测"
            if current == CircuitState.HALF_OPEN:
                return "WebSocket 处于半开试探状态 (HALF_OPEN)"
            return "WebSocket 运行正常 (CLOSED)"

    def configure(
        self,
        *,
        threshold: int | None = None,
        cooldown_minutes: int | None = None,
        cooldown_seconds: int | None = None,
        enabled: bool | None = None,
        action_mode: str | None = None,
    ) -> None:
        with self._lock:
            if threshold is not None:
                self.threshold = max(1, int(threshold))
            if cooldown_seconds is not None:
                self.cooldown_seconds = max(1, int(cooldown_seconds))
            elif cooldown_minutes is not None:
                self.cooldown_seconds = max(10, int(cooldown_minutes) * 60)
            if enabled is not None:
                self.enabled = bool(enabled)
                if not self.enabled:
                    self._state = CircuitState.CLOSED
                    self._consecutive_failures = 0
                    self._circuit_opened_at = None
                    self._half_open_probe_in_flight = False
            if action_mode is not None:
                self.action_mode = "auto_switch" if action_mode == "auto_switch" else "notify_only"

    def reset(self) -> None:
        with self._lock:
            self._state = CircuitState.CLOSED
            self._consecutive_failures = 0
            self._circuit_opened_at = None
            self._half_open_probe_in_flight = False

    def snapshot(self, transport_mode: str = "auto") -> dict[str, Any]:
        with self._lock:
            current = self._evaluate_state_locked()
            remaining = 0
            if current == CircuitState.OPEN and self._circuit_opened_at:
                remaining = max(0, int(self.cooldown_seconds - (time.time() - self._circuit_opened_at)))

            snap = CircuitBreakerSnapshot(
                state=current.value,
                consecutive_failures=self._consecutive_failures,
                threshold=self.threshold,
                cooldown_seconds=self.cooldown_seconds,
                cooldown_remaining_seconds=remaining,
                enabled=self.enabled,
                action_mode=self.action_mode,
                status_text=self.status_message(transport_mode),
                last_failure_reason=self._last_failure_reason,
                last_failure_at=self._last_failure_at,
                last_success_at=self._last_success_at,
                circuit_opened_at=self._circuit_opened_at,
            )
            return asdict(snap)


GLOBAL_CIRCUIT_BREAKER = TransportCircuitBreaker()
