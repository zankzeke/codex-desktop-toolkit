from __future__ import annotations

from pathlib import Path

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


# ---------------------------------------------------------------------------
# Transport status text: standalone proxy blocking is not itself an HTTP
# transport conversion. The GUI performs the config-level temporary downgrade.
# ---------------------------------------------------------------------------
path = "transport_policy.py"
text = read(path)
text = replace_once(
    text,
    '                action_desc = "已临时切换 HTTP" if self.action_mode == "auto_switch" else "建议使用 HTTP"\n',
    '                action_desc = "已触发自动降级策略" if self.action_mode == "auto_switch" else "仅提示，不改变传输"\n',
    "breaker honest status",
)
write(path, text)


# ---------------------------------------------------------------------------
# Proxy local control API: fail closed on malformed JSON/types. These endpoints
# are loopback-only, but strict parsing prevents accidental surprising changes.
# ---------------------------------------------------------------------------
path = "proxy.py"
text = read(path)
text = replace_once(
    text,
    '''    async def handle_control_transport(self, request: web.Request) -> web.Response:\n        data = await request.json()\n        mode = str(data.get("mode", "")).lower().strip()\n        if mode not in {"auto", "websocket", "http"}:\n            return web.json_response({"ok": False, "error": "invalid transport mode"}, status=400)\n        self.transport_mode = mode\n        self.stats.transport_mode = mode\n        return web.json_response({"ok": True, "transport_mode": mode})\n\n    async def handle_control_circuit_config(self, request: web.Request) -> web.Response:\n        data = await request.json()\n        try:\n            threshold = max(1, int(data.get("threshold", self.circuit_breaker.threshold)))\n            cooldown = max(1, int(data.get("cooldown_seconds", self.circuit_breaker.cooldown_seconds)))\n        except (TypeError, ValueError):\n            return web.json_response({"ok": False, "error": "invalid circuit configuration"}, status=400)\n        action = str(data.get("action_mode", self.circuit_breaker.action_mode))\n        if action not in {"auto_switch", "notify_only"}:\n            return web.json_response({"ok": False, "error": "invalid action_mode"}, status=400)\n        self.circuit_breaker.configure(\n            threshold=threshold,\n            cooldown_seconds=cooldown,\n            enabled=bool(data.get("enabled", self.circuit_breaker.enabled)),\n            action_mode=action,\n        )\n        return web.json_response({"ok": True, "circuit_breaker": self.circuit_breaker.snapshot(self.transport_mode)})\n''',
    '''    async def handle_control_transport(self, request: web.Request) -> web.Response:\n        try:\n            data = await request.json()\n        except (ValueError, json.JSONDecodeError):\n            return web.json_response({"ok": False, "error": "invalid JSON body"}, status=400)\n        if not isinstance(data, dict):\n            return web.json_response({"ok": False, "error": "JSON body must be an object"}, status=400)\n        mode = str(data.get("mode", "")).lower().strip()\n        if mode not in {"auto", "websocket", "http"}:\n            return web.json_response({"ok": False, "error": "invalid transport mode"}, status=400)\n        self.transport_mode = mode\n        self.stats.transport_mode = mode\n        return web.json_response({"ok": True, "transport_mode": mode})\n\n    async def handle_control_circuit_config(self, request: web.Request) -> web.Response:\n        try:\n            data = await request.json()\n        except (ValueError, json.JSONDecodeError):\n            return web.json_response({"ok": False, "error": "invalid JSON body"}, status=400)\n        if not isinstance(data, dict):\n            return web.json_response({"ok": False, "error": "JSON body must be an object"}, status=400)\n        try:\n            threshold = max(1, int(data.get("threshold", self.circuit_breaker.threshold)))\n            cooldown = max(1, int(data.get("cooldown_seconds", self.circuit_breaker.cooldown_seconds)))\n        except (TypeError, ValueError):\n            return web.json_response({"ok": False, "error": "invalid circuit configuration"}, status=400)\n        action = str(data.get("action_mode", self.circuit_breaker.action_mode))\n        if action not in {"auto_switch", "notify_only"}:\n            return web.json_response({"ok": False, "error": "invalid action_mode"}, status=400)\n        enabled = data.get("enabled", self.circuit_breaker.enabled)\n        if not isinstance(enabled, bool):\n            return web.json_response({"ok": False, "error": "enabled must be boolean"}, status=400)\n        self.circuit_breaker.configure(\n            threshold=threshold,\n            cooldown_seconds=cooldown,\n            enabled=enabled,\n            action_mode=action,\n        )\n        return web.json_response({"ok": True, "circuit_breaker": self.circuit_breaker.snapshot(self.transport_mode)})\n''',
    "strict proxy controls",
)
write(path, text)


