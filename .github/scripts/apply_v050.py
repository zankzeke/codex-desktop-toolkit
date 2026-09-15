from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


path = Path("codex_toolkit_gui.py")
text = path.read_text(encoding="utf-8")

text = replace_once(
    text,
    "from tkinter import messagebox, scrolledtext, ttk",
    "from tkinter import filedialog, messagebox, scrolledtext, ttk",
    "tkinter import",
)
text = replace_once(
    text,
    "from history_fixer import fix_rollout_file, fast_check_bad_ids, is_codex_running",
    "from history_fixer import (\n"
    "    fix_rollout_file, fast_check_bad_ids, is_codex_running,\n"
    "    list_session_backups, compare_backup_ids, restore_session_backup,\n"
    ")",
    "history imports",
)
text = replace_once(
    text,
    "from config_manager import (\n"
    "    enable_proxy_config, disable_proxy_config, get_proxy_config_status,\n"
    "    load_upstreams, save_upstreams,\n"
    "    load_proxies, save_proxies, ENV_PROXY_SENTINEL\n"
    ")",
    "from config_manager import (\n"
    "    enable_proxy_config, disable_proxy_config, get_proxy_config_status,\n"
    "    load_upstreams, save_upstreams,\n"
    "    load_proxies, save_proxies, ENV_PROXY_SENTINEL,\n"
    "    load_toolkit_settings, save_toolkit_settings,\n"
    "    TRANSPORT_AUTO, TRANSPORT_HTTP,\n"
    ")",
    "config imports",
)
text = replace_once(
    text,
    "from process_utils import (\n"
    "    get_port_owner,\n"
    "    is_packaged_toolkit_proxy,\n"
    "    terminate_process_tree,\n"
    "    wait_for_port_free,\n"
    ")\n",
    "from process_utils import (\n"
    "    get_port_owner,\n"
    "    is_packaged_toolkit_proxy,\n"
    "    terminate_process_tree,\n"
    "    wait_for_port_free,\n"
    ")\n"
    "from network_tools import (\n"
    "    discover_local_proxies, format_network_check, run_network_check,\n"
    "    transport_label, transport_mode_from_label,\n"
    ")\n"
    "from startup_manager import is_startup_enabled, set_startup_enabled\n"
    "from support_bundle import create_support_bundle\n",
    "feature imports",
)

text = replace_once(
    text,
    '    return False, "未找到 codex.exe"\n\n\n# ─── GUI',
    '    return False, "未找到 codex.exe"\n\n\n'
    'def launch_codex():\n'
    '    """Launch Codex Desktop without terminating an existing process."""\n'
    '    if is_codex_running():\n'
    '        return True, "Codex Desktop 已在运行"\n'
    '    exe = find_codex_exe()\n'
    '    if not exe:\n'
    '        return False, "未找到 codex.exe"\n'
    '    try:\n'
    '        subprocess.Popen([str(exe)], creationflags=subprocess.DETACHED_PROCESS)\n'
    '        return True, str(exe)\n'
    '    except Exception as exc:\n'
    '        return False, str(exc)\n\n\n# ─── GUI',
    "launch_codex insertion",
)

text = replace_once(
    text,
    "        self._closing = False\n",
    "        self._closing = False\n        self._background_requested = \"--background\" in sys.argv\n",
    "app background flag",
)
text = replace_once(
    text,
    "        nb = ttk.Notebook(self)\n        nb.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))",
    "        nb = ttk.Notebook(self)\n        self._notebook = nb\n        nb.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))",
    "notebook ref",
)
text = replace_once(
    text,
    "        self.after(0, self._theme_manager.refresh_widgets)\n        self.after(1800, self._check_updates_background)\n\n    def _apply_style",
    "        self.after(0, self._theme_manager.refresh_widgets)\n"
    "        self.after(900, self._run_startup_automation)\n"
    "        self.after(1100, self._enter_background_mode_if_requested)\n"
    "        self.after(1800, self._check_updates_background)\n\n"
    "    def _run_startup_automation(self):\n"
    "        if self._closing:\n"
    "            return\n"
    "        try:\n"
    "            self._proxy_tab._run_startup_automation()\n"
    "        except Exception:\n"
    "            pass\n\n"
    "    def _enter_background_mode_if_requested(self):\n"
    "        if not self._background_requested or self._closing:\n"
    "            return\n"
    "        if not self._tray.hide_window_to_tray():\n"
    "            self.withdraw()\n\n"
    "    def _apply_style",
    "app startup automation methods",
)

