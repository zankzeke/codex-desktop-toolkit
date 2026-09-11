import json
from pathlib import Path

from history_fixer import fix_rollout_file


def test_cross_line_reasoning_drop_and_reference_cleanup(tmp_path: Path):
    path = tmp_path / "rollout.jsonl"
    bad = "item_aaaaaaaaaaaaaaaa"
    lines = [
        {"type": "response_item", "payload": {"type": "reasoning", "id": bad}},
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "id": "item_bbbbbbbbbbbbbbbb",
                "previous_item_id": bad,
                "item_id": bad,
                "content": [{"type": "input_text", "text": f"literal {bad}"}],
            },
        },
    ]
    path.write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")
    fixes, drops, _ = fix_rollout_file(path, dry_run=False)
    assert drops == 1
    assert fixes >= 2
    out = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(out) == 1
    payload = out[0]["payload"]
    assert payload["id"] == "msg_bbbbbbbbbbbbbbbb"
    assert "previous_item_id" not in payload
    assert "item_id" not in payload
    assert payload["content"][0]["text"] == f"literal {bad}"
    assert list(tmp_path.glob("rollout.jsonl.bak.*"))
