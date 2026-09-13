# Changelog

## 0.2.2 - 2026-09-13

- Fixed packaged proxy processes surviving after the GUI window closes.
- Windows shutdown now terminates the complete PyInstaller proxy process tree.
- Detects a stale `CodexBridgeProxy.exe` listener on the selected local port and offers to clean it up before restarting.
- Added an in-GUI upstream proxy example dialog and `docs/PROXY_SETUP_ZH.md` for common Chinese proxy clients.
- Portable ZIP now includes the upstream proxy setup guide.

## 0.2.1 - 2026-09-13

- Fixed garbled/mojibake characters in the packaged Windows proxy console.
- Fixed Codex running-state detection when tasklist returns lowercase `codex.exe`.
- Runtime log separators and transport markers are now ASCII-safe across Windows code pages.
- No proxy request rewriting or upstream routing semantics changed.

## 0.2.0 - 2026-09-13

- Renamed the desktop UI to **Codex Bridge Toolkit** while keeping the repository URL stable.
- Added a dedicated application icon and four persistent GUI themes.
- Added Windows title-bar tinting where supported.
- Fixed case-sensitive `Codex.exe` process detection.
- Added packaged Windows release builds (`CodexBridgeToolkit.exe` + `CodexBridgeProxy.exe`).
- Added an automated Windows GitHub Release pipeline with a portable ZIP.
- Expanded Antigravity / `agy` helper documentation.

## 0.1.0 - 2026-09-11

Initial public release.

- Offline Codex JSONL session scanner/fixer with backups and atomic replacement.
- Structured synthetic ID repair with safe reasoning-item handling.
- Local HTTP/SSE/WebSocket compatibility proxy.
- WebSocket subprotocol and Codex upgrade-metadata forwarding.
- Tkinter GUI for proxy control, config management, session repair, and diagnostics.
- Optional PowerShell `agy` proxy wrapper with environment restoration.
- Windows CI and regression/integration tests.
- Final public source assembly verified against the reviewed release files.