text = replace_once(
    text,
    "        self._scan_btn.pack(side=tk.RIGHT, padx=(8, 0))\n",
    "        self._scan_btn.pack(side=tk.RIGHT, padx=(8, 0))\n"
    "        ttk.Button(top, text=\"🕘 备份历史\", command=self._show_backup_history).pack(side=tk.RIGHT, padx=(8, 0))\n",
    "backup history button",
)

session_methods = r'''
    def _show_backup_history(self):
        backups = list_session_backups(SESSIONS_DIR)
        if not backups:
            messagebox.showinfo("备份历史", "尚未找到 Toolkit 创建的会话备份。")
            return
        top = tk.Toplevel(self)
        top.title("会话备份历史")
        top.geometry("860x430")
        top.transient(self._app)
        tree = ttk.Treeview(top, columns=("time", "file", "size", "state"), show="headings")
        for col, title, width in (
            ("time", "备份时间", 150), ("file", "会话文件", 430),
            ("size", "大小", 90), ("state", "当前文件", 110),
        ):
            tree.heading(col, text=title)
            tree.column(col, width=width, anchor=tk.W)
        tree.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        rows = {}
        for item in backups:
            stamp = item.get("timestamp") or time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(item["mtime"]))
            iid = tree.insert("", tk.END, values=(
                stamp,
                str(item["original"].name),
                f"{int(item['size']) // 1024} KB",
                "存在" if item.get("exists_original") else "缺失",
            ))
            rows[iid] = item

        controls = ttk.Frame(top)
        controls.pack(fill=tk.X, padx=10, pady=(0, 10))

        def selected():
            sel = tree.selection()
            if not sel:
                messagebox.showwarning("未选择", "请先选择一个备份。", parent=top)
                return None
            return rows.get(sel[0])

        def show_diff():
            item = selected()
            if not item:
                return
            try:
                diff = compare_backup_ids(item["backup"], item["original"])
            except Exception as exc:
                messagebox.showerror("比较失败", str(exc), parent=top)
                return
            removed = diff["removed_ids"][:12]
            added = diff["added_ids"][:12]
            body = [
                f"备份 ID 数: {diff['backup_ids']}",
                f"当前 ID 数: {diff['current_ids']}",
                "",
                "备份中存在、当前已不存在:",
                *(f"  - {x}" for x in removed),
                "",
                "当前新增:",
                *(f"  + {x}" for x in added),
            ]
            if len(diff["removed_ids"]) > 12 or len(diff["added_ids"]) > 12:
                body.append("\n（仅展示前 12 个 ID；不会展示聊天正文。）")
            messagebox.showinfo("ID 差异预览", "\n".join(body), parent=top)

        def restore():
            item = selected()
            if not item:
                return
            if is_codex_running():
                messagebox.showwarning("请先关闭 Codex", "恢复会话备份前必须关闭 Codex Desktop。", parent=top)
                return
            if not messagebox.askyesno(
                "确认恢复",
                f"将恢复：\n{item['original']}\n\nToolkit 会先再次备份当前文件。确认继续？",
                parent=top,
            ):
                return
            try:
                restored = restore_session_backup(item["backup"], item["original"])
            except Exception as exc:
                messagebox.showerror("恢复失败", str(exc), parent=top)
                return
            messagebox.showinfo("恢复完成", f"已恢复：\n{restored}", parent=top)
            top.destroy()
            self._scan()

        ttk.Button(controls, text="查看 ID 差异", command=show_diff).pack(side=tk.LEFT)
        ttk.Button(controls, text="恢复此备份", command=restore, style="Accent.TButton").pack(side=tk.LEFT, padx=8)
        ttk.Button(controls, text="关闭", command=top.destroy).pack(side=tk.RIGHT)

'''
text = replace_once(text, "    def _on_click(self, event):\n", session_methods + "    def _on_click(self, event):\n", "session backup methods")

