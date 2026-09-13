from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent if Path(__file__).parent.name == 'scripts' else Path(__file__).resolve().parent
GUI = ROOT / 'codex_toolkit_gui.py'
HISTORY = ROOT / 'history_fixer.py'
README = ROOT / 'README.md'
CHANGELOG = ROOT / 'CHANGELOG.md'


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise RuntimeError(f'patch target not found: {label}')
    return text.replace(old, new, 1)


s = GUI.read_text(encoding='utf-8')

old = '''SCRIPT_DIR = Path(__file__).parent.resolve()\nCODEX_HOME = Path.home() / ".codex"\nSESSIONS_DIR = CODEX_HOME / "sessions"\nSTATE_DB = CODEX_HOME / "state_5.sqlite"\nPROXY_SCRIPT = SCRIPT_DIR / "proxy.py"\nVENV_PYTHON = SCRIPT_DIR / ".venv" / "Scripts" / "python.exe"\nPYTHON_EXE = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable\n'''
new = '''FROZEN = bool(getattr(sys, "frozen", False))\nAPP_DIR = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).parent.resolve()\nBUNDLE_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR))\nSCRIPT_DIR = APP_DIR\nCODEX_HOME = Path.home() / ".codex"\nSESSIONS_DIR = CODEX_HOME / "sessions"\nSTATE_DB = CODEX_HOME / "state_5.sqlite"\nPROXY_SCRIPT = SCRIPT_DIR / "proxy.py"\nPROXY_EXE = APP_DIR / "CodexBridgeProxy.exe"\nVENV_PYTHON = SCRIPT_DIR / ".venv" / "Scripts" / "python.exe"\nPYTHON_EXE = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable\nASSETS_DIR = BUNDLE_DIR / "assets"\n'''
s = replace_once(s, old, new, 'runtime paths')

import_anchor = 'from powershell_hook import install_hook, uninstall_hook, check_hook_status\n'
s = replace_once(s, import_anchor, import_anchor + 'from ui_theme import ThemeManager, APP_VERSION\n', 'theme import')

old = '''        self.title("Codex Toolkit")\n        self.geometry("900x680")\n        self.resizable(True, True)\n        self.configure(bg="#1e1e2e")\n\n        self._proxy_proc: subprocess.Popen | None = None\n        self._proxy_log_thread: threading.Thread | None = None\n        self._sessions: list[dict] = []\n\n        style = ttk.Style(self)\n        style.theme_use("clam")\n        self._apply_style(style)\n\n        nb = ttk.Notebook(self)\n        nb.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)\n'''
new = '''        self.title("Codex Bridge Toolkit")\n        self.geometry("1000x720")\n        self.minsize(860, 620)\n        self.resizable(True, True)\n\n        self._proxy_proc: subprocess.Popen | None = None\n        self._proxy_log_thread: threading.Thread | None = None\n        self._sessions: list[dict] = []\n\n        self._theme_manager = ThemeManager(self, ASSETS_DIR)\n        self.palette = self._theme_manager.palette\n        self._theme_manager.build_header()\n\n        nb = ttk.Notebook(self)\n        nb.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))\n'''
s = replace_once(s, old, new, 'App init')
s = replace_once(s, '        self.protocol("WM_DELETE_WINDOW", self._on_close)\n', '        self.protocol("WM_DELETE_WINDOW", self._on_close)\n        self.after(0, self._theme_manager.refresh_widgets)\n', 'runtime theme refresh')

