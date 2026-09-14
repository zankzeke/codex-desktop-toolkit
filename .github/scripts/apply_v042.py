from __future__ import annotations

from pathlib import Path
from textwrap import dedent


def read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8", newline="\n")


def replace_once(path: str, old: str, new: str) -> None:
    text = read(path)
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected exactly one match, got {count}: {old[:100]!r}")
    write(path, text.replace(old, new, 1))


def replace_section(path: str, start_marker: str, end_marker: str, replacement: str) -> None:
    text = read(path)
    start = text.index(start_marker)
    end = text.index(end_marker, start)
    write(path, text[:start] + replacement + text[end:])


# ---------------------------------------------------------------------------
# 1. Single authoritative version source
# ---------------------------------------------------------------------------
write(
    "version.py",
    '"""Single source of truth for Codex Bridge Toolkit versioning."""\n\nAPP_VERSION = "0.4.2"\n',
)
replace_once(
    "ui_theme.py",
    'APP_VERSION = "0.4.1"\nDEFAULT_THEME = "午夜蓝"',
    'from version import APP_VERSION\n\nDEFAULT_THEME = "午夜蓝"',
)
replace_once(
    "codex_toolkit_gui.py",
    "from ui_theme import ThemeManager, APP_VERSION\n",
    "from ui_theme import ThemeManager\nfrom version import APP_VERSION\n",
)


# ---------------------------------------------------------------------------
# 2. WebSocket reconnect semantics: parallel connections are not reconnects
# ---------------------------------------------------------------------------
replace_once(
    "runtime_stats.py",
    dedent(
        '''\
        def ws_connected(self) -> None:
            if self.ws_handshakes > 0:
                self.ws_reconnects += 1
            self.ws_handshakes += 1
            self.ws_active += 1
            self.last_transport = "websocket"
            self.ws_last_connected_at = _now()
        '''
    ),
    dedent(
        '''\
        def ws_connected(self) -> None:
            # Count a reconnect only when all previous WS connections had gone
            # idle and a new one is established. A second concurrent connection
            # is normal parallel traffic, not a reconnect.
            was_idle = self.ws_active == 0
            if self.ws_handshakes > 0 and was_idle:
                self.ws_reconnects += 1
            self.ws_handshakes += 1
            self.ws_active += 1
            self.last_transport = "websocket"
            self.ws_last_connected_at = _now()
        '''
    ),
)


# ---------------------------------------------------------------------------
# 3. Exact Responses endpoint matching
# ---------------------------------------------------------------------------
proxy_text = read("proxy.py")
request_marker = (
    "# ---------------------------------------------------------------------------\n"
    "# Request body sanitiser\n"
    "# ---------------------------------------------------------------------------\n"
)
if "def _is_responses_path(" not in proxy_text:
    helper = dedent(
        '''\
        def _is_responses_path(path: str) -> bool:
            """Return True only for the local Responses endpoint.

            The caller passes ``request.path`` (never the query string). A
            trailing slash is accepted, while substring/suffix lookalikes are
            deliberately rejected.
            """
            return path.rstrip("/") == "/v1/responses"


        '''
    )
    if request_marker not in proxy_text:
        raise SystemExit("proxy.py: request sanitiser marker not found")
    proxy_text = proxy_text.replace(request_marker, helper + request_marker, 1)
    write("proxy.py", proxy_text)

replace_once(
    "proxy.py",
    '        is_responses_endpoint = "/v1/responses" in path\n',
    "        is_responses_endpoint = _is_responses_path(request.path)\n",
)