text = replace_once(
    text,
    "        self._last_stats = {}\n        self._build()",
    "        self._last_stats = {}\n"
    "        self._traffic_notified = False\n"
    "        self._breaker_handled_at = None\n"
    "        self._startup_mode = False\n"
    "        self._codex_seen_running = False\n"
    "        self._build()",
    "proxy state flags",
)
text = replace_once(
    text,
    "        ttk.Button(row0, text=\"说明\", width=5, command=self._show_proxy_help).pack(\n"
    "            side=tk.LEFT, padx=(0, 12)\n"
    "        )",
    "        ttk.Button(row0, text=\"说明\", width=5, command=self._show_proxy_help).pack(\n"
    "            side=tk.LEFT, padx=(0, 4)\n"
    "        )\n"
    "        ttk.Button(row0, text=\"自动发现\", command=self._discover_proxies).pack(side=tk.LEFT, padx=(0, 12))",
    "proxy discover button",
)
text = replace_once(
    text,
    "        self._ws_var = tk.BooleanVar(value=True)\n"
    "        ttk.Checkbutton(row_toml, text=\"支持 WebSocket\", variable=self._ws_var).pack(side=tk.LEFT, padx=(0, 10))\n\n"
    "        ttk.Button(row_toml, text=\"✅ 启用代理配置\", command=self._enable_proxy_config).pack(side=tk.LEFT, padx=(0, 5))",
    "        settings = load_toolkit_settings()\n"
    "        ttk.Label(row_toml, text=\"传输模式：\").pack(side=tk.LEFT)\n"
    "        self._transport_var = tk.StringVar(value=transport_label(settings.get(\"transport_mode\", TRANSPORT_AUTO)))\n"
    "        self._transport_cb = ttk.Combobox(\n"
    "            row_toml, textvariable=self._transport_var,\n"
    "            values=[\"自动\", \"WebSocket\", \"强制 HTTP\"], state=\"readonly\", width=11\n"
    "        )\n"
    "        self._transport_cb.pack(side=tk.LEFT, padx=(0, 10))\n"
    "        self._transport_cb.bind(\"<<ComboboxSelected>>\", self._on_transport_changed)\n\n"
    "        ttk.Button(row_toml, text=\"✅ 启用代理配置\", command=self._enable_proxy_config).pack(side=tk.LEFT, padx=(0, 5))",
    "transport mode UI",
)
text = replace_once(
    text,
    "        ttk.Button(row_toml, text=\"❌ 恢复默认直连\", command=self._disable_proxy_config).pack(side=tk.LEFT, padx=5)\n",
    "        ttk.Button(row_toml, text=\"❌ 恢复默认直连\", command=self._disable_proxy_config).pack(side=tk.LEFT, padx=5)\n"
    "        ttk.Button(row_toml, text=\"⚙ 自动化\", command=self._show_automation_settings).pack(side=tk.LEFT, padx=5)\n",
    "automation button",
)

