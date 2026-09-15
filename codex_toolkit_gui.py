"""
codex_toolkit_gui.py — Codex Bridge Toolkit GUI
功能：
  - 会话修复：扫描并修复 codex++ 合成 ID，支持勾选批量修复
  - 代理控制：一键启动/停止本地 ID 修复代理，实时查看日志
  - 重启 Codex Desktop（修复后可选）
"""

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.request
import webbrowser
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk

# ─── 路径配置 ──────────────────────────────────────────────────────────────────

FROZEN = bool(getattr(sys, "frozen", False))
APP_DIR = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).parent.resolve()
BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR))
SCRIPT_DIR = APP_DIR
CODEX_HOME = Path.home() / ".codex"
SESSIONS_DIR = CODEX_HOME / "sessions"
STATE_DB = CODEX_HOME / "state_5.sqlite"
PROXY_SCRIPT = SCRIPT_DIR / "proxy.py"
PROXY_EXE = APP_DIR / "CodexBridgeProxy.exe"
VENV_PYTHON = SCRIPT_DIR / ".venv" / "Scripts" / "python.exe"
PYTHON_EXE = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable
ASSETS_DIR = BUNDLE_DIR / "assets"

CODEX_EXE_CANDIDATES = [
    Path.home() / "AppData" / "Local" / "OpenAI" / "Codex" / "Codex.exe",
    Path("C:/Program Files/OpenAI/Codex/Codex.exe"),
]

# ─── ID 修复逻辑（与 fix_codex_ids.py 共享） ──────────────────────────────────

from history_fixer import fix_rollout_file, fast_check_bad_ids, is_codex_running
from config_manager import (
    enable_proxy_config, disable_proxy_config, get_proxy_config_status,
    load_upstreams, save_upstreams,
    load_proxies, save_proxies, ENV_PROXY_SENTINEL,
    get_transport_mode, set_transport_mode, update_managed_ws_support,
    get_circuit_breaker_config, save_circuit_breaker_config,
)
from powershell_hook import install_hook, uninstall_hook, check_hook_status
from ui_theme import ThemeManager
from version import APP_VERSION
from tray_manager import TrayController
from update_checker import check_for_update
from process_utils import (
    get_port_owner,
    is_packaged_toolkit_proxy,
    terminate_process_tree,
    wait_for_port_free,
)
from transport_policy import GLOBAL_CIRCUIT_BREAKER, CircuitState
from network_diagnostics import run_full_diagnostics, evaluate_diagnostics, mask_url_sensitive
from proxy_discovery import discover_proxies, test_proxy_http, test_proxy_ws, DiscoveredProxy
from backup_manager import list_session_backups, compute_structured_diff, rollback_session
from support_bundle import create_support_bundle
from startup_manager import (
    GLOBAL_STARTUP_MANAGER, GLOBAL_NOTIFIER,
    load_automation_settings, save_automation_settings, AutomationSettings
)
from error_classifier import ErrorDiagnosis, classify_error, classify_ws_close


def load_thread_index():
    if not STATE_DB.exists():
        return {}
    try:
        conn = sqlite3.connect(str(STATE_DB))
        cur = conn.cursor()
        cur.execute("SELECT id, title FROM threads")
        result = {row[0]: (row[1] or "").strip() for row in cur.fetchall()}
        conn.close()
        return result
    except Exception:
        return {}


def thread_id_from_path(p: Path) -> str:
    parts = p.stem.split("-")
    ids = []
    for part in parts:
        if re.match(r"^[0-9a-f]+$", part, re.I):
            ids.append(part)
    if len(ids) >= 5:
        return "-".join(ids[:5])
    return ""