replacements = {
    'foreground="#a6e3a1"': 'foreground=self._app.palette["success"]',
    'foreground="#fab387"': 'foreground=self._app.palette["warn"]',
    'foreground="#f38ba8"': 'foreground=self._app.palette["danger"]',
    'foreground="#6c7086"': 'foreground=self._app.palette["muted"]',
    'foreground="#cdd6f4"': 'foreground=self._app.palette["fg"]',
    'bg="#181825", fg="#cdd6f4"': 'bg=self._app.palette["surface"], fg=self._app.palette["fg"]',
    'bg="#1e1e2e"': 'bg=self._app.palette["bg"]',
    'fill="#6c7086"': 'fill=self._app.palette["muted"]',
    'self._log.tag_config("info", foreground="#89b4fa")': 'self._log.tag_config("info", foreground=self._app.palette["accent"])',
    'self._log.tag_config("warn", foreground="#fab387")': 'self._log.tag_config("warn", foreground=self._app.palette["warn"])',
    'self._log.tag_config("error", foreground="#f38ba8")': 'self._log.tag_config("error", foreground=self._app.palette["danger"])',
    'self._log.tag_config("ok", foreground="#a6e3a1")': 'self._log.tag_config("ok", foreground=self._app.palette["success"])',
    'color = "#a6e3a1"': 'color = self._app.palette["success"]',
    'color = "#6c7086"': 'color = self._app.palette["muted"]',
}
for a, b in replacements.items():
    s = s.replace(a, b)

old = '''    def _start_proxy(self):\n        if not PROXY_SCRIPT.exists():\n            messagebox.showerror("错误", f"找不到 proxy.py：\\n{PROXY_SCRIPT}")\n            return\n\n        port = self._port_var.get().strip()\n'''
new = '''    def _start_proxy(self):\n        if FROZEN:\n            if not PROXY_EXE.exists():\n                messagebox.showerror(\n                    "缺少代理组件",\n                    f"找不到 CodexBridgeProxy.exe：\\n{PROXY_EXE}\\n\\n"\n                    "请使用 GitHub Release 的完整便携包，并保持两个 EXE 在同一目录。",\n                )\n                return\n        elif not PROXY_SCRIPT.exists():\n            messagebox.showerror("错误", f"找不到 proxy.py：\\n{PROXY_SCRIPT}")\n            return\n\n        port = self._port_var.get().strip()\n'''
s = replace_once(s, old, new, 'packaged proxy check')

old = '''        cmd = [\n            PYTHON_EXE, "-X", "utf8", str(PROXY_SCRIPT),\n            "--port", port,\n            "--upstream", upstream,\n            "--reasoning-mode", "safe",\n            "--log-level", "DEBUG",\n        ]\n'''
new = '''        if FROZEN:\n            cmd = [str(PROXY_EXE)]\n        else:\n            cmd = [PYTHON_EXE, "-X", "utf8", str(PROXY_SCRIPT)]\n        cmd += [\n            "--port", port,\n            "--upstream", upstream,\n            "--reasoning-mode", "safe",\n            "--log-level", "DEBUG",\n        ]\n'''
s = replace_once(s, old, new, 'packaged proxy command')

s = s.replace('            port_num = int(port)\n            from diagnostics import is_port_in_use', '            port_num = int(port)\n            if not 1 <= port_num <= 65535:\n                raise ValueError\n            from diagnostics import is_port_in_use', 1)
s = s.replace('messagebox.showerror("错误", "端口必须是数字")', 'messagebox.showerror("错误", "端口必须是 1–65535 之间的数字")', 1)
s = s.replace('text="✅ 已安装 (全局生效)"', 'text="✅ 已安装（agy 专属）"')
s = s.replace('codex_toolkit_gui.py — Codex Desktop 工具箱 GUI', 'codex_toolkit_gui.py — Codex Bridge Toolkit GUI', 1)
GUI.write_text(s, encoding='utf-8')

h = HISTORY.read_text(encoding='utf-8')
h = replace_once(h, 'return "Codex.exe" in res.stdout', 'return "codex.exe" in res.stdout.lower()', 'case-insensitive tasklist')
HISTORY.write_text(h, encoding='utf-8')

assets = ROOT / 'assets'
assets.mkdir(exist_ok=True)
S = 512
img = Image.new('RGBA', (S, S), (0, 0, 0, 0))
shadow = Image.new('RGBA', (S, S), (0, 0, 0, 0))
sd = ImageDraw.Draw(shadow)
sd.rounded_rectangle((40, 40, 472, 472), radius=112, fill=(0, 0, 0, 105))
shadow = shadow.filter(ImageFilter.GaussianBlur(18))
img.alpha_composite(shadow)
d = ImageDraw.Draw(img)
d.rounded_rectangle((52, 46, 460, 454), radius=105, fill=(20, 26, 47, 255), outline=(70, 91, 145, 255), width=6)
accent, accent2 = (112, 190, 255, 255), (120, 112, 255, 255)
for off, alpha in ((0, 210), (12, 120), (24, 55)):
    d.arc((118-off, 132-off, 394+off, 408+off), 200, 340, fill=(82, 164, 255, alpha), width=18 if off == 0 else 8)