proxy_methods = r'''
    def _current_transport_mode(self):
        return transport_mode_from_label(self._transport_var.get())

    def _on_transport_changed(self, event=None):
        save_toolkit_settings(transport_mode=self._current_transport_mode())

    def _discover_proxies(self):
        self._append_log("[检测] 正在扫描常见本地代理端口…", "info")

        def worker():
            found = discover_local_proxies()
            self._app.after(0, lambda: apply(found))

        def apply(found):
            if not found:
                messagebox.showinfo("自动发现代理", "未检测到常见本地 HTTP/Mixed 代理监听端口。")
                return
            for item in found:
                url = item["url"]
                self._proxies_dict.setdefault(url, item["display"])
            self._proxy_cb["values"] = list(self._proxies_dict.keys()) + ["<编辑/新增代理...>"]
            first = found[0]
            details = "\n".join(f"• {x['display']}  {x['url']}" for x in found)
            if messagebox.askyesno("发现本地代理", f"检测到：\n\n{details}\n\n是否使用第一项？"):
                self._proxy_var.set(first["url"])
                self._proxy_lbl.config(text=self._proxies_dict[first["url"]])
                save_proxies(self._proxies_dict, first["url"])
                self._append_log(f"[代理] 已选择自动发现的 {first['url']}", "ok")
            else:
                save_proxies(self._proxies_dict, self._proxy_var.get())

        threading.Thread(target=worker, daemon=True).start()

    def _show_automation_settings(self):
        settings = load_toolkit_settings()
        top = tk.Toplevel(self)
        top.title("自动化设置")
        top.geometry("560x390")
        top.transient(self._app)
        top.grab_set()

        startup_var = tk.BooleanVar(value=is_startup_enabled())
        auto_start_var = tk.BooleanVar(value=settings.get("auto_start_proxy", False))
        auto_apply_var = tk.BooleanVar(value=settings.get("auto_apply_config", False))
        auto_launch_var = tk.BooleanVar(value=settings.get("auto_launch_codex", False))
        stop_exit_var = tk.BooleanVar(value=settings.get("stop_proxy_on_codex_exit", False))
        breaker_var = tk.BooleanVar(value=settings.get("auto_circuit_breaker", False))

        ttk.Label(top, text="启动与网络自动化", font=("Segoe UI", 11, "bold")).pack(anchor=tk.W, padx=16, pady=(16, 8))
        ttk.Checkbutton(top, text="Windows 登录后自动启动 Toolkit（后台）", variable=startup_var).pack(anchor=tk.W, padx=18, pady=4)
        ttk.Checkbutton(top, text="Toolkit 启动后自动启动本地代理", variable=auto_start_var).pack(anchor=tk.W, padx=18, pady=4)
        ttk.Checkbutton(top, text="代理就绪后自动应用 Codex provider 配置", variable=auto_apply_var).pack(anchor=tk.W, padx=18, pady=4)
        ttk.Checkbutton(top, text="代理就绪后自动启动 Codex（若尚未运行）", variable=auto_launch_var).pack(anchor=tk.W, padx=18, pady=4)
        ttk.Checkbutton(top, text="检测到 Codex 退出后自动停止代理", variable=stop_exit_var).pack(anchor=tk.W, padx=18, pady=4)
        ttk.Checkbutton(top, text="WS 自动熔断：连续 3 次握手失败后切换强制 HTTP 并重启 Codex", variable=breaker_var).pack(anchor=tk.W, padx=18, pady=4)
        ttk.Label(
            top,
            text="说明：自动应用/自动启动 Codex 会同时启用“自动启动本地代理”。自动熔断默认关闭，只有显式开启后才会自动重启 Codex。",
            wraplength=510,
            foreground=self._app.palette["muted"],
        ).pack(anchor=tk.W, padx=18, pady=(10, 6))

        def save():
            if auto_apply_var.get() or auto_launch_var.get():
                auto_start_var.set(True)
            saved = save_toolkit_settings(
                transport_mode=self._current_transport_mode(),
                auto_circuit_breaker=breaker_var.get(),
                auto_start_proxy=auto_start_var.get(),
                auto_apply_config=auto_apply_var.get(),
                auto_launch_codex=auto_launch_var.get(),
                stop_proxy_on_codex_exit=stop_exit_var.get(),
            )
            script = None if FROZEN else Path(__file__)
            ok, msg = set_startup_enabled(startup_var.get(), sys.executable, script)
            if not ok:
                messagebox.showwarning("启动项设置失败", msg, parent=top)
            top.destroy()
            self._append_log(f"[自动化] 设置已保存: {saved}", "info")

        buttons = ttk.Frame(top)
        buttons.pack(fill=tk.X, padx=16, pady=14)
        ttk.Button(buttons, text="保存", command=save, style="Accent.TButton").pack(side=tk.RIGHT)
        ttk.Button(buttons, text="取消", command=top.destroy).pack(side=tk.RIGHT, padx=8)

    def _run_startup_automation(self):
        settings = load_toolkit_settings()
        self._transport_var.set(transport_label(settings.get("transport_mode", TRANSPORT_AUTO)))
        if not settings.get("auto_start_proxy"):
            return
        proc = self._app._proxy_proc
        if proc and proc.poll() is None:
            return
        self._startup_mode = True
        self._append_log("[自动化] 正在自动启动本地代理…", "info")
        self._start_proxy()

    def _apply_startup_after_ready(self, port_num: int):
        settings = load_toolkit_settings()
        if settings.get("auto_apply_config") or settings.get("auto_launch_codex"):
            ok, msg = enable_proxy_config(port_num, self._current_transport_mode())
            if ok:
                self._append_log("[自动化] 已应用 Codex 代理配置", "ok")
            else:
                self._append_log(f"[自动化] 应用配置失败: {msg}", "error")
                return
        if settings.get("auto_launch_codex") and not is_codex_running():
            ok, msg = launch_codex()
            self._append_log(f"[自动化] {'已启动 Codex: ' if ok else '启动 Codex 失败: '}{msg}", "ok" if ok else "error")
        self._app._tray.notify("Codex Bridge Toolkit", "本地代理已按自动化设置启动。")

    def _maybe_handle_runtime_automation(self, healthy: bool, cfg: dict, stats: dict):
        if healthy and stats.get("traffic_verified") and not self._traffic_notified:
            self._traffic_notified = True
            self._app._tray.notify("Codex 已连接 Toolkit", "已观察到真实 Codex 流量通过本地代理。")
        if not healthy:
            self._traffic_notified = False

        settings = load_toolkit_settings()
        running = is_codex_running()
        if running:
            self._codex_seen_running = True
        elif self._codex_seen_running and settings.get("stop_proxy_on_codex_exit"):
            self._codex_seen_running = False
            self._append_log("[自动化] 检测到 Codex 已退出，正在停止代理。", "warn")
            self._stop_proxy()
            self._app._tray.notify("Codex Bridge Toolkit", "Codex 已退出，本地代理已自动停止。")
            return

        ws = stats.get("websocket") or {}
        if not settings.get("auto_circuit_breaker"):
            return
        if self._current_transport_mode() != TRANSPORT_AUTO:
            return
        if not ws.get("breaker_tripped"):
            return
        token = ws.get("breaker_tripped_at") or ws.get("last_failure_at") or ws.get("failures")
        if token == self._breaker_handled_at:
            return
        self._breaker_handled_at = token
        try:
            port = int(self._port_var.get().strip() or "8787")
        except ValueError:
            return
        ok, msg = enable_proxy_config(port, TRANSPORT_HTTP)
        if not ok:
            self._append_log(f"[WS 熔断] 切换强制 HTTP 失败: {msg}", "error")
            return
        save_toolkit_settings(transport_mode=TRANSPORT_HTTP)
        self._transport_var.set(transport_label(TRANSPORT_HTTP))
        self._append_log("[WS 熔断] 连续 3 次 WS 握手失败，已切换为强制 HTTP。", "warn")
        self._app._tray.notify("WebSocket 自动熔断", "连续握手失败，已切换强制 HTTP。")
        if is_codex_running():
            ok, restart_msg = restart_codex()
            self._append_log(
                f"[WS 熔断] {'已重启 Codex: ' if ok else 'Codex 重启失败: '}{restart_msg}",
                "ok" if ok else "error",
            )

'''
text = replace_once(text, "    def _on_proxy_selected(self, event=None):\n", proxy_methods + "    def _on_proxy_selected(self, event=None):\n", "proxy feature methods")