def scan_sessions():
    """扫描所有 rollout 文件，返回有问题的会话列表。"""
    thread_index = load_thread_index()
    results = []
    if not SESSIONS_DIR.exists():
        return results
    files = sorted(
        [p for p in SESSIONS_DIR.rglob("*.jsonl") if ".bak" not in p.suffix],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for p in files:
        if not fast_check_bad_ids(p):
            continue
        fixes, drops, id_map = fix_rollout_file(p, dry_run=True)
        if fixes > 0 or drops > 0:
            tid = thread_id_from_path(p)
            title = thread_index.get(tid, "")
            results.append({
                "path": p,
                "fixes": fixes,
                "drops": drops,
                "id_map": id_map,
                "title": title or "(无标题)",
                "size_kb": p.stat().st_size // 1024,
                "date": time.strftime("%Y-%m-%d", time.localtime(p.stat().st_mtime)),
            })
    return results


# ─── Codex 重启 ────────────────────────────────────────────────────────────────

def find_codex_exe() -> Path | None:
    # 1. Try to find via running process first (most accurate)
    try:
        result = subprocess.run(
            ["powershell", "-Command",
             "(Get-Process codex -ErrorAction SilentlyContinue | Select-Object -First 1).Path"],
            capture_output=True, text=True, timeout=5
        )
        path_str = result.stdout.strip()
        if path_str and Path(path_str).exists():
            return Path(path_str)
    except Exception:
        pass
        
    # 2. Check candidates
    for p in CODEX_EXE_CANDIDATES:
        if p.exists():
            return p
            
    # 3. Search in bin folder for versioned directories
    bin_dir = Path.home() / "AppData" / "Local" / "OpenAI" / "Codex" / "bin"
    if bin_dir.exists():
        for exe in bin_dir.rglob("codex.exe"):
            if exe.exists():
                return exe
                
    return None

def restart_codex():
    """Kill Codex Desktop and relaunch it."""
    # Find the exe BEFORE we kill the process
    exe = find_codex_exe()
    
    subprocess.run(["taskkill", "/F", "/IM", "codex.exe"], capture_output=True)
    time.sleep(1.5)
    
    if exe:
        subprocess.Popen([str(exe)], creationflags=subprocess.DETACHED_PROCESS)
        return True, str(exe)
    return False, "未找到 codex.exe"


# ─── GUI ───────────────────────────────────────────────────────────────────────

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Codex Bridge Toolkit")
        self.geometry("1000x720")
        self.minsize(860, 620)
        self.resizable(True, True)

        self._proxy_proc: subprocess.Popen | None = None
        self._proxy_log_thread: threading.Thread | None = None
        self._sessions: list[dict] = []
        self._closing = False

        self._theme_manager = ThemeManager(self, ASSETS_DIR)
        self.palette = self._theme_manager.palette
        self._theme_manager.build_header()
        self._tray = TrayController(
            self, ASSETS_DIR / "codex_bridge.png",
            self._restore_from_tray, self._exit_and_restore,
        )

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        self._overview_tab = OverviewTab(self.notebook, self)
        self._proxy_tab = ProxyTab(self.notebook, self)
        self._session_tab = SessionTab(self.notebook, self)
        self._net_tab = NetworkDiagnosticsTab(self.notebook, self)
        self._agy_tab = AgyTab(self.notebook, self)
        self._diag_tab = DiagnosticsTab(self.notebook, self)
        self._settings_tab = SettingsTab(self.notebook, self)

        self.notebook.add(self._overview_tab, text="  🏠 首页概览  ")
        self.notebook.add(self._proxy_tab, text="  🔌 代理控制  ")
        self.notebook.add(self._session_tab, text="  🔧 会话修复  ")
        self.notebook.add(self._net_tab, text="  🌐 网络诊断  ")
        self.notebook.add(self._agy_tab, text="  🚀 Antigravity 网络  ")
        self.notebook.add(self._diag_tab, text="  🩺 诊断与支持  ")
        self.notebook.add(self._settings_tab, text="  ⚙️ 设置  ")

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(0, self._theme_manager.refresh_widgets)
        self.after(1800, self._check_updates_background)

        # Load persisted circuit settings for GUI fallback displays. The
        # running proxy receives the same settings through its CLI/control API.
        cb_cfg = get_circuit_breaker_config()
        GLOBAL_CIRCUIT_BREAKER.configure(
            threshold=cb_cfg.get("failure_threshold", 3),
            cooldown_seconds=cb_cfg.get("cooldown_seconds", 900),
            enabled=cb_cfg.get("enabled", True),
            action_mode=cb_cfg.get("action_mode", "auto_switch"),
        )

        # Automation check: auto-start proxy if enabled
        auto_settings = load_automation_settings()
        if auto_settings.auto_start_proxy:
            self.after(600, self._proxy_tab._start_proxy)

    def select_tab(self, tab):
        try:
            self.notebook.select(tab)
        except Exception:
            pass

    def _apply_style(self, style: ttk.Style):
        bg = "#1e1e2e"
        fg = "#cdd6f4"
        sel = "#313244"
        accent = "#89b4fa"
        style.configure(".", background=bg, foreground=fg, font=("Segoe UI", 10))
        style.configure("TNotebook", background=bg, borderwidth=0)
        style.configure("TNotebook.Tab", background="#313244", foreground=fg,
                         padding=[14, 6], font=("Segoe UI", 10))
        style.map("TNotebook.Tab", background=[("selected", "#45475a")],
                  foreground=[("selected", accent)])
        style.configure("TFrame", background=bg)
        style.configure("TLabel", background=bg, foreground=fg)
        style.configure("TButton", background="#313244", foreground=fg,
                         borderwidth=0, padding=[10, 5], font=("Segoe UI", 10))
        style.map("TButton",
                  background=[("active", "#45475a"), ("pressed", "#585b70")],
                  foreground=[("active", accent)])
        style.configure("Accent.TButton", background="#89b4fa", foreground="#1e1e2e",
                         font=("Segoe UI", 10, "bold"))
        style.map("Accent.TButton",
                  background=[("active", "#b4d0f7"), ("pressed", "#74a7e8")])
        style.configure("Danger.TButton", background="#f38ba8", foreground="#1e1e2e",
                         font=("Segoe UI", 10, "bold"))
        style.map("Danger.TButton",
                  background=[("active", "#f5a8bc"), ("pressed", "#e06c88")])
        style.configure("Treeview", background="#181825", foreground=fg,
                         fieldbackground="#181825", rowheight=26, borderwidth=0)
        style.configure("Treeview.Heading", background="#313244", foreground=accent,
                         font=("Segoe UI", 10, "bold"))
        style.map("Treeview", background=[("selected", "#45475a")])
        style.configure("TCheckbutton", background=bg, foreground=fg)
        style.configure("TEntry", fieldbackground="#313244", foreground=fg,
                         insertcolor=fg, borderwidth=0)
        style.configure("TLabelframe", background=bg, foreground=fg, borderwidth=1,
                         relief="groove")
        style.configure("TLabelframe.Label", background=bg, foreground=accent,
                         font=("Segoe UI", 10, "bold"))

    def _restore_from_tray(self):
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


class OverviewTab(ttk.Frame):
    def __init__(self, parent, app: App):
        super().__init__(parent)
        self._app = app
        self._build()

    def _build(self):
        # Header banner
        header = ttk.Frame(self)
        header.pack(fill=tk.X, padx=16, pady=(16, 8))
        ttk.Label(
            header,
            text=f"Codex Bridge Toolkit v{APP_VERSION}",
            font=("Segoe UI", 14, "bold"),
            foreground=self._app.palette["accent"],
        ).pack(anchor=tk.W)
        ttk.Label(
            header,
            text="Codex 网络兼容 + 自动诊断 + 自动恢复控制台",
            foreground=self._app.palette["muted"],
        ).pack(anchor=tk.W, pady=(2, 0))

        # Three-layer status card frame
        status_box = ttk.LabelFrame(self, text=" 实时连接状态（三层判定） ")
        status_box.pack(fill=tk.X, padx=16, pady=8)

        cards = ttk.Frame(status_box)
        cards.pack(fill=tk.X, padx=12, pady=12)
        for i in range(3):
            cards.columnconfigure(i, weight=1)

        # Layer 1: Local Proxy
        c1 = ttk.LabelFrame(cards, text=" 1. 本地代理服务 ")
        c1.grid(row=0, column=0, sticky="nsew", padx=6, pady=4)
        self._l1_dot = ttk.Label(c1, text="● 未运行", font=("Segoe UI", 11, "bold"), foreground=self._app.palette["muted"])
        self._l1_dot.pack(anchor=tk.W, padx=10, pady=(8, 2))
        self._l1_desc = ttk.Label(c1, text="127.0.0.1 端口未监听", foreground=self._app.palette["muted"])
        self._l1_desc.pack(anchor=tk.W, padx=10, pady=(0, 8))

        # Layer 2: Codex Provider
        c2 = ttk.LabelFrame(cards, text=" 2. Codex 配置接入 ")
        c2.grid(row=0, column=1, sticky="nsew", padx=6, pady=4)
        self._l2_dot = ttk.Label(c2, text="● 未接入", font=("Segoe UI", 11, "bold"), foreground=self._app.palette["muted"])
        self._l2_dot.pack(anchor=tk.W, padx=10, pady=(8, 2))
        self._l2_desc = ttk.Label(c2, text="config.toml 指向官方或未生效", foreground=self._app.palette["muted"])
        self._l2_desc.pack(anchor=tk.W, padx=10, pady=(0, 8))

        # Layer 3: Verified Traffic
        c3 = ttk.LabelFrame(cards, text=" 3. 真实流量捕获 ")
        c3.grid(row=0, column=2, sticky="nsew", padx=6, pady=4)
        self._l3_dot = ttk.Label(c3, text="● 未收到流量", font=("Segoe UI", 11, "bold"), foreground=self._app.palette["muted"])
        self._l3_dot.pack(anchor=tk.W, padx=10, pady=(8, 2))
        self._l3_desc = ttk.Label(c3, text="尚未检测到 Codex 业务请求", foreground=self._app.palette["muted"])
        self._l3_desc.pack(anchor=tk.W, padx=10, pady=(0, 8))

        # Transport & WS health card
        trans_box = ttk.LabelFrame(self, text=" 传输模式与 WebSocket 健康状况 ")
        trans_box.pack(fill=tk.X, padx=16, pady=8)

        t_inner = ttk.Frame(trans_box)
        t_inner.pack(fill=tk.X, padx=12, pady=10)
        self._trans_mode_lbl = ttk.Label(
            t_inner,
            text="当前模式: 自动 (Auto) | 熔断状态: CLOSED",
            font=("Segoe UI", 10, "bold"),
            foreground=self._app.palette["fg"],
        )
        self._trans_mode_lbl.pack(anchor=tk.W)

        self._trans_detail_lbl = ttk.Label(
            t_inner,
            text="WebSocket 运行正常 (CLOSED)",
            foreground=self._app.palette["muted"],
        )
        self._trans_detail_lbl.pack(anchor=tk.W, pady=(4, 0))

        # Quick Actions
        actions_box = ttk.LabelFrame(self, text=" 快捷控制 ")
        actions_box.pack(fill=tk.X, padx=16, pady=8)

        btn_row = ttk.Frame(actions_box)
        btn_row.pack(fill=tk.X, padx=12, pady=12)

        ttk.Button(
            btn_row,
            text="🚀 一键通过代理启动 Codex",
            style="Accent.TButton",
            command=lambda: self._app._proxy_tab._launch_codex_via_proxy(),
        ).pack(side=tk.LEFT, padx=(0, 10))

        ttk.Button(
            btn_row,
            text="🌐 开始网络体检",
            command=self._go_net_diag,
        ).pack(side=tk.LEFT, padx=(0, 10))

        ttk.Button(
            btn_row,
            text="🔧 扫描会话并修复",
            command=self._go_scan_sessions,
        ).pack(side=tk.LEFT, padx=(0, 10))

        ttk.Button(
            btn_row,
            text="⏹ 停止代理",
            style="Danger.TButton",
            command=lambda: self._app._proxy_tab._stop_proxy(),
        ).pack(side=tk.RIGHT)

    def _go_net_diag(self):
        self._app.select_tab(self._app._net_tab)
        self._app._net_tab._run_diagnostics()

    def _go_scan_sessions(self):
        self._app.select_tab(self._app._session_tab)
        self._app._session_tab._scan()

    def update_status(
        self,
        local_running: bool,
        config_active: bool,
        traffic_verified: bool,
        port: str,
        requests_count: int,
        transport_mode: str,
        cb_snap: dict,
    ):
        palette = self._app.palette
        # Layer 1
        if local_running:
            self._l1_dot.config(text=f"● 已运行 (:{port})", foreground=palette["success"])
            self._l1_desc.config(text="本地代理服务正常监听", foreground=palette["fg"])
        else:
            self._l1_dot.config(text="● 未运行", foreground=palette["muted"])
            self._l1_desc.config(text="本地代理服务已停止", foreground=palette["muted"])

        # Layer 2
        if config_active:
            self._l2_dot.config(text="● 已接入", foreground=palette["success"])
            self._l2_desc.config(text="Codex provider 指向 openai-idfix", foreground=palette["fg"])
        else:
            self._l2_dot.config(text="● 未接入", foreground=palette["warn"])
            self._l2_desc.config(text="未接入本地代理或外部修改", foreground=palette["muted"])

        # Layer 3
        if traffic_verified:
            self._l3_dot.config(text=f"● 流量已验证 ({requests_count} 请求)", foreground=palette["success"])
            self._l3_desc.config(text="Codex 真实流量已切实经过代理", foreground=palette["fg"])
        elif local_running and config_active:
            self._l3_dot.config(text="○ 等待流量", foreground=palette["warn"])
            self._l3_desc.config(text="已就绪，等待 Codex 发送首次请求", foreground=palette["muted"])
        else:
            self._l3_dot.config(text="● 未收到流量", foreground=palette["muted"])
            self._l3_desc.config(text="尚未检测到真实请求", foreground=palette["muted"])

        # Transport & WS health
        t_label = {"auto": "自动 (Auto)", "websocket": "强制 WebSocket", "http": "强制 HTTP"}.get(transport_mode, transport_mode)
        status_text = cb_snap.get("status_text") or "正常"
        self._trans_mode_lbl.config(text=f"当前传输模式: {t_label} | 熔断状态: {cb_snap.get('state', 'CLOSED')}")
        self._trans_detail_lbl.config(text=status_text)


class SessionTab(ttk.Frame):
    def __init__(self, parent, app: App):
        super().__init__(parent)
        self._app = app
        self._sessions: list[dict] = []
        self._check_vars: list[tk.BooleanVar] = []
        self._build()

    def _build(self):
        top = ttk.Frame(self)
        top.pack(fill=tk.X, padx=12, pady=(12, 6))

        ttk.Label(top, text="扫描 Codex Desktop 会话，修复 codex++ 合成 ID",
                  font=("Segoe UI", 11, "bold")).pack(side=tk.LEFT)
        self._scan_btn = ttk.Button(top, text="🔍 扫描", command=self._scan, style="Accent.TButton")
        self._scan_btn.pack(side=tk.RIGHT, padx=(8, 0))
        self._fix_btn = ttk.Button(top, text="✅ 修复选中", command=self._fix_selected)
        self._fix_btn.pack(side=tk.RIGHT)
        self._fix_all_btn = ttk.Button(top, text="修复全部", command=self._fix_all)
        self._fix_all_btn.pack(side=tk.RIGHT, padx=(0, 4))

        # Treeview
        tree_frame = ttk.Frame(self)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=4)

        cols = ("check", "title", "date", "size", "fixes", "ids")
        self._tree = ttk.Treeview(tree_frame, columns=cols, show="headings", selectmode="extended")
        self._tree.heading("check", text="☑")
        self._tree.heading("title", text="会话标题")
        self._tree.heading("date", text="日期")
        self._tree.heading("size", text="大小")
        self._tree.heading("fixes", text="修复数")
        self._tree.heading("ids", text="问题 ID 示例")

        self._tree.column("check", width=36, anchor=tk.CENTER, stretch=False)
        self._tree.column("title", width=300, anchor=tk.W)
        self._tree.column("date", width=90, anchor=tk.CENTER, stretch=False)
        self._tree.column("size", width=70, anchor=tk.CENTER, stretch=False)
        self._tree.column("fixes", width=60, anchor=tk.CENTER, stretch=False)
        self._tree.column("ids", width=260, anchor=tk.W)

        vsb = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self._tree.yview)
        self._tree.configure(yscrollcommand=vsb.set)
        self._tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

        self._tree.bind("<ButtonRelease-1>", self._on_click)

        # Status bar
        bottom = ttk.Frame(self)
        bottom.pack(fill=tk.X, padx=12, pady=(4, 10))

        self._restart_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(bottom, text="修复后重启 Codex Desktop",
                        variable=self._restart_var).pack(side=tk.LEFT)
        self._status_lbl = ttk.Label(bottom, text="就绪", foreground=self._app.palette["success"])
        self._status_lbl.pack(side=tk.RIGHT)

        # ── 会话备份与回滚历史 ───────────────────────────────────
        bak_frame = ttk.LabelFrame(self, text=" 会话修复历史与一键回滚 (Backup & Rollback) ")
        bak_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 10))

        b_top = ttk.Frame(bak_frame)
        b_top.pack(fill=tk.X, padx=8, pady=4)
        ttk.Label(b_top, text="历史备份列表（修改前自动创建）：", foreground=self._app.palette["muted"]).pack(side=tk.LEFT)
        ttk.Button(b_top, text="🔄 刷新备份", command=self._refresh_backups).pack(side=tk.RIGHT)
        ttk.Button(b_top, text="⏪ 恢复此版本", style="Danger.TButton", command=self._rollback_selected_backup).pack(side=tk.RIGHT, padx=(0, 6))
        ttk.Button(b_top, text="🔍 查看变更 (Diff)", command=self._show_backup_diff).pack(side=tk.RIGHT, padx=(0, 6))

        b_tree_frame = ttk.Frame(bak_frame)
        b_tree_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 6))

        b_cols = ("session", "time", "size", "status")
        self._bak_tree = ttk.Treeview(b_tree_frame, columns=b_cols, show="headings", height=4)
        self._bak_tree.heading("session", text="原始会话文件名")
        self._bak_tree.heading("time", text="备份时间")
        self._bak_tree.heading("size", text="大小")
        self._bak_tree.heading("status", text="原文件状态")

        self._bak_tree.column("session", width=320, anchor=tk.W)
        self._bak_tree.column("time", width=160, anchor=tk.CENTER)
        self._bak_tree.column("size", width=80, anchor=tk.CENTER)
        self._bak_tree.column("status", width=120, anchor=tk.CENTER)

        b_vsb = ttk.Scrollbar(b_tree_frame, orient=tk.VERTICAL, command=self._bak_tree.yview)
        self._bak_tree.configure(yscrollcommand=b_vsb.set)
        self._bak_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        b_vsb.pack(side=tk.RIGHT, fill=tk.Y)

        self._backups_cache: list[dict] = []
        self.after(300, self._refresh_backups)

    def _refresh_backups(self):
        for item in self._bak_tree.get_children():
            self._bak_tree.delete(item)
        self._backups_cache = list_session_backups()
        for idx, b in enumerate(self._backups_cache):
            st = "存在" if b["original_exists"] else "已移除"
            sz = f"{b['size_bytes'] / 1024:.1f} KB"
            self._bak_tree.insert("", tk.END, iid=str(idx), values=(b["session_name"], b["timestamp"], sz, st))

    def _show_backup_diff(self):
        sel = self._bak_tree.selection()
        if not sel:
            messagebox.showinfo("提示", "请先在下方备份列表中选中一个备份版本。")
            return
        idx = int(sel[0])
        if idx >= len(self._backups_cache):
            return
        item = self._backups_cache[idx]
        cur_p = Path(item["original_path"])
        bak_p = Path(item["backup_path"])

        diff = compute_structured_diff(cur_p, bak_p)

        top = tk.Toplevel(self)
        top.title(f"会话结构变更 — {diff.session_file}")
        top.geometry("640x480")
        top.transient(self)
        self._app._proxy_tab._center_window(top, self)

        pad = ttk.Frame(top, padding=12)
        pad.pack(fill=tk.BOTH, expand=True)

        ttk.Label(pad, text=f"会话变更对比: {diff.session_file}", font=("Segoe UI", 11, "bold")).pack(anchor=tk.W)
        ttk.Label(pad, text=f"修复 ID 处数: {len(diff.id_changes)}   |   清理旧 reasoning 处数: {diff.reasoning_drops}", foreground=self._app.palette["accent"]).pack(anchor=tk.W, pady=(2, 8))

        st = scrolledtext.ScrolledText(pad, bg=self._app.palette["surface"], fg=self._app.palette["fg"], font=("Consolas", 9), relief=tk.FLAT)
        st.pack(fill=tk.BOTH, expand=True, pady=(0, 8))

        lines = ["[ID 修复详情]"]
        if diff.id_changes:
            for c in diff.id_changes:
                lines.append(f"  • item id: {c['old_id']} -> {c['new_id']}")
        else:
            lines.append("  (无直接 ID 前缀修复)")

        if diff.reference_changes:
            lines.append("\n[引用修复详情]")
            for r in diff.reference_changes:
                lines.append(f"  • reference: {r['old_ref']} -> {r['new_ref']}")

        if diff.reasoning_drops:
            lines.append(f"\n[Reasoning 净化]\n  • 移除了 {diff.reasoning_drops} 处不合规或未签名的合成 reasoning 节点")

        lines.append("\n------------------------------------------------------------")
        lines.append("[隐私说明] 本对比仅展示结构 ID 变更，未包含任何用户聊天文本正文。")

        st.insert(tk.END, "\n".join(lines))
        st.config(state=tk.DISABLED)

        ttk.Button(pad, text="关闭", command=top.destroy).pack(side=tk.RIGHT)

    def _rollback_selected_backup(self):
        sel = self._bak_tree.selection()
        if not sel:
            messagebox.showinfo("提示", "请先在下方备份列表中选中一个备份版本。")
            return
        idx = int(sel[0])
        if idx >= len(self._backups_cache):
            return
        item = self._backups_cache[idx]
        cur_p = Path(item["original_path"])
        bak_p = Path(item["backup_path"])

        if is_codex_running():
            messagebox.showerror(
                "Codex 运行中",
                "Codex Desktop 当前正在运行。\n\n为避免会话写入冲突与数据丢失，必须先退出 Codex 后才能执行回滚操作。"
            )
            return

        if not messagebox.askyesno(
            "确认回滚",
            f"确定要将会话恢复至备份版本吗？\n\n会话：{item['session_name']}\n备份时间：{item['timestamp']}\n\nToolkit 会在回滚前自动创建当前状态的安全备份。"
        ):
            return

        ok, msg = rollback_session(cur_p, bak_p)
        if ok:
            messagebox.showinfo("回滚完成", msg)
            self._refresh_backups()
            self._scan()
        else:
            messagebox.showerror("回滚失败", msg)


    def _on_click(self, event):
        region = self._tree.identify_region(event.x, event.y)
        col = self._tree.identify_column(event.x)
        if col == "#1":  # check column
            item = self._tree.identify_row(event.y)
            if item:
                current = self._tree.set(item, "check")
                self._tree.set(item, "check", "☐" if current == "☑" else "☑")

    def _scan(self):
        self._status_lbl.config(text="扫描中…", foreground=self._app.palette["warn"])
        self._scan_btn.config(state=tk.DISABLED)
        self.update()

        def _do():
            try:
                results = scan_sessions()
                self._app.after(0, lambda: self._populate(results, success=True))
            except Exception as e:
                self._app.after(0, lambda err=str(e): messagebox.showerror("扫描失败", err))
                self._app.after(0, lambda: self._populate([], success=False))

        threading.Thread(target=_do, daemon=True).start()

    def _populate(self, sessions: list[dict], success: bool = True):
        self._sessions = sessions
        for item in self._tree.get_children():
            self._tree.delete(item)

        for s in sessions:
            example_ids = " | ".join(
                f"{old[:12]}…→{new[:6]}…" for old, new in list(s["id_map"].items())[:2]
            )
            self._tree.insert("", tk.END, values=(
                "☑",
                s["title"][:70],
                s["date"],
                f"{s['size_kb']} KB",
                f"{s['fixes']} (清理 {s['drops']})",
                example_ids,
            ))

        if not success:
            self._status_lbl.config(text="❌ 扫描失败，请检查日志", foreground=self._app.palette["danger"])
        elif sessions:
            self._status_lbl.config(
                text=f"找到 {len(sessions)} 个需要修复的会话", foreground=self._app.palette["warn"]
            )
        else:
            self._status_lbl.config(text="✅ 所有会话 ID 均正常，无需修复", foreground=self._app.palette["success"])

        self._scan_btn.config(state=tk.NORMAL)

    def _get_checked_sessions(self):
        checked = []
        for iid, s in zip(self._tree.get_children(), self._sessions):
            if self._tree.set(iid, "check") == "☑":
                checked.append(s)
        return checked

    def _fix_selected(self):
        targets = self._get_checked_sessions()
        if not targets:
            messagebox.showwarning("未选择", "请先勾选要修复的会话（点击 ☑ 列）。")
            return
        self._do_fix(targets)

    def _fix_all(self):
        if not self._sessions:
            messagebox.showinfo("无需修复", "没有找到需要修复的会话，请先点击「扫描」。")
            return
        self._do_fix(self._sessions)

    def _do_fix(self, targets: list[dict]):
        if is_codex_running():
            if messagebox.askokcancel(
                "Codex Desktop 运行中",
                "Codex Desktop 当前正在运行。\n\n为了避免数据损坏，必须先关闭 Codex 才能修改历史记录。\n\n点击【确定】自动关闭 Codex 并继续修复。"
            ):
                import subprocess
                subprocess.run(["taskkill", "/F", "/IM", "Codex.exe"], capture_output=True)
                import time
                time.sleep(1)
                if is_codex_running():
                    messagebox.showerror("关闭失败", "无法自动关闭 Codex，请手动退出后再试。")
                    return
            else:
                return

        names = "\n".join(f"  • {s['title'][:60]}" for s in targets[:8])
        if len(targets) > 8:
            names += f"\n  … 共 {len(targets)} 个"

        total_fixes_preview = sum(s["fixes"] for s in targets)
        total_drops_preview = sum(s.get("drops", 0) for s in targets)

        restart = self._restart_var.get()
        restart_note = "\n\n修复完成后将自动重启 Codex Desktop。" if restart else ""
        
        preview_text = f"即将修复以下 {len(targets)} 个会话：\n\n{names}\n\n"
        preview_text += f"预计修复普通 ID：{total_fixes_preview} 处\n"
        preview_text += f"预计安全清理不合规 reasoning：{total_drops_preview} 处\n"
        preview_text += "(会移除无法验证的旧 reasoning trace；普通消息历史会保留)\n"
        preview_text += f"{restart_note}\n\n确认继续？"

        if not messagebox.askyesno("确认修复", preview_text):
            return

        self._status_lbl.config(text="修复中…", foreground=self._app.palette["warn"])
        self.update()

        def _do():
            total = 0
            drops = 0
            try:
                for s in targets:
                    f, d, _ = fix_rollout_file(s["path"], dry_run=False)
                    total += f
                    drops += d
                self._app.after(0, lambda: self._after_fix(total, drops, restart))
            except Exception as e:
                self._app.after(0, lambda err=str(e): messagebox.showerror("修复错误", f"修复过程中出错:\n{err}"))
                self._app.after(0, lambda: self._status_lbl.config(text="修复失败", foreground=self._app.palette["danger"]))

        threading.Thread(target=_do, daemon=True).start()

    def _after_fix(self, total_fixes: int, total_drops: int, do_restart: bool):
        self._status_lbl.config(text=f"✅ 已修复 {total_fixes} 处，清理 {total_drops} 处", foreground=self._app.palette["success"])
        # Refresh tree
        self._scan()
        if do_restart:
            self._do_restart()

    def _do_restart(self):
        ok, msg = restart_codex()
        if ok:
            messagebox.showinfo("已重启", f"Codex Desktop 已重启。\n路径：{msg}")
        else:
            messagebox.showwarning(
                "重启失败",
                f"无法自动重启 Codex Desktop：{msg}\n\n请手动重新打开 Codex Desktop。"
            )


