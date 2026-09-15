from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from backup_manager import compute_structured_diff, rollback_session


def test_compute_structured_diff(tmp_path: Path):
    cur_file = tmp_path / "session-1.jsonl"
    bak_file = tmp_path / "session-1.jsonl.bak.20260915"

    bak_data = [
        {"id": "resp_12345678-1234-1234-1234-123456789abc_msg", "text": "super confidential user message"},
        {"id": "item_aaaaaaaaaaaaaaaa", "type": "reasoning", "content": "secret thought"},
    ]
    cur_data = [
        {"id": "msg_12345678-1234-1234-1234-123456789abc", "text": "super confidential user message"},
    ]

    bak_file.write_text("\n".join(json.dumps(x) for x in bak_data), encoding="utf-8")
    cur_file.write_text("\n".join(json.dumps(x) for x in cur_data), encoding="utf-8")

    diff = compute_structured_diff(cur_file, bak_file)
    assert len(diff.id_changes) >= 1
    assert diff.id_changes[0]["old_id"].startswith("resp_")
    assert diff.id_changes[0]["new_id"].startswith("msg_")
    assert diff.reasoning_drops >= 1

    # Verify that confidential text is NEVER present in the diff object
    diff_dict = diff.to_dict()
    diff_repr = json.dumps(diff_dict)
    assert "super confidential" not in diff_repr
    assert "secret thought" not in diff_repr


def test_rollback_refuses_when_codex_running(tmp_path: Path):
    cur_file = tmp_path / "session.jsonl"
    bak_file = tmp_path / "session.jsonl.bak"
    cur_file.write_text('{"id": "msg_1"}\n', encoding="utf-8")
    bak_file.write_text('{"id": "resp_1"}\n', encoding="utf-8")

    with patch("backup_manager.is_codex_running", return_value=True):
        ok, msg = rollback_session(cur_file, bak_file)
        assert ok is False
        assert "正在运行" in msg


def test_rollback_success(tmp_path: Path):
    cur_file = tmp_path / "session.jsonl"
    bak_file = tmp_path / "session.jsonl.bak"
    cur_file.write_text('{"id": "msg_fixed"}\n', encoding="utf-8")
    bak_file.write_text('{"id": "resp_broken"}\n', encoding="utf-8")

    with patch("backup_manager.is_codex_running", return_value=False):
        ok, msg = rollback_session(cur_file, bak_file)
        assert ok is True
        assert "成功恢复" in msg
        assert '{"id": "resp_broken"}' in cur_file.read_text(encoding="utf-8")
