from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]


def p(rel: str) -> Path:
    return ROOT / rel


def read(rel: str) -> str:
    return p(rel).read_text(encoding="utf-8")


def write(rel: str, text: str) -> None:
    p(rel).write_text(text, encoding="utf-8", newline="\n")


def replace_once(rel: str, old: str, new: str) -> None:
    text = read(rel)
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{rel}: expected one match, got {count}: {old[:120]!r}")
    write(rel, text.replace(old, new, 1))


# config_manager -----------------------------------------------------------------
replace_once(
    "config_manager.py",
    'ENV_PROXY_SENTINEL = "__SYSTEM_ENV__"',
    'ENV_PROXY_SENTINEL = "系统环境代理"',
)

# proxy.py ------------------------------------------------------------------------
replace_once(
    "proxy.py",
    "from sse_handler import SSELineBuffer, rewrite_sse_line\n",
    "from sse_handler import SSELineBuffer, rewrite_sse_line\nfrom runtime_stats import RuntimeStats\n",
)
replace_once(
    "proxy.py",
    '    def __init__(self, upstream_base: str, reasoning_mode: str, port: int, upstream_proxy: str | None = None) -> None:\n'
    '        self.upstream_base = upstream_base.rstrip("/")\n'
    '        self.reasoning_mode = reasoning_mode\n'
    '        self.port = port\n'
    '        self.upstream_proxy = upstream_proxy\n'
    '        self._session: aiohttp.ClientSession | None = None\n',
    '    def __init__(self, upstream_base: str, reasoning_mode: str, port: int, upstream_proxy: str | None = None, proxy_mode: str = "direct") -> None:\n'
    '        self.upstream_base = upstream_base.rstrip("/")\n'
    '        self.reasoning_mode = reasoning_mode\n'
    '        self.port = port\n'
    '        self.upstream_proxy = upstream_proxy\n'
    '        self.proxy_mode = proxy_mode\n'
    '        self.stats = RuntimeStats()\n'
    '        self._session: aiohttp.ClientSession | None = None\n',
)
replace_once("proxy.py", "            trust_env=True,\n", '            trust_env=self.proxy_mode == "env",\n')
replace_once(
    "proxy.py",
    '        if self.upstream_proxy:\n            logger.info("Upstream Proxy: %s", self._mask_url(self.upstream_proxy))\n        logger.info("Reasoning mode: %s", self.reasoning_mode)\n',
    '        logger.info("Proxy mode: %s", self.proxy_mode)\n'
    '        if self.proxy_mode == "explicit" and self.upstream_proxy:\n'
    '            logger.info("Upstream Proxy: %s", self._mask_url(self.upstream_proxy))\n'
    '        logger.info("Reasoning mode: %s", self.reasoning_mode)\n',
)
replace_once(
    "proxy.py",
    '            "reasoning_mode": self.reasoning_mode,\n            "upstream_proxy": self._mask_url(self.upstream_proxy),\n',
    '            "reasoning_mode": self.reasoning_mode,\n'
    '            "proxy_mode": self.proxy_mode,\n'
    '            "upstream_proxy": self._mask_url(self.upstream_proxy) if self.proxy_mode == "explicit" else None,\n',
)
replace_once(
    "proxy.py",
    '        })\n\n    # ------------------------------------------------------------------ #\n    # Main proxy catch-all',
    '        })\n\n'
    '    async def handle_stats(self, request: web.Request) -> web.Response:\n'
    '        return web.json_response(self.stats.snapshot())\n\n'
    '    # ------------------------------------------------------------------ #\n    # Main proxy catch-all',
)
replace_once(
    "proxy.py",
    '        path = request.raw_path  # includes query string\n        upstream_url = self._build_upstream_url(path)\n',
    '        path = request.raw_path  # includes query string\n'
    '        transport = "websocket" if request.headers.get("Upgrade", "").lower() == "websocket" else "http"\n'
    '        self.stats.record_request(request.method, path, transport)\n'
    '        upstream_url = self._build_upstream_url(path)\n',
)
replace_once(
    "proxy.py",
    '        if msg_fixes or reasoning_drops:\n            logger.info(\n',
    '        self.stats.record_rewrite(msg_fixes, reasoning_drops)\n\n'
    '        if msg_fixes or reasoning_drops:\n            logger.info(\n',
)
text = read("proxy.py").replace(
    "proxy=self.upstream_proxy,",
    'proxy=self.upstream_proxy if self.proxy_mode == "explicit" else None,',
)
write("proxy.py", text)
replace_once(
    "proxy.py",
    '        except aiohttp.ClientError as exc:\n            logger.error("Upstream connection error: %s", type(exc).__name__)\n            return web.Response(status=502, text="Proxy upstream connection failed")\n\n        logger.info("[IN ] upstream status: %d (url: %s)", upstream_resp.status, self._mask_url(upstream_url))\n',
    '        except aiohttp.ClientError as exc:\n'
    '            self.stats.record_error(502, "connection failed")\n'
    '            logger.error("Upstream connection error: %s", type(exc).__name__)\n'
    '            return web.Response(status=502, text="Proxy upstream connection failed")\n\n'
    '        self.stats.record_response(upstream_resp.status)\n'
    '        logger.info("[IN ] upstream status: %d (url: %s)", upstream_resp.status, self._mask_url(upstream_url))\n',
)
replace_once(
    "proxy.py",
    '        if is_sse:\n            return await self._stream_sse(request, upstream_resp, resp_headers, path)\n',
    '        if is_sse:\n'
    '            self.stats.record_sse()\n'
    '            return await self._stream_sse(request, upstream_resp, resp_headers, path)\n',
)
replace_once(
    "proxy.py",
    '        resp_body = await upstream_resp.read()\n        if is_responses_endpoint and resp_body:\n',
    '        resp_body = await upstream_resp.read()\n'
    '        if upstream_resp.status >= 400 and resp_body:\n'
    '            try:\n'
    '                err_obj = json.loads(resp_body)\n'
    '                err_val = err_obj.get("error", err_obj) if isinstance(err_obj, dict) else err_obj\n'
    '                self.stats.record_error(upstream_resp.status, str(err_val))\n'
    '            except Exception:\n'
    '                pass\n'
    '        if is_responses_endpoint and resp_body:\n',
)
replace_once(
    "proxy.py",
    '        logger.info("[WS ] connect -> %s", self._mask_url(upstream_url))\n        id_map: dict[str, str] = {}\n\n        try:\n',
    '        logger.info("[WS ] connect -> %s", self._mask_url(upstream_url))\n'
    '        id_map: dict[str, str] = {}\n'
    '        ws_close_code: int | None = None\n'
    '        ws_counted = False\n\n'
    '        try:\n',
)
replace_once(
    "proxy.py",
    '                await ws_client.prepare(request)\n                \n                logger.info("[WS ] upstream established; accepting client")\n',
    '                await ws_client.prepare(request)\n'
    '                self.stats.ws_connected()\n'
    '                ws_counted = True\n'
    '                \n'
    '                logger.info("[WS ] upstream established; accepting client")\n',
)
replace_once(
    "proxy.py",
    '                async def client_to_upstream():\n                    try:\n',
    '                async def client_to_upstream():\n'
    '                    nonlocal ws_close_code\n'
    '                    try:\n',
)
replace_once(
    "proxy.py",
    '                async def upstream_to_client():\n                    try:\n',
    '                async def upstream_to_client():\n'
    '                    nonlocal ws_close_code\n'
    '                    try:\n',
)
replace_once(
    "proxy.py",
    '                                    if f or d:\n                                        logger.info("WS c->u | msg-id fixes: %d | drops: %d", f, d)\n                                    await ws_upstream.send_str(json.dumps(obj, ensure_ascii=False))\n',
    '                                    if f or d:\n'
    '                                        self.stats.record_rewrite(f, d)\n'
    '                                        logger.info("WS c->u | msg-id fixes: %d | drops: %d", f, d)\n'
    '                                    await ws_upstream.send_str(json.dumps(obj, ensure_ascii=False))\n',
)
# Both client and upstream CLOSE blocks have this exact fragment.
text = read("proxy.py")
old_close = "                            elif msg.type == aiohttp.WSMsgType.CLOSE:\n                                extra = msg.extra.encode('utf-8') if isinstance(msg.extra, str) else msg.extra\n"
if text.count(old_close) != 2:
    raise RuntimeError(f"proxy.py: expected 2 WS CLOSE blocks, found {text.count(old_close)}")