class ProxyTab(ttk.Frame):
    def __init__(self, parent, app: App):
        super().__init__(parent)
        self._app = app
        self._launch_pending = False
        self._status_poll_busy = False
        self._last_stats = {}
        self._circuit_http_override = False
        self._circuit_probe_pending = False
        self._build()

    def _build(self):
        # ── 配置区 ─────────────────────────────────────────────
        cfg = ttk.LabelFrame(self, text=" 代理配置 ")
        cfg.pack(fill=tk.X, padx=12, pady=(12, 6))

        row0 = ttk.Frame(cfg)
        row0.pack(fill=tk.X, padx=10, pady=(8, 4))
        ttk.Label(row0, text="本地端口：").pack(side=tk.LEFT)
        self._port_var = tk.StringVar(value="8787")
        ttk.Entry(row0, textvariable=self._port_var, width=8).pack(side=tk.LEFT, padx=(0, 20))

        ttk.Label(row0, text="上游代理（科学上网）：").pack(side=tk.LEFT)
        self._proxies_dict, selected_proxy = load_proxies()
        self._proxy_var = tk.StringVar(value=selected_proxy)
        
        self._proxy_cb = ttk.Combobox(
            row0, textvariable=self._proxy_var,
            values=list(self._proxies_dict.keys()) + ["<编辑/新增代理...>"],
            state="readonly", width=22
        )
        self._proxy_cb.pack(side=tk.LEFT, padx=(0, 4))
        self._proxy_cb.bind("<<ComboboxSelected>>", self._on_proxy_selected)
        
        self._proxy_lbl = ttk.Label(row0, text=self._proxies_dict.get(selected_proxy, ""), foreground="#6c7086", width=14, anchor=tk.W)
        self._proxy_lbl.pack(side=tk.LEFT, padx=(0, 4))
        
        ttk.Button(row0, text="说明", width=5, command=self._show_proxy_help).pack(
            side=tk.LEFT, padx=(0, 12)
        )

        ttk.Label(row0, text="上游地址：").pack(side=tk.LEFT)
        
        self._upstreams_dict, selected_up = load_upstreams()
        self._upstream_var = tk.StringVar(value=selected_up)
        
        self._upstream_cb = ttk.Combobox(
            row0, textvariable=self._upstream_var,
            values=list(self._upstreams_dict.keys()) + ["<编辑/新增地址...>"],
            state="readonly", width=14
        )
        self._upstream_cb.pack(side=tk.LEFT, padx=(0, 4))
        self._upstream_cb.bind("<<ComboboxSelected>>", self._on_upstream_selected)
        
        self._upstream_url_lbl = ttk.Label(row0, text=self._upstreams_dict.get(selected_up, ""), foreground="#6c7086", width=25, anchor=tk.W)
        self._upstream_url_lbl.pack(side=tk.LEFT, padx=(0, 4))

        # ── Codex config.toml 注入区 ──────────────────────────────
        toml_frame = ttk.LabelFrame(self, text=" Codex 配置文件 (config.toml) & 传输策略 ")
        toml_frame.pack(fill=tk.X, padx=12, pady=6)

        row_toml = ttk.Frame(toml_frame)
        row_toml.pack(fill=tk.X, padx=10, pady=(8, 8))

        ttk.Label(row_toml, text="传输模式：").pack(side=tk.LEFT)
        current_tm = get_transport_mode()
        tm_map = {"auto": "自动 (默认)", "websocket": "WebSocket (保持)", "http": "强制 HTTP"}
        self._transport_mode_var = tk.StringVar(value=tm_map.get(current_tm, "自动 (默认)"))
        self._transport_cb = ttk.Combobox(
            row_toml, textvariable=self._transport_mode_var,
            values=["自动 (默认)", "WebSocket (保持)", "强制 HTTP"],
            state="readonly", width=16
        )
        self._transport_cb.pack(side=tk.LEFT, padx=(0, 10))
        self._transport_cb.bind("<<ComboboxSelected>>", self._on_transport_mode_changed)

        self._ws_var = tk.BooleanVar(value=current_tm != "http")

        cb_cfg = get_circuit_breaker_config()
        self._cb_enabled_var = tk.BooleanVar(value=cb_cfg.get("enabled", True))
        ttk.Checkbutton(
            row_toml, text="自动熔断", variable=self._cb_enabled_var,
            command=self._on_circuit_config_changed
        ).pack(side=tk.LEFT, padx=(0, 4))

        action_label = "自动临时 HTTP" if cb_cfg.get("action_mode") == "auto_switch" else "仅提示"
        self._cb_action_var = tk.StringVar(value=action_label)
        self._cb_action_cb = ttk.Combobox(
            row_toml, textvariable=self._cb_action_var,
            values=["自动临时 HTTP", "仅提示"],
            state="readonly", width=12,
        )
        self._cb_action_cb.pack(side=tk.LEFT, padx=(0, 6))
        self._cb_action_cb.bind("<<ComboboxSelected>>", self._on_circuit_config_changed)

        self._cooldown_var = tk.StringVar(value=f"{cb_cfg.get('cooldown_minutes', 15)} 分钟")
        self._cooldown_cb = ttk.Combobox(
            row_toml, textvariable=self._cooldown_var,
            values=["5 分钟", "15 分钟", "30 分钟"],
            state="readonly", width=8
        )
        self._cooldown_cb.pack(side=tk.LEFT, padx=(0, 12))
        self._cooldown_cb.bind("<<ComboboxSelected>>", self._on_circuit_config_changed)

        ttk.Button(row_toml, text="✅ 启用代理配置", command=self._enable_proxy_config).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(row_toml, text="❌ 恢复默认直连", command=self._disable_proxy_config).pack(side=tk.LEFT, padx=5)

        # ── 状态 + 控制 ────────────────────────────────────────
        ctrl = ttk.Frame(self)
        ctrl.pack(fill=tk.X, padx=12, pady=6)

        self._status_canvas = tk.Canvas(ctrl, width=14, height=14, bg=self._app.palette["bg"],
                                        highlightthickness=0)
        self._status_canvas.pack(side=tk.LEFT, padx=(0, 6))
        self._dot = self._status_canvas.create_oval(2, 2, 12, 12, fill=self._app.palette["muted"], outline="")

        self._status_lbl = ttk.Label(ctrl, text="未启动", foreground=self._app.palette["muted"],
                                     font=("Segoe UI", 10, "bold"))
        self._status_lbl.pack(side=tk.LEFT)

        self._stop_btn = ttk.Button(ctrl, text="⏹ 停止代理", command=self._stop_proxy,
                                    style="Danger.TButton", state=tk.DISABLED)
        self._stop_btn.pack(side=tk.RIGHT, padx=(8, 0))
        self._start_btn = ttk.Button(ctrl, text="▶ 启动代理", command=self._start_proxy,
                                     style="Accent.TButton")
        self._start_btn.pack(side=tk.RIGHT)

        ttk.Button(ctrl, text="🔃 重启 Codex", command=self._restart_codex).pack(side=tk.RIGHT, padx=(0, 8))
        ttk.Button(ctrl, text="🚀 通过代理启动 Codex", command=self._launch_codex_via_proxy).pack(side=tk.RIGHT, padx=(0, 8))

        status_strip = ttk.LabelFrame(self, text=" 连接状态（本地服务 / Codex 配置 / 实际流量） ")
        status_strip.pack(fill=tk.X, padx=12, pady=(0, 6))
        for i in range(3):
            status_strip.columnconfigure(i, weight=1)
        self._local_state_lbl = ttk.Label(status_strip, text="● 本地代理：未运行", foreground=self._app.palette["muted"], font=("Segoe UI", 10, "bold"))
        self._config_state_lbl = ttk.Label(status_strip, text="● Codex 配置：未接入", foreground=self._app.palette["muted"], font=("Segoe UI", 10, "bold"))
        self._traffic_state_lbl = ttk.Label(status_strip, text="● 实际流量：未验证", foreground=self._app.palette["muted"], font=("Segoe UI", 10, "bold"))
        self._local_state_lbl.grid(row=0, column=0, sticky="w", padx=10, pady=(7, 2))
        self._config_state_lbl.grid(row=0, column=1, sticky="w", padx=10, pady=(7, 2))
        self._traffic_state_lbl.grid(row=0, column=2, sticky="w", padx=10, pady=(7, 2))
        self._traffic_detail_lbl = ttk.Label(status_strip, text="等待代理启动…", foreground=self._app.palette["muted"])
        self._traffic_detail_lbl.grid(row=1, column=0, columnspan=3, sticky="w", padx=10, pady=(0, 7))

        # ── 日志 ───────────────────────────────────────────────
        log_frame = ttk.LabelFrame(self, text=" 代理日志 ")
        log_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=(4, 10))

        self._log = scrolledtext.ScrolledText(
            log_frame, bg=self._app.palette["surface"], fg=self._app.palette["fg"],
            font=("Consolas", 9), state=tk.DISABLED,
            relief=tk.FLAT, borderwidth=0,
        )
        self._log.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        self._log.tag_config("info", foreground=self._app.palette["accent"])
        self._log.tag_config("warn", foreground=self._app.palette["warn"])
        self._log.tag_config("error", foreground=self._app.palette["danger"])
        self._log.tag_config("ok", foreground=self._app.palette["success"])
        ttk.Button(log_frame, text="清空日志", command=self._clear_log).pack(
            side=tk.RIGHT, padx=4, pady=4
        )
        self.after(800, self._poll_runtime_status)

    def _center_window(self, win, parent):
        win.update_idletasks()
        w = win.winfo_width()
        h = win.winfo_height()
        x = parent.winfo_rootx() + (parent.winfo_width() // 2) - (w // 2)
        y = parent.winfo_rooty() + (parent.winfo_height() // 2) - (h // 2)
        win.geometry(f"{w}x{h}+{x}+{y}")

    def _on_proxy_selected(self, event=None):
        url = self._proxy_var.get()
        if url == "<编辑/新增代理...>":
            self._manage_proxies()
            return
        label = self._proxies_dict.get(url, "")
        self._proxy_lbl.config(text=label)
        save_proxies(self._proxies_dict, url)

    def _manage_proxies(self):
        # Reset combo to previous valid selection immediately
        # (if we cancel, it should not stay on "<编辑...>")
        prev = list(self._proxies_dict.keys())[0] if self._proxies_dict else ""
        for k in self._proxies_dict:
            if self._proxies_dict.get(k) == self._proxy_lbl.cget("text"):
                prev = k
                break
        self._proxy_var.set(prev)

        top = tk.Toplevel(self)
        top.title("管理上游代理")
        top.geometry("500x300")
        top.transient(self._app)
        self._center_window(top, self._app)
        top.grab_set()

        frame = ttk.Frame(top, padding=10)
        frame.pack(fill=tk.BOTH, expand=True)

        list_frame = ttk.Frame(frame)
        list_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))

        lb = tk.Listbox(list_frame, font=("Segoe UI", 10), bg=self._app.palette["surface"], fg=self._app.palette["fg"])
        lb.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        for url, lbl in self._proxies_dict.items():
            lb.insert(tk.END, f"{url} - {lbl}")

        def _add():
            add_top = tk.Toplevel(top)
            add_top.title("新增代理")
            add_top.geometry("400x200")
            add_top.transient(top)
            self._center_window(add_top, top)
            add_top.grab_set()

            ttk.Label(add_top, text="URL (如 http://127.0.0.1:7890):").pack(pady=(10, 0), padx=10, anchor=tk.W)
            url_var = tk.StringVar()
            ttk.Entry(add_top, textvariable=url_var).pack(fill=tk.X, padx=10)

            ttk.Label(add_top, text="标签名 (如 Clash):").pack(pady=(10, 0), padx=10, anchor=tk.W)
            name_var = tk.StringVar()
            ttk.Entry(add_top, textvariable=name_var).pack(fill=tk.X, padx=10)

            def _save():
                u = url_var.get().strip()
                n = name_var.get().strip()
                if not n or not u:
                    messagebox.showerror("错误", "URL和标签名不能为空", parent=add_top)
                    return
                if u in self._proxies_dict:
                    messagebox.showerror("错误", "代理URL已存在", parent=add_top)
                    return
                self._proxies_dict[u] = n
                lb.insert(tk.END, f"{u} - {n}")
                
                self._proxy_cb["values"] = list(self._proxies_dict.keys()) + ["<编辑/新增代理...>"]
                save_proxies(self._proxies_dict, self._proxy_var.get())
                add_top.destroy()

            btn_f = ttk.Frame(add_top)
            btn_f.pack(pady=15)
            ttk.Button(btn_f, text="保存", command=_save).pack(side=tk.LEFT, padx=5)
            ttk.Button(btn_f, text="取消", command=add_top.destroy).pack(side=tk.LEFT, padx=5)

        def _delete():
            sel = lb.curselection()
            if not sel: return
            idx = sel[0]
            val = lb.get(idx)
            url = val.split(" - ")[0]
            if url in ("", ENV_PROXY_SENTINEL):
                messagebox.showwarning("警告", "无法删除内置的直连/系统环境代理选项", parent=top)
                return
            del self._proxies_dict[url]
            lb.delete(idx)
            
            self._proxy_cb["values"] = list(self._proxies_dict.keys()) + ["<编辑/新增代理...>"]
            if self._proxy_var.get() == url:
                self._proxy_var.set("")
                self._on_proxy_selected()
            save_proxies(self._proxies_dict, self._proxy_var.get())

        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill=tk.X)
        ttk.Button(btn_frame, text="新增", command=_add).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(btn_frame, text="删除选定", command=_delete).pack(side=tk.LEFT)
        ttk.Button(btn_frame, text="完成", command=top.destroy).pack(side=tk.RIGHT)

    def _on_upstream_selected(self, event=None):
        label = self._upstream_var.get()
        if label == "<编辑/新增地址...>":
            self._manage_upstreams()
            return
        url = self._upstreams_dict.get(label, "")
        self._upstream_url_lbl.config(text=url)
        save_upstreams(self._upstreams_dict, label)

    def _manage_upstreams(self):
        prev = list(self._upstreams_dict.keys())[0] if self._upstreams_dict else ""
        for k in self._upstreams_dict:
            if k == self._upstream_url_lbl.cget("text"):
                pass # not working properly because label is different
        # Better fallback:
        prev = "官方直连"
        for k, v in self._upstreams_dict.items():
            if v == self._upstream_url_lbl.cget("text"):
                prev = k
                break
        self._upstream_var.set(prev)

        top = tk.Toplevel(self)
        top.title("管理上游地址")
        top.geometry("500x300")
        top.transient(self._app)
        self._center_window(top, self._app)
        top.grab_set()

        frame = ttk.Frame(top, padding=10)
        frame.pack(fill=tk.BOTH, expand=True)

        list_frame = ttk.Frame(frame)
        list_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))

        lb = tk.Listbox(list_frame, font=("Segoe UI", 10), bg=self._app.palette["surface"], fg=self._app.palette["fg"])
        lb.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        for lbl, url in self._upstreams_dict.items():
            lb.insert(tk.END, f"{lbl} - {url}")

        def _add():
            add_top = tk.Toplevel(top)
            add_top.title("新增上游地址")
            add_top.geometry("400x200")
            add_top.transient(top)
            self._center_window(add_top, top)
            add_top.grab_set()

            ttk.Label(add_top, text="标签名 (如 自定义):").pack(pady=(10, 0), padx=10, anchor=tk.W)
            name_var = tk.StringVar()
            ttk.Entry(add_top, textvariable=name_var).pack(fill=tk.X, padx=10)

            ttk.Label(add_top, text="URL (如 https://api.xxx/v1):").pack(pady=(10, 0), padx=10, anchor=tk.W)
            url_var = tk.StringVar()
            ttk.Entry(add_top, textvariable=url_var).pack(fill=tk.X, padx=10)

            def _save():
                n = name_var.get().strip()
                u = url_var.get().strip()
                if not n or not u:
                    messagebox.showerror("错误", "标签名和URL不能为空", parent=add_top)
                    return
                if n in self._upstreams_dict:
                    messagebox.showerror("错误", "标签名已存在", parent=add_top)
                    return
                self._upstreams_dict[n] = u
                lb.insert(tk.END, f"{n} - {u}")
                
                self._upstream_cb["values"] = list(self._upstreams_dict.keys()) + ["<编辑/新增地址...>"]
                save_upstreams(self._upstreams_dict, self._upstream_var.get())
                add_top.destroy()

            btn_f = ttk.Frame(add_top)
            btn_f.pack(pady=15)
            ttk.Button(btn_f, text="保存", command=_save).pack(side=tk.LEFT, padx=5)
            ttk.Button(btn_f, text="取消", command=add_top.destroy).pack(side=tk.LEFT, padx=5)

        def _delete():
            sel = lb.curselection()
            if not sel: return
            idx = sel[0]
            val = lb.get(idx)
            lbl = val.split(" - ")[0]
            if lbl == "官方直连":
                messagebox.showwarning("警告", "无法删除官方直连", parent=top)
                return
            del self._upstreams_dict[lbl]
            lb.delete(idx)
            
            # Update combobox
            self._upstream_cb["values"] = list(self._upstreams_dict.keys()) + ["<编辑/新增地址...>"]
            if self._upstream_var.get() == lbl:
                self._upstream_var.set("官方直连")
                self._on_upstream_selected()
            save_upstreams(self._upstreams_dict, self._upstream_var.get())

        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill=tk.X)
        ttk.Button(btn_frame, text="新增", command=_add).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(btn_frame, text="删除选定", command=_delete).pack(side=tk.LEFT)
        ttk.Button(btn_frame, text="完成", command=top.destroy).pack(side=tk.RIGHT)


    def _show_proxy_help(self):
        messagebox.showinfo(
            "如何获取科学上网地址",
            "如果你的网络无法直连官方 OpenAI 或第三方 API，需在此处填写你的翻墙软件局域网地址。\n\n"
            "如何查找：\n"
            "1. 打开你的 VPN/代理软件\n"
            "2. 寻找「局域网端口」、「本地监听」或「HTTP 代理」\n"
            "3. 组合格式为：http://127.0.0.1:端口号\n\n"
            "常见的默认地址（下拉列表已提供）：\n"
            "• v2rayN / NekoBox：http://127.0.0.1:10808\n"
            "• Clash (Verge 等)：http://127.0.0.1:7890\n"
            "• Shadowsocks：http://127.0.0.1:1080\n\n"
            "如果能直连，请选择“无代理（真正直连）”；若希望读取 HTTP_PROXY/HTTPS_PROXY/ALL_PROXY，请选择“系统环境代理”。"
        )

    def _append_log(self, text: str, tag: str = ""):
        self._log.config(state=tk.NORMAL)
        self._log.insert(tk.END, text + "\n", tag)
        
        # Limit to 5000 lines
        try:
            total_lines = int(self._log.index('end-1c').split('.')[0])
            if total_lines > 5000:
                self._log.delete("1.0", f"{total_lines - 4500}.0")
        except Exception:
            pass
            
        self._log.see(tk.END)
        self._log.config(state=tk.DISABLED)

    def _clear_log(self):
        self._log.config(state=tk.NORMAL)
        self._log.delete("1.0", tk.END)
        self._log.config(state=tk.DISABLED)

    def _set_running(self, running: bool):
        if running:
            color = self._app.palette["success"]
            self._status_canvas.itemconfig(self._dot, fill=color)
            self._status_lbl.config(text=f"运行中  :{self._port_var.get()}", foreground=color)
            self._start_btn.config(state=tk.DISABLED, text="▶ 运行中...")
            self._stop_btn.config(state=tk.NORMAL)
        else:
            color = self._app.palette["muted"]
            self._status_canvas.itemconfig(self._dot, fill=color)
            self._status_lbl.config(text="未启动", foreground=color)
            self._start_btn.config(state=tk.NORMAL, text="▶ 启动代理")
            self._stop_btn.config(state=tk.DISABLED)

    def _poll_runtime_status(self):
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

        # Update OverviewTab
        cb_snap = stats.get("circuit_breaker") or GLOBAL_CIRCUIT_BREAKER.snapshot(get_transport_mode())
        if hasattr(self._app, "_overview_tab"):
            self._app._overview_tab.update_status(
                local_running=healthy,
                config_active=bool(cfg.get("active_for_port")),
                traffic_verified=bool(stats.get("traffic_verified")),
                port=str(self._port_var.get()),
                requests_count=int(stats.get("requests_total", 0)),
                transport_mode=get_transport_mode(),
                cb_snap=cb_snap,
            )

        # Circuit breaker can optionally perform a real provider-level
        # temporary HTTP downgrade, while notify-only mode leaves transport alone.
        self._handle_circuit_transport_policy(cb_snap, cfg)

        # Automation checks
        auto_cfg = load_automation_settings()
        if auto_cfg.windows_notifications:
            if healthy and stats.get("traffic_verified"):
                GLOBAL_NOTIFIER.send_notification("Codex Bridge Toolkit", "Codex 已通过本地代理建立连接", key="traffic_ok")
            if cb_snap.get("state") == "OPEN":
                if cb_snap.get("action_mode") == "auto_switch":
                    notice = "WebSocket 连续失败，已启动临时 HTTP 降级策略"
                else:
                    notice = "WebSocket 连续失败；当前为仅提示模式，可运行网络体检或切换强制 HTTP"
                GLOBAL_NOTIFIER.send_notification("Codex Bridge Toolkit", notice, key="circuit_open")

        if auto_cfg.auto_stop_proxy_on_exit and healthy:
            codex_running = is_codex_running()
            if getattr(self, "_had_codex_running", False) and not codex_running:
                cfg_now = get_proxy_config_status()
                if cfg_now.get("active"):
                    ok, msg = disable_proxy_config()
                    if not ok:
                        self._append_log(f"[自动化] Codex 已退出，但安全恢复 provider 失败，代理保持运行: {msg}", "error")
                        self._had_codex_running = codex_running
                        return
                self._append_log("[自动化] 检测到 Codex 已退出，已恢复 provider 并停止代理", "info")
                self._stop_proxy()
            self._had_codex_running = codex_running

    def _handle_circuit_transport_policy(self, cb_snap: dict, cfg_status: dict) -> None:
        """Apply/revert temporary config-level HTTP downgrade for auto mode."""
        mode = get_transport_mode()
        state = str(cb_snap.get("state") or "CLOSED")
        action = str(cb_snap.get("action_mode") or "notify_only")
        enabled = bool(cb_snap.get("enabled", False))

        if mode != "auto" or not enabled or action != "auto_switch":
            if self._circuit_http_override:
                desired_ws = mode != "http"
                ok, msg = update_managed_ws_support(desired_ws)
                if ok:
                    was_probe = self._circuit_probe_pending
                    self._circuit_http_override = False
                    self._circuit_probe_pending = False
                    self._append_log("[熔断] 自动临时 HTTP 已取消，恢复用户传输策略。", "info")
                    if not was_probe and cfg_status.get("active") and is_codex_running():
                        restart_codex()
                else:
                    self._append_log(f"[熔断] 恢复 provider 失败: {msg}", "error")
            return

        # Only mutate the provider we manage, and only when it is actually active.
        if not cfg_status.get("active"):
            return

        if state == "OPEN":
            if (not self._circuit_http_override) or self._circuit_probe_pending:
                needs_restart = bool(cfg_status.get("supports_websockets", True)) or self._circuit_probe_pending
                ok, msg = update_managed_ws_support(False)
                if not ok:
                    self._append_log(f"[熔断] 临时 HTTP 降级失败: {msg}", "error")
                    return
                self._circuit_http_override = True
                self._circuit_probe_pending = False
                self._append_log("[熔断] 连续 WS 失败，已临时关闭 provider WebSocket 支持。", "warn")
                if needs_restart and is_codex_running():
                    ok_restart, restart_msg = restart_codex()
                    self._append_log(
                        f"[熔断] {'已重启 Codex 进入 HTTP 模式' if ok_restart else 'Codex 自动重启失败'}: {restart_msg}",
                        "warn" if ok_restart else "error",
                    )
            return

        if state == "HALF_OPEN" and self._circuit_http_override and not self._circuit_probe_pending:
            ok, msg = update_managed_ws_support(True)
            if not ok:
                self._append_log(f"[熔断] 半开探测启用 WS 失败: {msg}", "error")
                return
            self._circuit_probe_pending = True
            self._append_log("[熔断] 冷却结束，已临时恢复 WS 并准备一次半开探测。", "info")
            if is_codex_running():
                ok_restart, restart_msg = restart_codex()
                self._append_log(
                    f"[熔断] {'已重启 Codex 进行 WS 半开探测' if ok_restart else 'Codex 自动重启失败'}: {restart_msg}",
                    "info" if ok_restart else "error",
                )
            return

        if state == "CLOSED" and self._circuit_http_override:
            was_probe = self._circuit_probe_pending
            ok, msg = update_managed_ws_support(True)
            if not ok:
                self._append_log(f"[熔断] 恢复 WS provider 配置失败: {msg}", "error")
                return
            self._circuit_http_override = False
            self._circuit_probe_pending = False
            self._append_log("[熔断] WebSocket 已恢复稳定，自动 HTTP 降级结束。", "ok")
            # A successful half-open probe already ran with WS enabled; a manual
            # reset did not, so reload Codex only in the latter case.
            if not was_probe and is_codex_running():
                restart_codex()

    def _post_control(self, path: str, payload: dict | None = None) -> bool:
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
        if ok:
            self._circuit_http_override = False
            self._circuit_probe_pending = False
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

    def _on_transport_mode_changed(self, event=None):
        val = self._transport_mode_var.get()
        mode = "auto"
        if "WebSocket" in val:
            mode = "websocket"
        elif "HTTP" in val:
            mode = "http"
        self._set_transport_mode(mode, prompt_restart=True)

    def _on_circuit_config_changed(self, event=None):
        cd_str = self._cooldown_var.get().replace(" 分钟", "").strip()
        try:
            cd_min = int(cd_str)
        except ValueError:
            cd_min = 15
        current_cfg = get_circuit_breaker_config()
        cfg = {
            "enabled": self._cb_enabled_var.get(),
            "action_mode": "auto_switch" if self._cb_action_var.get() == "自动临时 HTTP" else "notify_only",
            "cooldown_minutes": cd_min,
            "failure_threshold": current_cfg.get("failure_threshold", 3),
        }
        save_circuit_breaker_config(cfg)
        GLOBAL_CIRCUIT_BREAKER.configure(
            enabled=cfg["enabled"],
            cooldown_minutes=cd_min,
            threshold=cfg["failure_threshold"],
            action_mode=cfg["action_mode"],
        )
        self._post_control("/control/circuit/config", {
            "enabled": cfg["enabled"],
            "action_mode": cfg["action_mode"],
            "threshold": cfg["failure_threshold"],
            "cooldown_seconds": cd_min * 60,
        })
        self._append_log(
            f"[熔断配置] 已更新: 启用={cfg['enabled']}, 策略={self._cb_action_var.get()}, 冷却={cd_min}分钟",
            "info",
        )
        if not cfg["enabled"] and self._circuit_http_override:
            desired_ws = get_transport_mode() != "http"
            ok_restore, restore_msg = update_managed_ws_support(desired_ws)
            if ok_restore:
                self._circuit_http_override = False
                self._circuit_probe_pending = False
                if is_codex_running():
                    restart_codex()
            else:
                self._append_log(f"[熔断] 关闭熔断器时恢复 provider 失败: {restore_msg}", "error")


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
        proc = self._app._proxy_proc
        if not proc or proc.poll() is not None:
            self._launch_pending = False

    def _on_proxy_ready(self, port_num: int):
        """Make a newly started proxy effective for an already-running Codex."""
        self._set_running(True)
        if self._launch_pending:
            self._launch_pending = False
            self._activate_and_launch_codex(port_num)
            return
        status = get_proxy_config_status(port_num)
        auto_cfg = load_automation_settings()
        if auto_cfg.auto_apply_provider and not status.get("active_for_port"):
            ok, msg = enable_proxy_config(port_num, self._ws_var.get())
            self._append_log(
                f"[自动化] {'已自动应用 provider' if ok else '自动应用 provider 失败'}: {msg}",
                "ok" if ok else "error",
            )
            status = get_proxy_config_status(port_num)

        if not is_codex_running():
            if not status.get("active_for_port"):
                self._append_log(
                    "[提示] 本地代理已启动，但 Codex 配置尚未指向该代理。请先启用代理配置，再启动 Codex。",
                    "warn",
                )
            return

        if status.get("active_for_port"):
            if messagebox.askyesno(
                "需要重启 Codex",
                "检测到 Codex Desktop 已经在运行。\n\n"
                "Codex 在启动时会读取 provider/连接状态；仅在运行期间启动本地代理，"
                "当前进程不一定会热切换到新代理。\n\n"
                "代理已经正常启动。是否现在重启 Codex，让它立即重新连接本地代理？",
            ):
                ok, msg = restart_codex()
                if ok:
                    self._append_log(f"[Codex 重启] 已重新加载代理配置: {msg}", "ok")
                else:
                    messagebox.showwarning("重启失败", f"{msg}\n\n代理仍在运行，请手动重启 Codex Desktop。")
            else:
                self._append_log(
                    "[提示] 代理已运行，但当前 Codex 可能仍使用启动时的旧连接；重启 Codex 后生效。",
                    "warn",
                )
            return

        if not messagebox.askyesno(
            "让 Codex 接入代理",
            "检测到 Codex Desktop 已经在运行，但当前 config.toml 并未指向刚启动的本地代理。\n\n"
            "只启动 127.0.0.1 的代理服务不会自动改写已经运行的 Codex 连接。\n"
            "需要写入 openai-idfix provider 并重启 Codex 才能立即生效。\n\n"
            "是否现在一键启用代理配置并重启 Codex？",
        ):
            self._append_log(
                "[提示] 本地代理已启动，但当前 Codex 尚未接入；启用代理配置并重启后生效。",
                "warn",
            )
            return

        ok, msg = enable_proxy_config(port_num, self._ws_var.get())
        if not ok:
            self._append_log(f"[配置错误] {msg}", "error")
            messagebox.showerror("写入失败", msg)
            return
        self._append_log(
            f"[配置更新] 已自动启用代理配置: 端口 {port_num}, WS={self._ws_var.get()}", "ok"
        )
        ok, msg = restart_codex()
        if ok:
            self._append_log(f"[Codex 重启] 已接入本地代理: {msg}", "ok")
        else:
            messagebox.showwarning(
                "重启失败",
                f"代理配置已写入，但自动重启失败：{msg}\n\n请手动重启 Codex Desktop；重启后会使用本地代理。",
            )

    def _start_proxy(self):
        if FROZEN:
            if not PROXY_EXE.exists():
                messagebox.showerror(
                    "缺少代理组件",
                    f"找不到 CodexBridgeProxy.exe：\n{PROXY_EXE}\n\n"
                    "请使用 GitHub Release 的完整便携包，并保持两个 EXE 在同一目录。",
                )
                return
        elif not PROXY_SCRIPT.exists():
            messagebox.showerror("错误", f"找不到 proxy.py：\n{PROXY_SCRIPT}")
            return

        port = self._port_var.get().strip()
        label = self._upstream_var.get().strip()
        upstream = self._upstreams_dict.get(label, "https://chatgpt.com/backend-api/codex")
        upstream_proxy = self._proxy_var.get().strip()
        if upstream_proxy == ENV_PROXY_SENTINEL:
            proxy_mode = "env"
            upstream_proxy_url = ""
        elif upstream_proxy:
            proxy_mode = "explicit"
            upstream_proxy_url = upstream_proxy
        else:
            proxy_mode = "direct"
            upstream_proxy_url = ""
        
        # Save selected label
        save_upstreams(self._upstreams_dict, label)
        
        import urllib.parse
        parsed = urllib.parse.urlparse(upstream)
        host = parsed.hostname or ""
        scheme = parsed.scheme.lower()
        if scheme not in ("http", "https") or not host:
            messagebox.showerror("错误", "上游地址必须是完整的 http:// 或 https:// URL")
            return
        
        trusted_hosts = {"chatgpt.com", "api.openai.com", "localhost", "127.0.0.1", "::1"}
        is_safe = False
        
        if host in trusted_hosts:
            if host in {"chatgpt.com", "api.openai.com"}:
                if scheme == "https":
                    is_safe = True
            else:
                if scheme in ("http", "https"):
                    is_safe = True
                    
        if not is_safe:
            if not messagebox.askyesno(
                "安全警告",
                f"你设置的上游地址不是官方 OpenAI/ChatGPT 服务器：\n\n{upstream}\n\n"
                "⚠️ 注意：代理会原样转发 Codex 的认证头（包含凭据）。\n"
                "除非你完全信任该服务器，否则不要继续。\n\n是否仍要启动？"
            ):
                return
                
        try:
            port_num = int(port)
            if not (1 <= port_num <= 65535):
                raise ValueError
            from diagnostics import is_port_in_use
            if is_port_in_use(port_num):
                owner_pid, owner_name = get_port_owner(port_num)
                if owner_pid and is_packaged_toolkit_proxy(owner_name):
                    if not messagebox.askyesno(
                        "检测到遗留代理",
                        f"端口 {port_num} 正被旧的 CodexBridgeProxy 进程占用。\n\n"
                        f"PID: {owner_pid}\n\n"
                        "这通常是上次关闭 Toolkit 时代理进程未完全退出导致的。\n"
                        "是否清理旧代理并重新启动？",
                    ):
                        return
                    if not terminate_process_tree(owner_pid) or not wait_for_port_free(port_num):
                        messagebox.showerror(
                            "清理失败",
                            f"无法停止占用端口 {port_num} 的旧代理。请在任务管理器中结束 CodexBridgeProxy.exe 后重试。",
                        )
                        return
                    self._append_log(
                        f"[清理] 已停止遗留 CodexBridgeProxy 进程 PID={owner_pid}", "warn"
                    )
                else:
                    owner_text = f"（{owner_name or '未知进程'}，PID {owner_pid}）" if owner_pid else ""
                    messagebox.showerror(
                        "端口被占用",
                        f"端口 {port_num} 已被其它进程占用{owner_text}，代理无法启动。",
                    )
                    return
        except ValueError:
            messagebox.showerror("错误", "端口必须是 1–65535 之间的整数")
            return

        if FROZEN:
            cmd = [str(PROXY_EXE)]
        else:
            cmd = [PYTHON_EXE, "-X", "utf8", str(PROXY_SCRIPT)]
        cb_cfg = get_circuit_breaker_config()
        cmd += [
            "--port", port,
            "--upstream", upstream,
            "--reasoning-mode", "safe",
            "--log-level", "DEBUG",
            "--proxy-mode", proxy_mode,
            "--transport-mode", get_transport_mode(),
            "--circuit-enabled", "true" if cb_cfg.get("enabled", True) else "false",
            "--circuit-action", cb_cfg.get("action_mode", "auto_switch"),
            "--circuit-threshold", str(cb_cfg.get("failure_threshold", 3)),
            "--circuit-cooldown-seconds", str(cb_cfg.get("cooldown_seconds", 900)),
        ]
        if proxy_mode == "explicit" and upstream_proxy_url:
            cmd += ["--upstream-proxy", upstream_proxy_url]

        # Mask credentials in logs
        from diagnostics import mask_url
        masked_cmd = []
        for c in cmd:
            if "://" in c:
                masked_cmd.append(mask_url(c) or "")
            else:
                masked_cmd.append(c)

        self._append_log(f"[启动] {' '.join(masked_cmd)}", "info")

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except Exception as e:
            self._append_log(f"[错误] 启动失败：{e}", "error")
            return

        self._app._proxy_proc = proc

        def _poll_health():
            import urllib.request, time
            for _ in range(20):
                if self._app._closing:
                    return
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1):
                        if not self._app._closing:
                            self._app.after(0, lambda p=port_num: self._on_proxy_ready(p))
                        return
                except Exception:
                    time.sleep(0.5)
            if self._app._closing:
                return
            self._app.after(0, lambda: self._append_log("[错误] 代理健康检查超时", "error"))
            self._app.after(0, lambda: self._stop_proxy())

        threading.Thread(target=_poll_health, daemon=True).start()

        def _read():
            for line in proc.stdout:
                line = line.rstrip()
                if not line:
                    continue
                tag = "info"
                if "WARNING" in line or "warn" in line.lower():
                    tag = "warn"
                elif "ERROR" in line or "error" in line.lower():
                    tag = "error"
                elif "200" in line or "ok" in line.lower() or "✅" in line:
                    tag = "ok"
                if not self._app._closing:
                    self._app.after(0, lambda l=line, t=tag: self._append_log(l, t))
            # Process ended. Do not schedule Tk callbacks while the window is closing.
            if not self._app._closing:
                self._app.after(0, lambda: self._set_running(False))
                self._app.after(0, lambda: self._append_log("[代理已停止]", "warn"))

        t = threading.Thread(target=_read, daemon=True)
        t.start()
        self._app._proxy_log_thread = t

    def _stop_proxy(self, for_exit: bool = False):
        proc = self._app._proxy_proc
        if proc and proc.poll() is None:
            pid = proc.pid
            stopped = terminate_process_tree(pid)
            if not stopped:
                # Last-resort fallback for source-mode Python or unusual systems.
                try:
                    proc.kill()
                    proc.wait(timeout=2)
                    stopped = True
                except Exception:
                    stopped = False
            else:
                try:
                    proc.wait(timeout=2)
                except Exception:
                    pass
            self._app._proxy_proc = None
            if not for_exit and not stopped:
                messagebox.showwarning(
                    "停止代理失败",
                    "代理进程未能完全退出。再次启动时 Toolkit 会尝试清理遗留的 CodexBridgeProxy 进程。",
                )
        if not for_exit:
            self._set_running(False)
            self._append_log("[用户手动停止代理]", "warn")

    def _restart_codex(self):
        if not messagebox.askyesno("重启确认", "确认关闭并重新启动 Codex Desktop？"):
            return
        ok, msg = restart_codex()
        if ok:
            self._append_log(f"[Codex 重启] {msg}", "ok")
        else:
            messagebox.showwarning("重启失败", f"{msg}\n\n请手动重新打开 Codex Desktop。")

    def _enable_proxy_config(self):
        port = self._port_var.get().strip()
        try:
            port = int(port)
            if not (1 <= port <= 65535):
                raise ValueError
        except ValueError:
            messagebox.showerror("错误", "端口必须是 1–65535 之间的整数")
            return
            
        ws_enabled = self._ws_var.get()
        ok, msg = enable_proxy_config(port, ws_enabled)
        if ok:
            self._append_log(f"[配置更新] 已启用代理配置: 端口 {port}, WS={ws_enabled}", "ok")
            if is_codex_running():
                if messagebox.askyesno(
                    "配置已写入，需要重启",
                    "Codex Desktop 当前正在运行。\n\n运行中的 Codex 不会可靠地热切换 provider。是否现在重启 Codex 立即生效？",
                ):
                    r_ok, r_msg = restart_codex()
                    if r_ok:
                        self._append_log(f"[Codex 重启] 已重新加载代理配置: {r_msg}", "ok")
                        messagebox.showinfo("成功", "代理配置已写入，Codex Desktop 已重启并重新加载配置。")
                    else:
                        messagebox.showwarning("重启失败", f"代理配置已写入，但自动重启失败：{r_msg}\n\n请手动重启 Codex Desktop。")
                else:
                    messagebox.showinfo("成功", "代理配置已写入。当前 Codex 仍可能继续使用旧连接，重启后生效。")
            else:
                messagebox.showinfo("成功", "代理配置已写入。下次启动 Codex Desktop 时会使用该代理。")
        else:
            self._append_log(f"[配置错误] {msg}", "error")
            messagebox.showerror("写入失败", msg)

    def _disable_proxy_config(self):
        ok, msg = disable_proxy_config()
        if ok:
            self._append_log(f"[配置更新] {msg}", "ok")
            messagebox.showinfo("成功", f"{msg}\n\n如果 Codex 正在运行，请重启 Codex Desktop 以生效。")
        else:
            self._append_log(f"[配置错误] {msg}", "error")
            messagebox.showerror("写入失败", msg)