# ---------------------------------------------------------------------------
# 4. Two-phase ID rewrite for forward references
# ---------------------------------------------------------------------------
new_sanitiser = dedent(
    '''\
    def prepare_rewrite_context(
        input_arr: List[Any],
        id_map: Dict[str, str],
        dropped_ids: Set[str] | None = None,
        safe_reasoning: bool = True,
    ) -> Tuple[Dict[str, str], Set[str]]:
        """Pre-scan a complete collection before rewriting any element.

        The first phase discovers typed synthetic IDs and every invalid
        reasoning ID. The second phase can therefore repair/remove references
        even when the reference appears before the item that defines it.
        """
        if dropped_ids is None:
            dropped_ids = set()

        for item in input_arr:
            if not isinstance(item, dict):
                continue
            inner_items: List[Dict] = []
            _extract_items(item, inner_items)
            for inner in inner_items:
                raw_id = inner.get("id", "")
                item_type = inner.get("type", "")
                if not isinstance(raw_id, str) or not raw_id:
                    continue
                if item_type == "reasoning" and safe_reasoning and not raw_id.startswith("rs_"):
                    dropped_ids.add(raw_id)
                    continue
                rewrite_single_id(raw_id, item_type, id_map)

        return id_map, dropped_ids


    def sanitise_input_array(
        input_arr: List[Any],
        id_map: Dict[str, str],
        dropped_ids: Set[str] | None = None,
        safe_reasoning: bool = True,
        *,
        context_prepared: bool = False,
    ) -> Tuple[List[Any], Dict[str, str], int, int]:
        """Sanitise an input array using deterministic two-phase rewriting.

        Returns ``(new_array, updated_id_map, fixes_count, dropped_count)``.
        ``context_prepared`` is used by the JSONL fixer after a file-wide
        pre-scan, which extends forward-reference handling across lines.
        """
        if dropped_ids is None:
            dropped_ids = set()

        if not context_prepared:
            prepare_rewrite_context(
                input_arr,
                id_map=id_map,
                dropped_ids=dropped_ids,
                safe_reasoning=safe_reasoning,
            )

        new_arr: List[Any] = []
        fixes = 0
        drops = 0

        for item in input_arr:
            if not isinstance(item, dict):
                new_arr.append(item)
                continue

            item_copy = copy.deepcopy(item)
            inner_items: List[Dict] = []
            _extract_items(item_copy, inner_items)
            invalid_reasoning = [
                inner
                for inner in inner_items
                if inner.get("type") == "reasoning"
                and safe_reasoning
                and isinstance(inner.get("id"), str)
                and inner.get("id")
                and not inner.get("id").startswith("rs_")
            ]
            if invalid_reasoning:
                drops += len(invalid_reasoning)
                continue

            fixes += _walk_and_rewrite(item_copy, id_map, dropped_ids)
            new_arr.append(item_copy)

        return new_arr, id_map, fixes, drops


    '''
)
replace_section(
    "id_rewriter.py",
    "def sanitise_input_array(",
    "def rewrite_response_object(",
    new_sanitiser,
)

replace_once(
    "history_fixer.py",
    "from id_rewriter import sanitise_input_array\n",
    "from id_rewriter import prepare_rewrite_context, sanitise_input_array\n",
)

history_text = read("history_fixer.py")
fix_start = history_text.index("def fix_rollout_file(")
new_fix_rollout = dedent(
    '''\
    def fix_rollout_file(path: Path, dry_run: bool = False) -> Tuple[int, int, Dict[str, str]]:
        """Fix a JSONL rollout using a file-wide two-pass rewrite context."""
        total_fixes = 0
        total_drops = 0
        id_map: Dict[str, str] = {}
        dropped_ids: set[str] = set()

        with open(path, "r", encoding="utf-8", errors="replace") as f:
            raw_lines = f.readlines()

        records: list[tuple[str, object]] = []
        parsed_objects: list[dict] = []
        for line in raw_lines:
            stripped = line.strip()
            if not stripped:
                records.append(("raw", line))
                continue
            try:
                obj = json.loads(stripped)
            except json.JSONDecodeError:
                records.append(("raw", line))
                continue
            records.append(("json", obj))
            if isinstance(obj, dict):
                parsed_objects.append(obj)

        # Pre-scan the entire JSONL file before rewriting any line. This makes
        # forward references deterministic even when the target is defined on
        # a later line.
        prepare_rewrite_context(
            parsed_objects,
            id_map=id_map,
            dropped_ids=dropped_ids,
            safe_reasoning=True,
        )

        out_lines: list[str] = []
        for kind, payload in records:
            if kind == "raw":
                out_lines.append(str(payload))
                continue

            obj = payload
            if not isinstance(obj, dict):
                out_lines.append(json.dumps(obj, ensure_ascii=False) + "\n")
                continue

            arr, id_map, fixes, drops = sanitise_input_array(
                [obj],
                id_map=id_map,
                dropped_ids=dropped_ids,
                safe_reasoning=True,
                context_prepared=True,
            )
            total_fixes += fixes
            total_drops += drops
            if arr:
                out_lines.append(json.dumps(arr[0], ensure_ascii=False) + "\n")

        if not dry_run and (total_fixes > 0 or total_drops > 0):
            backup_file(path)
            atomic_write_jsonl(path, out_lines)

        return total_fixes, total_drops, id_map
    '''
)
write("history_fixer.py", history_text[:fix_start] + new_fix_rollout)