text = text.replace(
    old_close,
    "                            elif msg.type == aiohttp.WSMsgType.CLOSE:\n                                ws_close_code = msg.data if isinstance(msg.data, int) else ws_close_code\n                                extra = msg.extra.encode('utf-8') if isinstance(msg.extra, str) else msg.extra\n",
)
# Track 1011 sanitizer failures.
text = text.replace(
    '                                    await ws_client.close(code=1011, message=b"Internal Proxy Error")\n                                    break\n',
    '                                    ws_close_code = 1011\n                                    await ws_client.close(code=1011, message=b"Internal Proxy Error")\n                                    break\n',
    1,
)
text = text.replace(
    '                                    await ws_upstream.close(code=1011, message=b"Internal Proxy Error")\n                                    break\n',
    '                                    ws_close_code = 1011\n                                    await ws_upstream.close(code=1011, message=b"Internal Proxy Error")\n                                    break\n',
    1,
)
write("proxy.py", text)
replace_once(
    "proxy.py",
    '        except aiohttp.ClientError as exc:\n            logger.error("WS Upstream handshake failed: %s", type(exc).__name__)\n            # Reject client if we haven\'t prepared yet\n            return web.Response(status=502, text="WS upstream connection failed")\n        except Exception as e:\n            logger.error("WS Proxy Error: %s", type(e).__name__)\n        finally:\n            logger.info("[WS ] disconnected")\n',
    '        except aiohttp.ClientError as exc:\n'
    '            self.stats.ws_failed("websocket handshake failed")\n'
    '            logger.error("WS Upstream handshake failed: %s", type(exc).__name__)\n'
    '            # Reject client if we haven\'t prepared yet\n'
    '            return web.Response(status=502, text="WS upstream connection failed")\n'
    '        except Exception as e:\n'
    '            self.stats.ws_failed("websocket proxy error")\n'
    '            logger.error("WS Proxy Error: %s", type(e).__name__)\n'
    '        finally:\n'
    '            if ws_counted:\n'
    '                code = ws_close_code\n'
    '                try:\n'
    '                    code = code or getattr(ws_upstream, "close_code", None) or getattr(ws_client, "close_code", None)\n'
    '                except Exception:\n'
    '                    pass\n'
    '                self.stats.ws_closed(code)\n'
    '            logger.info("[WS ] disconnected")\n',
)
replace_once(
    "proxy.py",
    '                                    code = err.get("code") or err.get("type") or evt_type\n                                    logger.warning("SSE error event: type=%r code=%r", evt_type, code)\n',
    '                                    code = err.get("code") or err.get("type") or evt_type\n'
    '                                    message = err.get("message") if isinstance(err, dict) else None\n'
    '                                    self.stats.record_error(upstream_resp.status, str(message or code), event_type=evt_type)\n'
    '                                    logger.warning("SSE error event: type=%r code=%r", evt_type, code)\n',
)
# SSE line-level rewrite fixes are already counted via line return; record once at end.
replace_once(
    "proxy.py",
    '        if total_fixes:\n            logger.info("sse stream complete: %d id fixes total", total_fixes)\n',
    '        if total_fixes:\n'
    '            self.stats.record_rewrite(total_fixes, 0)\n'
    '            logger.info("sse stream complete: %d id fixes total", total_fixes)\n',
)
replace_once(
    "proxy.py",
    'def make_app(upstream_base: str, reasoning_mode: str, port: int, upstream_proxy: str | None = None) -> web.Application:\n    proxy = CodexProxy(upstream_base=upstream_base, reasoning_mode=reasoning_mode, port=port, upstream_proxy=upstream_proxy)\n',
    'def make_app(upstream_base: str, reasoning_mode: str, port: int, upstream_proxy: str | None = None, proxy_mode: str = "direct") -> web.Application:\n'
    '    proxy = CodexProxy(upstream_base=upstream_base, reasoning_mode=reasoning_mode, port=port, upstream_proxy=upstream_proxy, proxy_mode=proxy_mode)\n',
)
replace_once(
    "proxy.py",
    '    app.router.add_get("/health/details", proxy.handle_health_details)\n    app.router.add_route("*", "/{path_info:.*}", proxy.handle_proxy)\n',
    '    app.router.add_get("/health/details", proxy.handle_health_details)\n'
    '    app.router.add_get("/stats", proxy.handle_stats)\n'
    '    app.router.add_route("*", "/{path_info:.*}", proxy.handle_proxy)\n',
)
replace_once(
    "proxy.py",
    '    p.add_argument("--upstream-proxy", default=os.environ.get("PROXY_UPSTREAM_PROXY", None), help="Optional HTTP proxy for outbound connections")\n',
    '    p.add_argument("--upstream-proxy", default=os.environ.get("PROXY_UPSTREAM_PROXY", None), help="HTTP proxy URL used when --proxy-mode=explicit")\n'
    '    p.add_argument("--proxy-mode", choices=["direct", "env", "explicit"], default=os.environ.get("PROXY_MODE", "direct"), help="Outbound networking: true direct, system environment proxy, or explicit proxy URL")\n',
)
replace_once(
    "proxy.py",
    '    upstream_proxy: str | None = args.upstream_proxy\n    reasoning_mode: str = args.reasoning_mode\n',
    '    upstream_proxy: str | None = args.upstream_proxy\n'
    '    proxy_mode: str = args.proxy_mode\n'
    '    if upstream_proxy and proxy_mode == "direct":\n'
    '        proxy_mode = "explicit"  # backwards-compatible CLI behaviour\n'
    '    reasoning_mode: str = args.reasoning_mode\n',
)
replace_once(
    "proxy.py",
    '    if parsed_upstream.scheme not in {"http", "https"} or not parsed_upstream.hostname:\n        raise SystemExit("--upstream must be an absolute http(s) URL")\n',
    '    if parsed_upstream.scheme not in {"http", "https"} or not parsed_upstream.hostname:\n'
    '        raise SystemExit("--upstream must be an absolute http(s) URL")\n'
    '    if proxy_mode == "explicit" and not upstream_proxy:\n'
    '        raise SystemExit("--proxy-mode=explicit requires --upstream-proxy")\n',
)
replace_once(
    "proxy.py",
    '    if upstream_proxy:\n        print(f"  Proxy:     {_mask_url(upstream_proxy)}")\n    print(f"  Reasoning mode: {reasoning_mode}")\n',
    '    print(f"  Proxy mode: {proxy_mode}")\n'
    '    if proxy_mode == "explicit" and upstream_proxy:\n'
    '        print(f"  Proxy:     {_mask_url(upstream_proxy)}")\n'
    '    print(f"  Reasoning mode: {reasoning_mode}")\n',
)
replace_once(
    "proxy.py",
    '    app = make_app(upstream_base=upstream, reasoning_mode=reasoning_mode, port=port, upstream_proxy=upstream_proxy)\n',
    '    app = make_app(upstream_base=upstream, reasoning_mode=reasoning_mode, port=port, upstream_proxy=upstream_proxy, proxy_mode=proxy_mode)\n',
)