# ---------------------------------------------------------------------------
# config_manager.py: make transient provider WS override atomic/cleanup-safe.
# ---------------------------------------------------------------------------
path = "config_manager.py"
text = read(path)
old = '''def update_managed_ws_support(ws_enabled: bool) -> Tuple[bool, str]:\n    """Update supports_websockets in config.toml without altering provider or base_url."""\n    if not CONFIG_PATH.exists():\n        return False, "Config file not found."\n    try:\n        with open(CONFIG_PATH, "r", encoding="utf-8") as f:\n            doc = tomlkit.load(f)\n        providers = doc.get("model_providers", {})\n        if "openai-idfix" not in providers:\n            return False, "openai-idfix provider not found in config.toml"\n\n        block = providers["openai-idfix"]\n        if block.get("supports_websockets") != bool(ws_enabled):\n            backup_file(CONFIG_PATH)\n        block["supports_websockets"] = bool(ws_enabled)\n\n        state = _load_state()\n        state["managed_proxy_ws"] = bool(ws_enabled)\n\n        temp_path = CONFIG_PATH.with_suffix(f".tmp.{time.time()}")\n        with open(temp_path, "w", encoding="utf-8") as f:\n            tomlkit.dump(doc, f)\n            f.flush()\n            os.fsync(f.fileno())\n        with open(temp_path, "r", encoding="utf-8") as f:\n            tomlkit.load(f)\n        os.replace(temp_path, CONFIG_PATH)\n        _save_state(state)\n        return True, f"Updated supports_websockets to {ws_enabled}"\n    except Exception as exc:\n        return False, f"Failed to update supports_websockets: {exc}"\n'''
new = '''def update_managed_ws_support(ws_enabled: bool) -> Tuple[bool, str]:\n    """Update supports_websockets atomically without changing provider/base_url."""\n    if not CONFIG_PATH.exists():\n        return False, "Config file not found."\n    temp_path = CONFIG_PATH.with_suffix(f".tmp.{time.time()}")\n    try:\n        with open(CONFIG_PATH, "r", encoding="utf-8") as f:\n            doc = tomlkit.load(f)\n        providers = doc.get("model_providers", {})\n        if "openai-idfix" not in providers:\n            return False, "openai-idfix provider not found in config.toml"\n\n        desired = bool(ws_enabled)\n        block = providers["openai-idfix"]\n        current = bool(block.get("supports_websockets", True))\n        if current == desired:\n            state = _load_state()\n            state["managed_proxy_ws"] = desired\n            _save_state(state)\n            return True, f"supports_websockets already {desired}"\n\n        backup_file(CONFIG_PATH)\n        block["supports_websockets"] = desired\n        with open(temp_path, "w", encoding="utf-8") as f:\n            tomlkit.dump(doc, f)\n            f.flush()\n            os.fsync(f.fileno())\n        with open(temp_path, "r", encoding="utf-8") as f:\n            tomlkit.load(f)\n        os.replace(temp_path, CONFIG_PATH)\n\n        state = _load_state()\n        state["managed_proxy_ws"] = desired\n        _save_state(state)\n        return True, f"Updated supports_websockets to {desired}"\n    except Exception as exc:\n        return False, f"Failed to update supports_websockets: {exc}"\n    finally:\n        if temp_path.exists():\n            try:\n                temp_path.unlink()\n            except OSError:\n                pass\n'''
text = replace_once(text, old, new, "atomic managed WS override")
write(path, text)