text = text.replace("enable_proxy_config(port_num, self._ws_var.get())", "enable_proxy_config(port_num, self._current_transport_mode())")
text = text.replace("enable_proxy_config(port, ws_enabled)", "enable_proxy_config(port, transport_mode)")
text = text.replace("f\"[配置更新] 已自动启用代理配置: 端口 {port_num}, WS={self._ws_var.get()}\"", "f\"[配置更新] 已自动启用代理配置: 端口 {port_num}, 传输={transport_label(self._current_transport_mode())}\"")

text = replace_once(
    text,
    "        self._set_running(True)\n        if self._launch_pending:",
    "        self._set_running(True)\n"
    "        if self._startup_mode:\n"
    "            self._startup_mode = False\n"
    "            self._apply_startup_after_ready(port_num)\n"
    "            return\n"
    "        if self._launch_pending:",
    "startup ready hook",
)

text = replace_once(
    text,
    "        ws_enabled = self._ws_var.get()\n        ok, msg = enable_proxy_config(port, transport_mode)\n        if ok:\n            self._append_log(f\"[配置更新] 已启用代理配置: 端口 {port}, WS={ws_enabled}\", \"ok\")",
    "        transport_mode = self._current_transport_mode()\n"
    "        save_toolkit_settings(transport_mode=transport_mode)\n"
    "        ok, msg = enable_proxy_config(port, transport_mode)\n"
    "        if ok:\n"
    "            self._append_log(f\"[配置更新] 已启用代理配置: 端口 {port}, 传输={transport_label(transport_mode)}\", \"ok\")",
    "enable config transport",
)

