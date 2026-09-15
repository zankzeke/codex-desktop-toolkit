from __future__ import annotations

import asyncio
import inspect
import json
import os
import sys
from unittest.mock import patch

import aiohttp
from aiohttp import ClientSession, web

from network_diagnostics import check_websocket_connectivity_async, evaluate_diagnostics
from proxy import make_app
from proxy_discovery import test_proxy_ws_async as probe_proxy_ws_async
from startup_manager import NotificationManager
from support_bundle import redact_text_content
from transport_policy import CircuitState, TransportCircuitBreaker


def test_notify_only_breaker_never_blocks_transport():
    cb = TransportCircuitBreaker(threshold=1, cooldown_seconds=60, action_mode="notify_only")
    cb.record_failure("boom")
    assert cb.state == CircuitState.OPEN
    assert cb.should_allow_websocket("auto") is True


def test_half_open_allows_only_one_concurrent_probe():
    cb = TransportCircuitBreaker(threshold=1, cooldown_seconds=1, action_mode="auto_switch")
    cb.record_failure("boom")
    cb._circuit_opened_at -= 2
    assert cb.state == CircuitState.HALF_OPEN
    assert cb.should_allow_websocket("auto") is True
    assert cb.should_allow_websocket("auto") is False
    cb.record_success()
    assert cb.should_allow_websocket("auto") is True


def test_proxy_uses_configured_breaker_and_live_control_endpoints():
    async def scenario():
        hits = 0
        upstream = web.Application()

        async def reject_ws(request):
            nonlocal hits
            hits += 1
            return web.Response(status=503, text="no ws")

        upstream.router.add_get("/responses", reject_ws)
        ur = web.AppRunner(upstream)
        await ur.setup()
        us = web.TCPSite(ur, "127.0.0.1", 0)
        await us.start()
        up_port = us._server.sockets[0].getsockname()[1]

        app = make_app(
            f"http://127.0.0.1:{up_port}",
            "safe",
            0,
            circuit_threshold=1,
            circuit_cooldown_seconds=60,
        )
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]

        try:
            async with ClientSession() as session:
                for expected in (502, 503):
                    try:
                        await session.ws_connect(f"http://127.0.0.1:{port}/responses")
                        raise AssertionError("WS unexpectedly connected")
                    except aiohttp.WSServerHandshakeError as exc:
                        assert exc.status == expected
                assert hits == 1

                async with session.post(
                    f"http://127.0.0.1:{port}/control/circuit/reset", json={}
                ) as resp:
                    assert resp.status == 200
                async with session.post(
                    f"http://127.0.0.1:{port}/control/transport", json={"mode": "http"}
                ) as resp:
                    body = await resp.json()
                    assert body["transport_mode"] == "http"
                async with session.get(f"http://127.0.0.1:{port}/health/details") as resp:
                    details = await resp.json()
                    assert details["transport_mode"] == "http"
        finally:
            await runner.cleanup()
            await ur.cleanup()

    asyncio.run(scenario())


def test_auth_rejection_is_not_reported_as_ws_upgrade_success():
    async def scenario():
        app = web.Application()

        async def auth_required(request):
            return web.Response(status=401)

        app.router.add_get("/responses", auth_required)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        try:
            result = await check_websocket_connectivity_async(
                f"http://127.0.0.1:{port}", timeout=2
            )
            assert result["ok"] is False
            assert result["reachable"] is True
            assert result["conclusive"] is False
            assert result["error_type"] == "auth_required"

            ok, _, msg = await probe_proxy_ws_async(
                "http://127.0.0.1:9", f"http://127.0.0.1:{port}", timeout=0.2
            )
            assert ok is False
        finally:
            await runner.cleanup()

    asyncio.run(scenario())


def test_evaluation_does_not_call_401_403_ws_success():
    local = {"listening": True, "health_ok": True, "stats": {"websocket": {"handshakes": 0}}}
    https = {"ok": True, "total_ms": 100}
    ws = {
        "ok": False,
        "reachable": True,
        "conclusive": False,
        "status": 403,
        "error": "auth required",
    }
    result = evaluate_diagnostics(local, https, ws, {})
    assert result["ws_inconclusive"] is True
    assert "不能判断" in result["conclusion"] or "无法确认" in result["conclusion"]
    assert "握手均正常" not in result["conclusion"]


def test_notification_text_is_not_interpolated_into_powershell():
    nm = NotificationManager(default_cooldown=0)
    evil = 'x"; Start-Process calc; #'
    captured = {}

    class DummyProc:
        pass

    def fake_popen(args, **kwargs):
        captured["args"] = args
        captured["env"] = kwargs.get("env", {})
        return DummyProc()

    with patch("startup_manager.sys.platform", "win32"), patch("subprocess.Popen", side_effect=fake_popen):
        assert nm.send_notification(evil, evil, cooldown=0) is True
    command_text = " ".join(captured["args"])
    assert "Start-Process calc" not in command_text
    assert captured["env"]["CODEX_BRIDGE_NOTIFY_TITLE"] == evil


def test_gui_review_wiring_has_no_stale_v050_symbols():
    import codex_toolkit_gui

    source = inspect.getsource(codex_toolkit_gui)
    for stale in (
        "_net_tab.start_diagnostics()",
        "_session_tab._scan_sessions()",
        "_transport_var",
        "_on_transport_mode_change()",
        "_proxy_mode_var",
        "_custom_entry",
        "_disable_proxy()",
    ):
        assert stale not in source
    assert '"--transport-mode", get_transport_mode()' in source
    assert '"--circuit-threshold"' in source



def test_support_bundle_redacts_basic_proxy_auth_cookies_and_jwt():
    jwt = "abcdefghijklmnop.qrstuvwxyzABCDE.fghijklmnopQRST"
    raw = (
        "Authorization: Basic dXNlcjpwYXNz\n"
        "Proxy-Authorization: Negotiate abcdef\n"
        "Set-Cookie: session=supersecret; Path=/\n"
        f"token={jwt}\n"
    )
    cleaned = redact_text_content(raw)
    assert "dXNlcjpwYXNz" not in cleaned
    assert "Negotiate abcdef" not in cleaned
    assert "supersecret" not in cleaned
    assert jwt not in cleaned


def test_gui_exposes_circuit_action_and_real_temporary_http_override_wiring():
    import codex_toolkit_gui

    source = inspect.getsource(codex_toolkit_gui)
    assert "自动临时 HTTP" in source
    assert "仅提示" in source
    assert "update_managed_ws_support(False)" in source
    assert "state == \"HALF_OPEN\"" in source
    assert "update_managed_ws_support(True)" in source