# codex_toolkit_gui.py -------------------------------------------------------------
replace_once("codex_toolkit_gui.py", "import tkinter as tk\n", "import tkinter as tk\nimport urllib.request\nimport webbrowser\n")
replace_once(
    "codex_toolkit_gui.py",
    "    load_proxies, save_proxies\n)\n",
    "    load_proxies, save_proxies, ENV_PROXY_SENTINEL\n)\n",
)
replace_once(
    "codex_toolkit_gui.py",
    "from ui_theme import ThemeManager, APP_VERSION\n",
    "from ui_theme import ThemeManager, APP_VERSION\nfrom tray_manager import TrayController\nfrom update_checker import check_for_update\n",
)
replace_once(
    "codex_toolkit_gui.py",
    '        self._theme_manager.build_header()\n\n        nb = ttk.Notebook(self)\n',
    '        self._theme_manager.build_header()\n'
    '        self._tray = TrayController(\n'
    '            self, ASSETS_DIR / "codex_bridge.png",\n'
    '            self._restore_from_tray, self._exit_and_restore,\n'
    '        )\n\n'
    '        nb = ttk.Notebook(self)\n',
)
replace_once(
    "codex_toolkit_gui.py",
    '        self.protocol("WM_DELETE_WINDOW", self._on_close)\n        self.after(0, self._theme_manager.refresh_widgets)\n',
    '        self.protocol("WM_DELETE_WINDOW", self._on_close)\n'
    '        self.after(0, self._theme_manager.refresh_widgets)\n'
    '        self.after(1800, self._check_updates_background)\n',
)
old_close = '''    def _on_close(self):
        if self._closing:
            return
        self._closing = True
        try:
            # PyInstaller one-file executables can have a bootloader child.
            # Stop the complete process tree before destroying the GUI so the
            # local port is not left occupied after exit.
            self._proxy_tab._stop_proxy(for_exit=True)
        finally:
            self.destroy()
'''
new_close = '''    def _restore_from_tray(self):
        self._tray.restore_window()

    def _force_exit(self):
        if self._closing:
            return
        self._closing = True
        try:
            self._tray.stop()
            self._proxy_tab._stop_proxy(for_exit=True)
        finally:
            self.destroy()

    def _exit_and_restore(self):
        status = get_proxy_config_status()
        if status.get("active"):
            ok, msg = disable_proxy_config()
            if not ok:
                messagebox.showerror(
                    "无法安全恢复配置",
                    f"{msg}\n\n为避免让 Codex 指向一个即将停止的本地端口，Toolkit 暂不退出。"
                    "请检查 config.toml 后重试。",
                )
                return
        self._force_exit()

    def _on_close(self):
        if self._closing:
            return
        proc = self._proxy_proc
        proxy_running = bool(proc and proc.poll() is None)
        cfg = get_proxy_config_status()

        if proxy_running:
            choice = messagebox.askyesnocancel(
                "关闭 Codex Bridge Toolkit",
                "代理当前正在运行。\n\n"
                "【是】最小化到系统托盘，继续保持代理\n"
                "【否】停止代理、恢复原 Codex provider 并退出\n"
                "【取消】返回 Toolkit",
            )
            if choice is None:
                return
            if choice is True:
                if self._tray.hide_window_to_tray():
                    return
                messagebox.showwarning("托盘不可用", "无法创建系统托盘图标，将保持窗口打开。")
                return
            self._exit_and_restore()
            return

        if cfg.get("active"):
            if messagebox.askyesno(
                "Codex 仍指向本地代理",
                "当前 config.toml 仍使用 openai-idfix，但本地代理没有运行。\n\n"
                "是否恢复原 provider 后退出？\n\n选择“否”会保留当前配置。",
            ):
                self._exit_and_restore()
            else:
                self._force_exit()
            return
        self._force_exit()

    def _check_updates_background(self):
        if self._closing:
            return
        def worker():
            result = check_for_update(APP_VERSION, force=False)
            if result.get("update_available") and not self._closing:
                self.after(0, lambda r=result: self._offer_update(r))
        threading.Thread(target=worker, daemon=True).start()

    def _offer_update(self, result):
        latest = result.get("latest_version") or "new"
        if messagebox.askyesno(
            "发现新版本",
            f"发现 Codex Bridge Toolkit v{latest}。\n\n"
            "Toolkit 不会静默下载或安装更新。是否打开 GitHub Release 页面？",
        ):
            webbrowser.open(result.get("release_url") or "https://github.com/zankzeke/codex-desktop-toolkit/releases")
'''
replace_once("codex_toolkit_gui.py", old_close, new_close)
replace_once(
    "codex_toolkit_gui.py",
    '        self._app = app\n        self._build()\n\n    def _build(self):\n        # ── 配置区',
    '        self._app = app\n'
    '        self._launch_pending = False\n'
    '        self._status_poll_busy = False\n'
    '        self._last_stats = {}\n'
    '        self._build()\n\n'
    '    def _build(self):\n        # ── 配置区',
)
replace_once(
    "codex_toolkit_gui.py",
    '        ttk.Button(ctrl, text="🔃 重启 Codex", command=self._restart_codex).pack(side=tk.RIGHT, padx=(0, 8))\n\n        # ── 日志',
    '        ttk.Button(ctrl, text="🔃 重启 Codex", command=self._restart_codex).pack(side=tk.RIGHT, padx=(0, 8))\n'
    '        ttk.Button(ctrl, text="🚀 通过代理启动 Codex", command=self._launch_codex_via_proxy).pack(side=tk.RIGHT, padx=(0, 8))\n\n'
    '        status_strip = ttk.LabelFrame(self, text=" 连接状态（本地服务 / Codex 配置 / 实际流量） ")\n'
    '        status_strip.pack(fill=tk.X, padx=12, pady=(0, 6))\n'
    '        for i in range(3):\n'
    '            status_strip.columnconfigure(i, weight=1)\n'
    '        self._local_state_lbl = ttk.Label(status_strip, text="● 本地代理：未运行", foreground=self._app.palette["muted"], font=("Segoe UI", 10, "bold"))\n'
    '        self._config_state_lbl = ttk.Label(status_strip, text="● Codex 配置：未接入", foreground=self._app.palette["muted"], font=("Segoe UI", 10, "bold"))\n'
    '        self._traffic_state_lbl = ttk.Label(status_strip, text="● 实际流量：未验证", foreground=self._app.palette["muted"], font=("Segoe UI", 10, "bold"))\n'
    '        self._local_state_lbl.grid(row=0, column=0, sticky="w", padx=10, pady=(7, 2))\n'
    '        self._config_state_lbl.grid(row=0, column=1, sticky="w", padx=10, pady=(7, 2))\n'
    '        self._traffic_state_lbl.grid(row=0, column=2, sticky="w", padx=10, pady=(7, 2))\n'
    '        self._traffic_detail_lbl = ttk.Label(status_strip, text="等待代理启动…", foreground=self._app.palette["muted"])\n'
    '        self._traffic_detail_lbl.grid(row=1, column=0, columnspan=3, sticky="w", padx=10, pady=(0, 7))\n\n'
    '        # ── 日志',
)
replace_once(
    "codex_toolkit_gui.py",
    '        self._log.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)\n\n    def _center_window',
    '        self._log.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)\n'
    '        self.after(800, self._poll_runtime_status)\n\n'
    '    def _center_window',
)
replace_once(
    "codex_toolkit_gui.py",
    '            if url == "":\n                messagebox.showwarning("警告", "无法删除直连选项", parent=top)\n                return\n',
    '            if url in ("", ENV_PROXY_SENTINEL):\n'
    '                messagebox.showwarning("警告", "无法删除内置的直连/系统环境代理选项", parent=top)\n'
    '                return\n',
)
text = read("codex_toolkit_gui.py")
text = text.replace(
    '            "如果能直连，下拉请选择“无代理 (直连)”。"\n',
    '            "如果能直连，请选择“无代理（真正直连）”；若希望读取 HTTP_PROXY/HTTPS_PROXY/ALL_PROXY，请选择“系统环境代理”。"\n',
)
write("codex_toolkit_gui.py", text)
anchor = '    def _on_proxy_ready(self, port_num: int):\n'
helpers = '''    def _poll_runtime_status(self):
        if self._app._closing:
            return
        if self._status_poll_busy:
            self.after(1800, self._poll_runtime_status)
            return
        try:
            port = int(self._port_var.get().strip() or "8787")
        except ValueError:
            self.after(1800, self._poll_runtime_status)
            return
        self._status_poll_busy = True

        def worker():
            healthy = False
            stats = {}
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=0.8) as response:
                    healthy = response.status == 200
                if healthy:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/stats", timeout=0.8) as response:
                        stats = json.loads(response.read().decode("utf-8"))
            except Exception:
                healthy = False
            cfg = get_proxy_config_status(port)
            if not self._app._closing:
                self._app.after(0, lambda: self._apply_runtime_status(healthy, cfg, stats))

        threading.Thread(target=worker, daemon=True).start()
        self.after(1800, self._poll_runtime_status)

    def _apply_runtime_status(self, healthy: bool, cfg: dict, stats: dict):
        self._status_poll_busy = False
        palette = self._app.palette
        self._last_stats = stats or {}
        if healthy:
            self._local_state_lbl.config(text=f"● 本地代理：运行中 :{self._port_var.get()}", foreground=palette["success"])
        else:
            self._local_state_lbl.config(text="● 本地代理：未运行", foreground=palette["muted"])

        if cfg.get("active_for_port"):
            self._config_state_lbl.config(text="● Codex 配置：已接入", foreground=palette["success"])
        elif cfg.get("active"):
            self._config_state_lbl.config(text="● Codex 配置：端口不匹配", foreground=palette["warn"])
        else:
            self._config_state_lbl.config(text="● Codex 配置：未接入", foreground=palette["muted"])

        if healthy and stats.get("traffic_verified"):
            self._traffic_state_lbl.config(text="● 实际流量：已验证", foreground=palette["success"])
            status = stats.get("last_response_status")
            detail = (
                f"最后请求：{stats.get('last_transport') or '-'}  "
                f"{stats.get('last_request_method') or '-'} {stats.get('last_request_path') or '-'}  "
                f"HTTP={status or '-'}  {stats.get('last_request_at') or ''}"
            )
            err = stats.get("last_error") or {}
            if err:
                detail += f"  | 最近错误：{err.get('title') or err.get('category')}"
            self._traffic_detail_lbl.config(text=detail, foreground=palette["muted"])
        else:
            self._traffic_state_lbl.config(
                text="● 实际流量：未验证",
                foreground=palette["warn"] if healthy and cfg.get("active_for_port") else palette["muted"],
            )
            self._traffic_detail_lbl.config(
                text="代理健康检查通过并不代表 Codex 已走代理；收到真实 Codex 请求后此项才会变绿。" if healthy else "等待代理启动…",
                foreground=palette["muted"],
            )

    def _activate_and_launch_codex(self, port_num: int):
        ok, msg = enable_proxy_config(port_num, self._ws_var.get())
        if not ok:
            self._append_log(f"[配置错误] {msg}", "error")
            messagebox.showerror("写入失败", msg)
            return
        self._append_log(f"[配置更新] 已启用本地代理端口 {port_num}", "ok")
        ok, msg = restart_codex()
        if ok:
            self._append_log(f"[Codex 启动] 已通过本地代理启动: {msg}", "ok")
        else:
            messagebox.showwarning("启动失败", f"代理与配置均已准备好，但无法自动启动 Codex：{msg}")

    def _launch_codex_via_proxy(self):
        try:
            port = int(self._port_var.get().strip() or "8787")
            if not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            messagebox.showerror("错误", "端口必须是 1–65535 之间的整数")
            return
        proc = self._app._proxy_proc
        if proc and proc.poll() is None:
            self._activate_and_launch_codex(port)
            return
        self._launch_pending = True
        self._start_proxy()

'''
text = read("codex_toolkit_gui.py")
if text.count(anchor) != 1:
    raise RuntimeError("GUI _on_proxy_ready anchor not unique")