text = replace_once(
    text,
    "                foreground=palette[\"muted\"],\n            )\n\n    def _activate_and_launch_codex",
    "                foreground=palette[\"muted\"],\n            )\n"
    "        self._maybe_handle_runtime_automation(healthy, cfg, stats or {})\n\n"
    "    def _activate_and_launch_codex",
    "runtime automation hook",
)

text = replace_once(
    text,
    "        ttk.Button(top, text=\"检查更新\", command=self._check_update).pack(side=tk.RIGHT, padx=(6, 0))\n"
    "        ttk.Button(top, text=\"复制脱敏报告\", command=self._copy_report).pack(side=tk.RIGHT, padx=(6, 0))\n"
    "        ttk.Button(top, text=\"刷新诊断\", command=self._refresh).pack(side=tk.RIGHT)",
    "        ttk.Button(top, text=\"检查更新\", command=self._check_update).pack(side=tk.RIGHT, padx=(6, 0))\n"
    "        ttk.Button(top, text=\"生成支持包\", command=self._export_support_bundle).pack(side=tk.RIGHT, padx=(6, 0))\n"
    "        ttk.Button(top, text=\"处理最近错误\", command=self._handle_recent_error).pack(side=tk.RIGHT, padx=(6, 0))\n"
    "        ttk.Button(top, text=\"网络体检\", command=self._run_network_check).pack(side=tk.RIGHT, padx=(6, 0))\n"
    "        ttk.Button(top, text=\"复制脱敏报告\", command=self._copy_report).pack(side=tk.RIGHT, padx=(6, 0))\n"
    "        ttk.Button(top, text=\"刷新诊断\", command=self._refresh).pack(side=tk.RIGHT)",
    "diagnostics buttons",
)
text = replace_once(
    text,
    "        out.append(f\"  model_provider: {cfg.get('provider') or '未知'}\")\n        out.append(f\"  本地 provider/端口匹配: {'是' if cfg.get('active_for_port') else '否'}\\n\")",
    "        out.append(f\"  model_provider: {cfg.get('provider') or '未知'}\")\n"
    "        out.append(f\"  传输模式: {transport_label(cfg.get('transport_mode'))}\")\n"
    "        out.append(f\"  supports_websockets: {cfg.get('supports_websockets')}\")\n"
    "        out.append(f\"  本地 provider/端口匹配: {'是' if cfg.get('active_for_port') else '否'}\\n\")",
    "diagnostics transport output",
)
text = replace_once(
    text,
    "        out.append(f\"  握手/代理失败: {ws.get('failures', 0)}\")\n"
    "        out.append(f\"  最近关闭码: {ws.get('last_close_code') if ws.get('last_close_code') is not None else '无'}\")",
    "        out.append(f\"  握手/代理失败: {ws.get('failures', 0)}\")\n"
    "        out.append(f\"  连续握手失败: {ws.get('consecutive_failures', 0)}\")\n"
    "        out.append(f\"  熔断建议: {'已触发' if ws.get('breaker_tripped') else '未触发'}\")\n"
    "        out.append(f\"  最近关闭码: {ws.get('last_close_code') if ws.get('last_close_code') is not None else '无'}\")",
    "diagnostics breaker output",
)

