from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    (ROOT / path).write_text(text, encoding="utf-8")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def replace_top_level_function(text: str, name: str, new_block: str) -> str:
    pattern = re.compile(
        rf"(?ms)^def {re.escape(name)}\(.*?(?=^def |^class |\Z)"
    )
    match = pattern.search(text)
    if not match:
        raise RuntimeError(f"top-level function not found: {name}")
    return text[:match.start()] + new_block.rstrip() + "\n\n" + text[match.end():]


# ---------------------------------------------------------------------------
# transport_policy.py: make notify-only truly non-blocking and serialize
# HALF_OPEN to one real probe at a time.
# ---------------------------------------------------------------------------
path = "transport_policy.py"
text = read(path)
text = replace_once(
    text,
    "        self._circuit_opened_at: float | None = None\n",
    "        self._circuit_opened_at: float | None = None\n        self._half_open_probe_in_flight = False\n",
    "breaker half-open field",
)
text = replace_once(
    text,
    """    def _evaluate_state_locked(self) -> CircuitState:\n        if self._state == CircuitState.OPEN and self._circuit_opened_at:\n            elapsed = time.time() - self._circuit_opened_at\n            if elapsed >= self.cooldown_seconds:\n                self._state = CircuitState.HALF_OPEN\n        return self._state\n""",
    """    def _evaluate_state_locked(self) -> CircuitState:\n        if self._state == CircuitState.OPEN and self._circuit_opened_at:\n            elapsed = time.time() - self._circuit_opened_at\n            if elapsed >= self.cooldown_seconds:\n                self._state = CircuitState.HALF_OPEN\n                self._half_open_probe_in_flight = False\n        return self._state\n""",
    "breaker state evaluation",
)
text = replace_once(
    text,
    """    def record_success(self) -> CircuitState:\n        \"\"\"Record a successful WS connection/handshake.\"\"\"\n        with self._lock:\n            self._consecutive_failures = 0\n            self._last_success_at = time.time()\n            self._state = CircuitState.CLOSED\n            return self._state\n""",
    """    def record_success(self) -> CircuitState:\n        \"\"\"Record a successful WS connection/handshake.\"\"\"\n        with self._lock:\n            self._consecutive_failures = 0\n            self._last_success_at = time.time()\n            self._state = CircuitState.CLOSED\n            self._circuit_opened_at = None\n            self._half_open_probe_in_flight = False\n            return self._state\n""",
    "breaker success",
)
text = replace_once(
    text,
    """            if self.enabled and self._consecutive_failures >= self.threshold:\n                self._state = CircuitState.OPEN\n                self._circuit_opened_at = time.time()\n            elif self._state == CircuitState.HALF_OPEN:\n                # Probing trial failed in half-open state, reopen circuit\n                self._state = CircuitState.OPEN\n                self._circuit_opened_at = time.time()\n\n            return self._state\n""",
    """            if self._state == CircuitState.HALF_OPEN:\n                # A failed half-open trial immediately reopens the circuit.\n                self._state = CircuitState.OPEN\n                self._circuit_opened_at = time.time()\n                self._half_open_probe_in_flight = False\n            elif self.enabled and self._consecutive_failures >= self.threshold:\n                self._state = CircuitState.OPEN\n                self._circuit_opened_at = time.time()\n                self._half_open_probe_in_flight = False\n\n            return self._state\n""",
    "breaker failure",
)
text = replace_once(
    text,
    """        # Auto mode:\n        if not self.enabled:\n            return True\n\n        with self._lock:\n            current = self._evaluate_state_locked()\n            return current != CircuitState.OPEN\n""",
    """        # Auto mode:\n        if not self.enabled:\n            return True\n\n        with self._lock:\n            current = self._evaluate_state_locked()\n            # notify_only tracks degradation but must never change transport.\n            if self.action_mode == \"notify_only\":\n                return True\n            if current == CircuitState.OPEN:\n                return False\n            if current == CircuitState.HALF_OPEN:\n                # Permit exactly one trial connection. Parallel requests wait\n                # for that trial to resolve rather than stampeding upstream.\n                if self._half_open_probe_in_flight:\n                    return False\n                self._half_open_probe_in_flight = True\n            return True\n""",
    "breaker allow",
)
text = replace_once(
    text,
    """        cooldown_minutes: int | None = None,\n        enabled: bool | None = None,\n        action_mode: str | None = None,\n    ) -> None:\n        with self._lock:\n            if threshold is not None:\n                self.threshold = max(1, int(threshold))\n            if cooldown_minutes is not None:\n                self.cooldown_seconds = max(10, int(cooldown_minutes) * 60)\n            if enabled is not None:\n                self.enabled = bool(enabled)\n            if action_mode is not None:\n                self.action_mode = \"auto_switch\" if action_mode == \"auto_switch\" else \"notify_only\"\n""",
    """        cooldown_minutes: int | None = None,\n        cooldown_seconds: int | None = None,\n        enabled: bool | None = None,\n        action_mode: str | None = None,\n    ) -> None:\n        with self._lock:\n            if threshold is not None:\n                self.threshold = max(1, int(threshold))\n            if cooldown_seconds is not None:\n                self.cooldown_seconds = max(1, int(cooldown_seconds))\n            elif cooldown_minutes is not None:\n                self.cooldown_seconds = max(10, int(cooldown_minutes) * 60)\n            if enabled is not None:\n                self.enabled = bool(enabled)\n                if not self.enabled:\n                    self._state = CircuitState.CLOSED\n                    self._consecutive_failures = 0\n                    self._circuit_opened_at = None\n                    self._half_open_probe_in_flight = False\n            if action_mode is not None:\n                self.action_mode = \"auto_switch\" if action_mode == \"auto_switch\" else \"notify_only\"\n""",
    "breaker configure",
)
text = replace_once(
    text,
    """            self._state = CircuitState.CLOSED\n            self._consecutive_failures = 0\n            self._circuit_opened_at = None\n""",
    """            self._state = CircuitState.CLOSED\n            self._consecutive_failures = 0\n            self._circuit_opened_at = None\n            self._half_open_probe_in_flight = False\n""",
    "breaker reset",
)
write(path, text)


# ---------------------------------------------------------------------------
# runtime_stats.py: use the proxy's configured breaker and treat abnormal
# established-WS closes as breaker failures.
# ---------------------------------------------------------------------------
path = "runtime_stats.py"
text = read(path)
text = replace_once(
    text,
    """    def __init__(self) -> None:\n        self.started_at = _now()\n""",
    """    def __init__(\n        self,\n        circuit_breaker: TransportCircuitBreaker | None = None,\n        transport_mode: str = \"auto\",\n    ) -> None:\n        self.started_at = _now()\n        self.transport_mode = transport_mode\n""",
    "runtime init",
)
text = replace_once(
    text,
    "        self.circuit_breaker = TransportCircuitBreaker()\n",
    "        self.circuit_breaker = circuit_breaker or TransportCircuitBreaker()\n",
    "runtime breaker injection",
)
text = replace_once(
    text,
    """        if info[\"category\"] != \"ok\":\n            self.last_error = {**info, \"at\": _now()}\n""",
    """        if info[\"category\"] != \"ok\":\n            self.last_error = {**info, \"at\": _now()}\n            self.circuit_breaker.record_failure(\n                f\"websocket closed abnormally ({self.ws_last_close_code})\"\n            )\n""",
    "runtime abnormal close",
)
text = replace_once(
    text,
    '            "circuit_breaker": self.circuit_breaker.snapshot(),\n',
    '            "circuit_breaker": self.circuit_breaker.snapshot(self.transport_mode),\n',
    "runtime snapshot mode",
)
write(path, text)


