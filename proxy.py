"""
proxy.py — Codex Desktop Responses-API compatibility proxy.

Usage
-----
    python proxy.py [--port 8787] [--reasoning-mode safe] [--log-level INFO]

What it does
------------
1.  Listens on 127.0.0.1:<port> only (never 0.0.0.0).
2.  Forwards every request to the official OpenAI / ChatGPT Codex backend,
    passing Authorization / Cookie / other auth headers verbatim.
3.  Before forwarding a POST /v1/responses request, rewrites the ``input``
    array to fix malformed IDs produced by codex++ in previous sessions.
4.  After receiving the upstream response:
      - Non-streaming (JSON):  rewrites ID fields in the response body.
      - Streaming (SSE):       rewrites ID fields in each data: event as
                               it arrives; never buffers the whole stream.
5.  Provides GET /health → {"status":"ok"}.

Security
--------
- Only listens on loopback.
- Auth headers are forwarded but NEVER logged.
- Request/response bodies are NEVER logged.
- Only anonymised metadata is logged (ID counts, status codes).

Upstream URL mapping
--------------------
Codex Desktop in "openai" provider mode sends requests to:

    https://chatgpt.com/backend-api/codex        (primary model calls)
    https://api.openai.com/v1/...                (other endpoints)

When you set base_url = "http://127.0.0.1:8787/v1" in config, the proxy
receives:

    POST http://127.0.0.1:8787/v1/responses

and forwards it to:

    POST https://chatgpt.com/backend-api/codex/responses

The UPSTREAM_BASE env-var (or --upstream flag) lets you override the target
base URL for testing or when using a different upstream.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import aiohttp
from aiohttp import web

from id_rewriter import sanitise_input_array, rewrite_response_object
from sse_handler import SSELineBuffer, rewrite_sse_line
from runtime_stats import RuntimeStats

# ---------------------------------------------------------------------------
# Logging setup — SAFE: no auth or body data
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("proxy")

# ---------------------------------------------------------------------------
# Configuration (from args / env)
# ---------------------------------------------------------------------------

DEFAULT_PORT = 8787
DEFAULT_UPSTREAM = "https://chatgpt.com/backend-api/codex"
DEFAULT_REASONING_MODE = "safe"

# Headers we must NOT forward to upstream (hop-by-hop or problematic).
_HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
        "content-length",  # we recalculate after body modification
        "content-encoding",  # we ask upstream for identity
        "host",  # we set this to upstream host
    }
)

# Headers we strip from upstream response before forwarding to client.
_UPSTREAM_RESP_STRIP = frozenset(
    {
        "content-length",  # body may be modified; let aiohttp set chunked
        "content-encoding",
        "transfer-encoding",
        "connection",
    }
)

# Auth/sensitive header names for log-safe detection.
_AUTH_HEADERS = frozenset(
    {"authorization", "cookie", "set-cookie", "x-access-token", "x-refresh-token"}
)


def _safe_header_log(headers: dict[str, str]) -> dict[str, str]:
    """Return headers with sensitive values masked — for debug logging only."""
    return {
        k: ("***" if k.lower() in _AUTH_HEADERS else v)
        for k, v in headers.items()
    }


def _is_responses_path(path: str) -> bool:
    """Return True only for the local Responses endpoint.

    The caller passes ``request.path`` (never the query string). A
    trailing slash is accepted, while substring/suffix lookalikes are
    deliberately rejected.
    """
    return path.rstrip("/") == "/v1/responses"


# ---------------------------------------------------------------------------
# Request body sanitiser
# ---------------------------------------------------------------------------


def _sanitise_request_body(
    body: dict[str, Any],
    reasoning_mode: str,
    id_map: dict[str, str] | None = None,
) -> tuple[dict[str, Any], int, int]:
    """
    Rewrite the ``input`` array of a Responses API request body.

    Returns (modified_body, msg_fixes, reasoning_drops).
    """
    input_arr = body.get("input")
    if not isinstance(input_arr, list):
        return body, 0, 0

    if id_map is None:
        id_map = {}
    new_input, id_map, msg_fixes, reasoning_drops = sanitise_input_array(
        input_arr, id_map=id_map, safe_reasoning=(reasoning_mode in ("safe", "drop_invalid"))
    )
    body = dict(body)
    body["input"] = new_input
    return body, msg_fixes, reasoning_drops


# ---------------------------------------------------------------------------
# Core proxy handler
# ---------------------------------------------------------------------------


class CodexProxy:
    def __init__(self, upstream_base: str, reasoning_mode: str, port: int, upstream_proxy: str | None = None, proxy_mode: str = "direct") -> None:
        self.upstream_base = upstream_base.rstrip("/")
        self.reasoning_mode = reasoning_mode
        self.port = port
        self.upstream_proxy = upstream_proxy
        self.proxy_mode = proxy_mode
        self.stats = RuntimeStats()
        self._session: aiohttp.ClientSession | None = None

    def _mask_url(self, url: str | None) -> str | None:
        """Return a log-safe URL with user-info, query, and fragment removed."""
        if not url:
            return url
        try:
            parsed = urlsplit(url)
            host = parsed.hostname or ""
            if ":" in host and not host.startswith("["):
                host = f"[{host}]"
            if parsed.port is not None:
                host = f"{host}:{parsed.port}"
            if parsed.username is not None or parsed.password is not None:
                host = f"***:***@{host}"
            return urlunsplit((parsed.scheme, host, parsed.path, "", ""))
        except Exception:
            return "<redacted-url>"

    def _build_upstream_url(self, raw_path: str) -> str:
        """Map a local request path onto the configured upstream base URL."""
        parsed = urlsplit(self.upstream_base)
        path = raw_path
        # ChatGPT Codex backend expects /responses rather than /v1/responses.
        # Also avoid doubling /v1 for API-style custom bases such as
        # https://api.openai.com/v1.
        if path.startswith("/v1/") and (
            parsed.hostname == "chatgpt.com" or parsed.path.rstrip("/").endswith("/v1")
        ):
            path = path[3:]
        return self.upstream_base.rstrip("/") + path

    async def startup(self) -> None:
        connector = aiohttp.TCPConnector(ssl=True)
        self._session = aiohttp.ClientSession(
            connector=connector,
            # Do not follow redirects automatically — let client handle them.
            # Timeouts: 30s connect, no total timeout (SSE streams can be long).
            timeout=aiohttp.ClientTimeout(connect=30, total=None),
            trust_env=self.proxy_mode == "env",
        )
        logger.info("Upstream: %s", self._mask_url(self.upstream_base))
        logger.info("Proxy mode: %s", self.proxy_mode)
        if self.proxy_mode == "explicit" and self.upstream_proxy:
            logger.info("Upstream Proxy: %s", self._mask_url(self.upstream_proxy))
        logger.info("Reasoning mode: %s", self.reasoning_mode)

    async def shutdown(self) -> None:
        if self._session:
            await self._session.close()

    # ------------------------------------------------------------------ #
    # Health endpoint                                                      #
    # ------------------------------------------------------------------ #

    async def handle_health(self, request: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    async def handle_health_details(self, request: web.Request) -> web.Response:
        return web.json_response({
            "status": "ok",
            "upstream": self._mask_url(self.upstream_base),
            "websocket_supported": True,
            "reasoning_mode": self.reasoning_mode,
            "proxy_mode": self.proxy_mode,
            "upstream_proxy": self._mask_url(self.upstream_proxy) if self.proxy_mode == "explicit" else None,
            "env_proxies": {
                "http_proxy": self._mask_url(os.environ.get("HTTP_PROXY")),
                "https_proxy": self._mask_url(os.environ.get("HTTPS_PROXY")),
                "all_proxy": self._mask_url(os.environ.get("ALL_PROXY")),
                "no_proxy": os.environ.get("NO_PROXY")
            }
        })

    async def handle_stats(self, request: web.Request) -> web.Response:
        return web.json_response(self.stats.snapshot())

    # ------------------------------------------------------------------ #
    # Main proxy catch-all                                                 #
    # ------------------------------------------------------------------ #

    async def handle_proxy(self, request: web.Request) -> web.StreamResponse:
        assert self._session is not None

        path = request.raw_path  # includes query string
        transport = "websocket" if request.headers.get("Upgrade", "").lower() == "websocket" else "http"
        self.stats.record_request(request.method, path, transport)
        upstream_url = self._build_upstream_url(path)

        # ---- Build forwarded headers ----
        fwd_headers: dict[str, str] = {}
        for name, value in request.headers.items():
            if name.lower() not in _HOP_BY_HOP:
                fwd_headers[name] = value


        # Force identity encoding so we can read/modify the response stream.
        # Host is intentionally omitted; aiohttp derives it from upstream_url.
        fwd_headers["Accept-Encoding"] = "identity"

        # ---- Check for WebSocket Upgrade ----
        if request.headers.get("Upgrade", "").lower() == "websocket":
            return await self._proxy_websocket(request, upstream_url, fwd_headers)

        # ---- Read request body ----
        body_bytes = await request.read()
        msg_fixes = 0
        reasoning_drops = 0
        is_responses_endpoint = _is_responses_path(request.path)

        if (
            is_responses_endpoint
            and request.method == "POST"
            and body_bytes
        ):
            try:
                body_obj = json.loads(body_bytes)
            except json.JSONDecodeError:
                body_obj = None

            if isinstance(body_obj, dict):
                body_obj, msg_fixes, reasoning_drops = _sanitise_request_body(
                    body_obj, self.reasoning_mode
                )
                body_bytes = json.dumps(body_obj, ensure_ascii=False).encode()

        self.stats.record_rewrite(msg_fixes, reasoning_drops)

        if msg_fixes or reasoning_drops:
            logger.info(
                "[OUT] %s %s | msg-id fixes: %d | reasoning drops: %d",
                request.method,
                path.split("?")[0],
                msg_fixes,
                reasoning_drops,
            )
        else:
            logger.info("[OUT] %s %s -> %s", request.method, path.split("?")[0], self._mask_url(upstream_url))

        # ---- Forward to upstream ----
        try:
            upstream_resp = await self._session.request(
                method=request.method,
                url=upstream_url,
                headers=fwd_headers,
                data=body_bytes if body_bytes else None,
                allow_redirects=False,
                proxy=self.upstream_proxy if self.proxy_mode == "explicit" else None,
            )
        except aiohttp.ClientError as exc:
            self.stats.record_error(502, "connection failed")
            logger.error("Upstream connection error: %s", type(exc).__name__)
            return web.Response(status=502, text="Proxy upstream connection failed")

        self.stats.record_response(upstream_resp.status)
        logger.info("[IN ] upstream status: %d (url: %s)", upstream_resp.status, self._mask_url(upstream_url))

        # ---- Build response headers ----
        resp_headers: dict[str, str] = {}
        for name, value in upstream_resp.headers.items():
            if name.lower() not in _UPSTREAM_RESP_STRIP:
                resp_headers[name] = value

        content_type = upstream_resp.content_type or ""
        is_sse = "text/event-stream" in content_type

        # ---- SSE streaming path ----
        if is_sse:
            self.stats.record_sse()
            return await self._stream_sse(request, upstream_resp, resp_headers, path)

        # ---- Non-streaming path ----
        resp_body = await upstream_resp.read()
        if upstream_resp.status >= 400 and resp_body:
            try:
                err_obj = json.loads(resp_body)
                err_val = err_obj.get("error", err_obj) if isinstance(err_obj, dict) else err_obj
                self.stats.record_error(upstream_resp.status, str(err_val))
            except Exception:
                pass
        if is_responses_endpoint and resp_body:
            try:
                resp_obj = json.loads(resp_body)
                if isinstance(resp_obj, dict):
                    resp_obj, _, fixes = rewrite_response_object(resp_obj)
                    if fixes:
                        self.stats.record_rewrite(fixes, 0)
                        logger.info("response body: %d id fixes", fixes)
                    resp_body = json.dumps(resp_obj, ensure_ascii=False).encode()
            except json.JSONDecodeError:
                pass

        return web.Response(
            status=upstream_resp.status,
            headers=resp_headers,
            body=resp_body,
        )

    async def _proxy_websocket(
        self, request: web.Request, upstream_url: str, headers: dict[str, str]
    ) -> web.WebSocketResponse:

        if upstream_url.startswith("https://"):
            upstream_url = "wss://" + upstream_url[8:]
        elif upstream_url.startswith("http://"):
            upstream_url = "ws://" + upstream_url[7:]

        # Clean WS handshake headers - aiohttp ws_connect handles these
        fwd_headers = {}
        ws_protocols = []
        for k, v in headers.items():
            kl = k.lower()
            if kl == "sec-websocket-protocol":
                ws_protocols = [p.strip() for p in v.split(",") if p.strip()]
            elif not kl.startswith("sec-websocket-") and kl not in ("connection", "upgrade", "host"):
                fwd_headers[k] = v

        logger.info("[WS ] connect -> %s", self._mask_url(upstream_url))
        id_map: dict[str, str] = {}
        ws_close_code: int | None = None
        ws_counted = False

        try:
            assert self._session is not None
            async with self._session.ws_connect(
                upstream_url,
                headers=fwd_headers,
                protocols=ws_protocols,
                proxy=self.upstream_proxy if self.proxy_mode == "explicit" else None,
                heartbeat=30
            ) as ws_upstream:
                
                # ONLY after upstream is successfully connected do we accept the client
                selected_protocol = ws_upstream.protocol
                ws_client = web.WebSocketResponse(
                    protocols=(selected_protocol,) if selected_protocol else ()
                )
                
                upstream_response = getattr(ws_upstream, "_response", None)
                up_headers = upstream_response.headers if upstream_response is not None else {}
                for name in (
                    "x-reasoning-included",
                    "openai-model",
                    "x-models-etag",
                    "x-codex-turn-state",
                ):
                    val = up_headers.get(name)
                    if val is not None:
                        ws_client.headers[name] = val
                        
                await ws_client.prepare(request)
                self.stats.ws_connected()
                ws_counted = True
                
                logger.info("[WS ] upstream established; accepting client")

                async def client_to_upstream():
                    nonlocal ws_close_code
                    try:
                        async for msg in ws_client:
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                try:
                                    obj = json.loads(msg.data)
                                except json.JSONDecodeError:
                                    await ws_upstream.send_str(msg.data)
                                    continue
                                
                                # If it's valid JSON, we must sanitise it successfully or fail.
                                try:
                                    obj, f, d = _sanitise_request_body(obj, self.reasoning_mode, id_map=id_map)
                                    if f or d:
                                        self.stats.record_rewrite(f, d)
                                        logger.info("WS c->u | msg-id fixes: %d | drops: %d", f, d)
                                    await ws_upstream.send_str(json.dumps(obj, ensure_ascii=False))
                                except Exception as e:
                                    logger.error("WS request sanitize error: %s", type(e).__name__)
                                    ws_close_code = 1011
                                    await ws_client.close(code=1011, message=b"Internal Proxy Error")
                                    break
                            elif msg.type == aiohttp.WSMsgType.BINARY:
                                await ws_upstream.send_bytes(msg.data)
                            elif msg.type == aiohttp.WSMsgType.CLOSE:
                                ws_close_code = msg.data if isinstance(msg.data, int) else ws_close_code
                                extra = msg.extra.encode('utf-8') if isinstance(msg.extra, str) else msg.extra
                                await ws_upstream.close(code=msg.data, message=extra)
                                break
                            elif msg.type == aiohttp.WSMsgType.ERROR:
                                break
                    except Exception as e:
                        logger.error("WS c->u loop error: %s", type(e).__name__)

                async def upstream_to_client():
                    nonlocal ws_close_code
                    try:
                        async for msg in ws_upstream:
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                try:
                                    obj = json.loads(msg.data)
                                except json.JSONDecodeError:
                                    await ws_client.send_str(msg.data)
                                    continue
                                    
                                try:
                                    obj, _, f = rewrite_response_object(obj, id_map=id_map)
                                    if f:
                                        self.stats.record_rewrite(f, 0)
                                    await ws_client.send_str(json.dumps(obj, ensure_ascii=False))
                                except Exception as e:
                                    logger.error("WS response sanitize error: %s", type(e).__name__)
                                    ws_close_code = 1011
                                    await ws_upstream.close(code=1011, message=b"Internal Proxy Error")
                                    break
                            elif msg.type == aiohttp.WSMsgType.BINARY:
                                await ws_client.send_bytes(msg.data)
                            elif msg.type == aiohttp.WSMsgType.CLOSE:
                                ws_close_code = msg.data if isinstance(msg.data, int) else ws_close_code
                                extra = msg.extra.encode('utf-8') if isinstance(msg.extra, str) else msg.extra
                                await ws_client.close(code=msg.data, message=extra)
                                break
                            elif msg.type == aiohttp.WSMsgType.ERROR:
                                break
                    except Exception as e:
                        logger.error("WS u->c loop error: %s", type(e).__name__)

                t1 = asyncio.create_task(client_to_upstream())
                t2 = asyncio.create_task(upstream_to_client())
                
                done, pending = await asyncio.wait([t1, t2], return_when=asyncio.FIRST_COMPLETED)
                for task in pending:
                    task.cancel()
                    import contextlib
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
                        
                if not ws_client.closed:
                    await ws_client.close()
                if not ws_upstream.closed:
                    await ws_upstream.close()
                    
        except aiohttp.ClientError as exc:
            self.stats.ws_failed("websocket handshake failed")
            logger.error("WS Upstream handshake failed: %s", type(exc).__name__)
            # Reject client if we haven't prepared yet
            return web.Response(status=502, text="WS upstream connection failed")
        except Exception as e:
            self.stats.ws_failed("websocket proxy error")
            logger.error("WS Proxy Error: %s", type(e).__name__)
        finally:
            if ws_counted:
                code = ws_close_code
                try:
                    code = code or getattr(ws_upstream, "close_code", None) or getattr(ws_client, "close_code", None)
                except Exception:
                    pass
                self.stats.ws_closed(code)
            logger.info("[WS ] disconnected")

        # Fallback if ws_client was prepared
        try:
            return ws_client
        except NameError:
            return web.Response(status=502, text="WS failed before prepare.")

    async def _stream_sse(
        self,
        request: web.Request,
        upstream_resp: aiohttp.ClientResponse,
        resp_headers: dict[str, str],
        path: str,
    ) -> web.StreamResponse:
        """Forward SSE stream with per-event ID rewriting."""
        client_resp = web.StreamResponse(
            status=upstream_resp.status,
            headers=resp_headers,
        )
        # Ensure chunked transfer — we don't know total length.
        client_resp.enable_chunked_encoding()

        await client_resp.prepare(request)

        buf = SSELineBuffer()
        id_map: dict[str, str] = {}
        total_fixes = 0
        first_event_logged = False

        try:
            async for chunk in upstream_resp.content.iter_any():
                for line in buf.feed(chunk):
                    new_line, fixes = rewrite_sse_line(line, id_map)
                    total_fixes += fixes

                    # Sniff first data event for error type (no user content logged)
                    if not first_event_logged and new_line.startswith(b"data:"):
                        payload = new_line[5:].strip()
                        if payload and payload != b"[DONE]":
                            try:
                                obj = json.loads(payload)
                                evt_type = obj.get("type", "")
                                first_event_logged = True
                                if "error" in evt_type.lower() or obj.get("error"):
                                    err = obj.get("error", {})
                                    code = err.get("code") or err.get("type") or evt_type
                                    message = err.get("message") if isinstance(err, dict) else None
                                    self.stats.record_error(upstream_resp.status, str(message or code), event_type=evt_type)
                                    logger.warning("SSE error event: type=%r code=%r", evt_type, code)
                                else:
                                    logger.debug("SSE first event type: %r", evt_type)
                            except Exception:
                                pass

                    await client_resp.write(new_line)
            # Flush any tail (shouldn't be anything useful, but be safe).
            tail = buf.flush()
            if tail:
                await client_resp.write(tail)
        except (ConnectionResetError, asyncio.CancelledError):
            logger.info("SSE stream disconnected by client.")
        finally:
            upstream_resp.release()
            try:
                await client_resp.write_eof()
            except Exception:
                pass

        if total_fixes:
            self.stats.record_rewrite(total_fixes, 0)
            logger.info("sse stream complete: %d id fixes total", total_fixes)
            
        return client_resp


# ---------------------------------------------------------------------------
# aiohttp Application factory
# ---------------------------------------------------------------------------


def make_app(upstream_base: str, reasoning_mode: str, port: int, upstream_proxy: str | None = None, proxy_mode: str = "direct") -> web.Application:
    proxy = CodexProxy(upstream_base=upstream_base, reasoning_mode=reasoning_mode, port=port, upstream_proxy=upstream_proxy, proxy_mode=proxy_mode)

    async def _startup(app: web.Application) -> None:
        await proxy.startup()

    async def _shutdown(app: web.Application) -> None:
        await proxy.shutdown()

    app = web.Application(client_max_size=100 * 1024 * 1024)  # 100 MB
    app.on_startup.append(_startup)
    app.on_cleanup.append(_shutdown)
    app.router.add_get("/health", proxy.handle_health)
    app.router.add_get("/health/details", proxy.handle_health_details)
    app.router.add_get("/stats", proxy.handle_stats)
    app.router.add_route("*", "/{path_info:.*}", proxy.handle_proxy)

    return app


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Codex Desktop ID compatibility proxy")
    p.add_argument("--port", type=int, default=int(os.environ.get("PROXY_PORT", DEFAULT_PORT)))
    p.add_argument("--upstream", default=os.environ.get("PROXY_UPSTREAM", DEFAULT_UPSTREAM))
    p.add_argument("--upstream-proxy", default=os.environ.get("PROXY_UPSTREAM_PROXY", None), help="HTTP proxy URL used when --proxy-mode=explicit")
    p.add_argument("--proxy-mode", choices=["direct", "env", "explicit"], default=os.environ.get("PROXY_MODE", "direct"), help="Outbound networking: true direct, system environment proxy, or explicit proxy URL")
    p.add_argument(
        "--reasoning-mode",
        default=os.environ.get("PROXY_REASONING_MODE", DEFAULT_REASONING_MODE),
        choices=["safe", "drop_invalid"],
        help="safe and drop_invalid are identical: drops malformed reasoning items to prevent upstream errors"
    )
    p.add_argument("--log-level", default="INFO")
    return p.parse_args()


def _mask_url(url: str | None) -> str | None:
    if not url:
        return url
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        if parsed.port is not None:
            host = f"{host}:{parsed.port}"
        if parsed.username is not None or parsed.password is not None:
            host = f"***:***@{host}"
        return urlunsplit((parsed.scheme, host, parsed.path, "", ""))
    except Exception:
        return "<redacted-url>"

def main() -> None:
    args = parse_args()
    logging.getLogger().setLevel(args.log_level.upper())

    port: int = args.port
    upstream: str = args.upstream
    upstream_proxy: str | None = args.upstream_proxy
    proxy_mode: str = args.proxy_mode
    if upstream_proxy and proxy_mode == "direct":
        proxy_mode = "explicit"  # backwards-compatible CLI behaviour
    reasoning_mode: str = args.reasoning_mode

    if not (1 <= port <= 65535):
        raise SystemExit("--port must be between 1 and 65535")
    parsed_upstream = urlsplit(upstream)
    if parsed_upstream.scheme not in {"http", "https"} or not parsed_upstream.hostname:
        raise SystemExit("--upstream must be an absolute http(s) URL")
    if proxy_mode == "explicit" and not upstream_proxy:
        raise SystemExit("--proxy-mode=explicit requires --upstream-proxy")

    print("=" * 60)
    print("  Codex ID compatibility proxy")
    print(f"  Listening: http://127.0.0.1:{port}")
    print(f"  Upstream:  {_mask_url(upstream)}")
    print(f"  Proxy mode: {proxy_mode}")
    if proxy_mode == "explicit" and upstream_proxy:
        print(f"  Proxy:     {_mask_url(upstream_proxy)}")
    print(f"  Reasoning mode: {reasoning_mode}")
    print("=" * 60)
    print(f"  Health check: http://127.0.0.1:{port}/health")
    print("=" * 60)

    app = make_app(upstream_base=upstream, reasoning_mode=reasoning_mode, port=port, upstream_proxy=upstream_proxy, proxy_mode=proxy_mode)
    web.run_app(app, host="127.0.0.1", port=port, access_log=None)


if __name__ == "__main__":
    main()