diag_methods = r'''
    def _run_network_check(self):
        proxy_tab = self._app._proxy_tab
        upstream_label = proxy_tab._upstream_var.get().strip()
        upstream = proxy_tab._upstreams_dict.get(upstream_label, "https://chatgpt.com/backend-api/codex")
        selected_proxy = proxy_tab._proxy_var.get().strip()
        if selected_proxy == ENV_PROXY_SENTINEL:
            proxy_mode, explicit_proxy = "env", None
        elif selected_proxy:
            proxy_mode, explicit_proxy = "explicit", selected_proxy
        else:
            proxy_mode, explicit_proxy = "direct", None
        try:
            port = int(proxy_tab._port_var.get().strip() or "8787")
        except ValueError:
            messagebox.showerror("端口无效", "代理端口必须是整数")
            return

        def worker():
            result = run_network_check(
                upstream=upstream,
                proxy_mode=proxy_mode,
                explicit_proxy=explicit_proxy,
                local_port=port,
                transport_mode=proxy_tab._current_transport_mode(),
            )
            self._app.after(0, lambda: self._show_network_check_result(result))

        threading.Thread(target=worker, daemon=True).start()

    def _show_network_check_result(self, result):
        top = tk.Toplevel(self)
        top.title("Codex 网络体检")
        top.geometry("760x500")
        top.transient(self._app)
        box = scrolledtext.ScrolledText(
            top, bg=self._app.palette["surface"], fg=self._app.palette["fg"],
            font=("Consolas", 10), relief=tk.FLAT,
        )
        box.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        box.insert(tk.END, format_network_check(result))
        box.config(state=tk.DISABLED)
        ttk.Button(top, text="关闭", command=top.destroy).pack(pady=(0, 10))

    def _handle_recent_error(self):
        if self._last_data is None:
            self._refresh()
        data = self._last_data or {}
        stats = (data.get("proxy") or {}).get("stats") or {}
        err = stats.get("last_error") or {}
        action = err.get("next_action") or "diagnostics"
        if not err:
            messagebox.showinfo("最近错误", "当前没有可处理的分类错误。")
            return
        if action == "scan_sessions":
            self._app._notebook.select(self._app._session_tab)
            self._app._session_tab._scan()
            return
        if action == "network_check":
            self._run_network_check()
            return
        if action == "force_http":
            if not messagebox.askyesno(
                "切换强制 HTTP",
                "最近错误与 WebSocket 失败有关。是否把 Codex provider 切换为强制 HTTP？\n\n"
                "这可以跳过重复 WS 重连；Codex 正在运行时需要重启才能可靠生效。",
            ):
                return
            proxy_tab = self._app._proxy_tab
            try:
                port = int(proxy_tab._port_var.get().strip() or "8787")
            except ValueError:
                messagebox.showerror("端口无效", "代理端口必须是整数")
                return
            ok, msg = enable_proxy_config(port, TRANSPORT_HTTP)
            if not ok:
                messagebox.showerror("切换失败", msg)
                return
            save_toolkit_settings(transport_mode=TRANSPORT_HTTP)
            proxy_tab._transport_var.set(transport_label(TRANSPORT_HTTP))
            if is_codex_running():
                ok, msg = restart_codex()
                if not ok:
                    messagebox.showwarning("需要手动重启", msg)
            messagebox.showinfo("已切换", "已切换为强制 HTTP。")
            return
        if action == "restore_direct":
            if messagebox.askyesno("恢复官方直连", "是否恢复 Codex 原 provider？"):
                ok, msg = disable_proxy_config()
                if ok and is_codex_running():
                    restart_codex()
                messagebox.showinfo("处理结果" if ok else "处理失败", msg)
            return
        messagebox.showinfo(
            "处理建议",
            f"{err.get('title') or err.get('category')}\n\n{err.get('action') or '请查看诊断报告。'}",
        )

    def _export_support_bundle(self):
        try:
            port = int(self._app._proxy_tab._port_var.get().strip() or "8787")
        except ValueError:
            messagebox.showerror("端口无效", "代理端口必须是整数")
            return
        stamp = time.strftime("%Y%m%d-%H%M%S")
        output = filedialog.asksaveasfilename(
            parent=self._app,
            title="保存脱敏支持包",
            defaultextension=".zip",
            initialfile=f"CodexBridge-support-{stamp}.zip",
            filetypes=[("ZIP archive", "*.zip")],
        )
        if not output:
            return
        try:
            log_text = self._app._proxy_tab._log.get("1.0", tk.END)
        except Exception:
            log_text = ""

        def worker():
            try:
                path = create_support_bundle(
                    port=port,
                    app_version=APP_VERSION,
                    output_path=output,
                    recent_log_text=log_text,
                )
            except Exception as exc:
                self._app.after(0, lambda: messagebox.showerror("生成失败", str(exc)))
                return
            self._app.after(0, lambda: messagebox.showinfo(
                "支持包已生成",
                f"已生成脱敏支持包：\n{path}\n\n发布到公开 Issue 前仍建议自行快速检查压缩包内容。",
            ))

        threading.Thread(target=worker, daemon=True).start()

'''
text = replace_once(text, "    def _copy_report(self):\n", diag_methods + "    def _copy_report(self):\n", "diagnostics feature methods")