# ---------------------------------------------------------------------------
# Support bundle: broaden credential redaction (Basic auth, proxy auth, JWT,
# common token prefixes) and sensitive config-key detection.
# ---------------------------------------------------------------------------
path = "support_bundle.py"
text = read(path)
text = replace_once(
    text,
    '''    # 2. Redact API keys / OpenAI keys: sk-...\n    text = re.sub(r"\\bsk-[a-zA-Z0-9_-]{10,}\\b", "sk-***REDACTED***", text)\n\n    # 3. Redact Bearer / Authorization tokens\n    text = re.sub(r"(?i)(authorization\\s*[:=]\\s*bearer\\s+)[^\\s,;\\\"]+", r"\\1***REDACTED***", text)\n    text = re.sub(r"(?i)(bearer\\s+)[a-zA-Z0-9\\._\\-]{20,}", r"\\1***REDACTED***", text)\n\n    # 4. Redact Cookies / Session tokens\n    text = re.sub(r"(?i)(session_token\\s*=\\s*)[^\\s,;\\\"]+", r"\\1***REDACTED***", text)\n    text = re.sub(r"(?i)(cookie\\s*[:=]\\s*)[^\\r\\n]+", r"\\1***REDACTED***", text)\n\n    # 5. Redact URLs with embedded user:pass or queries\n''',
    '''    # 2. Redact common API/token formats, including JWT-like values.\n    text = re.sub(r"\\bsk-[a-zA-Z0-9_-]{10,}\\b", "sk-***REDACTED***", text)\n    text = re.sub(r"\\b(?:ghp_|github_pat_)[a-zA-Z0-9_-]{10,}\\b", "***REDACTED***", text)\n    text = re.sub(\n        r"\\b[a-zA-Z0-9_-]{12,}\\.[a-zA-Z0-9_-]{12,}\\.[a-zA-Z0-9_-]{12,}\\b",\n        "***REDACTED-JWT***",\n        text,\n    )\n\n    # 3. Redact authentication headers regardless of auth scheme.\n    text = re.sub(\n        r"(?im)^((?:proxy-)?authorization\\s*[:=]\\s*)[^\\r\\n]+",\n        r"\\1***REDACTED***",\n        text,\n    )\n    text = re.sub(r"(?i)(bearer\\s+)[^\\s,;\\\"]+", r"\\1***REDACTED***", text)\n\n    # 4. Redact cookie/token header and key-value forms.\n    text = re.sub(r"(?im)^((?:set-)?cookie\\s*[:=]\\s*)[^\\r\\n]+", r"\\1***REDACTED***", text)\n    text = re.sub(\n        r"(?i)((?:session[_-]?token|access[_-]?token|refresh[_-]?token|x-access-token|x-refresh-token)\\s*[:=]\\s*)[^\\s,;\\\"]+",\n        r"\\1***REDACTED***",\n        text,\n    )\n\n    # 5. Redact URLs with embedded user:pass or queries\n''',
    "support bundle credential patterns",
)
text = replace_once(
    text,
    '''            if any(k in s.lower() for k in ("api_key", "secret", "token", "password")):\n''',
    '''            if any(k in s.lower() for k in (\n                "api_key", "apikey", "secret", "token", "password", "passwd",\n                "credential", "cookie", "authorization", "private_key", "access_key",\n            )):\n''',
    "support config sensitive keys",
)
write(path, text)


