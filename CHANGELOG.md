# Changelog

## 0.5.0 - 2026-09-15

- **Transport Mode Selection**: Added Auto / WebSocket / Force HTTP transport modes configurable via GUI, `config_manager.py`, and `--transport-mode` proxy CLI. Force HTTP mode gracefully rejects WebSocket upgrades to trigger immediate Codex client HTTP fallback.
- **WebSocket Circuit Breaker**: Implemented thread-safe `TransportCircuitBreaker` (CLOSED / OPEN / HALF_OPEN). Automatically trips upon consecutive WebSocket connection failures (default 3) to break infinite reconnect loops, with configurable cooldown (default 15m) and automatic half-open trial probing.
- **One-Click Network Diagnostics**: Added comprehensive connectivity evaluation testing local proxy health, upstream HTTPS reachability, and WebSocket handshake performance concurrently without sending credentials or chat transcripts. Provides structured conclusions and actionable repair suggestions.
- **Local Proxy Discovery**: Automatically scans and detects active local proxy applications (Clash Verge Rev, Clash, Mihomo, v2rayN, NekoRay, sing-box) by listening ports and process signatures, with live HTTP and WebSocket capability testing and one-click filling into outbound proxy settings.
- **Actionable Error Classifier**: Enhanced `error_classifier.py` to produce structured `ErrorDiagnosis` objects with categorized recommendations and `action_id` dispatch (navigating directly to session repair, network diagnostics, force HTTP, or proxy discovery).
- **Session Repair Center & Atomic Rollback**: Added `backup_manager.py` featuring backup history enumeration, privacy-preserving structured diffs (comparing line counts, item modifications, and fix counts without exposing conversation content), and atomic rollback with process conflict detection.
- **Sanitized Support Bundle**: Added `support_bundle.py` generating `CodexBridgeSupport-YYYYMMDD-HHMMSS.zip` diagnostics bundles with strict multi-layer redaction of usernames, home paths, tokens, authorization headers, cookies, and request bodies.
- **Startup & Automation Preferences**: Added `startup_manager.py` for per-user Windows HKCU Run key auto-start (no administrator elevation required), rate-limited desktop notifications, and persistent automation preferences.
- **GUI Reorganization**: Structured the toolkit into 7 comprehensive tabs: 首页概览 (Overview), 代理控制 (Proxy Control), 会话修复 (Session Repair), 网络诊断 (Network Diagnostics), Antigravity 网络, 诊断与支持 (Diagnostics & Support), and 设置 (Settings).

## 0.4.4 - 2026-09-14

- Fixed CodeQL partial-SSRF findings by forwarding only the Codex endpoints the compatibility proxy is expected to handle.
- Request query parameters are forwarded separately and are no longer concatenated into the configured upstream URL.
- Unknown local proxy paths now return HTTP 404 without contacting the configured upstream.
- Removed request-controlled path/URL values from runtime proxy log records to prevent log-forging/injection findings.
- Added regression coverage for route allowlisting and unsupported endpoint rejection.
- Re-ran the `security-extended` CodeQL query suite and confirmed zero SARIF findings after the fixes.

## 0.4.3 - 2026-09-14

- Added CodeQL Python security scanning on pushes, pull requests, and a weekly schedule using the current CodeQL Action v4.
- Added Dependabot version updates for Python dependencies and GitHub Actions, with grouped minor/patch updates.
- Added GitHub/Sigstore SLSA build provenance attestations for Windows release EXEs, the portable ZIP, and `SHA256SUMS.txt`.
- Release workflow now grants the OIDC/attestation permissions required to produce verifiable provenance and documents `gh attestation verify` usage.
- Existing exact release dependency locking, packaged EXE smoke tests, SHA256 checksums, and optional Authenticode signing remain in place.

## 0.4.2 - 2026-09-14

- Added `version.py` as the single authoritative version source for GUI and Release packaging.
- Added an exact `requirements-release.txt` lock for reproducible Windows Release builds.
- Added packaged GUI smoke testing; Releases now validate both GUI and proxy executables before publication.
- Replaced the v0.4/v0.4.1 source-string regression assertions with behavior-focused tests.
- WebSocket reconnect telemetry now distinguishes a real idle-to-active reconnect from ordinary parallel connections.
- Responses rewriting now uses exact `/v1/responses` path matching rather than a substring check.
- ID repair now uses a two-phase context scan so forward references are repaired or removed; JSONL repair pre-scans the whole file for cross-line references.
- Removed the completed one-off migration workflow and added CODEOWNERS / branch-protection guidance.
- Release pushes are constrained to authoritative version-file changes.

## 0.4.1 - 2026-09-14

- Fixed the upstream-manager dialog crash caused by the log clear button being attached from the wrong scope.
- One-click launch no longer leaves a stale pending launch flag after proxy startup validation fails.
- Runtime ID rewrite totals now include non-streaming and WebSocket response-side rewrites.
- Copied diagnostic reports redact the user home prefix and do not expose NO_PROXY contents.

## 0.4.0 - 2026-09-14

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

## 0.3.1 - 2026-09-14

- Fixed starting the local proxy after Codex Desktop is already running.
- The GUI now checks whether `config.toml` points at the selected local proxy and offers to apply the provider plus restart Codex when required.
- If the provider already matches, the GUI offers a restart so the existing Codex process reloads its connection state.
- Enabling the proxy config while Codex is running now offers an immediate restart.

## 0.3.0 - 2026-09-13

### ✨ 新特性与优化 (Features & Improvements)
- **上游地址与代理管理 (Upstream & Proxy Management)**: 
  - 将原有的单行文本输入框升级为支持记忆的下拉菜单。
  - 支持新增、编辑、删除自定义的上游 API 地址和科学上网代理。
  - 鼠标选中标签后，右侧自动显示该标签对应的真实 URL，告别盲填。
  - 支持配置持久化，软件重启后自动恢复上一次选择的代理和地址。
- **UI 细节优化 (UI Polish)**:
  - 所有的弹出管理窗口和新增对话框现在会完美居中显示，不再默认弹出到屏幕左上角。
  - 增大了“新增”窗口的默认高度，并补齐了操作对齐的“取消”按钮。
  - 将上游代理“示例”按钮替换为更清晰的“说明”弹窗，提供更直观的端口配置教程。

### 🐛 缺陷修复 (Bug Fixes)
- **Codex 重启修复 (Codex Restart Fix)**:
  - 修复了点击“🔃 重启 Codex”时提示“未找到 codex.exe”的 Bug。
  - 现在会在结束进程前优先捕获 Codex 的实际运行路径。
  - 增加了对 `AppData\Local\OpenAI\Codex\bin\` 目录下带有哈希值的动态版本的全局遍历回退支持。

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