# ---------------------------------------------------------------------------
# proxy.py: wire persisted breaker settings into the actual proxy process,
# expose loopback-only live control endpoints, and stop exposing raw NO_PROXY.
# ---------------------------------------------------------------------------
path = "proxy.py"
text = read(path)
text = replace_once(
    text,
    "from runtime_stats import RuntimeStats\n",
    "from runtime_stats import RuntimeStats\nfrom transport_policy import TransportCircuitBreaker\n",
    "proxy breaker import",
)
text = replace_once(
    text,
    """        upstream_proxy: str | None = None,\n        proxy_mode: str = \"direct\",\n        transport_mode: str = \"auto\",\n    ) -> None:\n""",
    """        upstream_proxy: str | None = None,\n        proxy_mode: str = \"direct\",\n        transport_mode: str = \"auto\",\n        circuit_enabled: bool = True,\n        circuit_action: str = \"auto_switch\",\n        circuit_threshold: int = 3,\n        circuit_cooldown_seconds: int = 15 * 60,\n    ) -> None:\n""",
    "proxy constructor signature",
)
text = replace_once(
    text,
    """        self.transport_mode = transport_mode.lower().strip() if transport_mode else \"auto\"\n        self.stats = RuntimeStats()\n        self._session: aiohttp.ClientSession | None = None\n""",
    """        mode = transport_mode.lower().strip() if transport_mode else \"auto\"\n        self.transport_mode = mode if mode in {\"auto\", \"websocket\", \"http\"} else \"auto\"\n        self.circuit_breaker = TransportCircuitBreaker(\n            threshold=max(1, int(circuit_threshold)),\n            cooldown_seconds=max(1, int(circuit_cooldown_seconds)),\n            enabled=bool(circuit_enabled),\n            action_mode=circuit_action,\n        )\n        self.stats = RuntimeStats(self.circuit_breaker, self.transport_mode)\n        self._session: aiohttp.ClientSession | None = None\n""",
    "proxy breaker construction",
)
text = replace_once(
    text,
    '                "no_proxy": os.environ.get("NO_PROXY")\n',
    '                "no_proxy": "set (contents redacted)" if (os.environ.get("NO_PROXY") or os.environ.get("no_proxy")) else None\n',
    "proxy no_proxy privacy",
)
text = replace_once(
    text,
    """    async def handle_stats(self, request: web.Request) -> web.Response:\n        return web.json_response(self.stats.snapshot())\n\n    # ------------------------------------------------------------------ #\n    # Main proxy catch-all                                                 #\n""",
    """    async def handle_stats(self, request: web.Request) -> web.Response:\n        return web.json_response(self.stats.snapshot())\n\n    async def handle_control_transport(self, request: web.Request) -> web.Response:\n        data = await request.json()\n        mode = str(data.get(\"mode\", \"\")).lower().strip()\n        if mode not in {\"auto\", \"websocket\", \"http\"}:\n            return web.json_response({\"ok\": False, \"error\": \"invalid transport mode\"}, status=400)\n        self.transport_mode = mode\n        self.stats.transport_mode = mode\n        return web.json_response({\"ok\": True, \"transport_mode\": mode})\n\n    async def handle_control_circuit_config(self, request: web.Request) -> web.Response:\n        data = await request.json()\n        try:\n            threshold = max(1, int(data.get(\"threshold\", self.circuit_breaker.threshold)))\n            cooldown = max(1, int(data.get(\"cooldown_seconds\", self.circuit_breaker.cooldown_seconds)))\n        except (TypeError, ValueError):\n            return web.json_response({\"ok\": False, \"error\": \"invalid circuit configuration\"}, status=400)\n        action = str(data.get(\"action_mode\", self.circuit_breaker.action_mode))\n        if action not in {\"auto_switch\", \"notify_only\"}:\n            return web.json_response({\"ok\": False, \"error\": \"invalid action_mode\"}, status=400)\n        self.circuit_breaker.configure(\n            threshold=threshold,\n            cooldown_seconds=cooldown,\n            enabled=bool(data.get(\"enabled\", self.circuit_breaker.enabled)),\n            action_mode=action,\n        )\n        return web.json_response({\"ok\": True, \"circuit_breaker\": self.circuit_breaker.snapshot(self.transport_mode)})\n\n    async def handle_control_circuit_reset(self, request: web.Request) -> web.Response:\n        self.circuit_breaker.reset()\n        return web.json_response({\"ok\": True, \"circuit_breaker\": self.circuit_breaker.snapshot(self.transport_mode)})\n\n    # ------------------------------------------------------------------ #\n    # Main proxy catch-all                                                 #\n""",
    "proxy control handlers",
)
text = replace_once(
    text,
    '            logger.info("[WS ] circuit breaker open or mode is HTTP, fast-rejecting WS handshake to trigger HTTP fallback")\n            return web.Response(status=503, text="WebSocket circuit breaker open or disabled, fallback to HTTP")\n',
    '            logger.info("[WS ] local transport policy is blocking this WebSocket attempt")\n            return web.Response(status=503, text="WebSocket disabled by local transport policy")\n',
    "proxy ws block wording",
)
text = replace_once(
    text,
    """    proxy_mode: str = \"direct\",\n    transport_mode: str = \"auto\",\n) -> web.Application:\n""",
    """    proxy_mode: str = \"direct\",\n    transport_mode: str = \"auto\",\n    circuit_enabled: bool = True,\n    circuit_action: str = \"auto_switch\",\n    circuit_threshold: int = 3,\n    circuit_cooldown_seconds: int = 15 * 60,\n) -> web.Application:\n""",
    "make_app signature",
)
text = replace_once(
    text,
    """        proxy_mode=proxy_mode,\n        transport_mode=transport_mode,\n    )\n""",
    """        proxy_mode=proxy_mode,\n        transport_mode=transport_mode,\n        circuit_enabled=circuit_enabled,\n        circuit_action=circuit_action,\n        circuit_threshold=circuit_threshold,\n        circuit_cooldown_seconds=circuit_cooldown_seconds,\n    )\n""",
    "make_app constructor args",
)
text = replace_once(
    text,
    """    app.router.add_get(\"/stats\", proxy.handle_stats)\n    app.router.add_route(\"*\", \"/{path_info:.*}\", proxy.handle_proxy)\n""",
    """    app.router.add_get(\"/stats\", proxy.handle_stats)\n    app.router.add_post(\"/control/transport\", proxy.handle_control_transport)\n    app.router.add_post(\"/control/circuit/config\", proxy.handle_control_circuit_config)\n    app.router.add_post(\"/control/circuit/reset\", proxy.handle_control_circuit_reset)\n    app.router.add_route(\"*\", \"/{path_info:.*}\", proxy.handle_proxy)\n""",
    "proxy control routes",
)
text = replace_once(
    text,
    '    p.add_argument("--transport-mode", choices=["auto", "websocket", "http"], default=os.environ.get("PROXY_TRANSPORT_MODE", "auto"), help="Transport policy: auto, websocket, or http")\n',
    '    p.add_argument("--transport-mode", choices=["auto", "websocket", "http"], default=os.environ.get("PROXY_TRANSPORT_MODE", "auto"), help="Transport policy: auto, websocket, or http")\n    p.add_argument("--circuit-enabled", choices=["true", "false"], default=os.environ.get("PROXY_CIRCUIT_ENABLED", "true"), help="Enable WS circuit breaker")\n    p.add_argument("--circuit-action", choices=["auto_switch", "notify_only"], default=os.environ.get("PROXY_CIRCUIT_ACTION", "auto_switch"))\n    p.add_argument("--circuit-threshold", type=int, default=int(os.environ.get("PROXY_CIRCUIT_THRESHOLD", "3")))\n    p.add_argument("--circuit-cooldown-seconds", type=int, default=int(os.environ.get("PROXY_CIRCUIT_COOLDOWN_SECONDS", "900")))\n',
    "proxy cli breaker args",
)
text = replace_once(
    text,
    """    transport_mode: str = args.transport_mode\n\n    if not (1 <= port <= 65535):\n""",
    """    transport_mode: str = args.transport_mode\n    circuit_enabled = args.circuit_enabled == \"true\"\n    circuit_action: str = args.circuit_action\n    circuit_threshold: int = args.circuit_threshold\n    circuit_cooldown_seconds: int = args.circuit_cooldown_seconds\n\n    if not (1 <= port <= 65535):\n""",
    "proxy main breaker vars",
)
text = replace_once(
    text,
    """    if proxy_mode == \"explicit\" and not upstream_proxy:\n        raise SystemExit(\"--proxy-mode=explicit requires --upstream-proxy\")\n""",
    """    if proxy_mode == \"explicit\" and not upstream_proxy:\n        raise SystemExit(\"--proxy-mode=explicit requires --upstream-proxy\")\n    if circuit_threshold < 1 or circuit_cooldown_seconds < 1:\n        raise SystemExit(\"circuit threshold and cooldown must be positive\")\n""",
    "proxy main breaker validation",
)
text = replace_once(
    text,
    '    print(f"  Transport: {transport_mode}")\n',
    '    print(f"  Transport: {transport_mode}")\n    print(f"  Circuit:   enabled={circuit_enabled} action={circuit_action} threshold={circuit_threshold} cooldown={circuit_cooldown_seconds}s")\n',
    "proxy banner breaker",
)
text = replace_once(
    text,
    """        proxy_mode=proxy_mode,\n        transport_mode=transport_mode,\n    )\n    web.run_app(app, host=\"127.0.0.1\", port=port, access_log=None)\n""",
    """        proxy_mode=proxy_mode,\n        transport_mode=transport_mode,\n        circuit_enabled=circuit_enabled,\n        circuit_action=circuit_action,\n        circuit_threshold=circuit_threshold,\n        circuit_cooldown_seconds=circuit_cooldown_seconds,\n    )\n    web.run_app(app, host=\"127.0.0.1\", port=port, access_log=None)\n""",
    "proxy main make_app args",
)
write(path, text)