# ---------------------------------------------------------------------------
# GUI: expose breaker action policy and make auto_switch a real temporary HTTP
# downgrade at the managed provider level. The override is reversible after
# cooldown/HALF_OPEN and does not alter the user's persistent transport choice.
# ---------------------------------------------------------------------------
path = "codex_toolkit_gui.py"
text = read(path)
text = replace_once(
    text,
    '''    get_transport_mode, set_transport_mode,\n    get_circuit_breaker_config, save_circuit_breaker_config,\n''',
    '''    get_transport_mode, set_transport_mode, update_managed_ws_support,\n    get_circuit_breaker_config, save_circuit_breaker_config,\n''',
    "GUI WS override import",
)
text = replace_once(
    text,
    '''        self._launch_pending = False\n        self._status_poll_busy = False\n        self._last_stats = {}\n        self._build()\n''',
    '''        self._launch_pending = False\n        self._status_poll_busy = False\n        self._last_stats = {}\n        self._circuit_http_override = False\n        self._circuit_probe_pending = False\n        self._build()\n''',
    "GUI circuit flags",
)
text = replace_once(
    text,
    '''        ttk.Checkbutton(\n            row_toml, text="自动熔断", variable=self._cb_enabled_var,\n            command=self._on_circuit_config_changed\n        ).pack(side=tk.LEFT, padx=(0, 4))\n\n        self._cooldown_var = tk.StringVar(value=f"{cb_cfg.get('cooldown_minutes', 15)} 分钟")\n''',
    '''        ttk.Checkbutton(\n            row_toml, text="自动熔断", variable=self._cb_enabled_var,\n            command=self._on_circuit_config_changed\n        ).pack(side=tk.LEFT, padx=(0, 4))\n\n        action_label = "自动临时 HTTP" if cb_cfg.get("action_mode") == "auto_switch" else "仅提示"\n        self._cb_action_var = tk.StringVar(value=action_label)\n        self._cb_action_cb = ttk.Combobox(\n            row_toml, textvariable=self._cb_action_var,\n            values=["自动临时 HTTP", "仅提示"],\n            state="readonly", width=12,\n        )\n        self._cb_action_cb.pack(side=tk.LEFT, padx=(0, 6))\n        self._cb_action_cb.bind("<<ComboboxSelected>>", self._on_circuit_config_changed)\n\n        self._cooldown_var = tk.StringVar(value=f"{cb_cfg.get('cooldown_minutes', 15)} 分钟")\n''',
    "GUI breaker action selector",
)
# Inject actual temporary downgrade handler before _post_control.
anchor = '''    def _post_control(self, path: str, payload: dict | None = None) -> bool:\n'''
handler = '''    def _handle_circuit_transport_policy(self, cb_snap: dict, cfg_status: dict) -> None:\n        """Apply/revert temporary config-level HTTP downgrade for auto mode."""\n        mode = get_transport_mode()\n        state = str(cb_snap.get("state") or "CLOSED")\n        action = str(cb_snap.get("action_mode") or "notify_only")\n        enabled = bool(cb_snap.get("enabled", False))\n\n        if mode != "auto" or not enabled or action != "auto_switch":\n            if self._circuit_http_override:\n                desired_ws = mode != "http"\n                ok, msg = update_managed_ws_support(desired_ws)\n                if ok:\n                    was_probe = self._circuit_probe_pending\n                    self._circuit_http_override = False\n                    self._circuit_probe_pending = False\n                    self._append_log("[熔断] 自动临时 HTTP 已取消，恢复用户传输策略。", "info")\n                    if not was_probe and cfg_status.get("active") and is_codex_running():\n                        restart_codex()\n                else:\n                    self._append_log(f"[熔断] 恢复 provider 失败: {msg}", "error")\n            return\n\n        # Only mutate the provider we manage, and only when it is actually active.\n        if not cfg_status.get("active"):\n            return\n\n        if state == "OPEN":\n            if (not self._circuit_http_override) or self._circuit_probe_pending:\n                needs_restart = bool(cfg_status.get("supports_websockets", True)) or self._circuit_probe_pending\n                ok, msg = update_managed_ws_support(False)\n                if not ok:\n                    self._append_log(f"[熔断] 临时 HTTP 降级失败: {msg}", "error")\n                    return\n                self._circuit_http_override = True\n                self._circuit_probe_pending = False\n                self._append_log("[熔断] 连续 WS 失败，已临时关闭 provider WebSocket 支持。", "warn")\n                if needs_restart and is_codex_running():\n                    ok_restart, restart_msg = restart_codex()\n                    self._append_log(\n                        f"[熔断] {'已重启 Codex 进入 HTTP 模式' if ok_restart else 'Codex 自动重启失败'}: {restart_msg}",\n                        "warn" if ok_restart else "error",\n                    )\n            return\n\n        if state == "HALF_OPEN" and self._circuit_http_override and not self._circuit_probe_pending:\n            ok, msg = update_managed_ws_support(True)\n            if not ok:\n                self._append_log(f"[熔断] 半开探测启用 WS 失败: {msg}", "error")\n                return\n            self._circuit_probe_pending = True\n            self._append_log("[熔断] 冷却结束，已临时恢复 WS 并准备一次半开探测。", "info")\n            if is_codex_running():\n                ok_restart, restart_msg = restart_codex()\n                self._append_log(\n                    f"[熔断] {'已重启 Codex 进行 WS 半开探测' if ok_restart else 'Codex 自动重启失败'}: {restart_msg}",\n                    "info" if ok_restart else "error",\n                )\n            return\n\n        if state == "CLOSED" and self._circuit_http_override:\n            was_probe = self._circuit_probe_pending\n            ok, msg = update_managed_ws_support(True)\n            if not ok:\n                self._append_log(f"[熔断] 恢复 WS provider 配置失败: {msg}", "error")\n                return\n            self._circuit_http_override = False\n            self._circuit_probe_pending = False\n            self._append_log("[熔断] WebSocket 已恢复稳定，自动 HTTP 降级结束。", "ok")\n            # A successful half-open probe already ran with WS enabled; a manual\n            # reset did not, so reload Codex only in the latter case.\n            if not was_probe and is_codex_running():\n                restart_codex()\n\n'''
if anchor not in text:
    raise RuntimeError("GUI circuit handler anchor missing")
