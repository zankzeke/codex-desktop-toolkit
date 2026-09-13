"""Theme and Windows chrome helpers for Codex Bridge Toolkit."""
from __future__ import annotations

import ctypes
import json
import os
import tkinter as tk
from pathlib import Path
from tkinter import ttk

APP_VERSION = "0.2.0"
DEFAULT_THEME = "午夜蓝"
UI_STATE_PATH = Path.home() / ".codex-toolkit" / "ui.json"

THEMES = {
    "午夜蓝": {
        "bg": "#0f1220", "panel": "#171b2e", "surface": "#111526",
        "hover": "#252b46", "selection": "#303a61", "border": "#343b59",
        "fg": "#e7ecff", "muted": "#9aa6c6", "accent": "#6ea8ff",
        "accent_hover": "#8fbcff", "danger": "#ff6b8b", "danger_hover": "#ff8da5",
        "success": "#66d9a8", "warn": "#f5bd72", "titlebar": "#11172a",
    },
    "石墨黑": {
        "bg": "#121416", "panel": "#1b1f23", "surface": "#15191d",
        "hover": "#2a3036", "selection": "#343b43", "border": "#353c44",
        "fg": "#f0f2f4", "muted": "#9da6ae", "accent": "#8ab4f8",
        "accent_hover": "#a8c7fa", "danger": "#ff7b88", "danger_hover": "#ff9ba5",
        "success": "#7bdba8", "warn": "#f2c66d", "titlebar": "#171a1d",
    },
    "深海蓝": {
        "bg": "#07151e", "panel": "#0c2230", "surface": "#091c28",
        "hover": "#12364a", "selection": "#17465e", "border": "#1e4f64",
        "fg": "#e4f6ff", "muted": "#91b4c3", "accent": "#45c2ff",
        "accent_hover": "#75d2ff", "danger": "#ff708b", "danger_hover": "#ff91a5",
        "success": "#62d8b1", "warn": "#f0bd68", "titlebar": "#08202d",
    },
    "明亮": {
        "bg": "#f3f6fb", "panel": "#ffffff", "surface": "#f8faff",
        "hover": "#e7edf7", "selection": "#dce8fb", "border": "#ccd6e5",
        "fg": "#182235", "muted": "#627089", "accent": "#2f6fda",
        "accent_hover": "#225fc1", "danger": "#d64562", "danger_hover": "#bf3853",
        "success": "#238a62", "warn": "#ad6b12", "titlebar": "#e9eef7",
    },
}