write("codex_toolkit_gui.py", text.replace(anchor, helpers + anchor, 1))
replace_once(
    "codex_toolkit_gui.py",
    '        self._set_running(True)\n        status = get_proxy_config_status(port_num)\n',
    '        self._set_running(True)\n'
    '        if self._launch_pending:\n'
    '            self._launch_pending = False\n'
    '            self._activate_and_launch_codex(port_num)\n'
    '            return\n'
    '        status = get_proxy_config_status(port_num)\n',
)
replace_once(
    "codex_toolkit_gui.py",
    '        upstream_proxy = self._proxy_var.get().strip()\n        \n        # Save selected label\n',
    '        upstream_proxy = self._proxy_var.get().strip()\n'
    '        if upstream_proxy == ENV_PROXY_SENTINEL:\n'
    '            proxy_mode = "env"\n'
    '            upstream_proxy_url = ""\n'
    '        elif upstream_proxy:\n'
    '            proxy_mode = "explicit"\n'
    '            upstream_proxy_url = upstream_proxy\n'
    '        else:\n'
    '            proxy_mode = "direct"\n'
    '            upstream_proxy_url = ""\n'
    '        \n'
    '        # Save selected label\n',
)
replace_once(
    "codex_toolkit_gui.py",
    '            "--reasoning-mode", "safe",\n            "--log-level", "DEBUG",\n        ]\n        if upstream_proxy:\n            cmd += ["--upstream-proxy", upstream_proxy]\n',
    '            "--reasoning-mode", "safe",\n'
    '            "--log-level", "DEBUG",\n'
    '            "--proxy-mode", proxy_mode,\n'
    '        ]\n'
    '        if proxy_mode == "explicit" and upstream_proxy_url:\n'
    '            cmd += ["--upstream-proxy", upstream_proxy_url]\n',
)

