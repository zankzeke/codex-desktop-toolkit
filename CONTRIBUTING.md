# Contributing

Thanks for helping improve Codex Desktop Toolkit.

## Before opening a PR

1. Keep the project Windows-first and Python 3.11+ compatible.
2. Do not add code that logs credentials, request bodies, response bodies, or private session text.
3. Keep structured-ID repair centralized in `id_rewriter.py`; do not add a second rewrite implementation to the GUI or CLI.
4. Preserve the safe reasoning rule: synthetic reasoning must not be blindly renamed to an `rs_*` ID.
5. Preserve per-request/per-connection state isolation for concurrent SSE/WebSocket traffic.

## Tests

```powershell
python -m pip install -r requirements-dev.txt
python -m compileall -q .
python -m pytest -q
```

Add a regression test for any bug fix that can reasonably be reproduced without real credentials or private Codex data.

## Test data

Use synthetic IDs and fabricated payloads only. Never commit or attach real `~/.codex/sessions` files.
