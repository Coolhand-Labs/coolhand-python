"""Streaming capture must not change the host's stream or duplicate the body.

httpx builds aiter_lines on aiter_text on aiter_bytes on aiter_raw, each looked up
on the response instance. The interceptor wraps all four, so a wrapper that forced a
chunk size stalled small SSE events behind httpx's buffer, and one that captured at
every layer recorded each chunk several times.
"""

import asyncio
import importlib.util

import pytest

from coolhand import httpx_interceptor as interceptor

LLM_URL = "https://api.openai.com/v1/chat/completions"
EVENT = b"data: " + b"x" * 100 + b"\n\n"
EVENTS = 4

CLIENTS = [
    pytest.param("httpx", id="httpx"),
    pytest.param(
        "httpx2",
        id="httpx2",
        marks=pytest.mark.skipif(
            importlib.util.find_spec("httpx2") is None, reason="httpx2 not installed"
        ),
    ),
]


@pytest.fixture
def captured():
    interceptor.unpatch()
    interceptor.set_exclude_api_patterns(None)
    calls: list[tuple] = []
    interceptor.set_handler(lambda req, res, err: calls.append((req, res, err)))
    assert interceptor.patch() is True
    yield calls
    interceptor.unpatch()
    interceptor.set_handler(None)


def _client_for(module_name):
    module = __import__(module_name)
    consumed_first = asyncio.Event()

    class Stream(module.AsyncByteStream):
        async def __aiter__(self):
            for index in range(EVENTS):
                yield EVENT
                if index == 0:
                    # Hold the rest back until the consumer has seen event one. If
                    # the wrapper buffered small chunks, this never fires.
                    await asyncio.wait_for(consumed_first.wait(), timeout=2)

    transport = module.MockTransport(
        lambda request: module.Response(
            200, stream=Stream(), headers={"content-type": "text/event-stream"}
        )
    )
    return module.AsyncClient(transport=transport), consumed_first


@pytest.mark.asyncio
@pytest.mark.parametrize("module_name", CLIENTS)
@pytest.mark.parametrize("method", ["aiter_bytes", "aiter_text", "aiter_raw"])
async def test_first_event_is_delivered_immediately_and_captured_once(
    captured, module_name, method
):
    client, consumed_first = _client_for(module_name)
    received = 0
    async with client:
        async with client.stream("POST", LLM_URL, json={}) as response:
            async for chunk in getattr(response, method)():
                received += len(chunk)
                consumed_first.set()

    assert received == len(EVENT) * EVENTS
    assert len(captured) == 1
    _, res, err = captured[0]
    assert err is None
    assert res["is_streaming"] is True
    assert len(res["body"]) == len(EVENT) * EVENTS


@pytest.mark.asyncio
@pytest.mark.parametrize("module_name", CLIENTS)
async def test_aiter_lines_is_captured_once(captured, module_name):
    client, consumed_first = _client_for(module_name)
    lines = []
    async with client:
        async with client.stream("POST", LLM_URL, json={}) as response:
            async for line in response.aiter_lines():
                if line:
                    lines.append(line)
                    consumed_first.set()

    assert len(lines) == EVENTS
    assert len(captured) == 1
    body = captured[0][1]["body"]
    # One entry per event, not one per wrapper layer.
    assert body.count("data: ") == EVENTS


@pytest.mark.asyncio
@pytest.mark.parametrize("module_name", CLIENTS)
async def test_breaking_out_early_reports_no_partial_body(captured, module_name):
    """Abandoning a stream half way is not a completed response, so no record is
    emitted for it (and nothing raises while the generator is torn down)."""
    client, consumed_first = _client_for(module_name)
    async with client:
        async with client.stream("POST", LLM_URL, json={}) as response:
            async for _ in response.aiter_bytes():
                consumed_first.set()
                break
            await asyncio.sleep(0)

    assert captured == []


@pytest.mark.asyncio
@pytest.mark.parametrize("module_name", CLIENTS)
async def test_mid_stream_transport_error_reaches_the_host(captured, module_name):
    module = __import__(module_name)

    class Broken(module.AsyncByteStream):
        async def __aiter__(self):
            yield EVENT
            raise module.ReadError("connection dropped")

    transport = module.MockTransport(
        lambda request: module.Response(
            200, stream=Broken(), headers={"content-type": "text/event-stream"}
        )
    )
    async with module.AsyncClient(transport=transport) as client:
        async with client.stream("POST", LLM_URL, json={}) as response:
            with pytest.raises(module.ReadError):
                async for _ in response.aiter_bytes():
                    pass
