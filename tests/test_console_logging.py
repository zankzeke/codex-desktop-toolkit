from pathlib import Path


def test_proxy_runtime_log_markers_are_ascii_safe():
    text = Path("proxy.py").read_text(encoding="utf-8")
    required = [
        "%(name)s | %(message)s",
        "[OUT] %s route=%s",
        "[IN ] upstream status",
        "[WS ] connect ->",
        "[WS ] upstream established; accepting client",
        "[WS ] disconnected",
    ]
    for marker in required:
        assert marker in text
        assert marker.isascii()

    forbidden_runtime_markers = [
        "%(name)s — %(message)s",
        "logger.info(\"→",
        "logger.info(\"←",
        "logger.info(\"⇆",
    ]
    for marker in forbidden_runtime_markers:
        assert marker not in text
