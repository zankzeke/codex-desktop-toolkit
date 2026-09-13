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
from config_manager import enable_proxy_config, disable_proxy_config
from powershell_hook import install_hook, uninstall_hook, check_hook_status
from ui_theme import ThemeManager, APP_VERSION
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
    for p in CODEX_EXE_CANDIDATES:
        if p.exists():
            return p
    # Try to find via tasklist
    try:
        result = subprocess.run(
            ["powershell", "-Command",
             "(Get-Process Codex -ErrorAction SilentlyContinue | Select-Object -First 1).Path"],
            capture_output=True, text=True, timeout=5
        )
        path_str = result.stdout.strip()
        if path_str and Path(path_str).exists():
            return Path(path_str)
    except Exception:
        pass
    return None


def restart_codex():
    """Kill Codex Desktop and relaunch it."""
    subprocess.run(["taskkill", "/F", "/IM", "Codex.exe"], capture_output=True)
    time.sleep(1.5)
    exe = find_codex_exe()
    if exe:
        subprocess.Popen([str(exe)], creationflags=subprocess.DETACHED_PROCESS)
        return True, str(exe)
    return False, "未找到 Codex.exe"


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

    def _on_close(self):
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
        self._proxy_var = tk.StringVar(value="")
        ttk.Entry(row0, textvariable=self._proxy_var, width=26).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(row0, text="示例", width=5, command=self._show_proxy_examples).pack(
            side=tk.LEFT, padx=(0, 12)
        )

        ttk.Label(row0, text="上游地址：").pack(side=tk.LEFT)
        self._upstream_var = tk.StringVar(value="https://chatgpt.com/backend-api/codex")
        ttk.Entry(row0, textvariable=self._upstream_var, width=38).pack(side=tk.LEFT)

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

    def _show_proxy_examples(self):
        messagebox.showinfo(
            "常见上游代理地址",
            "常见填写示例（实际端口以代理软件当前设置为准）：\n\n"
            "Clash Verge Rev:  http://127.0.0.1:7897\n"
            "Clash / Mihomo:    http://127.0.0.1:7890\n"
            "v2rayN (HTTP):     http://127.0.0.1:10809\n"
            "NekoRay (HTTP):    http://127.0.0.1:2081\n\n"
            "这里只填写 HTTP / Mixed 端口；8787 是 Toolkit 自己的本地端口。\n"
            "更多说明见仓库 docs/PROXY_SETUP_ZH.md。",
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
        upstream = self._upstream_var.get().strip()
        upstream_proxy = self._proxy_var.get().strip()
        
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
        ]
        if upstream_proxy:
            cmd += ["--upstream-proxy", upstream_proxy]

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
                            self._app.after(0, lambda: self._set_running(True))
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
            messagebox.showinfo("成功", "代理配置已写入。\n如果 Codex 正在运行，请重启 Codex Desktop 以生效。")
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
        self._build()

    def _build(self):
        top = ttk.Frame(self)
        top.pack(fill=tk.X, padx=12, pady=(12, 6))

        ttk.Label(top, text="运行诊断", font=("Segoe UI", 11, "bold")).pack(side=tk.LEFT)
        ttk.Button(top, text="🔄 刷新诊断", command=self._refresh).pack(side=tk.RIGHT)

        self._text = scrolledtext.ScrolledText(
            self, bg=self._app.palette["surface"], fg=self._app.palette["fg"],
            font=("Consolas", 10), state=tk.DISABLED,
            relief=tk.FLAT, borderwidth=0,
        )
        self._text.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)

    def _refresh(self):
        from diagnostics import get_diagnostics
        port = int(self._app._proxy_tab._port_var.get().strip() or "8787")
        data = get_diagnostics(port)

        self._text.config(state=tk.NORMAL)
        self._text.delete("1.0", tk.END)
        self._text.insert(tk.END, "====== 运行诊断 ======\n\n")

        self._text.insert(tk.END, "[Codex Desktop]\n")
        self._text.insert(tk.END, f"  运行状态: {'🟢 正在运行' if data['codex']['running'] else '🔴 未运行'}\n")
        self._text.insert(tk.END, f"  配置文件: {'✅ 存在' if data['codex']['config_exists'] else '❌ 丢失'}\n\n")

        self._text.insert(tk.END, "[ID-Fix Proxy]\n")
        pr_run = data['proxy']['running']
        self._text.insert(tk.END, f"  运行状态: {'🟢 正在运行' if pr_run else '🔴 未运行或异常'}\n")
        self._text.insert(tk.END, f"  监听端口: {port}\n")
        if pr_run and "details" in data["proxy"]:
            d = data["proxy"]["details"]
            self._text.insert(tk.END, f"  上游代理: {d.get('upstream_proxy') or '无 (直连)'}\n")
            self._text.insert(tk.END, f"  上游地址: {d.get('upstream')}\n")
            self._text.insert(tk.END, f"  WS 支持: {'✅' if d.get('websocket_supported') else '❌'}\n")
        self._text.insert(tk.END, "\n")

        self._text.insert(tk.END, "[系统环境变量 (影响 agy)]\n")
        for k, v in data["network"].items():
            self._text.insert(tk.END, f"  {k}: {v or '未设置'}\n")

        self._text.config(state=tk.DISABLED)

# ─── 入口 ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = App()
    app.mainloop()
