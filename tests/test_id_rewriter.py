import copy

from id_rewriter import sanitise_input_array, rewrite_single_id


def test_rewrite_resp_message_id():
    id_map = {}
    raw = "resp_12345678-1234-1234-1234-123456789abc_msg"
    assert rewrite_single_id(raw, "message", id_map) == "msg_12345678-1234-1234-1234-123456789abc"


def test_rewrite_typed_item_ids():
    suffix = "abcdef0123456789"
    expected = {
        "message": "msg_",
        "function_call": "fc_",
        "function_call_output": "fco_",
        "computer_call": "cc_",
        "computer_call_output": "cco_",
    }
    for item_type, prefix in expected.items():
        assert rewrite_single_id(f"item_{suffix}", item_type, {}) == f"{prefix}{suffix}"


def test_reasoning_is_dropped_and_content_is_not_rewritten():
    raw_reasoning = "item_aaaaaaaaaaaaaaaa"
    input_items = [
        {"type": "reasoning", "id": raw_reasoning, "summary": []},
        {
            "type": "message",
            "id": "item_bbbbbbbbbbbbbbbb",
            "previous_item_id": raw_reasoning,
            "content": [{"type": "input_text", "text": f"literal {raw_reasoning}"}],
        },
    ]
    original = copy.deepcopy(input_items)
    dropped = set()
    out, _, fixes, drops = sanitise_input_array(input_items, {}, dropped_ids=dropped, safe_reasoning=True)
    assert drops == 1
    assert fixes >= 2
    assert len(out) == 1
    assert out[0]["id"] == "msg_bbbbbbbbbbbbbbbb"
    assert "previous_item_id" not in out[0]
    assert out[0]["content"][0]["text"] == f"literal {raw_reasoning}"
    assert input_items == original


def test_all_reference_keys_to_dropped_reasoning_are_removed():
    bad = "item_aaaaaaaaaaaaaaaa"
    out, _, _, _ = sanitise_input_array(
        [
            {"type": "reasoning", "id": bad},
            {
                "type": "message",
                "id": "item_bbbbbbbbbbbbbbbb",
                "item_id": bad,
                "message_id": bad,
                "previous_item_id": bad,
                "parent_id": bad,
                "response_id": bad,
            },
        ],
        {},
        dropped_ids=set(),
        safe_reasoning=True,
    )
    assert len(out) == 1
    for key in ("item_id", "message_id", "previous_item_id", "parent_id", "response_id"):
        assert key not in out[0]
