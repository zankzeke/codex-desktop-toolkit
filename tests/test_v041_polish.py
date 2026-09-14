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