# ---------------------------------------------------------------------------
# 5. Packaged GUI smoke-test mode
# ---------------------------------------------------------------------------
replace_once(
    "codex_toolkit_gui.py",
    dedent(
        '''\
        # ─── 入口 ──────────────────────────────────────────────────────────────────────

        if __name__ == "__main__":
            app = App()
            app.mainloop()
        '''
    ),
    dedent(
        '''\
        # ─── 入口 ──────────────────────────────────────────────────────────────────────

        def run_gui_smoke_test() -> int:
            """Construct the real packaged GUI once, then exit without interaction."""
            app = App()
            try:
                app.update_idletasks()
                app.update()
                if not hasattr(app, "_proxy_tab") or not hasattr(app, "_diag_tab"):
                    raise RuntimeError("GUI tabs failed to initialise")
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
        '''
    ),
)


# ---------------------------------------------------------------------------
# 6. Exact release dependency lock
# ---------------------------------------------------------------------------
write(
    "requirements-release.txt",
    dedent(
        '''\
        aiohappyeyeballs==2.7.1
        aiohttp==3.14.3
        aiosignal==1.4.0
        altgraph==0.17.5
        attrs==26.1.0
        colorama==0.4.6
        frozenlist==1.8.0
        idna==3.19
        iniconfig==2.3.0
        multidict==6.8.0
        packaging==26.3
        pefile==2024.8.26
        Pillow==12.3.0
        pluggy==1.6.0
        propcache==0.5.2
        Pygments==2.21.0
        pyinstaller==6.22.3
        pyinstaller-hooks-contrib==2026.7
        pystray==0.19.5
        pytest==9.1.1
        pywin32-ctypes==0.2.3
        setuptools==65.5.0
        six==1.17.0
        tomlkit==0.15.1
        typing_extensions==4.16.0
        yarl==1.24.5
        '''
    ),
)


# ---------------------------------------------------------------------------
# 7. Replace brittle source-string tests with behavior tests
# ---------------------------------------------------------------------------
write(
    "tests/test_gui_metadata.py",
    dedent(
        '''\
        import ui_theme
        import version


        def test_themes_and_version_are_exposed_without_starting_tk():
            assert ui_theme.APP_VERSION == version.APP_VERSION == "0.4.2"
            assert {"午夜蓝", "石墨黑", "深海蓝", "明亮"} <= set(ui_theme.THEMES)
            for theme in ui_theme.THEMES.values():
                for key in ("bg", "panel", "surface", "fg", "accent", "success", "warn", "danger"):
                    assert key in theme
        '''
    ),
)

write(
    "tests/test_v040_features.py",
    dedent(
        '''\
        from error_classifier import classify_error, classify_ws_close
        from id_rewriter import sanitise_input_array
        from proxy import _is_responses_path
        from runtime_stats import RuntimeStats
        from update_checker import is_newer


        def test_error_classification():
            assert classify_error(503, "Selected model is at capacity")["category"] == "capacity"
            assert classify_error(429, "too many requests")["category"] == "rate_limit"
            assert classify_error(400, "invalid_id_prefix")["category"] == "invalid_id"
            assert classify_ws_close(1008)["category"] == "ws_policy"
            assert classify_ws_close(1006)["category"] == "ws_abnormal"


        def test_runtime_stats_marks_real_traffic_and_counts_only_real_reconnects():
            stats = RuntimeStats()
            assert stats.snapshot()["traffic_verified"] is False
            stats.record_request("POST", "/v1/responses?secret=nope")
            stats.record_response(200)
            stats.record_rewrite(2, 1)

            stats.ws_connected()       # first connection
            stats.ws_connected()       # parallel connection, not reconnect
            assert stats.snapshot()["websocket"]["reconnects"] == 0
            stats.ws_closed(1000)
            stats.ws_closed(1000)      # all WS connections idle
            stats.ws_connected()       # real reconnect: idle -> active

            snap = stats.snapshot()
            assert snap["traffic_verified"] is True
            assert snap["last_request_path"] == "/v1/responses"
            assert snap["id_fixes_total"] == 2
            assert snap["reasoning_drops_total"] == 1
            assert snap["websocket"]["handshakes"] == 3
            assert snap["websocket"]["reconnects"] == 1


        def test_version_compare():
            assert is_newer("0.4.2", "0.4.1")
            assert not is_newer("0.4.2", "0.4.2")
            assert not is_newer("0.3.9", "0.4.2")


        def test_responses_endpoint_matching_is_exact():
            assert _is_responses_path("/v1/responses")
            assert _is_responses_path("/v1/responses/")
            assert not _is_responses_path("/foo/v1/responses")
            assert not _is_responses_path("/v1/responses-extra")
            assert not _is_responses_path("/v1/models")


        def test_two_phase_rewrite_repairs_forward_reference():
            raw = "item_aaaaaaaaaaaaaaaa"
            items = [
                {"type": "message", "id": "msg_parent", "previous_item_id": raw},
                {"type": "function_call", "id": raw},
            ]
            out, _, fixes, drops = sanitise_input_array(items, id_map={})
            assert drops == 0
            assert out[0]["previous_item_id"] == "fc_aaaaaaaaaaaaaaaa"
            assert out[1]["id"] == "fc_aaaaaaaaaaaaaaaa"
            assert fixes >= 2


        def test_two_phase_rewrite_removes_forward_reference_to_dropped_reasoning():
            raw = "item_bbbbbbbbbbbbbbbb"
            items = [
                {"type": "message", "id": "msg_parent", "previous_item_id": raw},
                {"type": "reasoning", "id": raw},
            ]
            out, _, fixes, drops = sanitise_input_array(items, id_map={})
            assert drops == 1
            assert len(out) == 1
            assert "previous_item_id" not in out[0]
            assert fixes >= 1
        '''
    ),
)

