from pathlib import Path

p = Path('.github/scripts/apply_v042.py')
text = p.read_text(encoding='utf-8')

# Fix class-method indentation in the generated RuntimeStats replacement.
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
text = text[:start] + replacement + text[end:]

# apply_v042.py contains Python source inside Python triple-quoted strings.
# Escape backslash-n one extra level in the two generated-code sections so the
# generated files contain "\\n" literals instead of a real newline inside a
# quoted Python string.
def escape_nested_newlines(source: str, start_text: str, end_text: str) -> str:
    a = source.index(start_text)
    b = source.index(end_text, a)
    segment = source[a:b]
    segment = segment.replace(r'\n', r'\\n')
    return source[:a] + segment + source[b:]

text = escape_nested_newlines(
    text,
    'new_fix_rollout = dedent(',
    'write("history_fixer.py", history_text[:fix_start] + new_fix_rollout)',
)
text = escape_nested_newlines(
    text,
    'write(\n    "tests/test_v041_polish.py",',
    '# ---------------------------------------------------------------------------\n# 8. Release workflow:',
)

p.write_text(text, encoding='utf-8', newline='\n')
