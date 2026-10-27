"""Streaming capture must not change the host's stream or duplicate the body.

httpx builds aiter_lines on aiter_text on aiter_bytes on aiter_raw, each looked up
on the response instance. The interceptor wraps all four, so a wrapper that forced a
chunk size stalled small SSE events behind httpx's buffer, and one that captured at
every layer recorded each chunk several times.
"""

import asyncio
import importlib.util
import json

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


# --- sync client -----------------------------------------------------------------

ANTHROPIC_EVENTS = [
    ("message_start", {"message": {"id": "msg_01ABC", "usage": {"input_tokens": 12}}}),
    ("content_block_delta", {"delta": {"type": "text_delta", "text": "Hello"}}),
    ("content_block_delta", {"delta": {"type": "text_delta", "text": " world"}}),
    (
        "message_delta",
        {"delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 7}},
    ),
    ("message_stop", {}),
]
ANTHROPIC_SSE = "".join(
    f"event: {name}\ndata: {json.dumps({'type': name, **data})}\n\n"
    for name, data in ANTHROPIC_EVENTS
).encode()


def _sync_client(
    module_name, chunks, content_type="text/event-stream", fail_after=None
):
    """A sync client streaming ``chunks``; ``produced`` counts those yielded."""
    module = __import__(module_name)
    produced: list[int] = []

    class Stream(module.SyncByteStream):
        def __iter__(self):
            for chunk in chunks:
                produced.append(1)
                yield chunk
            if fail_after is not None:
                raise module.ReadError("connection dropped")

    transport = module.MockTransport(
        lambda request: module.Response(
            200, stream=Stream(), headers={"content-type": content_type}
        )
    )
    return module.Client(transport=transport), produced


def _sse_chunks(size=7):
    return [ANTHROPIC_SSE[i : i + size] for i in range(0, len(ANTHROPIC_SSE), size)]


@pytest.mark.parametrize("module_name", CLIENTS)
@pytest.mark.parametrize("method", ["iter_bytes", "iter_text", "iter_raw"])
def test_sync_iter_variant_is_captured_once_with_full_body(
    captured, module_name, method
):
    client, _ = _sync_client(module_name, _sse_chunks())
    with client:
        with client.stream("POST", LLM_URL, json={}) as response:
            for _ in getattr(response, method)():
                pass

    assert len(captured) == 1
    _, res, err = captured[0]
    assert err is None
    assert res["status_code"] == 200
    assert res["is_streaming"] is True
    assert res["body_truncated"] is False
    assert res["body"] == ANTHROPIC_SSE.decode()
    assert res["duration"] >= 0


@pytest.mark.parametrize("module_name", CLIENTS)
def test_sync_iter_lines_is_captured_once_without_layer_duplicates(
    captured, module_name
):
    client, _ = _sync_client(module_name, _sse_chunks())
    with client:
        with client.stream("POST", LLM_URL, json={}) as response:
            lines = [line for line in response.iter_lines() if line]

    assert len(lines) == len(ANTHROPIC_EVENTS) * 2
    assert len(captured) == 1
    body = captured[0][1]["body"]
    # One entry per event, not one per wrapper layer (lines -> text -> bytes -> raw).
    assert body.count("data: ") == len(ANTHROPIC_EVENTS)
    assert body.count("event: ") == len(ANTHROPIC_EVENTS)
    data = [
        json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")
    ]
    assert [event["type"] for event in data] == [name for name, _ in ANTHROPIC_EVENTS]
    assert data[3]["usage"] == {"output_tokens": 7}


@pytest.mark.parametrize("module_name", CLIENTS)
def test_sync_first_chunk_is_delivered_before_the_rest_is_read(captured, module_name):
    client, produced = _sync_client(module_name, [EVENT] * EVENTS)
    with client:
        with client.stream("POST", LLM_URL, json={}) as response:
            iterator = response.iter_bytes()
            first = next(iterator)
            # Nothing buffered ahead, nothing reported before the stream ends.
            assert first == EVENT
            assert len(produced) == 1
            assert captured == []
            assert b"".join(iterator) == EVENT * (EVENTS - 1)

    assert len(captured) == 1
    assert len(captured[0][1]["body"]) == len(EVENT) * EVENTS


@pytest.mark.parametrize("module_name", CLIENTS)
def test_sync_chunk_size_is_forwarded_untouched(captured, module_name):
    client, _ = _sync_client(module_name, [b"x" * 100])
    with client:
        with client.stream("POST", LLM_URL, json={}) as response:
            sizes = [len(c) for c in response.iter_bytes(chunk_size=10)]

    assert sizes == [10] * 10
    assert len(captured) == 1
    assert captured[0][1]["body"] == "x" * 100


@pytest.mark.parametrize("module_name", CLIENTS)
def test_sync_read_after_streaming_send_is_captured_once(captured, module_name):
    client, _ = _sync_client(module_name, _sse_chunks())
    with client:
        response = client.send(client.build_request("POST", LLM_URL), stream=True)
        response.read()
        response.close()

    assert len(captured) == 1
    assert captured[0][1]["body"] == ANTHROPIC_SSE.decode()
    assert captured[0][1]["is_streaming"] is True


@pytest.mark.parametrize("module_name", CLIENTS)
def test_sync_empty_stream_reports_nothing(captured, module_name):
    client, _ = _sync_client(module_name, [])
    with client:
        with client.stream("POST", LLM_URL, json={}) as response:
            assert list(response.iter_bytes()) == []

    assert captured == []


@pytest.mark.parametrize("module_name", CLIENTS)
def test_sync_stream_over_the_cap_is_truncated(captured, module_name):
    interceptor.set_max_body_bytes(100)
    client, _ = _sync_client(module_name, [EVENT] * EVENTS)
    with client:
        with client.stream("POST", LLM_URL, json={}) as response:
            received = sum(len(c) for c in response.iter_bytes())

    assert received == len(EVENT) * EVENTS
    assert len(captured) == 1
    res = captured[0][1]
    assert res["body_truncated"] is True
    assert res["body"].startswith("data: xxx")
    assert res["body"].endswith(
        f"[truncated: {len(EVENT) * EVENTS} bytes, kept first 100]"
    )


@pytest.mark.parametrize("module_name", CLIENTS)
def test_sync_non_streaming_response_is_unchanged(captured, module_name):
    module = __import__(module_name)
    transport = module.MockTransport(
        lambda request: module.Response(200, json={"id": "chatcmpl-1"})
    )
    with module.Client(transport=transport) as client:
        response = client.post(LLM_URL, json={})
        assert response.json() == {"id": "chatcmpl-1"}

    assert len(captured) == 1
    res = captured[0][1]
    assert res["is_streaming"] is False
    assert "body_truncated" not in res
    assert json.loads(res["body"]) == {"id": "chatcmpl-1"}


@pytest.mark.parametrize("module_name", CLIENTS)
def test_sync_event_stream_read_without_streaming_is_captured_once(
    captured, module_name
):
    """client.post() reads the body inside send(); iterating it later adds nothing."""
    module = __import__(module_name)
    transport = module.MockTransport(
        lambda request: module.Response(
            200, content=ANTHROPIC_SSE, headers={"content-type": "text/event-stream"}
        )
    )
    with module.Client(transport=transport) as client:
        response = client.post(LLM_URL, json={})
        assert list(response.iter_lines())

    assert len(captured) == 1
    res = captured[0][1]
    assert res["is_streaming"] is True
    assert res["body"] == ANTHROPIC_SSE.decode()


@pytest.mark.parametrize("module_name", CLIENTS)
@pytest.mark.parametrize("method", ["iter_bytes", "iter_lines"])
def test_sync_breaking_out_early_reports_no_partial_body(captured, module_name, method):
    client, _ = _sync_client(module_name, _sse_chunks())
    with client:
        with client.stream("POST", LLM_URL, json={}) as response:
            iterator = getattr(response, method)()
            next(iterator)
            iterator.close()

    assert captured == []


@pytest.mark.parametrize("module_name", CLIENTS)
def test_sync_closing_the_response_early_reports_nothing(captured, module_name):
    client, _ = _sync_client(module_name, _sse_chunks())
    with client:
        response = client.send(client.build_request("POST", LLM_URL), stream=True)
        next(response.iter_bytes())
        response.close()

    assert captured == []


@pytest.mark.parametrize("module_name", CLIENTS)
def test_sync_mid_stream_transport_error_reaches_the_host(captured, module_name):
    module = __import__(module_name)
    client, _ = _sync_client(module_name, [EVENT], fail_after=True)
    with client:
        with client.stream("POST", LLM_URL, json={}) as response:
            with pytest.raises(module.ReadError):
                for _ in response.iter_bytes():
                    pass

    assert captured == []


@pytest.mark.parametrize("module_name", CLIENTS)
def test_sync_handler_exception_does_not_break_the_stream(module_name):
    interceptor.unpatch()
    interceptor.set_exclude_api_patterns(None)
    calls = []

    def exploding(req, res, err):
        calls.append(res)
        raise RuntimeError("handler bug")

    interceptor.set_handler(exploding)
    assert interceptor.patch() is True
    try:
        client, _ = _sync_client(module_name, _sse_chunks())
        with client:
            with client.stream("POST", LLM_URL, json={}) as response:
                body = b"".join(response.iter_bytes())
    finally:
        interceptor.unpatch()
        interceptor.set_handler(None)

    assert body == ANTHROPIC_SSE
    assert len(calls) == 1


@pytest.mark.parametrize("module_name", CLIENTS)
def test_sync_capture_bug_does_not_break_the_stream(captured, module_name, monkeypatch):
    def broken(self, text):
        raise RuntimeError("capture bug")

    monkeypatch.setattr(interceptor._BoundedChunks, "append", broken)
    client, _ = _sync_client(module_name, _sse_chunks())
    with client:
        with client.stream("POST", LLM_URL, json={}) as response:
            body = b"".join(response.iter_bytes())

    assert body == ANTHROPIC_SSE
    assert captured == []


@pytest.mark.parametrize("module_name", CLIENTS)
@pytest.mark.parametrize("method", ["iter_bytes", "iter_raw"])
def test_sync_multibyte_character_split_across_chunks_is_captured_intact(
    captured, module_name, method
):
    body = "data: café ☕ 日本語\n\n".encode()
    chunks = [body[i : i + 1] for i in range(len(body))]
    client, _ = _sync_client(module_name, chunks)
    with client:
        with client.stream("POST", LLM_URL, json={}) as response:
            for _ in getattr(response, method)():
                pass

    assert len(captured) == 1
    assert captured[0][1]["body"] == body.decode()