# ---------------------------------------------------------------------------
# config_manager.py: typing import, validated breaker values, config backups,
# and state/config consistency for transport changes.
# ---------------------------------------------------------------------------
path = "config_manager.py"
text = read(path)
text = replace_once(text, "from typing import Tuple\n", "from typing import Any, Tuple\n", "config Any import")
text = replace_once(
    text,
    """    cd_min = int(cfg.get(\"cooldown_minutes\", 15))\n    cd_sec = int(cfg.get(\"cooldown_seconds\", cd_min * 60))\n    return {\n        \"enabled\": bool(cfg.get(\"enabled\", True)),\n        \"action_mode\": str(cfg.get(\"action_mode\", \"auto_switch\")),\n        \"cooldown_minutes\": cd_min,\n        \"cooldown_seconds\": cd_sec,\n        \"failure_threshold\": int(cfg.get(\"failure_threshold\", 3)),\n    }\n""",
    """    cd_min = max(1, int(cfg.get(\"cooldown_minutes\", 15)))\n    cd_sec = max(1, int(cfg.get(\"cooldown_seconds\", cd_min * 60)))\n    action = str(cfg.get(\"action_mode\", \"auto_switch\"))\n    return {\n        \"enabled\": bool(cfg.get(\"enabled\", True)),\n        \"action_mode\": action if action in {\"auto_switch\", \"notify_only\"} else \"auto_switch\",\n        \"cooldown_minutes\": cd_min,\n        \"cooldown_seconds\": cd_sec,\n        \"failure_threshold\": max(1, int(cfg.get(\"failure_threshold\", 3))),\n    }\n""",
    "config breaker get validation",
)
text = replace_once(
    text,
    """    state[\"circuit_breaker\"] = {\n        \"enabled\": bool(d_enabled),\n        \"action_mode\": \"auto_switch\" if d_action == \"auto_switch\" else \"notify_only\",\n        \"cooldown_minutes\": cd_min,\n        \"cooldown_seconds\": cd_sec,\n        \"failure_threshold\": int(d_thresh),\n    }\n""",
    """    cd_sec = max(1, int(cd_sec))\n    cd_min = max(1, int(cd_min))\n    state[\"circuit_breaker\"] = {\n        \"enabled\": bool(d_enabled),\n        \"action_mode\": \"auto_switch\" if d_action == \"auto_switch\" else \"notify_only\",\n        \"cooldown_minutes\": cd_min,\n        \"cooldown_seconds\": cd_sec,\n        \"failure_threshold\": max(1, int(d_thresh)),\n    }\n""",
    "config breaker save validation",
)
new_set_transport = '''def set_transport_mode(mode: str, port: int | None = None) -> Tuple[bool, str]:
    """Persist transport mode and safely update the managed provider when present."""
    mode = str(mode).lower().strip()
    if mode not in VALID_TRANSPORT_MODES:
        return False, f"Invalid transport mode: {mode!r}. Expected one of {VALID_TRANSPORT_MODES}"

    ws_enabled = mode != "http"
    state = _load_state()

    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                doc = tomlkit.load(f)
            providers = doc.get("model_providers", {})
            block = providers.get("openai-idfix") if providers else None
            if block is not None and block.get("supports_websockets") != ws_enabled:
                backup_file(CONFIG_PATH)
                block["supports_websockets"] = ws_enabled
                temp_path = CONFIG_PATH.with_suffix(f".tmp.{time.time()}")
                try:
                    with open(temp_path, "w", encoding="utf-8") as f:
                        tomlkit.dump(doc, f)
                        f.flush()
                        os.fsync(f.fileno())
                    with open(temp_path, "r", encoding="utf-8") as f:
                        tomlkit.load(f)
                    os.replace(temp_path, CONFIG_PATH)
                finally:
                    if temp_path.exists():
                        try:
                            temp_path.unlink()
                        except OSError:
                            pass
                state["managed_proxy_ws"] = ws_enabled
        except Exception as exc:
            return False, f"保存传输模式配置失败: {exc}"

    state["transport_mode"] = mode
    try:
        _save_state(state)
    except Exception as exc:
        return False, f"保存传输模式状态失败: {exc}"
    return True, f"传输模式已更新为 {mode} (supports_websockets={ws_enabled})。"
'''
text = replace_top_level_function(text, "set_transport_mode", new_set_transport)
text = replace_once(
    text,
    """        block = providers[\"openai-idfix\"]\n        block[\"supports_websockets\"] = bool(ws_enabled)\n\n        state = _load_state()\n""",
    """        block = providers[\"openai-idfix\"]\n        if block.get(\"supports_websockets\") != bool(ws_enabled):\n            backup_file(CONFIG_PATH)\n        block[\"supports_websockets\"] = bool(ws_enabled)\n\n        state = _load_state()\n""",
    "config managed ws backup",
)
write(path, text)