d.rounded_rectangle((124, 258, 388, 288), radius=15, fill=accent)
d.rounded_rectangle((154, 212, 184, 334), radius=15, fill=accent2)
d.rounded_rectangle((328, 212, 358, 334), radius=15, fill=accent2)
for cx, cy, color in ((168, 198, accent), (344, 198, accent), (168, 350, accent2), (344, 350, accent2)):
    d.ellipse((cx-26, cy-26, cx+26, cy+26), fill=(20, 26, 47, 255), outline=color, width=10)
d.line((224, 226, 194, 258, 224, 290), fill=(235, 244, 255, 255), width=12, joint='curve')
d.line((288, 226, 318, 258, 288, 290), fill=(235, 244, 255, 255), width=12, joint='curve')
d.polygon([(392,104),(407,139),(444,146),(414,169),(419,207),(392,185),(363,207),(371,169),(340,146),(378,139)], fill=(160,235,255,255))
img.save(assets / 'codex_bridge.png')
img.save(assets / 'codex_bridge.ico', format='ICO', sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])

r = README.read_text(encoding='utf-8')
r = r.replace('# Codex Desktop Toolkit', '# Codex Bridge Toolkit for Windows', 1)
r = r.replace('> Unofficial Windows compatibility toolkit for Codex Desktop.  \n> Windows 下的 Codex Desktop 会话修复、Responses API 本地兼容代理与诊断工具。', '> **Codex Desktop session repair + ID-fix compatibility proxy + Antigravity/agy network helper for Windows.**  \n> Windows 下的 Codex Desktop 会话修复、Responses API / SSE / WebSocket 本地兼容代理、Antigravity `agy` 专属代理与运行诊断工具。', 1)
r = r.replace('Codex Desktop Toolkit is designed for a specific compatibility problem:', 'Codex Bridge Toolkit is designed for a specific compatibility problem:', 1)
if '## Download\n' not in r:
    r = r.replace('## Requirements\n', '''## Download\n\nFor most Windows users, use the packaged build from **GitHub Releases**:\n\n- [Latest Release](https://github.com/zankzeke/codex-desktop-toolkit/releases/latest)\n- Download `CodexBridgeToolkit-v0.2.0-windows-x64.zip`.\n- Extract it and keep `CodexBridgeToolkit.exe` and `CodexBridgeProxy.exe` in the same folder.\n- Start `CodexBridgeToolkit.exe`.\n\nThe GUI includes four persistent themes: **午夜蓝 / 石墨黑 / 深海蓝 / 明亮**, a dedicated app icon, and Windows title-bar tinting where supported.\n\n## Antigravity / `agy` helper\n\nThe **Antigravity 网络** tab installs an optional PowerShell wrapper for `agy`. Proxy variables are set only while `agy` is running and are restored afterward, so Codex, Git, Python, npm, and the rest of the shell are not globally proxied. The PowerShell profile is backed up before edits and the hook can be removed from the GUI.\n\n## Requirements\n''', 1)
README.write_text(r, encoding='utf-8')

c = CHANGELOG.read_text(encoding='utf-8')
if '## 0.2.0' not in c:
    c = c.replace('# Changelog\n', '''# Changelog\n\n## 0.2.0 - 2026-09-13\n\n- Renamed the desktop UI to **Codex Bridge Toolkit** while keeping the repository URL stable.\n- Added a dedicated application icon and four persistent GUI themes.\n- Added Windows title-bar tinting where supported.\n- Fixed case-sensitive `Codex.exe` process detection.\n- Added packaged Windows release builds (`CodexBridgeToolkit.exe` + `CodexBridgeProxy.exe`).\n- Expanded Antigravity / `agy` helper documentation.\n\n''', 1)
CHANGELOG.write_text(c, encoding='utf-8')