# Replace DiagnosticsTab in one shot.
text = read("codex_toolkit_gui.py")
start = text.index("class DiagnosticsTab(ttk.Frame):")
end = text.index("# ─── 入口", start)
new_diag = r'''class DiagnosticsTab(ttk.Frame):
    def __init__(self, parent, app: App):
        super().__init__(parent)
        self._app = app
        self._last_data = None
        self._build()

    def _build(self):
        top = ttk.Frame(self)
        top.pack(fill=tk.X, padx=12, pady=(12, 6))
        ttk.Label(top, text="诊断中心", font=("Segoe UI", 11, "bold")).pack(side=tk.LEFT)
        ttk.Button(top, text="检查更新", command=self._check_update).pack(side=tk.RIGHT, padx=(6, 0))
        ttk.Button(top, text="复制脱敏报告", command=self._copy_report).pack(side=tk.RIGHT, padx=(6, 0))
        ttk.Button(top, text="刷新诊断", command=self._refresh).pack(side=tk.RIGHT)

        self._text = scrolledtext.ScrolledText(
            self, bg=self._app.palette["surface"], fg=self._app.palette["fg"],
            font=("Consolas", 10), state=tk.DISABLED,
            relief=tk.FLAT, borderwidth=0,
        )
        self._text.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)
        self.after(500, self._refresh)

    def _refresh(self):
        from diagnostics import get_diagnostics
        try:
            port = int(self._app._proxy_tab._port_var.get().strip() or "8787")
        except ValueError:
            messagebox.showerror("端口无效", "代理端口必须是整数")
            return
        data = get_diagnostics(port)
        self._last_data = data
        codex = data["codex"]
        cfg = codex.get("config", {})
        proxy = data["proxy"]
        details = proxy.get("details", {})
        stats = proxy.get("stats", {})
        ws = stats.get("websocket", {}) if stats else {}
        err = (stats.get("last_error") or {}) if stats else {}

        out = []
        out.append("====== Codex Bridge Toolkit 诊断中心 ======\n")
        out.append("[Codex Desktop]")
        out.append(f"  运行状态: {'正在运行' if codex.get('running') else '未运行'}")
        out.append(f"  版本: {codex.get('version') or '未知'}")
        out.append(f"  路径: {codex.get('path') or '未知'}")
        out.append(f"  model_provider: {cfg.get('provider') or '未知'}")
        out.append(f"  本地 provider/端口匹配: {'是' if cfg.get('active_for_port') else '否'}\n")

        out.append("[本地代理]")
        out.append(f"  运行状态: {'正在运行' if proxy.get('running') else '未运行或异常'}")
        out.append(f"  监听端口: {port}")
        out.append(f"  端口所有者: {proxy.get('owner_name') or '未知'} / PID {proxy.get('owner_pid') or '未知'}")
        if details:
            out.append(f"  上游地址: {details.get('upstream') or '未知'}")
            out.append(f"  出站代理模式: {details.get('proxy_mode') or '未知'}")
            out.append(f"  显式上游代理: {details.get('upstream_proxy') or '无'}")
            out.append(f"  WebSocket 支持: {'是' if details.get('websocket_supported') else '否'}")
        out.append("")

        out.append("[真实流量 / ID 修复]")
        out.append(f"  流量已验证: {'是' if stats.get('traffic_verified') else '否'}")
        out.append(f"  请求总数: {stats.get('requests_total', 0)}")
        out.append(f"  最近传输: {stats.get('last_transport') or '无'}")
        out.append(f"  最近请求: {stats.get('last_request_method') or '-'} {stats.get('last_request_path') or '-'}")
        out.append(f"  最近 HTTP 状态: {stats.get('last_response_status') or '无'}")
        out.append(f"  ID 修复累计: {stats.get('id_fixes_total', 0)}")
        out.append(f"  reasoning 清理累计: {stats.get('reasoning_drops_total', 0)}\n")

        out.append("[WebSocket]")
        out.append(f"  当前连接: {ws.get('active', 0)}")
        out.append(f"  握手次数: {ws.get('handshakes', 0)}")
        out.append(f"  重连计数: {ws.get('reconnects', 0)}")
        out.append(f"  握手/代理失败: {ws.get('failures', 0)}")
        out.append(f"  最近关闭码: {ws.get('last_close_code') if ws.get('last_close_code') is not None else '无'}")
        out.append(f"  最近关闭分类: {ws.get('last_close_category') or '无'}\n")

        out.append("[最近错误分类]")
        if err:
            out.append(f"  类别: {err.get('category')}")
            out.append(f"  说明: {err.get('title')}")
            out.append(f"  建议: {err.get('action')}")
            out.append(f"  时间: {err.get('at')}")
        else:
            out.append("  无")
        out.append("")

        out.append("[系统环境代理变量（已脱敏）]")
        for key, value in data["network"].items():
            out.append(f"  {key}: {value or '未设置'}")

        self._text.config(state=tk.NORMAL)
        self._text.delete("1.0", tk.END)
        self._text.insert(tk.END, "\n".join(out))
        self._text.config(state=tk.DISABLED)

    def _copy_report(self):
        from diagnostics import build_diagnostic_report, get_diagnostics
        if self._last_data is None:
            try:
                port = int(self._app._proxy_tab._port_var.get().strip() or "8787")
            except ValueError:
                messagebox.showerror("端口无效", "代理端口必须是整数")
                return
            self._last_data = get_diagnostics(port)
        report = build_diagnostic_report(self._last_data, APP_VERSION)
        self._app.clipboard_clear()
        self._app.clipboard_append(report)
        self._app.update()
        messagebox.showinfo("已复制", "脱敏诊断报告已复制到剪贴板。不会包含认证头、Cookie、请求正文或 URL query。")

    def _check_update(self):
        def worker():
            result = check_for_update(APP_VERSION, force=True)
            self._app.after(0, lambda: self._show_update_result(result))
        threading.Thread(target=worker, daemon=True).start()

    def _show_update_result(self, result):
        if result.get("error"):
            messagebox.showwarning("检查失败", f"无法检查 GitHub Release：{result['error']}")
        elif result.get("update_available"):
            self._app._offer_update(result)
        else:
            messagebox.showinfo("已是最新", f"当前版本 v{APP_VERSION} 已是最新公开版本。")


'''
write("codex_toolkit_gui.py", text[:start] + new_diag + text[end:])

