"""Post-PyInstaller smoke test used by release.yml on Windows."""
from __future__ import annotations

import argparse
import asyncio
import json
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

from aiohttp import ClientSession, WSMsgType, web

RAW = "resp_12345678-1234-1234-1234-123456789abc_msg"
FIXED = "msg_12345678-1234-1234-1234-123456789abc"


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def main(exe: Path):
    upstream = web.Application()

    async def json_handler(request):
        return web.json_response({"type": "message", "id": RAW})

    async def sse_handler(request):
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        payload = {"type": "response.output_item.added", "item": {"type": "message", "id": "item_dddddddddddddddd"}}
        await response.write(("data: " + json.dumps(payload) + "\n\n").encode())
        await response.write(b"data: [DONE]\n\n")
        await response.write_eof()
        return response

    async def ws_handler(request):
        ws = web.WebSocketResponse(protocols=("codex-smoke",))
        await ws.prepare(request)
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                await ws.send_str(msg.data)
        return ws

    async def dispatch(request):
        if request.headers.get("Upgrade", "").lower() == "websocket":
            return await ws_handler(request)
        if request.query.get("stream") == "1":
            return await sse_handler(request)
        return await json_handler(request)

    upstream.router.add_route("*", "/v1/responses", dispatch)
    runner = web.AppRunner(upstream)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    upstream_port = site._server.sockets[0].getsockname()[1]
    proxy_port = free_port()

    proc = subprocess.Popen([
        str(exe), "--port", str(proxy_port),
        "--upstream", f"http://127.0.0.1:{upstream_port}",
        "--proxy-mode", "direct", "--log-level", "INFO",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{proxy_port}/health", timeout=1) as response:
                    if response.status == 200:
                        break
            except Exception:
                await asyncio.sleep(0.25)
        else:
            raise RuntimeError("packaged proxy health timeout")

        async with ClientSession() as session:
            async with session.post(f"http://127.0.0.1:{proxy_port}/v1/responses", json={"input": []}) as response:
                data = await response.json()
                assert data["id"] == FIXED

            async with session.get(f"http://127.0.0.1:{proxy_port}/v1/responses?stream=1") as response:
                text = await response.text()
                assert "msg_dddddddddddddddd" in text

            ws = await session.ws_connect(
                f"http://127.0.0.1:{proxy_port}/v1/responses",
                protocols=("codex-smoke",),
            )
            await ws.send_str(json.dumps({"input": [{"type": "message", "id": RAW}]}))
            msg = await ws.receive(timeout=3)
            assert FIXED in msg.data
            await ws.close()

            async with session.get(f"http://127.0.0.1:{proxy_port}/stats") as response:
                stats = await response.json()
                assert stats["traffic_verified"] is True
                assert stats["requests_total"] >= 3
                assert stats["websocket"]["handshakes"] >= 1
    finally:
        proc.kill()
        try:
            proc.wait(timeout=5)
        except Exception:
            pass
        await runner.cleanup()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--proxy-exe", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(main(args.proxy_exe.resolve()))