class AgyTab(ttk.Frame):
    def __init__(self, parent, app: App):
        super().__init__(parent)
        self._app = app
        self._build()

    def _build(self):
        # ── 介绍区 ─────────────────────────────────────────────
        desc_frame = ttk.Frame(self)
        desc_frame.pack(fill=tk.X, padx=12, pady=(12, 6))
        ttk.Label(desc_frame, text="解决 Antigravity (agy) 在国内网络下的连通性问题", font=("Segoe UI", 11, "bold")).pack(anchor=tk.W)
        ttk.Label(desc_frame, text="通过在 PowerShell 配置中注入代理拦截器，让 agy 命令自动走代理，而不会影响系统全局。").pack(anchor=tk.W, pady=(4, 0))

        # ── 配置区 ─────────────────────────────────────────────
        cfg = ttk.LabelFrame(self, text=" 代理配置 ")
        cfg.pack(fill=tk.X, padx=12, pady=10)

        row0 = ttk.Frame(cfg)
        row0.pack(fill=tk.X, padx=10, pady=10)
        ttk.Label(row0, text="目标代理 (HTTP/SOCKS5)：").pack(side=tk.LEFT)
        self._proxy_var = tk.StringVar(value="127.0.0.1:10808")
        ttk.Entry(row0, textvariable=self._proxy_var, width=30).pack(side=tk.LEFT, padx=(0, 20))

        # ── 拦截器管理 ─────────────────────────────────────────
        act = ttk.LabelFrame(self, text=" PowerShell 终端拦截器 ")
        act.pack(fill=tk.X, padx=12, pady=6)
        
        row1 = ttk.Frame(act)
        row1.pack(fill=tk.X, padx=10, pady=10)
        ttk.Label(row1, text="状态：").pack(side=tk.LEFT)
        self._status_lbl = ttk.Label(row1, text="正在检查...", foreground=self._app.palette["fg"])
        self._status_lbl.pack(side=tk.LEFT, padx=(0, 20))

        ttk.Button(row1, text="🚀 安装 agy 专属代理拦截器", command=self._install_hook, style="Accent.TButton").pack(side=tk.LEFT, padx=(0, 10))
        ttk.Button(row1, text="🗑️ 卸载拦截器", command=self._uninstall_hook).pack(side=tk.LEFT)
        
        self._check_status()

    def _check_status(self):
        self._set_status(check_hook_status())

    def _set_status(self, installed: bool):
        if installed:
            self._status_lbl.config(text="✅ 已安装（agy 专属）", foreground=self._app.palette["success"])
        else:
            self._status_lbl.config(text="❌ 未安装", foreground=self._app.palette["danger"])

    def _install_hook(self):
        proxy = self._proxy_var.get().strip()
        try:
            ok, profile_path = install_hook(proxy)
        except ValueError as e:
            messagebox.showerror("无效的代理格式", str(e))
            return
        except OSError as e:
            messagebox.showerror("安装失败", str(e))
            return
            
        self._check_status()
        if ok:
            messagebox.showinfo("安装成功", f"拦截器已写入 PowerShell Profile：\n{profile_path}\n\n【注意】你需要新开一个 PowerShell 窗口才能生效！")

    def _uninstall_hook(self):
        if uninstall_hook():
            self._check_status()
            messagebox.showinfo("卸载成功", "专属代理拦截器已移除。")
        else:
            messagebox.showinfo("卸载失败", "未找到安装的拦截器。")