path.write_text(text, encoding="utf-8")

# Redact NO_PROXY from localhost health details as well.
proxy_path = Path("proxy.py")
proxy_text = proxy_path.read_text(encoding="utf-8")
proxy_text = replace_once(
    proxy_text,
    '                "no_proxy": os.environ.get("NO_PROXY")\n',
    '                "no_proxy": "set (contents redacted)" if os.environ.get("NO_PROXY") else None\n',
    "proxy NO_PROXY redaction",
)
proxy_path.write_text(proxy_text, encoding="utf-8")

# Documentation and release notes.
readme_path = Path("README.md")
readme = readme_path.read_text(encoding="utf-8")
readme = replace_once(
    readme,
    "- Multi-theme GUI for session scanning/fixing, proxy control, config injection, logs, and diagnostics.\n",
    "- Multi-theme GUI for session scanning/fixing, proxy control, config injection, logs, and diagnostics.\n"
    "- Transport modes: **自动 / WebSocket / 强制 HTTP**, including an opt-in WS circuit breaker after repeated handshake failures.\n"
    "- One-click network health check that distinguishes HTTP reachability, WebSocket path failures, local proxy state, and real Codex traffic evidence.\n"
    "- Automatic discovery of common local Clash/Mihomo/v2rayN/NekoRay proxy listeners.\n"
    "- Actionable error remediation, session backup history with ID-only diff/rollback, and privacy-safe support bundle export.\n"
    "- Optional Windows startup automation: start Toolkit/proxy, apply provider, launch Codex, stop proxy after Codex exits, and tray notifications.\n",
    "README feature bullets",
)
readme = replace_once(
    readme,
    "supports_websockets = true\n```",
    "supports_websockets = true\n```\n\nThe GUI exposes three transport modes. **强制 HTTP** writes `supports_websockets = false`; **自动** and **WebSocket** allow WS. In 自动 mode, the optional circuit breaker can switch future Codex connections to HTTP after three consecutive upstream WS handshake failures and restart Codex only when the user explicitly enabled that automation.",
    "README transport docs",
)
readme = replace_once(
    readme,
    "update_checker.py      notification-only GitHub Release update checker\n",
    "update_checker.py      notification-only GitHub Release update checker\n"
    "network_tools.py       transport modes, network health checks, local proxy discovery\n"
    "startup_manager.py     Windows login-startup registration\n"
    "support_bundle.py      privacy-safe diagnostics/support ZIP export\n",
    "README project layout",
)
readme_path.write_text(readme, encoding="utf-8")

changelog_path = Path("CHANGELOG.md")
changelog = changelog_path.read_text(encoding="utf-8")
entry = '''# Changelog\n\n## v0.5.0 — Network Compatibility & Recovery\n\n- Added **自动 / WebSocket / 强制 HTTP** transport modes for the managed Codex provider.\n- Added one-click network health checks for HTTP, WebSocket, local proxy, config, and real-traffic evidence.\n- Added opt-in WebSocket circuit breaker after three consecutive upstream handshake failures.\n- Added discovery of common local Clash/Mihomo/v2rayN/NekoRay proxy listeners.\n- Added actionable remediation for classified errors.\n- Added session backup history, ID-only diff previews, and atomic rollback.\n- Added privacy-safe support bundle ZIP export.\n- Added Windows startup/background automation and tray notifications.\n- Redacted `NO_PROXY` contents from localhost health details.\n\n'''
if changelog.startswith("# Changelog\n"):
    changelog = entry + changelog[len("# Changelog\n\n"):]
else:
    changelog = entry + changelog
changelog_path.write_text(changelog, encoding="utf-8")

version_path = Path("version.py")
version = version_path.read_text(encoding="utf-8")
version = replace_once(version, 'APP_VERSION = "0.4.4"', 'APP_VERSION = "0.5.0"', "version bump")
version_path.write_text(version, encoding="utf-8")