class ThemeManager:
    def __init__(self, root: tk.Tk, assets_dir: Path):
        self.root = root
        self.assets_dir = Path(assets_dir)
        self.name = self._load_theme()
        self.palette = THEMES[self.name]
        self.root.palette = self.palette
        self.style = ttk.Style(root)
        self.style.theme_use("clam")
        self._header_icon = None
        self._app_icon = None
        self._set_app_id()
        self._load_icons()
        self.apply_styles()

    def _load_theme(self) -> str:
        try:
            data = json.loads(UI_STATE_PATH.read_text(encoding="utf-8"))
            if data.get("theme") in THEMES:
                return data["theme"]
        except Exception:
            pass
        return DEFAULT_THEME

    def _save_theme(self):
        try:
            UI_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = UI_STATE_PATH.with_suffix(".tmp")
            tmp.write_text(json.dumps({"theme": self.name}, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, UI_STATE_PATH)
        except Exception:
            pass

    def _set_app_id(self):
        if os.name != "nt":
            return
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "zankzeke.CodexBridgeToolkit"
            )
        except Exception:
            pass

    def _load_icons(self):
        ico = self.assets_dir / "codex_bridge.ico"
        png = self.assets_dir / "codex_bridge.png"
        if os.name == "nt" and ico.exists():
            try:
                self.root.iconbitmap(default=str(ico))
            except tk.TclError:
                pass
        if png.exists():
            try:
                self._app_icon = tk.PhotoImage(file=str(png))
                self.root.iconphoto(True, self._app_icon)
            except tk.TclError:
                self._app_icon = None

    def build_header(self):
        header = ttk.Frame(self.root, style="Header.TFrame")
        header.pack(fill=tk.X, padx=10, pady=(10, 8))
        png = self.assets_dir / "codex_bridge.png"
        if png.exists():
            try:
                raw = tk.PhotoImage(file=str(png))
                factor = max(1, raw.width() // 34)
                self._header_icon = raw.subsample(factor, factor)
                ttk.Label(header, image=self._header_icon, style="Header.TLabel").pack(
                    side=tk.LEFT, padx=(10, 8), pady=8
                )
            except tk.TclError:
                pass
        title_box = ttk.Frame(header, style="Header.TFrame")
        title_box.pack(side=tk.LEFT, fill=tk.Y, pady=7)
        ttk.Label(title_box, text="Codex Bridge Toolkit", style="HeaderTitle.TLabel").pack(anchor=tk.W)
        ttk.Label(
            title_box,
            text="Session repair · Compatibility proxy · Antigravity helper",
            style="HeaderSub.TLabel",
        ).pack(anchor=tk.W, pady=(1, 0))
        right = ttk.Frame(header, style="Header.TFrame")
        right.pack(side=tk.RIGHT, padx=10, pady=9)
        ttk.Label(right, text=f"v{APP_VERSION}", style="HeaderSub.TLabel").pack(side=tk.RIGHT, padx=(10, 0))
        self.theme_var = tk.StringVar(value=self.name)
        box = ttk.Combobox(
            right, textvariable=self.theme_var, values=list(THEMES),
            state="readonly", width=9, style="Theme.TCombobox"
        )
        box.pack(side=tk.RIGHT)
        box.bind("<<ComboboxSelected>>", self._on_select)
        ttk.Label(right, text="主题", style="HeaderSub.TLabel").pack(side=tk.RIGHT, padx=(0, 7))
        return header

    def apply_styles(self):
        p, s = self.palette, self.style
        self.root.configure(bg=p["bg"])
        s.configure(".", background=p["bg"], foreground=p["fg"], font=("Segoe UI", 10))
        s.configure("Header.TFrame", background=p["panel"], relief="flat")
        s.configure("Header.TLabel", background=p["panel"], foreground=p["fg"])
        s.configure("HeaderTitle.TLabel", background=p["panel"], foreground=p["fg"], font=("Segoe UI Variable Display", 13, "bold"))
        s.configure("HeaderSub.TLabel", background=p["panel"], foreground=p["muted"], font=("Segoe UI", 9))
        s.configure("TNotebook", background=p["bg"], borderwidth=0)
        s.configure("TNotebook.Tab", background=p["panel"], foreground=p["muted"], padding=[16, 8], font=("Segoe UI", 10, "bold"), borderwidth=0)
        s.map("TNotebook.Tab", background=[("selected", p["hover"]), ("active", p["hover"])], foreground=[("selected", p["accent"]), ("active", p["fg"])])
        s.configure("TFrame", background=p["bg"])
        s.configure("TLabel", background=p["bg"], foreground=p["fg"])
        s.configure("TButton", background=p["panel"], foreground=p["fg"], borderwidth=0, padding=[11, 6], font=("Segoe UI", 10))
        s.map("TButton", background=[("active", p["hover"]), ("pressed", p["selection"])], foreground=[("active", p["accent"])])
        s.configure("Accent.TButton", background=p["accent"], foreground=p["bg"], font=("Segoe UI", 10, "bold"), padding=[12, 6])
        s.map("Accent.TButton", background=[("active", p["accent_hover"])])
        s.configure("Danger.TButton", background=p["danger"], foreground="#ffffff", font=("Segoe UI", 10, "bold"), padding=[12, 6])
        s.map("Danger.TButton", background=[("active", p["danger_hover"])])
        s.configure("Treeview", background=p["surface"], foreground=p["fg"], fieldbackground=p["surface"], rowheight=30, borderwidth=0)
        s.configure("Treeview.Heading", background=p["panel"], foreground=p["accent"], font=("Segoe UI", 10, "bold"), relief="flat")
        s.map("Treeview", background=[("selected", p["selection"])], foreground=[("selected", p["fg"])])
        s.configure("TCheckbutton", background=p["bg"], foreground=p["fg"])
        s.configure("TEntry", fieldbackground=p["panel"], foreground=p["fg"], insertcolor=p["fg"], padding=5)
        s.configure("TLabelframe", background=p["bg"], foreground=p["fg"], borderwidth=1, relief="solid")
        s.configure("TLabelframe.Label", background=p["bg"], foreground=p["accent"], font=("Segoe UI", 10, "bold"))
        s.configure("Theme.TCombobox", fieldbackground=p["surface"], background=p["surface"], foreground=p["fg"], arrowcolor=p["accent"], padding=4)
        s.map("Theme.TCombobox", fieldbackground=[("readonly", p["surface"])], foreground=[("readonly", p["fg"])])

    def refresh_widgets(self):
        p = self.palette
        self.root.palette = p

        def walk(widget):
            try:
                if isinstance(widget, tk.Canvas):
                    widget.configure(bg=p["bg"], highlightthickness=0)
                elif isinstance(widget, tk.Text):
                    widget.configure(bg=p["surface"], fg=p["fg"], insertbackground=p["fg"], selectbackground=p["selection"], selectforeground=p["fg"])
                    for tag, key in (("info", "accent"), ("warn", "warn"), ("error", "danger"), ("ok", "success")):
                        try:
                            widget.tag_config(tag, foreground=p[key])
                        except tk.TclError:
                            pass
            except tk.TclError:
                pass
            for child in widget.winfo_children():
                walk(child)

        walk(self.root)
        self._style_windows_chrome()

    def _on_select(self, _event=None):
        name = self.theme_var.get()
        if name not in THEMES:
            return
        self.name = name
        self.palette = THEMES[name]
        self.root.palette = self.palette
        self._save_theme()
        self.apply_styles()
        self.refresh_widgets()

    @staticmethod
    def _colorref(value: str) -> int:
        value = value.lstrip("#")
        r, g, b = int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)
        return r | (g << 8) | (b << 16)

    def _style_windows_chrome(self):
        if os.name != "nt":
            return
        try:
            self.root.update_idletasks()
            hwnd = self.root.winfo_id()
            dark = ctypes.c_int(0 if self.name == "明亮" else 1)
            for attr in (20, 19):
                try:
                    ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(dark), ctypes.sizeof(dark))
                    break
                except Exception:
                    pass
            for attr, key in ((35, "titlebar"), (34, "border"), (36, "fg")):
                color = ctypes.c_int(self._colorref(self.palette[key]))
                try:
                    ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(color), ctypes.sizeof(color))
                except Exception:
                    pass
        except Exception:
            pass