write(
    "tests/test_v041_polish.py",
    dedent(
        '''\
        from pathlib import Path

        from diagnostics import build_diagnostic_report
        from history_fixer import fix_rollout_file


        def test_diagnostic_report_redacts_home_and_no_proxy():
            fake_home = Path.home()
            data = {
                "codex": {
                    "running": True,
                    "path": str(fake_home / "AppData/Local/OpenAI/Codex/Codex.exe"),
                    "config_exists": True,
                    "config": {"provider": "openai-idfix", "active_for_port": True},
                },
                "proxy": {"running": True, "port": 8787, "stats": {}, "details": {}},
                "network": {"no_proxy": "secret.corp.internal,localhost"},
            }
            report = build_diagnostic_report(data, "0.4.2")
            assert str(fake_home) not in report
            assert report.count("~") >= 1
            assert "secret.corp.internal" not in report
            assert "set (contents redacted)" in report


        def test_history_fixer_repairs_cross_line_forward_reference(tmp_path):
            path = tmp_path / "rollout.jsonl"
            raw = "item_cccccccccccccccc"
            path.write_text(
                '{"type":"message","id":"msg_parent","previous_item_id":"' + raw + '"}\n'
                '{"type":"function_call","id":"' + raw + '"}\n',
                encoding="utf-8",
            )
            fixes, drops, _ = fix_rollout_file(path, dry_run=False)
            text = path.read_text(encoding="utf-8")
            assert drops == 0
            assert fixes >= 2
            assert "fc_cccccccccccccccc" in text
            assert raw not in text
        '''
    ),
)