# Version/docs/tests ---------------------------------------------------------------
replace_once("ui_theme.py", 'APP_VERSION = "0.3.1"', 'APP_VERSION = "0.4.0"')
replace_once("tests/test_gui_metadata.py", "assert ui_theme.APP_VERSION == '0.3.1'", "assert ui_theme.APP_VERSION == '0.4.0'")

changelog = read("CHANGELOG.md")
entry = '''## 0.4.0 - 2026-09-14

- Added three-layer connection truth: local proxy health, Codex provider attachment, and verified real Codex traffic.
- Added in-memory `/stats` telemetry with last transport/status, rewrite counters, WebSocket handshakes/reconnects/close codes, and privacy-safe error categories.
- Outbound networking is deterministic: true direct mode ignores proxy environment variables, with separate system-environment and explicit-proxy modes.
- Added system-tray background mode and safe exit that restores the previous Codex provider before stopping the proxy.
- Added one-click **Launch Codex through proxy** workflow.
- Hardened config restore so external `config.toml` changes are not silently overwritten.
- Expanded Diagnostics into a diagnostics center with masked copyable Markdown reports.
- Added notification-only GitHub update checks; no silent downloads or installs.
- Release CI smoke-tests the packaged proxy executable across health, HTTP, SSE and WebSocket.
- Release artifacts include SHA256 checksums and optional Authenticode signing when repository signing secrets are configured.

'''
if entry not in changelog:
    changelog = changelog.replace("# Changelog\n\n", "# Changelog\n\n" + entry, 1)
    write("CHANGELOG.md", changelog)

