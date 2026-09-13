from types import SimpleNamespace

import diagnostics


class _UrlOpenFailure:
    def __enter__(self):
        raise OSError("offline")

    def __exit__(self, exc_type, exc, tb):
        return False


def test_diagnostics_codex_process_detection_is_case_insensitive(monkeypatch):
    def fake_run(*args, **kwargs):
        return SimpleNamespace(
            stdout="codex.exe                 1234 Console                    1     50,000 K\n"
        )

    def fake_urlopen(*args, **kwargs):
        raise OSError("proxy unavailable")

    monkeypatch.setattr("subprocess.run", fake_run)
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    diag = diagnostics.get_diagnostics(8787)
    assert diag["codex"]["running"] is True


def test_diagnostics_codex_process_detection_returns_false_when_absent(monkeypatch):
    def fake_run(*args, **kwargs):
        return SimpleNamespace(
            stdout="INFO: No tasks are running which match the specified criteria.\n"
        )

    def fake_urlopen(*args, **kwargs):
        raise OSError("proxy unavailable")

    monkeypatch.setattr("subprocess.run", fake_run)
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    diag = diagnostics.get_diagnostics(8787)
    assert diag["codex"]["running"] is False