# ---------------------------------------------------------------------------
# network_diagnostics.py: an unauthenticated 401/403 proves reachability, not
# a successful WebSocket upgrade. Include real local stats as separate evidence.
# ---------------------------------------------------------------------------
path = "network_diagnostics.py"
text = read(path)
text = text.replace("import ssl\n", "")
text = replace_once(
    text,
    """                res[\"health_ok\"] = True\n                res[\"details\"] = json.loads(resp.read().decode(\"utf-8\"))\n""",
    """                res[\"health_ok\"] = True\n                res[\"details\"] = json.loads(resp.read().decode(\"utf-8\"))\n                try:\n                    stats_req = urllib.request.Request(f\"http://127.0.0.1:{port}/stats\")\n                    with urllib.request.urlopen(stats_req, timeout=2.0) as stats_resp:\n                        if stats_resp.status == 200:\n                            res[\"stats\"] = json.loads(stats_resp.read().decode(\"utf-8\"))\n                except Exception:\n                    res[\"stats\"] = {}\n""",
    "network local stats",
)
new_ws_diag = '''async def check_websocket_connectivity_async(
    target_url: str = "https://chatgpt.com/backend-api/codex",
    timeout: float = 6.0,
    proxy_url: str | None = None,
) -> dict[str, Any]:
    """Probe WebSocket reachability without pretending auth rejection is a 101 upgrade."""
    res: dict[str, Any] = {
        "ok": False,
        "reachable": False,
        "conclusive": True,
        "target": mask_url_sensitive(target_url),
        "status": None,
        "handshake_ms": None,
        "protocol": None,
        "close_code": None,
        "error": None,
        "error_type": None,
    }

    ws_url = target_url
    if ws_url.startswith("https://"):
        ws_url = "wss://" + ws_url[8:]
    elif ws_url.startswith("http://"):
        ws_url = "ws://" + ws_url[7:]
    ws_url = ws_url.rstrip("/") + "/responses"

    t0 = time.monotonic()
    try:
        timeout_cfg = aiohttp.ClientTimeout(total=timeout, connect=timeout * 0.7)
        async with aiohttp.ClientSession(timeout=timeout_cfg) as session:
            async with session.ws_connect(
                ws_url,
                proxy=proxy_url,
                timeout=timeout,
                protocols=("codex-diagnostics",),
            ) as ws:
                res["handshake_ms"] = round((time.monotonic() - t0) * 1000, 1)
                res["ok"] = True
                res["reachable"] = True
                res["protocol"] = ws.protocol
                await ws.close()
    except aiohttp.WSServerHandshakeError as exc:
        res["handshake_ms"] = round((time.monotonic() - t0) * 1000, 1)
        res["status"] = exc.status
        res["reachable"] = True
        if exc.status in (401, 403):
            res["conclusive"] = False
            res["error"] = (
                f"已到达上游，但匿名探测被鉴权拒绝 (HTTP {exc.status})；"
                "这不能证明 WebSocket 已完成 101 升级"
            )
            res["error_type"] = "auth_required"
        elif exc.status >= 500:
            res["error"] = f"上游服务拒绝 WebSocket 握手 (HTTP {exc.status})"
            res["error_type"] = f"http_{exc.status}"
        else:
            res["error"] = f"WebSocket 握手失败: HTTP {exc.status}"
            res["error_type"] = f"http_{exc.status}"
    except asyncio.TimeoutError:
        res["handshake_ms"] = round((time.monotonic() - t0) * 1000, 1)
        res["error"] = "WebSocket 握手超时 (Timeout)"
        res["error_type"] = "ws_timeout"
    except Exception as exc:
        res["handshake_ms"] = round((time.monotonic() - t0) * 1000, 1)
        res["error"] = f"WebSocket 异常: {type(exc).__name__}"
        res["error_type"] = type(exc).__name__

    return res
'''
text = replace_top_level_function(text, "check_websocket_connectivity_async", new_ws_diag)
new_eval = '''def evaluate_diagnostics(
    local_res: dict[str, Any],
    https_res: dict[str, Any],
    ws_res: dict[str, Any],
    sys_proxy: dict[str, Any],
) -> dict[str, Any]:
    """Compare HTTP and WS evidence without overstating unauthenticated probes."""
    https_ok = bool(https_res.get("ok"))
    ws_ok = bool(ws_res.get("ok"))
    ws_inconclusive = bool(ws_res.get("reachable")) and not bool(ws_res.get("conclusive", True))
    stats = local_res.get("stats") or {}
    ws_stats = stats.get("websocket") or {}
    real_ws_handshakes = int(ws_stats.get("handshakes") or 0)

    https_status = f"正常 ({https_res.get('total_ms')}ms)" if https_ok else f"失败: {https_res.get('error')}"
    if ws_ok:
        ws_status = f"升级成功 ({ws_res.get('handshake_ms')}ms)"
    elif ws_inconclusive:
        ws_status = f"上游可达，但匿名探测无法确认升级 ({ws_res.get('status')})"
    else:
        ws_status = f"失败: {ws_res.get('error')}"

    recommendations: list[str] = []

    if not local_res.get("listening"):
        conclusion = "本地 Toolkit 代理未启动或未正常监听指定端口。"
        recommendations.append("请先在「代理控制」页面启动本地代理。")
    elif https_ok and ws_inconclusive:
        if real_ws_handshakes > 0:
            conclusion = (
                "HTTPS 正常；匿名 WebSocket 探测因缺少认证无法确认 101 升级，"
                f"但 Toolkit 已观察到 {real_ws_handshakes} 次真实 Codex WebSocket 成功握手。"
            )
            recommendations.append("优先参考真实 Codex 流量统计；若仍频繁出现 1006/timeout，再考虑强制 HTTP。")
        else:
            conclusion = (
                "HTTPS 正常；匿名 WebSocket 探测已到达上游，但被鉴权拒绝。"
                "仅凭 401/403 不能判断 WebSocket 是否真正可用。"
            )
            recommendations.append("让 Codex 发送一次真实请求后再查看 WebSocket 统计，或结合近期 timeout/1006 判断。")
    elif https_ok and not ws_ok:
        conclusion = (
            "HTTPS 访问正常，但 WebSocket 握手失败或超时。\n"
            "这可能来自代理覆盖不完整、节点/中间层长连接兼容性，或上游暂时异常。"
        )
        recommendations.append("在 Toolkit 中切换为「强制 HTTP」可避免 Codex 主动尝试 WebSocket。")
        recommendations.append("若使用 Clash / Mihomo 等工具，可检查 TUN/分流规则并尝试更换节点。")
    elif not https_ok and not ws_ok:
        h_type = https_res.get("error_type")
        if h_type == "upstream_5xx" or (https_res.get("status") and https_res.get("status") >= 500):
            conclusion = "HTTPS 与 WebSocket 均出现上游 5xx，疑似远端服务暂时不可用。"
            recommendations.append("此故障通常属于服务端临时故障，Toolkit 本地无法修复，请稍后重试。")
        elif h_type == "timeout" or ws_res.get("error_type") == "ws_timeout":
            conclusion = "HTTPS 与 WebSocket 连接均超时，本地网络当前无法稳定到达上游。"
            recommendations.append("检查系统代理、Toolkit 出站代理、VPN/TUN 与节点连通性。")
        else:
            conclusion = f"无法连通上游服务 ({https_res.get('error') or '网络连接受阻'})。"
            recommendations.append("检查 DNS、本地安全软件以及代理软件配置。")
    elif https_ok and ws_ok:
        conclusion = "上游网络连通良好，HTTPS 与 WebSocket 101 升级均已实际成功。"
        recommendations.append("无需额外调整，可继续使用自动传输模式。")
    else:
        conclusion = "WebSocket 探测成功但 HTTPS 探测异常，建议重新体检确认是否为瞬时网络抖动。"
        recommendations.append("稍后重新运行体检并结合真实 Codex 流量判断。")

    return {
        "https_status": https_status,
        "ws_status": ws_status,
        "ws_inconclusive": ws_inconclusive,
        "conclusion": conclusion,
        "recommendations": recommendations,
    }
'''
text = replace_top_level_function(text, "evaluate_diagnostics", new_eval)
write(path, text)


