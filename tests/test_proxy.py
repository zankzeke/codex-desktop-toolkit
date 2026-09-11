import asyncio
import json

from aiohttp import ClientSession, WSMsgType, web

from proxy import CodexProxy, make_app


def test_url_mapping_and_masking():
    p = CodexProxy("https://chatgpt.com/backend-api/codex", "safe", 8787)
    assert p._build_upstream_url("/v1/responses?x=1") == "https://chatgpt.com/backend-api/codex/responses?x=1"
    p2 = CodexProxy("https://api.openai.com/v1", "safe", 8787)
    assert p2._build_upstream_url("/v1/responses") == "https://api.openai.com/v1/responses"
    assert p._mask_url("https://user:secret@example.com/path?token=abc#x") == "https://***:***@example.com/path"


def test_websocket_rewrite_protocol_and_upgrade_metadata():
    async def scenario():
        upstream = web.Application()

        async def ws_handler(request):
            ws = web.WebSocketResponse(protocols=("codex-test",))
            ws.headers["X-Reasoning-Included"] = "true"
            ws.headers["OpenAI-Model"] = "gpt-test"
            ws.headers["X-Codex-Turn-State"] = "turn-state-test"
            await ws.prepare(request)
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    await ws.send_str(msg.data)
            return ws

        upstream.router.add_get("/responses", ws_handler)
        upstream_runner = web.AppRunner(upstream)
        await upstream_runner.setup()
        upstream_site = web.TCPSite(upstream_runner, "127.0.0.1", 0)
        await upstream_site.start()
        upstream_port = upstream_site._server.sockets[0].getsockname()[1]

        app = make_app(f"http://127.0.0.1:{upstream_port}", "safe", 0)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]

        try:
            async with ClientSession() as session:
                ws = await session.ws_connect(
                    f"http://127.0.0.1:{port}/responses",
                    protocols=("codex-test",),
                )
                assert ws.protocol == "codex-test"
                assert ws._response.headers.get("X-Reasoning-Included") == "true"
                assert ws._response.headers.get("OpenAI-Model") == "gpt-test"
                assert ws._response.headers.get("X-Codex-Turn-State") == "turn-state-test"
                raw = "resp_12345678-1234-1234-1234-123456789abc_msg"
                await ws.send_str(json.dumps({"input": [{"type": "message", "id": raw}]}))
                msg = await ws.receive(timeout=2)
                data = json.loads(msg.data)
                assert data["input"][0]["id"] == "msg_12345678-1234-1234-1234-123456789abc"
                await ws.close()
        finally:
            await runner.cleanup()
            await upstream_runner.cleanup()

    asyncio.run(scenario())


def test_two_proxy_instances_do_not_share_id_maps():
    # Regression guard for the old shared-request/shared-map race.
    from proxy import _sanitise_request_body

    raw = "item_dddddddddddddddd"
    a, _, _ = _sanitise_request_body({"input": [{"type": "message", "id": raw}]}, "safe", id_map={})
    b, _, _ = _sanitise_request_body({"input": [{"type": "function_call", "id": raw}]}, "safe", id_map={})
    assert a["input"][0]["id"] == "msg_dddddddddddddddd"
    assert b["input"][0]["id"] == "fc_dddddddddddddddd"


def test_concurrent_sse_streams_keep_rewrite_state_isolated():
    async def scenario():
        upstream = web.Application()

        async def sse_handler(request):
            kind = request.query["kind"]
            response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await response.prepare(request)
            raw = "item_dddddddddddddddd"
            payload = {"type": "response.output_item.added", "item": {"type": kind, "id": raw}}
            # Encourage overlap between two concurrent requests.
            await asyncio.sleep(0.03 if kind == "message" else 0.01)
            await response.write(("data: " + json.dumps(payload) + "\n\n").encode())
            await asyncio.sleep(0.02)
            await response.write(b"data: [DONE]\n\n")
            await response.write_eof()
            return response

        upstream.router.add_get("/responses", sse_handler)
        upstream_runner = web.AppRunner(upstream)
        await upstream_runner.setup()
        upstream_site = web.TCPSite(upstream_runner, "127.0.0.1", 0)
        await upstream_site.start()
        upstream_port = upstream_site._server.sockets[0].getsockname()[1]

        app = make_app(f"http://127.0.0.1:{upstream_port}", "safe", 0)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]

        async def read_kind(session, kind):
            async with session.get(f"http://127.0.0.1:{port}/responses?kind={kind}") as resp:
                text = await resp.text()
                line = next(
                    x for x in text.splitlines()
                    if x.startswith("data:") and x[5:].strip() != "[DONE]"
                )
                return json.loads(line[5:].strip())["item"]["id"]

        try:
            async with ClientSession() as session:
                a, b = await asyncio.gather(
                    read_kind(session, "message"),
                    read_kind(session, "function_call"),
                )
                assert a == "msg_dddddddddddddddd"
                assert b == "fc_dddddddddddddddd"
        finally:
            await runner.cleanup()
            await upstream_runner.cleanup()

    asyncio.run(scenario())
