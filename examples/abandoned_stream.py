#!/usr/bin/env python3
# Usage: cd <repo-root> && uv run python examples/abandoned_stream.py
"""
Offline end-to-end check that abandoned streamed responses reach Coolhand marked.

No keys and no outside network. Streams OpenAI-shaped SSE bodies from a mock transport
through the real interceptor and the real Coolhand client, which delivers to a throwaway
local HTTP server standing in for the Coolhand API. The check is on what that server
receives: the exact ``raw_request`` of each POST. A stream read to the end must arrive
without ``stream_incomplete``; one abandoned part-way (a sync ``break``, an explicit
``close()``, an async ``break``, a cancelled task) must arrive once, carrying the partial
body and ``stream_incomplete: true``. Exits 1 on any mismatch, 0 otherwise.
"""

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import _live_check

NAME = "abandoned_stream"
OPENAI_URL = "https://api.openai.com/v1/chat/completions"

import httpx  # noqa: E402

import coolhand  # noqa: E402
from coolhand import httpx_interceptor  # noqa: E402

SSE = (
    b'data: {"id": "chatcmpl-Example", "choices": [{"delta": {"content": "ok"}}]}\n\n'
    b'data: {"id": "chatcmpl-Example", "choices": [], "usage": {"total_tokens": 12}}\n\n'
    b"data: [DONE]\n\n"
)
CHUNKS = [SSE[i : i + 40] for i in range(0, len(SSE), 40)]
HEADERS = {"content-type": "text/event-stream"}


class SyncChunks(httpx.SyncByteStream):
    def __iter__(self):
        yield from CHUNKS


class AsyncChunks(httpx.AsyncByteStream):
    async def __aiter__(self):
        for chunk in CHUNKS:
            yield chunk
            await asyncio.sleep(0)


def sync_client() -> httpx.Client:
    return httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, stream=SyncChunks(), headers=HEADERS)
        )
    )


def async_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, stream=AsyncChunks(), headers=HEADERS)
        )
    )


# Each scenario returns whether the stream was abandoned part-way. The partial body is
# always the first chunk, since every scenario takes exactly one chunk before stopping.
def sync_complete() -> bool:
    with sync_client() as client:
        with client.stream("POST", OPENAI_URL, json={}) as response:
            for _ in response.iter_bytes():
                pass
    return False


def sync_break() -> bool:
    with sync_client() as client:
        with client.stream("POST", OPENAI_URL, json={}) as response:
            for _ in response.iter_bytes():
                break
    return True


def sync_close() -> bool:
    with sync_client() as client:
        with client.stream("POST", OPENAI_URL, json={}) as response:
            next(response.iter_bytes())
            response.close()
    return True


async def async_complete() -> bool:
    async with async_client() as client:
        async with client.stream("POST", OPENAI_URL, json={}) as response:
            async for _ in response.aiter_bytes():
                pass
    return False


async def async_break() -> bool:
    async with async_client() as client:
        async with client.stream("POST", OPENAI_URL, json={}) as response:
            async for _ in response.aiter_bytes():
                break
    return True


async def async_cancelled() -> bool:
    first_chunk_read = asyncio.Event()

    async def consume() -> None:
        async with async_client() as client:
            async with client.stream("POST", OPENAI_URL, json={}) as response:
                async for _ in response.aiter_bytes():
                    first_chunk_read.set()
                    await asyncio.sleep(3600)

    task = asyncio.create_task(consume())
    await first_chunk_read.wait()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    return True


SCENARIOS = [
    sync_complete,
    sync_break,
    sync_close,
    async_complete,
    async_break,
    async_cancelled,
]


class FakeCoolhandApi(BaseHTTPRequestHandler):
    received: list[dict] = []

    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers.get("content-length", 0)))
        type(self).received.append(json.loads(body))
        self.send_response(201)
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *_: object) -> None:
        pass


def delivered_raw_requests() -> list[dict]:
    coolhand.shutdown()
    return [post["llm_request_log"]["raw_request"] for post in FakeCoolhandApi.received]


def run_scenario(scenario) -> None:
    FakeCoolhandApi.received.clear()
    result = scenario()
    abandoned = asyncio.run(result) if asyncio.iscoroutine(result) else result
    raw_requests = delivered_raw_requests()
    if len(raw_requests) != 1:
        _live_check.fail(
            scenario.__name__, f"expected 1 delivered log, got {len(raw_requests)}"
        )
    raw = raw_requests[0]
    if raw.get("is_streaming") is not True:
        _live_check.fail(scenario.__name__, f"is_streaming={raw.get('is_streaming')!r}")
    if abandoned:
        if raw.get("stream_incomplete") is not True:
            _live_check.fail(
                scenario.__name__,
                f"abandoned stream delivered without stream_incomplete: {raw}",
            )
        if raw.get("response_body") != CHUNKS[0].decode():
            _live_check.fail(
                scenario.__name__,
                f"partial body not preserved: {raw.get('response_body')!r}",
            )
    else:
        if "stream_incomplete" in raw:
            _live_check.fail(
                scenario.__name__, "completed stream wrongly marked stream_incomplete"
            )
        if raw.get("response_body") != SSE.decode():
            _live_check.fail(scenario.__name__, "completed stream body incomplete")
    print(
        f"PASS {scenario.__name__}: "
        + (
            "partial body delivered with stream_incomplete"
            if abandoned
            else "full body delivered, no marker"
        )
    )


def main() -> None:
    server = HTTPServer(("127.0.0.1", 0), FakeCoolhandApi)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    httpx_interceptor.set_exclude_api_patterns(None)
    try:
        coolhand.Coolhand(
            {
                "api_key": "example-key-0123456789",
                "base_url": f"http://127.0.0.1:{server.server_port}",
                "silent": True,
            }
        )
        for scenario in SCENARIOS:
            run_scenario(scenario)
    finally:
        httpx_interceptor.unpatch()
        httpx_interceptor.set_handler(None)
        server.shutdown()
        server.server_close()
    print(f"PASS {NAME}: {len(SCENARIOS)} scenarios delivered as expected")


if __name__ == "__main__":
    main()
