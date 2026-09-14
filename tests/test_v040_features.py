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
