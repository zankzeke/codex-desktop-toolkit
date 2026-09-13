from types import SimpleNamespace

import history_fixer


def test_codex_process_detection_is_case_insensitive(monkeypatch):
    def fake_run(*args, **kwargs):
        return SimpleNamespace(stdout='codex.exe                 1234 Console                    1     50,000 K\n')

    monkeypatch.setattr('subprocess.run', fake_run)
    assert history_fixer.is_codex_running() is True


def test_codex_process_detection_returns_false_when_absent(monkeypatch):
    def fake_run(*args, **kwargs):
        return SimpleNamespace(stdout='INFO: No tasks are running which match the specified criteria.\n')

    monkeypatch.setattr('subprocess.run', fake_run)
    assert history_fixer.is_codex_running() is False