class DiagnosticsTab(ttk.Frame):
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
        ttk.Button(top, text="📦 生成脱敏支持包", style="Accent.TButton", command=self._generate_support_bundle).pack(side=tk.RIGHT, padx=(6, 0))
        ttk.Button(top, text="复制脱敏报告", command=self._copy_report).pack(side=tk.RIGHT, padx=(6, 0))
        ttk.Button(top, text="刷新诊断", command=self._refresh).pack(side=tk.RIGHT)

        # Error action card
        self._err_card = ttk.LabelFrame(self, text=" ⚠️ 最近异常诊断与推荐操作 ")
        self._err_title = ttk.Label(self._err_card, text="", font=("Segoe UI", 10, "bold"))
        self._err_title.pack(anchor=tk.W, padx=10, pady=(6, 2))
        self._err_desc = ttk.Label(self._err_card, text="", wraplength=800, justify=tk.LEFT)
        self._err_desc.pack(anchor=tk.W, padx=10, pady=(0, 4))
        self._err_btn = ttk.Button(self._err_card, text="", style="Accent.TButton")
        self._err_btn.pack(anchor=tk.W, padx=10, pady=(0, 8))

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

        # Update dynamic error action card
        if err and err.get("category") and err.get("category") != "ok":
            self._err_card.pack(fill=tk.X, padx=12, pady=(0, 6), before=self._text)
            self._err_title.config(text=f"[{err.get('category', '异常')}] {err.get('title', '未知异常')}")
            action_text = err.get("action") or err.get("recommended_action") or "处理"
            self._err_desc.config(text=f"说明: {err.get('explanation') or err.get('detail') or '-'}\n建议操作: {action_text}")
            action_id = err.get("action_id", "none")
            if action_id != "none":
                self._err_btn.config(text=f"执行建议: {action_text}", command=lambda: self._dispatch_action(action_id))
                self._err_btn.pack(anchor=tk.W, padx=10, pady=(0, 8))
            else:
                self._err_btn.pack_forget()
        else:
            self._err_card.pack_forget()

    def _dispatch_action(self, action_id: str):
        if action_id == "scan_sessions":
            self._app.select_tab(self._app._session_tab)
            self._app._session_tab._scan()
        elif action_id == "open_net_diagnostics":
            self._app.select_tab(self._app._net_tab)
            self._app._net_tab._run_diagnostics()
        elif action_id == "switch_force_http":
            self._app.select_tab(self._app._proxy_tab)
            self._app._proxy_tab._set_transport_mode("http")
            messagebox.showinfo("模式已切换", "传输模式已自动切换为「强制 HTTP」。")
        elif action_id == "open_proxy_discovery":
            self._app.select_tab(self._app._net_tab)
            self._app._net_tab._discover_proxies()
        elif action_id == "restore_config":
            self._app.select_tab(self._app._proxy_tab)
            self._app._proxy_tab._disable_proxy_config()
        else:
            messagebox.showinfo("提示", "当前异常暂无自动修复操作，请参考诊断详情手动处理。")

    def _generate_support_bundle(self):
        try:
            port = int(self._app._proxy_tab._port_var.get().strip() or "8787")
        except ValueError:
            port = 8787
        try:
            zip_path = create_support_bundle(port=port)
            messagebox.showinfo(
                "支持包已生成",
                f"脱敏诊断支持包已成功生成并保存至：\n{zip_path}\n\n该压缩包已严格脱敏，不包含密钥、Token、Cookie 或聊天内容。",
            )
            if sys.platform == "win32":
                try:
                    subprocess.run(["explorer", f"/select,{zip_path}"], check=False)
                except Exception:
                    pass
        except Exception as exc:
            messagebox.showerror("生成失败", f"生成诊断支持包时发生错误：\n{exc}")

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


