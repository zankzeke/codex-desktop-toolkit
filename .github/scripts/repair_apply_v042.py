from pathlib import Path

p = Path('.github/scripts/apply_v042.py')
text = p.read_text(encoding='utf-8')
start_marker = '# ---------------------------------------------------------------------------\n# 2. WebSocket reconnect semantics: parallel connections are not reconnects\n# ---------------------------------------------------------------------------\n'
end_marker = '# ---------------------------------------------------------------------------\n# 3. Exact Responses endpoint matching\n# ---------------------------------------------------------------------------\n'
start = text.index(start_marker)
end = text.index(end_marker, start)
replacement = '''# ---------------------------------------------------------------------------
# 2. WebSocket reconnect semantics: parallel connections are not reconnects
# ---------------------------------------------------------------------------
replace_section(
    "runtime_stats.py",
    "    def ws_connected(self) -> None:\\n",
    "    def ws_failed(self, message: str | None = None) -> None:\\n",
    """    def ws_connected(self) -> None:
        # Count a reconnect only after all earlier WS connections became idle.
        # A second simultaneous connection is ordinary parallel traffic.
        was_idle = self.ws_active == 0
        if self.ws_handshakes > 0 and was_idle:
            self.ws_reconnects += 1
        self.ws_handshakes += 1
        self.ws_active += 1
        self.last_transport = "websocket"
        self.ws_last_connected_at = _now()

""",
)


'''
p.write_text(text[:start] + replacement + text[end:], encoding='utf-8', newline='\n')
