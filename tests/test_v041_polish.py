from pathlib import Path
def test_log_controls_are_built_in_build_scope():
    text = Path("codex_toolkit_gui.py").read_text(encoding="utf-8")
    assert text.count("ttk.Button(log_frame, text=\"清空日志\"") == 1
    assert text.index("ttk.Button(log_frame, text=\"清空日志\"") < text.index("    def _center_window")
    manage = text[text.index("    def _manage_upstreams"):text.index("    def _show_proxy_help")]
    assert "log_frame" not in manage

def test_one_click_pending_launch_is_cleared_on_failed_start():
    text = Path("codex_toolkit_gui.py").read_text(encoding="utf-8")
    assert "if not proc or proc.poll() is not None:" in text
    assert "self._launch_pending = False" in text

def test_response_side_rewrites_update_stats():
    text = Path("proxy.py").read_text(encoding="utf-8")
    assert "self.stats.record_rewrite(fixes, 0)" in text
    assert "self.stats.record_rewrite(f, 0)" in text

def test_diagnostic_report_redacts_home_and_no_proxy():
    text = Path("diagnostics.py").read_text(encoding="utf-8")
    assert "contents redacted" in text
    assert "codex_path = \"~\" + codex_path[len(home):]" in text