# ---------------------------------------------------------------------------
# proxy_discovery.py: 401/403 is reachable-but-inconclusive, not WS success.
# ---------------------------------------------------------------------------
path = "proxy_discovery.py"
text = read(path)
text = replace_once(
    text,
    """        if exc.status in (401, 403):\n            # The handshake reached upstream and rejected unauthenticated probe -> WS forwarding works!\n            return True, elapsed, f\"WebSocket 握手成功 (HTTP {exc.status} 鉴权正常)\"\n""",
    """        if exc.status in (401, 403):\n            # Reaching an HTTP auth rejection proves routing/reachability, but\n            # not that a WebSocket 101 upgrade actually succeeded.\n            return False, elapsed, f\"上游可达，但匿名探测无法确认 WS 升级 (HTTP {exc.status})\"\n""",
    "proxy discovery auth semantics",
)
write(path, text)


# ---------------------------------------------------------------------------
# startup_manager.py: do not interpolate notification text into PowerShell.
# ---------------------------------------------------------------------------
path = "startup_manager.py"
text = read(path)
old_method = '''    def send_notification(self, title: str, message: str, key: str | None = None, cooldown: float = 60.0) -> bool:
        """Send a non-blocking Windows notification if rate limit allows."""
        dedup_key = key or f"{title}:{message[:40]}"
        if not self.should_notify(dedup_key, cooldown):
            return False

        self.record_notified(dedup_key)

        # Dispatch via PowerShell notification if on Windows
        if sys.platform == "win32":
            import subprocess
            ps_script = (
                f'[void] [System.Reflection.Assembly]::LoadWithPartialName("System.Windows.Forms"); '
                f'$obj = New-Object System.Windows.Forms.NotifyIcon; '
                f'$obj.Icon = [System.Drawing.SystemIcons]::Information; '
                f'$obj.BalloonTipTitle = "{title}"; '
                f'$obj.BalloonTipText = "{message}"; '
                f'$obj.Visible = $True; '
                f'$obj.ShowBalloonTip(3000);'
            )
            try:
                subprocess.Popen(["powershell", "-NoProfile", "-Command", ps_script],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                pass
        return True
'''
new_method = '''    def send_notification(self, title: str, message: str, key: str | None = None, cooldown: float = 60.0) -> bool:
        """Send a non-blocking Windows notification without shell interpolation."""
        safe_title = " ".join(str(title).splitlines())[:120]
        safe_message = " ".join(str(message).splitlines())[:500]
        dedup_key = key or f"{safe_title}:{safe_message[:40]}"
        if not self.should_notify(dedup_key, cooldown):
            return False

        if sys.platform == "win32":
            import subprocess
            # Notification text is carried only through environment variables;
            # the PowerShell program itself is fixed and contains no user data.
            ps_script = (
                '[void] [System.Reflection.Assembly]::LoadWithPartialName("System.Windows.Forms"); '
                '$obj = New-Object System.Windows.Forms.NotifyIcon; '
                '$obj.Icon = [System.Drawing.SystemIcons]::Information; '
                '$obj.BalloonTipTitle = $env:CODEX_BRIDGE_NOTIFY_TITLE; '
                '$obj.BalloonTipText = $env:CODEX_BRIDGE_NOTIFY_MESSAGE; '
                '$obj.Visible = $True; '
                '$obj.ShowBalloonTip(3000); '
                'Start-Sleep -Milliseconds 3200; '
                '$obj.Dispose();'
            )
            env = os.environ.copy()
            env["CODEX_BRIDGE_NOTIFY_TITLE"] = safe_title
            env["CODEX_BRIDGE_NOTIFY_MESSAGE"] = safe_message
            try:
                subprocess.Popen(
                    ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    env=env,
                )
            except Exception:
                return False
        self.record_notified(dedup_key)
        return True
'''
text = replace_once(text, old_method, new_method, "notification shell safety")
write(path, text)


