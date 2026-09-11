# Security Policy

## What this project handles

Codex Desktop Toolkit is a local compatibility proxy. During normal operation it can receive authentication headers from Codex Desktop and forward them to the configured upstream.

It is therefore important to understand that the proxy is **not** an authentication sandbox or credential vault.

## Safe defaults

- The proxy binds to `127.0.0.1` only.
- Request/response bodies are not intentionally logged.
- Authentication headers are not intentionally logged.
- Diagnostic URLs redact embedded user-info and remove query strings.
- Session/config changes create backups and use atomic replacement where practical.
- The GUI warns before using a non-standard upstream.

## Custom upstreams

A custom upstream can receive the same authentication information that Codex sends through the proxy. Only configure an upstream that you fully trust. Prefer HTTPS for remote upstreams.

## Reporting a vulnerability

Please do **not** include any of the following in a public issue:

- Authorization or Cookie headers
- access/refresh tokens
- complete Codex session JSONL files
- private prompts, model output, or account identifiers
- unredacted HTTP/WebSocket captures

Use GitHub private vulnerability reporting / a private security advisory when available. If you must open a public issue, provide only a minimal redacted reproduction and ask the maintainer for a private channel.

## Supported versions

Until tagged releases are established, security fixes are made on the latest `main` branch only.
