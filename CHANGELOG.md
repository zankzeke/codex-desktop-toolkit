# Changelog

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