# ---------------------------------------------------------------------------
# backup_manager.py: derive structural changes from the same deterministic
# rewrite context instead of zipping ID lists that shift after dropped items.
# ---------------------------------------------------------------------------
path = "backup_manager.py"
text = read(path)
text = replace_once(
    text,
    "from history_fixer import backup_file, is_codex_running\n",
    "from history_fixer import backup_file, is_codex_running\nfrom id_rewriter import prepare_rewrite_context\n",
    "backup rewrite import",
)
new_diff = '''def compute_structured_diff(current_path: Path, backup_path: Path) -> StructuredDiff:
    """Compute a privacy-safe structural diff using deterministic ID mappings."""
    def load_records(path: Path) -> tuple[list[dict[str, Any]], int]:
        records: list[dict[str, Any]] = []
        line_count = 0
        if not path.exists():
            return records, line_count
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for raw in f:
                if not raw.strip():
                    continue
                line_count += 1
                try:
                    obj = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    records.append(obj)
        return records, line_count

    def collect_ids_and_refs(obj: Any) -> tuple[set[str], set[str]]:
        ids: set[str] = set()
        refs: set[str] = set()
        ref_keys = {"item_id", "message_id", "previous_item_id", "parent_id", "response_id"}

        def walk(value: Any) -> None:
            if isinstance(value, dict):
                raw_id = value.get("id")
                if isinstance(raw_id, str):
                    ids.add(raw_id)
                for key in ref_keys:
                    ref = value.get(key)
                    if isinstance(ref, str):
                        refs.add(ref)
                for key, child in value.items():
                    if key not in {"content", "text"} and isinstance(child, (dict, list)):
                        walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)

        walk(obj)
        return ids, refs

    current_records, current_lines = load_records(current_path)
    backup_records, backup_lines = load_records(backup_path)

    id_map: dict[str, str] = {}
    dropped_ids: set[str] = set()
    prepare_rewrite_context(
        backup_records,
        id_map=id_map,
        dropped_ids=dropped_ids,
        safe_reasoning=True,
    )

    current_ids: set[str] = set()
    current_refs: set[str] = set()
    backup_refs: set[str] = set()
    for obj in current_records:
        ids, refs = collect_ids_and_refs(obj)
        current_ids.update(ids)
        current_refs.update(refs)
    for obj in backup_records:
        _, refs = collect_ids_and_refs(obj)
        backup_refs.update(refs)

    id_changes = [
        {"old_id": old, "new_id": new}
        for old, new in sorted(id_map.items())
        if new in current_ids
    ]
    reference_changes = [
        {"old_ref": old, "new_ref": new}
        for old, new in sorted(id_map.items())
        if old in backup_refs and new in current_refs
    ]
    reasoning_drops = sum(1 for old in dropped_ids if old not in current_ids)

    return StructuredDiff(
        session_file=current_path.name,
        backup_file=backup_path.name,
        id_changes=id_changes,
        reference_changes=reference_changes,
        reasoning_drops=reasoning_drops,
        lines_original=current_lines,
        lines_backup=backup_lines,
    )
'''
text = replace_top_level_function(text, "compute_structured_diff", new_diff)
write(path, text)


# ---------------------------------------------------------------------------
# GUI wiring / automation. Keep the existing UI but replace stale v0.5 names,
# pass actual transport+breaker settings to the proxy, and sync live controls.
# ---------------------------------------------------------------------------
path = "codex_toolkit_gui.py"
text = read(path)
text = replace_once(
    text,
    """        # Automation check: auto-start proxy if enabled\n        auto_settings = load_automation_settings()\n        if auto_settings.auto_start_proxy:\n            self.after(600, self._proxy_tab._start_proxy)\n""",
    """        # Load persisted circuit settings for GUI fallback displays. The\n        # running proxy receives the same settings through its CLI/control API.\n        cb_cfg = get_circuit_breaker_config()\n        GLOBAL_CIRCUIT_BREAKER.configure(\n            threshold=cb_cfg.get(\"failure_threshold\", 3),\n            cooldown_seconds=cb_cfg.get(\"cooldown_seconds\", 900),\n            enabled=cb_cfg.get(\"enabled\", True),\n            action_mode=cb_cfg.get(\"action_mode\", \"auto_switch\"),\n        )\n\n        # Automation check: auto-start proxy if enabled\n        auto_settings = load_automation_settings()\n        if auto_settings.auto_start_proxy:\n            self.after(600, self._proxy_tab._start_proxy)\n""",
    "gui breaker initial sync",
)
text = replace_once(
    text,
    "        self._app._net_tab.start_diagnostics()\n",
    "        self._app._net_tab._run_diagnostics()\n",
    "overview network button",
)
# Add ProxyTab helper methods before _on_transport_mode_changed.
anchor = "    def _on_transport_mode_changed(self, event=None):\n"
helpers = '''    def _post_control(self, path: str, payload: dict | None = None) -> bool:
        try:
            port = int(self._port_var.get().strip() or "8787")
            data = json.dumps(payload or {}).encode("utf-8")
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}{path}",
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=1.0) as resp:
                return resp.status == 200
        except Exception:
            return False

    def _set_transport_mode(self, mode: str, *, prompt_restart: bool = True):
        labels = {"auto": "自动 (默认)", "websocket": "WebSocket (保持)", "http": "强制 HTTP"}
        mode = mode if mode in labels else "auto"
        self._transport_mode_var.set(labels[mode])
        self._ws_var.set(mode != "http")
        ok, msg = set_transport_mode(mode)
        self._append_log(f"[传输模式] {msg}", "info" if ok else "warn")
        if ok:
            self._post_control("/control/transport", {"mode": mode})
        if ok and prompt_restart and is_codex_running():
            if messagebox.askyesno(
                "传输模式已更新",
                f"传输模式已切换为「{labels[mode]}」。\n\n"
                "Codex Desktop 正在运行；supports_websockets 在启动时读取。\n\n是否立即重启 Codex？",
            ):
                restart_codex()
        return ok

    def _selected_outbound_proxy_url(self) -> str | None:
        selected = self._proxy_var.get().strip()
        if selected == ENV_PROXY_SENTINEL:
            return (
                os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
                or os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy")
                or os.environ.get("ALL_PROXY") or os.environ.get("all_proxy")
            )
        return selected or None

    def _selected_upstream_url(self) -> str:
        label = self._upstream_var.get().strip()
        return self._upstreams_dict.get(label, "https://chatgpt.com/backend-api/codex")

    def _apply_discovered_proxy(self, proxy_url: str, label: str) -> None:
        self._proxies_dict[proxy_url] = label or "自动发现"
        self._proxy_cb["values"] = list(self._proxies_dict.keys()) + ["<编辑/新增代理...>"]
        self._proxy_var.set(proxy_url)
        self._proxy_lbl.config(text=self._proxies_dict[proxy_url])
        save_proxies(self._proxies_dict, proxy_url)

'''
if anchor not in text:
    raise RuntimeError("GUI helper anchor missing")