# ---------------------------------------------------------------------------
# 8. Release workflow: version source, locked deps, two packaged smoke tests
# ---------------------------------------------------------------------------
release_yml = dedent(
    r'''
    name: Build Windows Release

    on:
      workflow_dispatch:
        inputs:
          tag:
            description: Optional tag; must match version.py (for example v0.4.2)
            required: false
            default: ''
      push:
        branches: [main]
        paths:
          - 'version.py'

    permissions:
      contents: write

    concurrency:
      group: windows-release
      cancel-in-progress: false

    jobs:
      build-release:
        if: github.event_name == 'workflow_dispatch' || contains(github.event.head_commit.message, '[release]')
        runs-on: windows-latest
        steps:
          - uses: actions/checkout@v4

          - uses: actions/setup-python@v5
            with:
              python-version: '3.11'

          - name: Resolve release version
            id: version
            shell: pwsh
            run: |
              $version = (python -c "from version import APP_VERSION; print(APP_VERSION)").Trim()
              if (-not $version) { throw "version.py returned an empty version" }
              $tag = "v$version"
              $requested = '${{ inputs.tag }}'.Trim()
              if ('${{ github.event_name }}' -eq 'workflow_dispatch' -and $requested -and $requested -ne $tag) {
                throw "Requested tag $requested does not match version.py ($tag)"
              }
              "version=$version" >> $env:GITHUB_OUTPUT
              "tag=$tag" >> $env:GITHUB_OUTPUT

          - name: Install locked release dependencies
            run: python -m pip install -r requirements-release.txt

          - name: Run tests before packaging
            run: |
              python -m compileall -q .
              python -m pytest -q

          - name: Build proxy executable
            shell: pwsh
            run: |
              pyinstaller --noconfirm --clean --onefile `
                --name CodexBridgeProxy `
                --icon assets/codex_bridge.ico `
                proxy.py

          - name: Build GUI executable
            shell: pwsh
            run: |
              pyinstaller --noconfirm --clean --onefile --windowed `
                --name CodexBridgeToolkit `
                --icon assets/codex_bridge.ico `
                --add-data "assets;assets" `
                --collect-all pystray `
                --hidden-import pystray._win32 `
                codex_toolkit_gui.py

          - name: Smoke-test packaged proxy executable
            run: python tests/packaged_smoke.py --proxy-exe dist/CodexBridgeProxy.exe

          - name: Smoke-test packaged GUI executable
            shell: pwsh
            run: |
              $p = Start-Process -FilePath "$PWD/dist/CodexBridgeToolkit.exe" -ArgumentList "--smoke-test" -PassThru
              if (-not $p.WaitForExit(20000)) {
                try { $p.Kill() } catch {}
                throw "Packaged GUI smoke test timed out"
              }
              if ($p.ExitCode -ne 0) { throw "Packaged GUI smoke test failed with exit code $($p.ExitCode)" }

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
              $signtool = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin" -Filter signtool.exe -Recurse |
                Sort-Object FullName -Descending |
                Select-Object -First 1 -ExpandProperty FullName
              if (-not $signtool) { throw "signtool.exe not found" }
              foreach ($file in @("dist/CodexBridgeToolkit.exe", "dist/CodexBridgeProxy.exe")) {
                & $signtool sign /fd SHA256 /f signing.pfx /p $env:SIGN_PASSWORD /tr http://timestamp.digicert.com /td SHA256 $file
                if ($LASTEXITCODE -ne 0) { throw "signtool failed for $file" }
              }
              Remove-Item signing.pfx -Force

          - name: Create portable package
            id: package
            shell: pwsh
            run: |
              $version = '${{ steps.version.outputs.version }}'
              $dir = "release/CodexBridgeToolkit-$version-windows-x64"
              New-Item -ItemType Directory -Force -Path $dir | Out-Null
              Copy-Item dist/CodexBridgeToolkit.exe "$dir/CodexBridgeToolkit.exe"
              Copy-Item dist/CodexBridgeProxy.exe "$dir/CodexBridgeProxy.exe"
              Copy-Item README.md "$dir/README.md"
              Copy-Item docs/PROXY_SETUP_ZH.md "$dir/PROXY_SETUP_ZH.md"
              Copy-Item LICENSE "$dir/LICENSE"
              @"
              Codex Bridge Toolkit v$version

              Start CodexBridgeToolkit.exe.
              Keep CodexBridgeToolkit.exe and CodexBridgeProxy.exe in the same directory.

              v0.4.2 hardening:
              - Single authoritative version source
              - Locked Windows release dependency set
              - Packaged Proxy + GUI smoke tests before publishing
              - Exact Responses endpoint detection
              - Two-phase ID repair including forward references
              - Correct WebSocket reconnect telemetry for parallel connections
              - SHA256 checksums and optional Authenticode signing
              "@ | Set-Content -Encoding utf8 "$dir/PORTABLE_README.txt"
              $zip = "release/CodexBridgeToolkit-$version-windows-x64.zip"
              Compress-Archive -Path "$dir/*" -DestinationPath $zip -Force
              "zip=$zip" >> $env:GITHUB_OUTPUT

          - name: Generate SHA256 checksums
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

          - name: Upload workflow artifact
            uses: actions/upload-artifact@v4
            with:
              name: CodexBridgeToolkit-windows-x64
              path: |
                dist/CodexBridgeToolkit.exe
                dist/CodexBridgeProxy.exe
                ${{ steps.package.outputs.zip }}
                release/SHA256SUMS.txt

          - name: Publish GitHub Release
            shell: pwsh
            env:
              GH_TOKEN: ${{ github.token }}
            run: |
              $tag = '${{ steps.version.outputs.tag }}'
              $zip = '${{ steps.package.outputs.zip }}'
              $version = '${{ steps.version.outputs.version }}'
              @"
              ## Codex Bridge Toolkit $tag

              Windows packaged release. No separate Python installation is required.

              **Recommended:** download `CodexBridgeToolkit-$version-windows-x64.zip`, extract it, and keep both executables together.

              ### v0.4.2 Repository & Release Hardening
              - Single version source shared by GUI and release pipeline
              - Exact locked release/build dependencies
              - Packaged GUI smoke test in addition to HTTP/SSE/WebSocket proxy smoke tests
              - Correct WebSocket reconnect telemetry with parallel connections
              - Exact `/v1/responses` matching rather than substring matching
              - Two-phase ID repair for forward references, including cross-line JSONL history repair
              - SHA256 integrity file remains included; Authenticode is used when signing secrets are configured

              Privacy note: runtime stats are local-only and do not store authentication headers, cookies, request/response bodies, or URL query strings.

              This is an unofficial community project and is not affiliated with OpenAI.
              "@ | Set-Content -Encoding utf8 release-notes.md

              if (gh release view $tag 2>$null) {
                gh release edit $tag --title "Codex Bridge Toolkit $tag" --notes-file release-notes.md
                gh release upload $tag $zip dist/CodexBridgeToolkit.exe dist/CodexBridgeProxy.exe release/SHA256SUMS.txt --clobber
              } else {
                gh release create $tag $zip dist/CodexBridgeToolkit.exe dist/CodexBridgeProxy.exe release/SHA256SUMS.txt `
                  --target $env:GITHUB_SHA `
                  --title "Codex Bridge Toolkit $tag" `
                  --notes-file release-notes.md
              }
    '''
).lstrip()
write(".github/workflows/release.yml", release_yml)


