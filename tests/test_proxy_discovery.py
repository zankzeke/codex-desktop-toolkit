from __future__ import annotations

from unittest.mock import patch

from proxy_discovery import (
    COMMON_PROXY_PORTS,
    DiscoveredProxy,
    discover_proxies,
    match_process_name,
)


def test_match_process_name():
    assert match_process_name("clash-verge.exe") == "Clash Verge Rev"
    assert match_process_name("Clash.exe") == "Clash"
    assert match_process_name("mihomo-windows-amd64.exe") == "Mihomo"
    assert match_process_name("v2rayN.exe") == "v2rayN"
    assert match_process_name("NekoRay.exe") == "NekoRay"
    assert match_process_name("sing-box.exe") == "sing-box"
    assert match_process_name("notepad.exe") is None


def test_discover_proxies_with_mock():
    with patch("proxy_discovery.is_port_listening", side_effect=lambda port, **kw: port in (7897, 8888)):
        with patch("proxy_discovery.get_port_owner", side_effect=lambda port: (1234, "clash-verge.exe") if port == 7897 else (5678, "custom.exe")):
            results = discover_proxies(candidate_ports=[7897, 8888, 9999])
            assert len(results) == 2
            
            p1 = results[0]
            assert p1.name == "Clash Verge Rev"
            assert p1.port == 7897
            assert p1.identified is True

            p2 = results[1]
            assert "未识别本地代理" in p2.name
            assert p2.port == 8888
            assert p2.identified is False
