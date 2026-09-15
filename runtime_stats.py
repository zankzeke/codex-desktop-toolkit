"""In-memory, privacy-safe runtime telemetry for the local proxy."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from error_classifier import classify_error, classify_ws_close

WS_BREAKER_THRESHOLD = 3


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_path(path: str) -> str:
    return path.split("?", 1)[0][:240]


class RuntimeStats:
    """Small in-memory status store exposed only on localhost via /stats."""

    def __init__(self) -> None:
        self.started_at = _now()
        self.requests_total = 0
        self.responses_total = 0
        self.status_counts: dict[str, int] = {}
        self.last_request_at: str | None = None
        self.last_request_method: str | None = None
        self.last_request_path: str | None = None
        self.last_response_status: int | None = None
        self.last_success_at: str | None = None
        self.last_transport: str | None = None
        self.id_fixes_total = 0
        self.reasoning_drops_total = 0
        self.last_error: dict[str, Any] | None = None

        self.ws_active = 0
        self.ws_handshakes = 0
        self.ws_reconnects = 0
        self.ws_failures = 0
        self.ws_consecutive_failures = 0
        self.ws_breaker_threshold = WS_BREAKER_THRESHOLD
        self.ws_breaker_tripped = False
        self.ws_breaker_tripped_at: str | None = None
        self.ws_last_failure_at: str | None = None
        self.ws_last_connected_at: str | None = None
        self.ws_last_disconnected_at: str | None = None
        self.ws_last_close_code: int | None = None
        self.ws_last_close_category: str | None = None

    def record_request(self, method: str, path: str, transport: str = "http") -> None:
        self.requests_total += 1
        self.last_request_at = _now()
        self.last_request_method = method
        self.last_request_path = _safe_path(path)
        self.last_transport = transport

    def record_response(self, status: int, *, message: str | None = None, event_type: str | None = None) -> None:
        self.responses_total += 1
        self.last_response_status = int(status)
        key = str(int(status))
        self.status_counts[key] = self.status_counts.get(key, 0) + 1
        if 200 <= status < 400:
            self.last_success_at = _now()
        else:
            info = classify_error(status, message, event_type=event_type)
            self.last_error = {**info, "at": _now()}

    def record_error(self, status: int | None = None, message: str | None = None, *, event_type: str | None = None) -> None:
        info = classify_error(status, message, event_type=event_type)
        self.last_error = {**info, "at": _now()}

    def record_rewrite(self, fixes: int = 0, drops: int = 0) -> None:
        self.id_fixes_total += int(fixes or 0)
        self.reasoning_drops_total += int(drops or 0)

    def record_sse(self) -> None:
        self.last_transport = "sse"

    def ws_connected(self) -> None:
        # Count a reconnect only after all earlier WS connections became idle.
        # A second simultaneous connection is ordinary parallel traffic.
        was_idle = self.ws_active == 0
        if self.ws_handshakes > 0 and was_idle:
            self.ws_reconnects += 1
        self.ws_handshakes += 1
        self.ws_active += 1
        self.ws_consecutive_failures = 0
        self.ws_breaker_tripped = False
        self.ws_breaker_tripped_at = None
        self.last_transport = "websocket"
        self.ws_last_connected_at = _now()

    def ws_failed(self, message: str | None = None) -> None:
        self.ws_failures += 1
        self.ws_consecutive_failures += 1
        self.ws_last_failure_at = _now()
        if self.ws_consecutive_failures >= self.ws_breaker_threshold:
            if not self.ws_breaker_tripped:
                self.ws_breaker_tripped_at = _now()
            self.ws_breaker_tripped = True
        self.record_error(502, message or "websocket connection failed")

    def ws_closed(self, code: int | None) -> None:
        self.ws_active = max(0, self.ws_active - 1)
        self.ws_last_disconnected_at = _now()
        self.ws_last_close_code = int(code) if isinstance(code, int) else None
        info = classify_ws_close(self.ws_last_close_code)
        self.ws_last_close_category = info["category"]
        if info["category"] != "ok":
            self.last_error = {**info, "at": _now()}
            # Abnormal closes are meaningful for fallback guidance, but they do
            # not represent failed upstream handshakes and therefore do not
            # increment the handshake circuit breaker counter here.

    def snapshot(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at,
            "traffic_verified": self.requests_total > 0,
            "requests_total": self.requests_total,
            "responses_total": self.responses_total,
            "status_counts": dict(self.status_counts),
            "last_request_at": self.last_request_at,
            "last_request_method": self.last_request_method,
            "last_request_path": self.last_request_path,
            "last_response_status": self.last_response_status,
            "last_success_at": self.last_success_at,
            "last_transport": self.last_transport,
            "id_fixes_total": self.id_fixes_total,
            "reasoning_drops_total": self.reasoning_drops_total,
            "last_error": dict(self.last_error) if self.last_error else None,
            "websocket": {
                "active": self.ws_active,
                "handshakes": self.ws_handshakes,
                "reconnects": self.ws_reconnects,
                "failures": self.ws_failures,
                "consecutive_failures": self.ws_consecutive_failures,
                "breaker_threshold": self.ws_breaker_threshold,
                "breaker_tripped": self.ws_breaker_tripped,
                "breaker_tripped_at": self.ws_breaker_tripped_at,
                "last_failure_at": self.ws_last_failure_at,
                "last_connected_at": self.ws_last_connected_at,
                "last_disconnected_at": self.ws_last_disconnected_at,
                "last_close_code": self.ws_last_close_code,
                "last_close_category": self.ws_last_close_category,
            },
        }