# ---------------------------------------------------------------------------
# 9. Repository-side preparation for main-branch protection
# ---------------------------------------------------------------------------
write(".github/CODEOWNERS", "* @zankzeke\n")
write(
    "docs/BRANCH_PROTECTION.md",
    dedent(
        '''\
        # Recommended `main` branch ruleset

        The repository is prepared for protected-branch development with stable
        Windows CI job names and `CODEOWNERS`.

        Configure a GitHub ruleset for `main` with these settings:

        - Block branch deletion and force pushes.
        - Require pull requests before merging when accepting external contributions.
        - Require status checks `test-windows (3.11)` and `test-windows (3.13)`.
        - Require branches to be up to date before merging.
        - Require conversation resolution for pull requests.
        - Optionally require Code Owner review when more maintainers are added.

        The ChatGPT GitHub App used to maintain this repository has repository
        contents/workflow permissions but not GitHub `administration:write`, which
        GitHub requires to create or modify branch rulesets. Therefore the ruleset
        itself must be enabled once by the repository owner in **Settings → Rules →
        Rulesets**.
        '''
    ),
)


# ---------------------------------------------------------------------------
# 10. README / changelog consistency
# ---------------------------------------------------------------------------
replace_once(
    "README.md",
    "- Download `CodexBridgeToolkit-0.4.0-windows-x64.zip`.\n",
    "- Download the `CodexBridgeToolkit-<version>-windows-x64.zip` asset shown on **Latest Release**.\n",
)
replace_once(
    "README.md",
    "ui_theme.py            theme manager, app icon integration, Windows chrome styling\n",
    "ui_theme.py            theme manager, app icon integration, Windows chrome styling\n"
    "version.py             single source of truth for the application/release version\n",
)
replace_once(
    "README.md",
    "diagnostics.py         local health/diagnostic helpers\n",
    "diagnostics.py         local health/diagnostic helpers\n"
    "runtime_stats.py       privacy-safe in-memory HTTP/SSE/WebSocket telemetry\n"
    "error_classifier.py    human-readable network/upstream error classification\n"
    "tray_manager.py        Windows system-tray background lifecycle\n"
    "update_checker.py      notification-only GitHub Release update checker\n"
    "process_utils.py       Windows process-tree and listening-port helpers\n",
)
replace_once(
    "README.md",
    "CI runs the test suite on Windows with Python 3.11 and 3.13.\n",
    "CI runs the test suite on Windows with Python 3.11 and 3.13.\n\n"
    "Windows Release builds use `requirements-release.txt`, an exact dependency lock, and smoke-test both packaged executables before publishing. Recommended main-branch protection settings are documented in [docs/BRANCH_PROTECTION.md](docs/BRANCH_PROTECTION.md).\n",
)

changelog = read("CHANGELOG.md")
entry = dedent(
    '''\
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

    '''
)
if entry not in changelog:
    changelog = changelog.replace("# Changelog\n\n", "# Changelog\n\n" + entry, 1)
    write("CHANGELOG.md", changelog)
