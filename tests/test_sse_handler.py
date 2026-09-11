import json

from sse_handler import SSELineBuffer, rewrite_sse_line


def test_line_buffer_handles_arbitrary_chunks():
    buf = SSELineBuffer()
    chunks = [b"event: x\r", b"\ndata: {\"type\":\"message\",", b"\"id\":\"item_abcdef0123456789\"}\r\n\r", b"\ndata: [DONE]\r\n\r\n"]
    lines = []
    for chunk in chunks:
        lines.extend(buf.feed(chunk))
    assert b"" == buf.flush()
    assert lines[0] == b"event: x\r\n"
    assert lines[-2] == b"data: [DONE]\r\n"


def test_sse_rewrites_nested_structured_id_and_preserves_done():
    raw = b'data: {"type":"response.output_item.added","item":{"type":"message","id":"item_abcdef0123456789"}}\r\n'
    out, fixes = rewrite_sse_line(raw, {})
    payload = json.loads(out.rstrip(b"\r\n")[5:])
    assert payload["item"]["id"] == "msg_abcdef0123456789"
    assert fixes == 1
    done = b"data: [DONE]\r\n"
    assert rewrite_sse_line(done, {}) == (done, 0)
