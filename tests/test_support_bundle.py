from __future__ import annotations

import zipfile
from pathlib import Path

from support_bundle import (
    create_support_bundle,
    redact_text_content,
    redact_url_sensitive,
    redact_user_paths,
)


def test_redact_user_paths():
    home = str(Path.home())
    test_path = f"{home}\\AppData\\Local\\OpenAI\\Codex\\Codex.exe"
    redacted = redact_user_paths(test_path)
    assert home not in redacted
    assert redacted.startswith("~\\") or redacted.startswith("~/")


def test_redact_url_sensitive():
    url = "http://alice:supersecret@127.0.0.1:7890/v1/responses?token=secrettoken&user=alice#fragment"
    clean = redact_url_sensitive(url)
    assert clean == "http://127.0.0.1:7890/v1/responses"
    assert "supersecret" not in clean
    assert "secrettoken" not in clean
    assert "alice" not in clean


def test_redact_text_content_comprehensive():
    text = (
        "Error occurred: Authorization: Bearer sk-abcdef1234567890abcdef123456\n"
        "Cookie: session_token=secret_session_abc123;\n"
        "Path is C:\\Users\\Alice\\.codex\\config.toml\n"
        "Upstream proxy: http://admin:pass123@proxy.corp.internal:8080/pac?auth=xyz\n"
    )
    sanitized = redact_text_content(text)
    assert "sk-abcdef1234567890" not in sanitized
    assert "secret_session_abc123" not in sanitized
    assert "pass123" not in sanitized
    assert "auth=xyz" not in sanitized


def test_create_support_bundle(tmp_path: Path):
    bundle_zip = create_support_bundle(output_dir=tmp_path, port=8787)
    assert bundle_zip.exists()
    assert bundle_zip.suffix == ".zip"

    with zipfile.ZipFile(bundle_zip, "r") as zf:
        namelist = zf.namelist()
        assert "diagnostics.md" in namelist
        assert "environment.json" in namelist
        assert "proxy-stats.json" in namelist
        assert "codex-version.txt" in namelist
        assert "config-redacted.toml" in namelist
        assert "recent-errors.txt" in namelist

        # Verify no token or home directory leaked across all files in the archive
        home_str = str(Path.home()).lower()
        for name in namelist:
            content = zf.read(name).decode("utf-8", errors="replace")
            assert "sk-" not in content
            assert "bearer ey" not in content.lower()
            # If home string is longer than 5 chars, ensure it doesn't appear
            if len(home_str) > 5:
                assert home_str not in content.lower()