# ─── 网络诊断 Tab ─────────────────────────────────────────────────────────────

class NetworkDiagnosticsTab(ttk.Frame):
    def __init__(self, parent, app: App):
        super().__init__(parent)
        self._app = app
        self._discovered: list[DiscoveredProxy] = []
        self._build()

    def _build(self):
        toolbar = ttk.Frame(self)
        toolbar.pack(fill=tk.X, padx=12, pady=(12, 6))
        ttk.Label(toolbar, text="网络诊断与本地代理发现", font=("Segoe UI", 11, "bold")).pack(side=tk.LEFT)

        self._run_btn = ttk.Button(toolbar, text="🚀 运行网络体检", style="Accent.TButton", command=self._run_diagnostics)
        self._run_btn.pack(side=tk.RIGHT, padx=(6, 0))
        self._scan_btn = ttk.Button(toolbar, text="🔍 扫描本地代理软件", command=self._discover_proxies)
        self._scan_btn.pack(side=tk.RIGHT, padx=(6, 0))

        # 1. 连通性体检结果
        diag_frame = ttk.LabelFrame(self, text=" 🌐 上游连通性体检（对比 HTTP vs WebSocket） ")
        diag_frame.pack(fill=tk.X, padx=12, pady=(0, 8))

        status_grid = ttk.Frame(diag_frame)
        status_grid.pack(fill=tk.X, padx=10, pady=8)

        ttk.Label(status_grid, text="本地代理状态:", font=("Segoe UI", 9, "bold")).grid(row=0, column=0, sticky=tk.W, padx=4, pady=2)
        self._lbl_local = ttk.Label(status_grid, text="未检测", foreground="#a6adc8")
        self._lbl_local.grid(row=0, column=1, sticky=tk.W, padx=(0, 20), pady=2)

        ttk.Label(status_grid, text="上游 HTTPS:", font=("Segoe UI", 9, "bold")).grid(row=0, column=2, sticky=tk.W, padx=4, pady=2)
        self._lbl_https = ttk.Label(status_grid, text="未检测", foreground="#a6adc8")
        self._lbl_https.grid(row=0, column=3, sticky=tk.W, padx=(0, 20), pady=2)

        ttk.Label(status_grid, text="WebSocket 握手:", font=("Segoe UI", 9, "bold")).grid(row=1, column=0, sticky=tk.W, padx=4, pady=2)
        self._lbl_ws = ttk.Label(status_grid, text="未检测", foreground="#a6adc8")
        self._lbl_ws.grid(row=1, column=1, sticky=tk.W, padx=(0, 20), pady=2)

        ttk.Label(status_grid, text="系统代理配置:", font=("Segoe UI", 9, "bold")).grid(row=1, column=2, sticky=tk.W, padx=4, pady=2)
        self._lbl_sys = ttk.Label(status_grid, text="未检测", foreground="#a6adc8")
        self._lbl_sys.grid(row=1, column=3, sticky=tk.W, padx=(0, 20), pady=2)

        # Conclusion & recommendations
        rec_frame = ttk.Frame(diag_frame)
        rec_frame.pack(fill=tk.X, padx=10, pady=(0, 8))
        self._lbl_conclusion = ttk.Label(rec_frame, text="", wraplength=850, justify=tk.LEFT)
        self._lbl_conclusion.pack(anchor=tk.W, pady=(2, 4))

        self._action_btn = ttk.Button(rec_frame, text="一键切换为「强制 HTTP」", style="Accent.TButton", command=self._switch_to_force_http)

        # 2. Local Proxy Discovery
        disc_frame = ttk.LabelFrame(self, text=" 🧭 本地科学上网代理自动发现 ")
        disc_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 12))

        tree_frame = ttk.Frame(disc_frame)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        cols = ("name", "port", "proc", "http_status", "ws_status")
        self._tree = ttk.Treeview(tree_frame, columns=cols, show="headings", height=5)
        self._tree.heading("name", text="代理软件 / 标签")
        self._tree.heading("port", text="端口")
        self._tree.heading("proc", text="进程 (PID)")
        self._tree.heading("http_status", text="HTTP 连通性")
        self._tree.heading("ws_status", text="WebSocket 支持")

        self._tree.column("name", width=180, anchor=tk.W)
        self._tree.column("port", width=80, anchor=tk.CENTER)
        self._tree.column("proc", width=160, anchor=tk.W)
        self._tree.column("http_status", width=200, anchor=tk.W)
        self._tree.column("ws_status", width=200, anchor=tk.W)

        tree_scroll = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self._tree.yview)
        self._tree.configure(yscrollcommand=tree_scroll.set)
        self._tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        disc_actions = ttk.Frame(disc_frame)
        disc_actions.pack(fill=tk.X, padx=8, pady=(0, 8))
        self._test_sel_btn = ttk.Button(disc_actions, text="⚡ 测试选中代理", command=self._test_selected_proxy)
        self._test_sel_btn.pack(side=tk.LEFT, padx=(0, 8))
        self._apply_sel_btn = ttk.Button(disc_actions, text="📥 填入并应用到代理控制", style="Accent.TButton", command=self._apply_selected_proxy)
        self._apply_sel_btn.pack(side=tk.LEFT)

    def _run_diagnostics(self):
        self._run_btn.config(state=tk.DISABLED, text="正在体检中...")
        self._lbl_conclusion.config(text="正在探测本地代理、上游 HTTPS 及 WebSocket 握手，请稍候...", foreground="#89b4fa")
        self._action_btn.pack_forget()

        try:
            port = int(self._app._proxy_tab._port_var.get().strip() or "8787")
        except ValueError:
            port = 8787

        proxy_url = self._app._proxy_tab._selected_outbound_proxy_url()
        upstream_url = self._app._proxy_tab._selected_upstream_url()

        def worker():
            try:
                res = run_full_diagnostics(port=port, upstream_url=upstream_url, proxy_url=proxy_url)
                self.after(0, lambda: self._on_diagnostics_done(res))
            except Exception as exc:
                self.after(0, lambda: self._on_diagnostics_error(str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _on_diagnostics_done(self, res: dict):
        self._run_btn.config(state=tk.NORMAL, text="🚀 运行网络体检")

        local = res.get("local", {})
        if local.get("listening") and local.get("health_ok"):
            self._lbl_local.config(text=f"监听中 ({local.get('latency_ms', 0)}ms)", foreground="#a6e3a1")
        elif local.get("listening"):
            self._lbl_local.config(text="端口已监听 (健康端点异常)", foreground="#f9e2af")
        else:
            self._lbl_local.config(text="未启动", foreground="#f38ba8")

        https = res.get("https", {})
        if https.get("ok"):
            self._lbl_https.config(text=f"正常 ({https.get('total_ms')}ms)", foreground="#a6e3a1")
        else:
            err = https.get("error") or "失败"
            self._lbl_https.config(text=f"异常: {err[:25]}", foreground="#f38ba8")

        ws = res.get("websocket", {})
        if ws.get("ok"):
            self._lbl_ws.config(text=f"101 升级成功 ({ws.get('handshake_ms')}ms)", foreground="#a6e3a1")
        elif ws.get("reachable") and not ws.get("conclusive", True):
            self._lbl_ws.config(text=f"上游可达 / 升级未确认 (HTTP {ws.get('status')})", foreground="#f9e2af")
        else:
            err = ws.get("error") or "失败"
            self._lbl_ws.config(text=f"握手异常: {err[:25]}", foreground="#f38ba8")

        sys_p = res.get("system_proxy", {})
        win_p = sys_p.get("windows_settings", {})
        if win_p.get("proxy_enabled"):
            self._lbl_sys.config(text=f"系统代理: {win_p.get('proxy_server')}", foreground="#89b4fa")
        elif sys_p.get("http_proxy") != "unset":
            self._lbl_sys.config(text=f"环境代理: {sys_p.get('http_proxy')}", foreground="#89b4fa")
        else:
            self._lbl_sys.config(text="系统代理未启用 (直连)", foreground="#a6adc8")

        eval_data = res.get("evaluation", {})
        conclusion = eval_data.get("conclusion", "")
        recs = eval_data.get("recommendations", [])
        rec_text = "\n• " + "\n• ".join(recs) if recs else ""
        self._lbl_conclusion.config(text=f"诊断结论：\n{conclusion}\n\n建议操作：{rec_text}", foreground="#cdd6f4")

        if any("强制 HTTP" in r for r in recs):
            self._action_btn.pack(anchor=tk.W, pady=(4, 0))
        else:
            self._action_btn.pack_forget()

    def _on_diagnostics_error(self, err_msg: str):
        self._run_btn.config(state=tk.NORMAL, text="🚀 运行网络体检")
        self._lbl_conclusion.config(text=f"诊断执行异常：\n{err_msg}", foreground="#f38ba8")

    def _switch_to_force_http(self):
        self._app.select_tab(self._app._proxy_tab)
        self._app._proxy_tab._set_transport_mode("http")
        messagebox.showinfo("已切换", "传输模式已切换为「强制 HTTP」。")

    def _discover_proxies(self):
        self._scan_btn.config(state=tk.DISABLED, text="正在扫描...")
        try:
            current_port = int(self._app._proxy_tab._port_var.get().strip() or "8787")
        except ValueError:
            current_port = 8787

        def worker():
            found = discover_proxies(exclude_port=current_port)
            self.after(0, lambda: self._on_proxies_discovered(found))

        threading.Thread(target=worker, daemon=True).start()

    def _on_proxies_discovered(self, found: list[DiscoveredProxy]):
        self._scan_btn.config(state=tk.NORMAL, text="🔍 扫描本地代理软件")
        self._discovered = found
        for item in self._tree.get_children():
            self._tree.delete(item)

        if not found:
            self._tree.insert("", tk.END, values=("未发现正在监听的本地代理软件", "-", "-", "-", "-"))
            return

        for p in found:
            proc_str = f"{p.process_name or '未知'} ({p.pid or '-'})"
            self._tree.insert(
                "",
                tk.END,
                iid=str(p.port),
                values=(p.name, p.port, proc_str, "待测试", "待测试"),
            )

    def _test_selected_proxy(self):
        sel = self._tree.selection()
        if not sel:
            messagebox.showinfo("提示", "请先在列表中选中一个代理软件。")
            return
        port_str = sel[0]
        try:
            port = int(port_str)
        except ValueError:
            return

        target_proxy = next((p for p in self._discovered if p.port == port), None)
        if not target_proxy:
            return

        self._tree.set(port_str, "http_status", "正在测试 HTTP...")
        self._tree.set(port_str, "ws_status", "正在测试 WS...")

        def worker():
            http_ok, http_ms, http_msg = test_proxy_http(target_proxy.proxy_url)
            ws_ok, ws_ms, ws_msg = test_proxy_ws(target_proxy.proxy_url)
            self.after(0, lambda: self._on_proxy_test_done(port_str, http_msg, ws_msg))

        threading.Thread(target=worker, daemon=True).start()

    def _on_proxy_test_done(self, port_str: str, http_msg: str, ws_msg: str):
        try:
            self._tree.set(port_str, "http_status", http_msg)
            self._tree.set(port_str, "ws_status", ws_msg)
        except Exception:
            pass

    def _apply_selected_proxy(self):
        sel = self._tree.selection()
        if not sel:
            messagebox.showinfo("提示", "请先在列表中选中一个代理软件。")
            return
        port_str = sel[0]
        try:
            port = int(port_str)
        except ValueError:
            return

        target_proxy = next((p for p in self._discovered if p.port == port), None)
        if not target_proxy:
            return

        proxy_url = target_proxy.proxy_url
        self._app._proxy_tab._apply_discovered_proxy(proxy_url, target_proxy.name)
        self._app.select_tab(self._app._proxy_tab)
        messagebox.showinfo(
            "已应用代理",
            f"已将发现的代理软件 [{target_proxy.name}] 地址：\n{proxy_url}\n填入「代理控制」自定义出站代理配置中！",
        )


# ─── 设置 Tab ─────────────────────────────────────────────────────────────────

class SettingsTab(ttk.Frame):
    def __init__(self, parent, app: App):
        super().__init__(parent)
        self._app = app
        self._build()

    def _build(self):
        # 1. 开机与自动化设置
        auto_frame = ttk.LabelFrame(self, text=" ⚡ 系统开机与运行自动化 ")
        auto_frame.pack(fill=tk.X, padx=12, pady=(12, 8))

        self._startup_var = tk.BooleanVar(value=GLOBAL_STARTUP_MANAGER.is_startup_enabled())
        self._auto_proxy_var = tk.BooleanVar()
        self._auto_provider_var = tk.BooleanVar()
        self._auto_stop_var = tk.BooleanVar()
        self._notifications_var = tk.BooleanVar()

        auto_cfg = load_automation_settings()
        self._auto_proxy_var.set(auto_cfg.auto_start_proxy)
        self._auto_provider_var.set(auto_cfg.auto_apply_provider)
        self._auto_stop_var.set(auto_cfg.auto_stop_proxy_on_exit)
        self._notifications_var.set(auto_cfg.windows_notifications)

        ttk.Checkbutton(
            auto_frame,
            text="开机自动启动 Toolkit (写入 HKCU Run 注册表，无需管理员权限)",
            variable=self._startup_var,
            command=self._toggle_startup,
        ).pack(anchor=tk.W, padx=12, pady=(8, 4))
        ttk.Checkbutton(
            auto_frame,
            text="Toolkit 启动时自动运行本地代理",
            variable=self._auto_proxy_var,
            command=self._save_automation,
        ).pack(anchor=tk.W, padx=12, pady=4)
        ttk.Checkbutton(
            auto_frame,
            text="代理启动成功后自动注入 provider 到 Codex config.toml",
            variable=self._auto_provider_var,
            command=self._save_automation,
        ).pack(anchor=tk.W, padx=12, pady=4)
        ttk.Checkbutton(
            auto_frame,
            text="Codex 退出后自动停止代理并安全恢复原 provider",
            variable=self._auto_stop_var,
            command=self._save_automation,
        ).pack(anchor=tk.W, padx=12, pady=4)
        ttk.Checkbutton(
            auto_frame,
            text="启用 Windows 系统通知与气泡提醒 (速率受限，避免频繁打扰)",
            variable=self._notifications_var,
            command=self._save_automation,
        ).pack(anchor=tk.W, padx=12, pady=(4, 8))

        # 2. 熔断器配置
        cb_frame = ttk.LabelFrame(self, text=" 🛡️ WebSocket 熔断器与容灾设置 ")
        cb_frame.pack(fill=tk.X, padx=12, pady=(0, 8))

        cb_cfg = get_circuit_breaker_config()
        grid = ttk.Frame(cb_frame)
        grid.pack(fill=tk.X, padx=12, pady=8)

        ttk.Label(grid, text="连续 WS 失败熔断阈值:").grid(row=0, column=0, sticky=tk.W, padx=4, pady=4)
        self._cb_thresh_var = tk.StringVar(value=str(cb_cfg.get("failure_threshold", 3)))
        thresh_ent = ttk.Entry(grid, textvariable=self._cb_thresh_var, width=8)
        thresh_ent.grid(row=0, column=1, sticky=tk.W, padx=4, pady=4)
        ttk.Label(grid, text="次 (达到后按代理控制页策略：仅提示或临时 HTTP；临时 HTTP 需要重载 Codex)").grid(row=0, column=2, sticky=tk.W, padx=4, pady=4)

        ttk.Label(grid, text="熔断冷却重试周期:").grid(row=1, column=0, sticky=tk.W, padx=4, pady=4)
        self._cb_cooldown_var = tk.StringVar(value=str(cb_cfg.get("cooldown_seconds", 900)))
        cooldown_ent = ttk.Entry(grid, textvariable=self._cb_cooldown_var, width=8)
        cooldown_ent.grid(row=1, column=1, sticky=tk.W, padx=4, pady=4)
        ttk.Label(grid, text="秒 (半开探测前等待时间，默认 900 秒 / 15 分钟)").grid(row=1, column=2, sticky=tk.W, padx=4, pady=4)

        cb_btns = ttk.Frame(cb_frame)
        cb_btns.pack(fill=tk.X, padx=12, pady=(0, 8))
        ttk.Button(cb_btns, text="保存熔断配置", style="Accent.TButton", command=self._save_cb_config).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(cb_btns, text="重置熔断状态 (恢复 CLOSED)", command=self._reset_cb).pack(side=tk.LEFT)

        self._cb_status_lbl = ttk.Label(cb_btns, text="", foreground="#89b4fa")
        self._cb_status_lbl.pack(side=tk.LEFT, padx=12)

        # 3. 关于与元数据
        about_frame = ttk.LabelFrame(self, text=" ℹ️ 关于 Codex Bridge Toolkit ")
        about_frame.pack(fill=tk.X, padx=12, pady=(0, 12))

        about_text = (
            f"版本：v{APP_VERSION} (Network Compatibility & Recovery)\n"
            f"项目地址：https://github.com/zankzeke/codex-desktop-toolkit\n"
            f"运行环境：Python {sys.version.split()[0]} ({sys.platform})\n"
            f"配置路径：~/.codex/config.toml\n"
            f"自动化状态：~/.codex-toolkit/state.json"
        )
        ttk.Label(about_frame, text=about_text, justify=tk.LEFT, font=("Consolas", 9)).pack(anchor=tk.W, padx=12, pady=8)

        self._refresh_cb_status()

    def _toggle_startup(self):
        if self._startup_var.get():
            ok = GLOBAL_STARTUP_MANAGER.enable_startup()
            if not ok:
                messagebox.showerror("设置失败", "无法写入开机启动项到注册表。")
                self._startup_var.set(False)
        else:
            GLOBAL_STARTUP_MANAGER.disable_startup()
        self._save_automation()

    def _save_automation(self):
        cfg = AutomationSettings(
            launch_on_startup=self._startup_var.get(),
            auto_start_proxy=self._auto_proxy_var.get(),
            auto_apply_provider=self._auto_provider_var.get(),
            auto_stop_proxy_on_exit=self._auto_stop_var.get(),
            windows_notifications=self._notifications_var.get(),
        )
        save_automation_settings(cfg)

    def _save_cb_config(self):
        try:
            thresh = int(self._cb_thresh_var.get().strip())
            cd = int(self._cb_cooldown_var.get().strip())
            if thresh < 1 or cd < 1:
                raise ValueError()
        except ValueError:
            messagebox.showerror("参数无效", "阈值与冷却时间必须为大于 0 的整数。")
            return

        current = get_circuit_breaker_config()
        save_circuit_breaker_config(
            failure_threshold=thresh,
            cooldown_seconds=cd,
            enabled=current.get("enabled", True),
            action_mode=current.get("action_mode", "auto_switch"),
        )
        GLOBAL_CIRCUIT_BREAKER.configure(
            threshold=thresh,
            cooldown_seconds=cd,
            enabled=current.get("enabled", True),
            action_mode=current.get("action_mode", "auto_switch"),
        )
        self._app._proxy_tab._post_control("/control/circuit/config", {
            "threshold": thresh,
            "cooldown_seconds": cd,
            "enabled": current.get("enabled", True),
            "action_mode": current.get("action_mode", "auto_switch"),
        })
        self._refresh_cb_status()
        messagebox.showinfo("已保存", f"熔断配置已更新：失败阈值 {thresh} 次，冷却时间 {cd} 秒。")

    def _reset_cb(self):
        GLOBAL_CIRCUIT_BREAKER.reset()
        self._app._proxy_tab._post_control("/control/circuit/reset", {})
        self._refresh_cb_status()
        messagebox.showinfo("已重置", "WebSocket 熔断器已重置为 CLOSED 状态。")

    def _refresh_cb_status(self):
        snap = GLOBAL_CIRCUIT_BREAKER.snapshot()
        self._cb_status_lbl.config(text=f"当前熔断状态: {snap['state']} (连续失败: {snap.get('consecutive_failures', 0)})")
        self.after(3000, self._refresh_cb_status)


# ─── 入口 ──────────────────────────────────────────────────────────────────────

def run_gui_smoke_test() -> int:
    """Construct the real packaged GUI once, then exit without interaction."""
    app = App()
    try:
        app.update_idletasks()
        app.update()
        for attr in ("_overview_tab", "_proxy_tab", "_session_tab", "_net_tab", "_agy_tab", "_diag_tab", "_settings_tab"):
            if not hasattr(app, attr):
                raise RuntimeError(f"GUI tab {attr} failed to initialise")
        return 0
    finally:
        app._closing = True
        try:
            app._tray.stop()
        except Exception:
            pass
        app.destroy()


if __name__ == "__main__":
    if "--smoke-test" in sys.argv:
        raise SystemExit(run_gui_smoke_test())
    app = App()
    app.mainloop()