text = text.replace(anchor, helpers + anchor, 1)
text = replace_once(
    text,
    """    def _on_transport_mode_changed(self, event=None):\n        val = self._transport_mode_var.get()\n        mode = \"auto\"\n        if \"WebSocket\" in val:\n            mode = \"websocket\"\n        elif \"HTTP\" in val:\n            mode = \"http\"\n\n        self._ws_var.set(mode != \"http\")\n        ok, msg = set_transport_mode(mode)\n        self._append_log(f\"[传输模式] {msg}\", \"info\" if ok else \"warn\")\n\n        if is_codex_running():\n            if messagebox.askyesno(\n                \"传输模式已更新\",\n                f\"传输模式已切换为「{val}」。\\n\\nCodex Desktop 正在运行，必须重启 Codex 才能生效。\\n\\n是否立即重启 Codex？\"\n            ):\n                restart_codex()\n""",
    """    def _on_transport_mode_changed(self, event=None):\n        val = self._transport_mode_var.get()\n        mode = \"auto\"\n        if \"WebSocket\" in val:\n            mode = \"websocket\"\n        elif \"HTTP\" in val:\n            mode = \"http\"\n        self._set_transport_mode(mode, prompt_restart=True)\n""",
    "gui transport handler",
)
text = replace_once(
    text,
    """        GLOBAL_CIRCUIT_BREAKER.configure(\n            enabled=cfg[\"enabled\"],\n            cooldown_minutes=cd_min,\n            threshold=3,\n        )\n        self._append_log(f\"[熔断配置] 已更新: 启用={cfg['enabled']}, 冷却={cd_min}分钟\", \"info\")\n""",
    """        GLOBAL_CIRCUIT_BREAKER.configure(\n            enabled=cfg[\"enabled\"],\n            cooldown_minutes=cd_min,\n            threshold=3,\n            action_mode=cfg[\"action_mode\"],\n        )\n        self._post_control(\"/control/circuit/config\", {\n            \"enabled\": cfg[\"enabled\"],\n            \"action_mode\": cfg[\"action_mode\"],\n            \"threshold\": 3,\n            \"cooldown_seconds\": cd_min * 60,\n        })\n        self._append_log(f\"[熔断配置] 已更新: 启用={cfg['enabled']}, 冷却={cd_min}分钟\", \"info\")\n""",
    "gui quick breaker sync",
)
# Auto-apply provider on proxy ready, after launch-pending handling and before status branching.
text = replace_once(
    text,
    """        status = get_proxy_config_status(port_num)\n\n        if not is_codex_running():\n""",
    """        status = get_proxy_config_status(port_num)\n        auto_cfg = load_automation_settings()\n        if auto_cfg.auto_apply_provider and not status.get(\"active_for_port\"):\n            ok, msg = enable_proxy_config(port_num, self._ws_var.get())\n            self._append_log(\n                f\"[自动化] {'已自动应用 provider' if ok else '自动应用 provider 失败'}: {msg}\",\n                \"ok\" if ok else \"error\",\n            )\n            status = get_proxy_config_status(port_num)\n\n        if not is_codex_running():\n""",
    "gui auto provider",
)
# Pass transport and actual breaker settings to child proxy.
text = replace_once(
    text,
    """        cmd += [\n            \"--port\", port,\n            \"--upstream\", upstream,\n            \"--reasoning-mode\", \"safe\",\n            \"--log-level\", \"DEBUG\",\n            \"--proxy-mode\", proxy_mode,\n        ]\n""",
    """        cb_cfg = get_circuit_breaker_config()\n        cmd += [\n            \"--port\", port,\n            \"--upstream\", upstream,\n            \"--reasoning-mode\", \"safe\",\n            \"--log-level\", \"DEBUG\",\n            \"--proxy-mode\", proxy_mode,\n            \"--transport-mode\", get_transport_mode(),\n            \"--circuit-enabled\", \"true\" if cb_cfg.get(\"enabled\", True) else \"false\",\n            \"--circuit-action\", cb_cfg.get(\"action_mode\", \"auto_switch\"),\n            \"--circuit-threshold\", str(cb_cfg.get(\"failure_threshold\", 3)),\n            \"--circuit-cooldown-seconds\", str(cb_cfg.get(\"cooldown_seconds\", 900)),\n        ]\n""",
    "gui proxy command settings",
)
# Auto-stop now restores managed provider before killing proxy, avoiding a dead local base_url.
text = replace_once(
    text,
    """        if auto_cfg.auto_stop_proxy_on_exit and healthy:\n            codex_running = is_codex_running()\n            if getattr(self, \"_had_codex_running\", False) and not codex_running:\n                self._stop_proxy()\n            self._had_codex_running = codex_running\n""",
    """        if auto_cfg.auto_stop_proxy_on_exit and healthy:\n            codex_running = is_codex_running()\n            if getattr(self, \"_had_codex_running\", False) and not codex_running:\n                cfg_now = get_proxy_config_status()\n                if cfg_now.get(\"active\"):\n                    ok, msg = disable_proxy_config()\n                    if not ok:\n                        self._append_log(f\"[自动化] Codex 已退出，但安全恢复 provider 失败，代理保持运行: {msg}\", \"error\")\n                        self._had_codex_running = codex_running\n                        return\n                self._append_log(\"[自动化] 检测到 Codex 已退出，已恢复 provider 并停止代理\", \"info\")\n                self._stop_proxy()\n            self._had_codex_running = codex_running\n""",
    "gui auto stop restore",
)
# Diagnostics actions: replace stale method/attribute names.
text = text.replace("self._app._session_tab._scan_sessions()", "self._app._session_tab._scan()")
text = text.replace("self._app._proxy_tab._transport_var.set(\"http\")\n            self._app._proxy_tab._on_transport_mode_change()", "self._app._proxy_tab._set_transport_mode(\"http\")")
text = text.replace("self._app._proxy_tab._disable_proxy()", "self._app._proxy_tab._disable_proxy_config()")
# Network diagnostics should use the real proxy selector and selected upstream.
old_diag_selector = '''        proxy_mode = self._app._proxy_tab._proxy_mode_var.get()
        proxy_url = None
        if proxy_mode == "custom":
            proxy_url = self._app._proxy_tab._custom_entry.get().strip() or None
        elif proxy_mode == "env":
            proxy_url = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or os.environ.get("ALL_PROXY")

        def worker():
            try:
                res = run_full_diagnostics(port=port, proxy_url=proxy_url)
'''
new_diag_selector = '''        proxy_url = self._app._proxy_tab._selected_outbound_proxy_url()
        upstream_url = self._app._proxy_tab._selected_upstream_url()

        def worker():
            try:
                res = run_full_diagnostics(port=port, upstream_url=upstream_url, proxy_url=proxy_url)
'''
text = replace_once(text, old_diag_selector, new_diag_selector, "gui diagnostics selector")
# Render inconclusive WS as yellow rather than falsely green/red.
text = replace_once(
    text,
    """        ws = res.get(\"websocket\", {})\n        if ws.get(\"ok\"):\n            self._lbl_ws.config(text=f\"握手正常 ({ws.get('handshake_ms')}ms)\", foreground=\"#a6e3a1\")\n        else:\n            err = ws.get(\"error\") or \"失败\"\n            self._lbl_ws.config(text=f\"握手异常: {err[:25]}\", foreground=\"#f38ba8\")\n""",
    """        ws = res.get(\"websocket\", {})\n        if ws.get(\"ok\"):\n            self._lbl_ws.config(text=f\"101 升级成功 ({ws.get('handshake_ms')}ms)\", foreground=\"#a6e3a1\")\n        elif ws.get(\"reachable\") and not ws.get(\"conclusive\", True):\n            self._lbl_ws.config(text=f\"上游可达 / 升级未确认 (HTTP {ws.get('status')})\", foreground=\"#f9e2af\")\n        else:\n            err = ws.get(\"error\") or \"失败\"\n            self._lbl_ws.config(text=f\"握手异常: {err[:25]}\", foreground=\"#f38ba8\")\n""",
    "gui ws diagnostic state",
)
# Force HTTP action uses the canonical ProxyTab helper.
text = replace_once(
    text,
    """    def _switch_to_force_http(self):\n        self._app.select_tab(self._app._proxy_tab)\n        self._app._proxy_tab._transport_var.set(\"http\")\n        self._app._proxy_tab._on_transport_mode_change()\n        messagebox.showinfo(\"已切换\", \"传输模式已切换为「强制 HTTP」。\")\n""",
    """    def _switch_to_force_http(self):\n        self._app.select_tab(self._app._proxy_tab)\n        self._app._proxy_tab._set_transport_mode(\"http\")\n        messagebox.showinfo(\"已切换\", \"传输模式已切换为「强制 HTTP」。\")\n""",
    "gui force http action",
)
# Apply discovered proxy through actual selector model.
old_apply = '''        proxy_url = target_proxy.proxy_url
        self._app._proxy_tab._proxy_mode_var.set("custom")
        self._app._proxy_tab._custom_entry.delete(0, tk.END)
        self._app._proxy_tab._custom_entry.insert(0, proxy_url)
        self._app._proxy_tab._on_proxy_mode_change()
        self._app.select_tab(self._app._proxy_tab)
'''
new_apply = '''        proxy_url = target_proxy.proxy_url
        self._app._proxy_tab._apply_discovered_proxy(proxy_url, target_proxy.name)
        self._app.select_tab(self._app._proxy_tab)
'''
text = replace_once(text, old_apply, new_apply, "gui discovered proxy apply")
# Settings label reflects what the implementation actually does.
text = replace_once(
    text,
    '            text="退出 Toolkit 时自动停止代理并恢复原始 config.toml 配置",\n',
    '            text="Codex 退出后自动停止代理并安全恢复原 provider",\n',
    "gui auto stop label",
)
# Fix breaker setting AttributeError and synchronize actual running proxy.
text = replace_once(
    text,
    """        save_circuit_breaker_config(thresh, cd)\n        GLOBAL_CIRCUIT_BREAKER.failure_threshold = thresh\n        GLOBAL_CIRCUIT_BREAKER.cooldown_seconds = cd\n        messagebox.showinfo(\"已保存\", f\"熔断配置已更新：失败阈值 {thresh} 次，冷却时间 {cd} 秒。\")\n""",
    """        current = get_circuit_breaker_config()\n        save_circuit_breaker_config(\n            failure_threshold=thresh,\n            cooldown_seconds=cd,\n            enabled=current.get(\"enabled\", True),\n            action_mode=current.get(\"action_mode\", \"auto_switch\"),\n        )\n        GLOBAL_CIRCUIT_BREAKER.configure(\n            threshold=thresh,\n            cooldown_seconds=cd,\n            enabled=current.get(\"enabled\", True),\n            action_mode=current.get(\"action_mode\", \"auto_switch\"),\n        )\n        self._app._proxy_tab._post_control(\"/control/circuit/config\", {\n            \"threshold\": thresh,\n            \"cooldown_seconds\": cd,\n            \"enabled\": current.get(\"enabled\", True),\n            \"action_mode\": current.get(\"action_mode\", \"auto_switch\"),\n        })\n        self._refresh_cb_status()\n        messagebox.showinfo(\"已保存\", f\"熔断配置已更新：失败阈值 {thresh} 次，冷却时间 {cd} 秒。\")\n""",
    "gui settings breaker save",
)
text = replace_once(
    text,
    """    def _reset_cb(self):\n        GLOBAL_CIRCUIT_BREAKER.reset()\n        self._refresh_cb_status()\n        messagebox.showinfo(\"已重置\", \"WebSocket 熔断器已重置为 CLOSED 状态。\")\n""",
    """    def _reset_cb(self):\n        GLOBAL_CIRCUIT_BREAKER.reset()\n        self._app._proxy_tab._post_control(\"/control/circuit/reset\", {})\n        self._refresh_cb_status()\n        messagebox.showinfo(\"已重置\", \"WebSocket 熔断器已重置为 CLOSED 状态。\")\n""",
    "gui breaker reset live",
)
# Guard against stale names surviving the review patch.
for stale in (
    "_net_tab.start_diagnostics()",
    "_session_tab._scan_sessions()",
    "_transport_var",
    "_on_transport_mode_change()",
    "_proxy_mode_var",
    "_custom_entry",
    "_disable_proxy()",
):
    if stale in text:
        raise RuntimeError(f"stale GUI symbol remains: {stale}")
