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
    load_proxies, save_proxies, ENV_PROXY_SENTINEL
)
from powershell_hook import install_hook, uninstall_hook, check_hook_status
from ui_theme import ThemeManager, APP_VERSION
from tray_manager import TrayController
from update_checker import check_for_update
from process_utils import (
    get_port_owner,
    is_packaged_toolkit_proxy,
    terminate_process_tree,
    wait_for_port_free,
)


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

        nb = ttk.Notebook(self)
        nb.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        self._session_tab = SessionTab(nb, self)
        self._proxy_tab = ProxyTab(nb, self)
        self._agy_tab = AgyTab(nb, self)
        self._diag_tab = DiagnosticsTab(nb, self)
        nb.add(self._session_tab, text="  🔧 会话修复  ")
        nb.add(self._proxy_tab, text="  🔌 代理控制  ")
        nb.add(self._agy_tab, text="  🚀 Antigravity 网络  ")
        nb.add(self._diag_tab, text="  🩺 运行诊断  ")

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(0, self._theme_manager.refresh_widgets)
        self.after(1800, self._check_updates_background)

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
        toml_frame = ttk.LabelFrame(self, text=" Codex 配置文件 (config.toml) ")
        toml_frame.pack(fill=tk.X, padx=12, pady=6)

        row_toml = ttk.Frame(toml_frame)
        row_toml.pack(fill=tk.X, padx=10, pady=(8, 8))

        ttk.Label(row_toml, text="一键将上面的代理配置写入 Codex Desktop：").pack(side=tk.LEFT)
        
        self._ws_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(row_toml, text="支持 WebSocket", variable=self._ws_var).pack(side=tk.LEFT, padx=(0, 10))

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
            self._start_btn.config(state=tk.DISABLED)
            self._stop_btn.config(state=tk.NORMAL)
        else:
            color = self._app.palette["muted"]
            self._status_canvas.itemconfig(self._dot, fill=color)
            self._status_lbl.config(text="未启动", foreground=color)
            self._start_btn.config(state=tk.NORMAL)
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
        cmd += [
            "--port", port,
            "--upstream", upstream,
            "--reasoning-mode", "safe",
            "--log-level", "DEBUG",
            "--proxy-mode", proxy_mode,
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


# ─── 入口 ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = App()
    app.mainloop()