readme = read("README.md")
readme = re.sub(r"CodexBridgeToolkit-0\.\d+\.\d+-windows-x64\.zip", "CodexBridgeToolkit-0.4.0-windows-x64.zip", readme)
extra = '''
### v0.4 可靠性与诊断平台

- **三层连接状态**：区分“本地代理已运行 / Codex 配置已接入 / 已收到真实 Codex 流量”，避免只看绿灯误判。
- **确定性出站模式**：真正直连（忽略环境代理）、跟随系统 `HTTP_PROXY/HTTPS_PROXY/ALL_PROXY`、显式 HTTP/Mixed 代理三种模式。
- **系统托盘后台模式**：关闭窗口时可以继续保持代理；安全退出会先恢复原 provider，再停止代理。
- **一键通过代理启动 Codex**：启动代理、写入 provider、启动/重启 Codex 一次完成。
- **诊断中心**：Codex 版本/provider、最后请求、HTTP 状态、WS close code/重连、ID 修复计数、错误分类，并可复制脱敏 Markdown 报告。
- **隐私安全遥测**：`/stats` 只保留计数、路径（去 query）、状态码和分类，不保存认证头、Cookie 或请求/响应正文。
- **更新检查**：只提示新的 GitHub Release 并由用户决定是否打开，不静默下载/安装。
- **发布完整性**：Release 提供 `SHA256SUMS.txt`；仓库配置签名证书 secrets 后可选 Authenticode 签名。

'''
if "### v0.4 可靠性与诊断平台" not in readme:
    readme = readme.replace("## 特性\n", "## 特性\n" + extra, 1)
write("README.md", readme)

# Tests ---------------------------------------------------------------------------
write(
    "tests/test_v040_features.py",
    r'''from pathlib import Path

from error_classifier import classify_error, classify_ws_close
from runtime_stats import RuntimeStats
from update_checker import is_newer


def test_error_classification():
    assert classify_error(503, "Selected model is at capacity")["category"] == "capacity"
    assert classify_error(429, "too many requests")["category"] == "rate_limit"
    assert classify_error(400, "invalid_id_prefix")["category"] == "invalid_id"
    assert classify_ws_close(1008)["category"] == "ws_policy"
    assert classify_ws_close(1006)["category"] == "ws_abnormal"


def test_runtime_stats_marks_real_traffic_and_ws():
    stats = RuntimeStats()
    assert stats.snapshot()["traffic_verified"] is False
    stats.record_request("POST", "/v1/responses?secret=nope")
    stats.record_response(200)
    stats.record_rewrite(2, 1)
    stats.ws_connected()
    stats.ws_closed(1000)
    snap = stats.snapshot()
    assert snap["traffic_verified"] is True
    assert snap["last_request_path"] == "/v1/responses"
    assert snap["id_fixes_total"] == 2
    assert snap["reasoning_drops_total"] == 1
    assert snap["websocket"]["handshakes"] == 1


def test_version_compare():
    assert is_newer("0.4.1", "0.4.0")
    assert not is_newer("0.4.0", "0.4.0")
    assert not is_newer("0.3.9", "0.4.0")


def test_gui_contains_three_layer_status_tray_and_one_click_launch():
    text = Path("codex_toolkit_gui.py").read_text(encoding="utf-8")
    assert "实际流量：已验证" in text
    assert "self._tray.hide_window_to_tray()" in text
    assert "通过代理启动 Codex" in text
    assert 'proxy_mode = "env"' in text


def test_proxy_has_stats_endpoint_and_direct_mode():
    text = Path("proxy.py").read_text(encoding="utf-8")
    assert 'app.router.add_get("/stats", proxy.handle_stats)' in text
    assert 'trust_env=self.proxy_mode == "env"' in text
    assert '--proxy-mode' in text
''',
)
config_tests = read("tests/test_config_manager.py")
extra_tests = r'''

def test_disable_refuses_external_provider_change(tmp_path: Path, monkeypatch):
    config = tmp_path / "config.toml"
    state = tmp_path / "state.json"
    config.write_text('model_provider = "openai"\n', encoding="utf-8")
    monkeypatch.setattr(config_manager, "CONFIG_PATH", config)
    monkeypatch.setattr(config_manager, "STATE_PATH", state)
    ok, _ = config_manager.enable_proxy_config(8787, True)
    assert ok
    doc = tomlkit.parse(config.read_text(encoding="utf-8"))
    doc["model_provider"] = "openai"
    config.write_text(tomlkit.dumps(doc), encoding="utf-8")
    ok, msg = config_manager.disable_proxy_config()
    assert ok is False
    assert "外部修改" in msg
    assert tomlkit.parse(config.read_text(encoding="utf-8"))["model_provider"] == "openai"


def test_disable_refuses_external_idfix_url_change(tmp_path: Path, monkeypatch):
    config = tmp_path / "config.toml"
    state = tmp_path / "state.json"
    config.write_text('model_provider = "openai"\n', encoding="utf-8")
    monkeypatch.setattr(config_manager, "CONFIG_PATH", config)
    monkeypatch.setattr(config_manager, "STATE_PATH", state)
    ok, _ = config_manager.enable_proxy_config(8787, True)
    assert ok
    doc = tomlkit.parse(config.read_text(encoding="utf-8"))
    doc["model_providers"]["openai-idfix"]["base_url"] = "http://127.0.0.1:9999/v1"
    config.write_text(tomlkit.dumps(doc), encoding="utf-8")
    ok, msg = config_manager.disable_proxy_config()
    assert ok is False
    assert "base_url" in msg
'''
if "test_disable_refuses_external_provider_change" not in config_tests:
    write("tests/test_config_manager.py", config_tests + extra_tests)