write(path, text)


# ---------------------------------------------------------------------------
# Tests for review findings. These are behavior-focused except one narrow
# wiring regression guard for stale GUI symbol names that previously crashed.
# ---------------------------------------------------------------------------
new_tests = r'''from __future__ import annotations

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
from proxy_discovery import test_proxy_ws_async
from startup_manager import NotificationManager
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

            ok, _, msg = await test_proxy_ws_async(
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
'''
write("tests/test_v050_review_fixes.py", new_tests)

# Update the older diagnostic test fixture to include the new explicit fields.
path = "tests/test_network_diagnostics.py"
text = read(path)
text = replace_once(
    text,
    '    ws_res = {"ok": False, "handshake_ms": 2500, "error": "WebSocket 握手超时 (Timeout)", "error_type": "ws_timeout"}\n',
    '    ws_res = {"ok": False, "reachable": False, "conclusive": True, "handshake_ms": 2500, "error": "WebSocket 握手超时 (Timeout)", "error_type": "ws_timeout"}\n',
    "network test failure fields",
)
text = replace_once(
    text,
    '    ws_res = {"ok": True, "handshake_ms": 150, "error": None}\n',
    '    ws_res = {"ok": True, "reachable": True, "conclusive": True, "handshake_ms": 150, "error": None}\n',
    "network test success fields",
)
write(path, text)

print("v0.5.0 review fixes applied")