text = text.replace(anchor, handler + anchor, 1)
text = replace_once(
    text,
    '''        # Automation checks\n        auto_cfg = load_automation_settings()\n        if auto_cfg.windows_notifications:\n            if healthy and stats.get("traffic_verified"):\n                GLOBAL_NOTIFIER.send_notification("Codex Bridge Toolkit", "Codex 已通过本地代理建立连接", key="traffic_ok")\n            if cb_snap.get("state") == "OPEN":\n                GLOBAL_NOTIFIER.send_notification("Codex Bridge Toolkit", "WebSocket 连续失败，已临时切换 HTTP", key="circuit_open")\n''',
    '''        # Circuit breaker can optionally perform a real provider-level\n        # temporary HTTP downgrade, while notify-only mode leaves transport alone.\n        self._handle_circuit_transport_policy(cb_snap, cfg)\n\n        # Automation checks\n        auto_cfg = load_automation_settings()\n        if auto_cfg.windows_notifications:\n            if healthy and stats.get("traffic_verified"):\n                GLOBAL_NOTIFIER.send_notification("Codex Bridge Toolkit", "Codex 已通过本地代理建立连接", key="traffic_ok")\n            if cb_snap.get("state") == "OPEN":\n                if cb_snap.get("action_mode") == "auto_switch":\n                    notice = "WebSocket 连续失败，已启动临时 HTTP 降级策略"\n                else:\n                    notice = "WebSocket 连续失败；当前为仅提示模式，可运行网络体检或切换强制 HTTP"\n                GLOBAL_NOTIFIER.send_notification("Codex Bridge Toolkit", notice, key="circuit_open")\n''',
    "GUI real circuit policy",
)
# User manual transport changes cancel any stale auto-override bookkeeping.
text = replace_once(
    text,
    '''        ok, msg = set_transport_mode(mode)\n        self._append_log(f"[传输模式] {msg}", "info" if ok else "warn")\n''',
    '''        ok, msg = set_transport_mode(mode)\n        if ok:\n            self._circuit_http_override = False\n            self._circuit_probe_pending = False\n        self._append_log(f"[传输模式] {msg}", "info" if ok else "warn")\n''',
    "GUI manual transport override bookkeeping",
)
text = replace_once(
    text,
    '''        cfg = {\n            "enabled": self._cb_enabled_var.get(),\n            "action_mode": "auto_switch",\n            "cooldown_minutes": cd_min,\n            "failure_threshold": 3,\n        }\n''',
    '''        current_cfg = get_circuit_breaker_config()\n        cfg = {\n            "enabled": self._cb_enabled_var.get(),\n            "action_mode": "auto_switch" if self._cb_action_var.get() == "自动临时 HTTP" else "notify_only",\n            "cooldown_minutes": cd_min,\n            "failure_threshold": current_cfg.get("failure_threshold", 3),\n        }\n''',
    "GUI action mapping",
)
text = replace_once(
    text,
    '''            threshold=3,\n            action_mode=cfg["action_mode"],\n        )\n        self._post_control("/control/circuit/config", {\n            "enabled": cfg["enabled"],\n            "action_mode": cfg["action_mode"],\n            "threshold": 3,\n''',
    '''            threshold=cfg["failure_threshold"],\n            action_mode=cfg["action_mode"],\n        )\n        self._post_control("/control/circuit/config", {\n            "enabled": cfg["enabled"],\n            "action_mode": cfg["action_mode"],\n            "threshold": cfg["failure_threshold"],\n''',
    "GUI action live sync threshold",
)
text = replace_once(
    text,
    '''        self._append_log(f"[熔断配置] 已更新: 启用={cfg['enabled']}, 冷却={cd_min}分钟", "info")\n''',
    '''        self._append_log(\n            f"[熔断配置] 已更新: 启用={cfg['enabled']}, 策略={self._cb_action_var.get()}, 冷却={cd_min}分钟",\n            "info",\n        )\n        if not cfg["enabled"] and self._circuit_http_override:\n            desired_ws = get_transport_mode() != "http"\n            ok_restore, restore_msg = update_managed_ws_support(desired_ws)\n            if ok_restore:\n                self._circuit_http_override = False\n                self._circuit_probe_pending = False\n                if is_codex_running():\n                    restart_codex()\n            else:\n                self._append_log(f"[熔断] 关闭熔断器时恢复 provider 失败: {restore_msg}", "error")\n''',
    "GUI disable breaker restore",
)
text = replace_once(
    text,
    '        ttk.Label(grid, text="次 (达到后自动跳过 WS 握手，直接降级 HTTP)").grid(row=0, column=2, sticky=tk.W, padx=4, pady=4)\n',
    '        ttk.Label(grid, text="次 (达到后按代理控制页策略：仅提示或临时 HTTP；临时 HTTP 需要重载 Codex)").grid(row=0, column=2, sticky=tk.W, padx=4, pady=4)\n',
    "GUI honest settings description",
)
write(path, text)


# ---------------------------------------------------------------------------
# Regression tests for final hardening.
# ---------------------------------------------------------------------------
path = "tests/test_v050_review_fixes.py"
text = read(path)
text = replace_once(
    text,
    "from startup_manager import NotificationManager\n",
    "from startup_manager import NotificationManager\nfrom support_bundle import redact_text_content\n",
    "test support import",
)
text += r'''


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
'''
write(path, text)

print("final v0.5 review hardening applied")