write(
    "tests/packaged_smoke.py",
    r'''"""Post-PyInstaller smoke test used by release.yml on Windows."""
from __future__ import annotations

import argparse
import asyncio
import json
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

from aiohttp import ClientSession, WSMsgType, web

RAW = "resp_12345678-1234-1234-1234-123456789abc_msg"
FIXED = "msg_12345678-1234-1234-1234-123456789abc"


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def main(exe: Path):
    upstream = web.Application()

    async def json_handler(request):
        return web.json_response({"type": "message", "id": RAW})

    async def sse_handler(request):
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        payload = {"type": "response.output_item.added", "item": {"type": "message", "id": "item_dddddddddddddddd"}}
        await response.write(("data: " + json.dumps(payload) + "\n\n").encode())
        await response.write(b"data: [DONE]\n\n")
        await response.write_eof()
        return response

    async def ws_handler(request):
        ws = web.WebSocketResponse(protocols=("codex-smoke",))
        await ws.prepare(request)
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                await ws.send_str(msg.data)
        return ws

    async def dispatch(request):
        if request.headers.get("Upgrade", "").lower() == "websocket":
            return await ws_handler(request)
        if request.query.get("stream") == "1":
            return await sse_handler(request)
        return await json_handler(request)

    upstream.router.add_route("*", "/v1/responses", dispatch)
    runner = web.AppRunner(upstream)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    upstream_port = site._server.sockets[0].getsockname()[1]
    proxy_port = free_port()

    proc = subprocess.Popen([
        str(exe), "--port", str(proxy_port),
        "--upstream", f"http://127.0.0.1:{upstream_port}",
        "--proxy-mode", "direct", "--log-level", "INFO",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{proxy_port}/health", timeout=1) as response:
                    if response.status == 200:
                        break
            except Exception:
                await asyncio.sleep(0.25)
        else:
            raise RuntimeError("packaged proxy health timeout")

        async with ClientSession() as session:
            async with session.post(f"http://127.0.0.1:{proxy_port}/v1/responses", json={"input": []}) as response:
                data = await response.json()
                assert data["id"] == FIXED

            async with session.get(f"http://127.0.0.1:{proxy_port}/v1/responses?stream=1") as response:
                text = await response.text()
                assert "msg_dddddddddddddddd" in text

            ws = await session.ws_connect(
                f"http://127.0.0.1:{proxy_port}/v1/responses",
                protocols=("codex-smoke",),
            )
            await ws.send_str(json.dumps({"input": [{"type": "message", "id": RAW}]}))
            msg = await ws.receive(timeout=3)
            assert FIXED in msg.data
            await ws.close()

            async with session.get(f"http://127.0.0.1:{proxy_port}/stats") as response:
                stats = await response.json()
                assert stats["traffic_verified"] is True
                assert stats["requests_total"] >= 3
                assert stats["websocket"]["handshakes"] >= 1
    finally:
        proc.kill()
        try:
            proc.wait(timeout=5)
        except Exception:
            pass
        await runner.cleanup()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--proxy-exe", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(main(args.proxy_exe.resolve()))
''',
)

# release.yml ---------------------------------------------------------------------
release = read(".github/workflows/release.yml")
release = release.replace("default: v0.3.0", "default: v0.4.0")
release = release.replace("DEFAULT_TAG: v0.3.0", "DEFAULT_TAG: v0.4.0")
build_gui = '''      - name: Build GUI executable
        shell: pwsh
        run: |
          pyinstaller --noconfirm --clean --onefile --windowed `
            --name CodexBridgeToolkit `
            --icon assets/codex_bridge.ico `
            --add-data "assets;assets" `
            codex_toolkit_gui.py
'''
extra_release_steps = build_gui + '''
      - name: Smoke-test packaged proxy executable
        run: python tests/packaged_smoke.py --proxy-exe dist/CodexBridgeProxy.exe

      - name: Optional Authenticode signing
        shell: pwsh
        env:
          SIGN_CERT: ${{ secrets.WINDOWS_SIGNING_CERT_BASE64 }}
          SIGN_PASSWORD: ${{ secrets.WINDOWS_SIGNING_CERT_PASSWORD }}
        run: |
          if (-not $env:SIGN_CERT) {
            Write-Host "Signing secrets are not configured; publishing unsigned binaries with SHA256SUMS."
            exit 0
          }
          [IO.File]::WriteAllBytes("signing.pfx", [Convert]::FromBase64String($env:SIGN_CERT))
          $signtool = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin" -Filter signtool.exe -Recurse | Sort-Object FullName -Descending | Select-Object -First 1 -ExpandProperty FullName
          if (-not $signtool) { throw "signtool.exe not found" }
          foreach ($file in @("dist/CodexBridgeToolkit.exe", "dist/CodexBridgeProxy.exe")) {
            & $signtool sign /fd SHA256 /f signing.pfx /p $env:SIGN_PASSWORD /tr http://timestamp.digicert.com /td SHA256 $file
            if ($LASTEXITCODE -ne 0) { throw "signtool failed for $file" }
          }
          Remove-Item signing.pfx -Force
'''
if "Smoke-test packaged proxy executable" not in release:
    if build_gui not in release:
        raise RuntimeError("release.yml GUI build block not found")
    release = release.replace(build_gui, extra_release_steps, 1)

artifact_anchor = "      - name: Upload workflow artifact\n"
hash_step = '''      - name: Generate SHA256 checksums
        shell: pwsh
        run: |
          $files = @(
            "dist/CodexBridgeToolkit.exe",
            "dist/CodexBridgeProxy.exe",
            "${{ steps.package.outputs.zip }}"
          )
          $lines = foreach ($file in $files) {
            $hash = (Get-FileHash $file -Algorithm SHA256).Hash.ToLower()
            "$hash  $([IO.Path]::GetFileName($file))"
          }
          $lines | Set-Content -Encoding ascii release/SHA256SUMS.txt

'''
if "Generate SHA256 checksums" not in release:
    release = release.replace(artifact_anchor, hash_step + artifact_anchor, 1)
release = release.replace(
    '            ${{ steps.package.outputs.zip }}\n\n      - name: Publish GitHub Release',
    '            ${{ steps.package.outputs.zip }}\n            release/SHA256SUMS.txt\n\n      - name: Publish GitHub Release',
)
release = release.replace(
    "gh release upload $tag $zip dist/CodexBridgeToolkit.exe dist/CodexBridgeProxy.exe --clobber",
    "gh release upload $tag $zip dist/CodexBridgeToolkit.exe dist/CodexBridgeProxy.exe release/SHA256SUMS.txt --clobber",
)
release = release.replace(
    "gh release create $tag $zip dist/CodexBridgeToolkit.exe dist/CodexBridgeProxy.exe `",
    "gh release create $tag $zip dist/CodexBridgeToolkit.exe dist/CodexBridgeProxy.exe release/SHA256SUMS.txt `",
)
release = re.sub(
    r"          ### Highlights\n(?:          - .*\n)+",
    "          ### Highlights\n"
    "          - Three-layer connection truth: proxy health / Codex provider / verified traffic\n"
    "          - True direct, system-environment and explicit outbound proxy modes\n"
    "          - System-tray background mode and safe restore-on-exit\n"
    "          - One-click launch Codex through the local compatibility proxy\n"
    "          - Diagnostics center, privacy-safe report copy, error classification and WebSocket telemetry\n"
    "          - Notification-only update checks (no silent installer)\n"
    "          - Packaged EXE smoke test plus SHA256SUMS; optional Authenticode signing when secrets are configured\n",
    release,
    count=1,
)
write(".github/workflows/release.yml", release)

print("v0.4.0 migration applied")
