"""Streaming capture must not change the host's stream or duplicate the body.

httpx builds aiter_lines on aiter_text on aiter_bytes on aiter_raw, each looked up
on the response instance. The interceptor wraps all four, so a wrapper that forced a
chunk size stalled small SSE events behind httpx's buffer, and one that captured at
every layer recorded each chunk several times.

A stream is complete only if the wrapped iterator was exhausted. Any other end that
captured data is reported once with ``stream_incomplete: True``.
"""

import asyncio
import importlib.util
import json
import threading
import time
from types import SimpleNamespace

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
async def test_breaking_out_early_reports_the_partial_body_as_incomplete(
    captured, module_name
):
    """``break`` leaves the async generator for the event loop to finalise, so the
    record arrives a moment after the loop exits, not at the ``break`` itself."""
    client, consumed_first = _client_for(module_name)
    async with client:
        async with client.stream("POST", LLM_URL, json={}) as response:
            async for _ in response.aiter_bytes():
                consumed_first.set()
                break
            await asyncio.sleep(0.01)

    assert len(captured) == 1
    res = captured[0][1]
    assert res["stream_incomplete"] is True
    assert res["is_streaming"] is True
    assert res["body"] == EVENT.decode()


@pytest.mark.asyncio
@pytest.mark.parametrize("module_name", CLIENTS)
async def test_explicit_aclose_reports_the_partial_body_once(captured, module_name):
    client, consumed_first = _client_for(module_name)
    async with client:
        async with client.stream("POST", LLM_URL, json={}) as response:
            agen = response.aiter_bytes()
            await agen.__anext__()
            consumed_first.set()
            await agen.aclose()
            assert len(captured) == 1
            await agen.aclose()

    assert len(captured) == 1
    assert captured[0][1]["stream_incomplete"] is True
    assert captured[0][1]["body"] == EVENT.decode()


@pytest.mark.asyncio
@pytest.mark.parametrize("module_name", CLIENTS)
async def test_cancelled_reader_reports_the_partial_body_as_incomplete(
    captured, module_name
):
    module = __import__(module_name)
    stalled = asyncio.Event()

    class Hanging(module.AsyncByteStream):
        async def __aiter__(self):
            yield EVENT
            stalled.set()
            await asyncio.sleep(30)
            yield EVENT

    transport = module.MockTransport(
        lambda request: module.Response(
            200, stream=Hanging(), headers={"content-type": "text/event-stream"}
        )
    )

    async def read(client):
        async with client.stream("POST", LLM_URL, json={}) as response:
            async for _ in response.aiter_lines():
                pass

    async with module.AsyncClient(transport=transport) as client:
        task = asyncio.create_task(read(client))
        await asyncio.wait_for(stalled.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert len(captured) == 1
    res = captured[0][1]
    assert res["stream_incomplete"] is True
    assert res["body"] == EVENT.decode().rstrip("\n") + "\n"


@pytest.mark.parametrize("module_name", CLIENTS)
def test_generator_finalised_by_loop_shutdown_reports_the_partial_body(
    captured, module_name
):
    """A generator still suspended when ``asyncio.run`` ends is closed by the loop's
    ``shutdown_asyncgens``; that finalisation is what sends the record."""
    holder = []

    async def main():
        client, consumed_first = _client_for(module_name)
        async with client:
            async with client.stream("POST", LLM_URL, json={}) as response:
                agen = response.aiter_bytes()
                holder.append(agen)  # keeps it alive: only the loop can close it
                await agen.__anext__()
                consumed_first.set()
        assert captured == []

    asyncio.run(main())

    assert len(captured) == 1
    res = captured[0][1]
    assert res["stream_incomplete"] is True
    assert res["body"] == EVENT.decode()


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

    assert len(captured) == 1
    res = captured[0][1]
    assert res["stream_incomplete"] is True
    assert res["body"] == EVENT.decode()


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
def test_sync_breaking_out_early_reports_the_partial_body_as_incomplete(
    captured, module_name, method
):
    client, produced = _sync_client(module_name, _sse_chunks())
    with client:
        with client.stream("POST", LLM_URL, json={}) as response:
            iterator = getattr(response, method)()
            first = next(iterator)
            assert captured == []
            iterator.close()

    assert len(captured) == 1
    res = captured[0][1]
    assert res["stream_incomplete"] is True
    assert res["is_streaming"] is True
    assert res["body_truncated"] is False
    assert res["body"] == ANTHROPIC_SSE[: len(res["body"])].decode()
    assert 0 < len(res["body"]) < len(ANTHROPIC_SSE)
    assert len(produced) < len(_sse_chunks())
    assert first


@pytest.mark.parametrize("module_name", CLIENTS)
def test_sync_closing_the_response_and_the_iterator_reports_the_partial_body_once(
    captured, module_name
):
    client, _ = _sync_client(module_name, _sse_chunks())
    with client:
        response = client.send(client.build_request("POST", LLM_URL), stream=True)
        iterator = response.iter_bytes()
        next(iterator)
        response.close()
        iterator.close()
        del iterator

    assert len(captured) == 1
    res = captured[0][1]
    assert res["stream_incomplete"] is True
    assert res["body"] == _sse_chunks()[0].decode()


@pytest.mark.parametrize("module_name", CLIENTS)
def test_sync_mid_stream_transport_error_reaches_the_host_and_reports_incomplete(
    captured, module_name
):
    module = __import__(module_name)
    client, _ = _sync_client(module_name, [EVENT], fail_after=True)
    with client:
        with client.stream("POST", LLM_URL, json={}) as response:
            with pytest.raises(module.ReadError):
                for _ in response.iter_bytes():
                    pass

    assert len(captured) == 1
    res = captured[0][1]
    assert res["stream_incomplete"] is True
    assert res["body"] == EVENT.decode()


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


# --- consumers that stop at the [DONE] sentinel ------------------------------------
#
# The OpenAI SDK breaks out of its SSE loop at ``data: [DONE]`` and never exhausts the
# underlying iterator. That is an abandoned stream like any other: one rule, the
# iterator was not exhausted, so the record carries ``stream_incomplete``.

OPENAI_SSE = (
    b'data: {"id": "chatcmpl-1", "choices": [{"delta": {"content": "ok"}}]}\n\n'
    b'data: {"id": "chatcmpl-1", "choices": [], "usage": {"total_tokens": 12}}\n\n'
    b"data: [DONE]\n\n"
)


def _openai_chunks(size=13):
    return [OPENAI_SSE[i : i + size] for i in range(0, len(OPENAI_SSE), size)]


@pytest.mark.parametrize("module_name", CLIENTS)
@pytest.mark.parametrize("method", ["iter_lines", "iter_bytes", "iter_text"])
def test_sync_stopping_at_done_reports_the_body_once_as_incomplete(
    captured, module_name, method
):
    client, _ = _sync_client(module_name, _openai_chunks())
    seen = ""
    with client:
        with client.stream("POST", LLM_URL, json={}) as response:
            for item in getattr(response, method)():
                seen += item if isinstance(item, str) else item.decode()
                if "[DONE]" in seen:
                    break

    assert len(captured) == 1
    res = captured[0][1]
    assert res["is_streaming"] is True
    assert res["body_truncated"] is False
    assert res["stream_incomplete"] is True
    assert '"total_tokens": 12' in res["body"]
    assert '"id": "chatcmpl-1"' in res["body"]
    assert "data: [DONE]" in res["body"]


@pytest.mark.parametrize("module_name", CLIENTS)
def test_sync_exhausting_a_done_terminated_stream_is_complete(captured, module_name):
    client, _ = _sync_client(module_name, _openai_chunks())
    with client:
        with client.stream("POST", LLM_URL, json={}) as response:
            for _ in response.iter_bytes():
                pass

    assert len(captured) == 1
    res = captured[0][1]
    assert "stream_incomplete" not in res
    assert res["body"] == OPENAI_SSE.decode()


@pytest.mark.parametrize("module_name", CLIENTS)
def test_sync_stopping_at_done_of_an_over_cap_stream_is_truncated_and_incomplete(
    captured, module_name, monkeypatch
):
    monkeypatch.setattr(interceptor, "_max_body_bytes", 40)
    client, _ = _sync_client(module_name, _openai_chunks())
    with client:
        with client.stream("POST", LLM_URL, json={}) as response:
            for line in response.iter_lines():
                if "[DONE]" in line:
                    break

    assert len(captured) == 1
    res = captured[0][1]
    assert res["body_truncated"] is True
    assert res["stream_incomplete"] is True
    assert "[truncated: " in res["body"]


@pytest.mark.parametrize("module_name", CLIENTS)
def test_sync_over_cap_stream_read_to_the_end_is_truncated_but_not_incomplete(
    captured, module_name, monkeypatch
):
    monkeypatch.setattr(interceptor, "_max_body_bytes", 40)
    client, _ = _sync_client(module_name, _openai_chunks())
    with client:
        with client.stream("POST", LLM_URL, json={}) as response:
            for _ in response.iter_lines():
                pass

    assert len(captured) == 1
    res = captured[0][1]
    assert res["body_truncated"] is True
    assert "stream_incomplete" not in res


@pytest.mark.parametrize("module_name", CLIENTS)
def test_sync_stopping_before_done_reports_the_partial_body_as_incomplete(
    captured, module_name
):
    client, _ = _sync_client(module_name, _openai_chunks())
    with client:
        with client.stream("POST", LLM_URL, json={}) as response:
            for line in response.iter_lines():
                if "total_tokens" in line:
                    break

    assert len(captured) == 1
    res = captured[0][1]
    assert res["stream_incomplete"] is True
    assert '"total_tokens": 12' in res["body"]
    assert "[DONE]" not in res["body"]


@pytest.mark.parametrize("module_name", CLIENTS)
def test_sync_error_after_done_is_still_incomplete(captured, module_name):
    """The iterator never finished, so there is one rule: not exhausted, so marked."""
    client, _ = _sync_client(module_name, _openai_chunks(), fail_after=True)
    with client:
        with pytest.raises(Exception, match="connection dropped"):
            with client.stream("POST", LLM_URL, json={}) as response:
                for _ in response.iter_lines():
                    pass

    assert len(captured) == 1
    assert captured[0][1]["stream_incomplete"] is True
    assert captured[0][1]["body"].rstrip().endswith("data: [DONE]")


@pytest.mark.parametrize("module_name", CLIENTS)
def test_sync_abandoned_multibyte_stream_drops_a_split_trailing_character(
    captured, module_name
):
    body = "data: café ☕\n\n".encode()
    cut = body.index("☕".encode()) + 1  # one byte into a three-byte character
    client, _ = _sync_client(module_name, [body[:cut], body[cut:]])
    with client:
        with client.stream("POST", LLM_URL, json={}) as response:
            next(response.iter_bytes())

    assert len(captured) == 1
    assert captured[0][1]["body"] == "data: café "
    assert captured[0][1]["stream_incomplete"] is True


def _bare_capture(text="data: x\n\n"):
    capture = interceptor._StreamCapture(
        SimpleNamespace(status_code=200, headers={}), {}, time.time()
    )
    capture.add(text.encode())
    return capture


def test_a_later_outcome_does_not_send_a_second_record_or_change_the_first(captured):
    capture = _bare_capture()

    capture.send(incomplete=True)
    capture.send()
    capture.send(incomplete=True)

    assert len(captured) == 1
    assert captured[0][1]["stream_incomplete"] is True


def test_concurrent_outcomes_send_exactly_one_record(captured):
    capture = _bare_capture()
    barrier = threading.Barrier(16)

    def race(incomplete):
        barrier.wait()
        capture.send(incomplete=incomplete)

    threads = [threading.Thread(target=race, args=(n % 2 == 0,)) for n in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(captured) == 1


@pytest.mark.parametrize("module_name", CLIENTS)
def test_sync_closing_an_exhausted_stream_again_sends_nothing_more(
    captured, module_name
):
    client, _ = _sync_client(module_name, _sse_chunks())
    with client:
        with client.stream("POST", LLM_URL, json={}) as response:
            iterator = response.iter_bytes()
            for _ in iterator:
                pass
            iterator.close()
        response.close()

    assert len(captured) == 1
    assert "stream_incomplete" not in captured[0][1]


@pytest.mark.parametrize("module_name", CLIENTS)
def test_sync_buffer_is_released_after_the_report(captured, module_name, monkeypatch):
    captures = []
    original = interceptor._StreamCapture.__init__

    def spy(self, *args, **kwargs):
        original(self, *args, **kwargs)
        captures.append(self)

    monkeypatch.setattr(interceptor._StreamCapture, "__init__", spy)
    client, _ = _sync_client(module_name, _sse_chunks())
    with client:
        with client.stream("POST", LLM_URL, json={}) as response:
            next(response.iter_bytes())

    assert len(captured) == 1
    assert not captures[0]._chunks


@pytest.mark.asyncio
@pytest.mark.parametrize("module_name", CLIENTS)
async def test_async_stopping_at_done_reports_the_body_once_as_incomplete(
    captured, module_name
):
    module = __import__(module_name)

    class Stream(module.AsyncByteStream):
        async def __aiter__(self):
            for chunk in _openai_chunks():
                yield chunk

    transport = module.MockTransport(
        lambda request: module.Response(
            200, stream=Stream(), headers={"content-type": "text/event-stream"}
        )
    )
    async with module.AsyncClient(transport=transport) as client:
        async with client.stream("POST", LLM_URL, json={}) as response:
            async for line in response.aiter_lines():
                if "[DONE]" in line:
                    break

    # An abandoned async generator is finalised by the event loop, not at ``break``.
    await asyncio.sleep(0.01)
    assert len(captured) == 1
    res = captured[0][1]
    assert res["stream_incomplete"] is True
    assert '"total_tokens": 12' in res["body"]
    assert res["body"].rstrip().endswith("data: [DONE]")


@pytest.mark.asyncio
@pytest.mark.parametrize("module_name", CLIENTS)
async def test_async_exhausted_stream_is_complete(captured, module_name):
    module = __import__(module_name)

    class Stream(module.AsyncByteStream):
        async def __aiter__(self):
            for chunk in _openai_chunks():
                yield chunk

    transport = module.MockTransport(
        lambda request: module.Response(
            200, stream=Stream(), headers={"content-type": "text/event-stream"}
        )
    )
    async with module.AsyncClient(transport=transport) as client:
        async with client.stream("POST", LLM_URL, json={}) as response:
            async for _ in response.aiter_bytes():
                pass

    await asyncio.sleep(0.01)
    assert len(captured) == 1
    assert "stream_incomplete" not in captured[0][1]
    assert captured[0][1]["body"] == OPENAI_SSE.decode()


@pytest.mark.asyncio
@pytest.mark.parametrize("module_name", CLIENTS)
async def test_async_buffer_is_released_after_the_report(
    captured, module_name, monkeypatch
):
    captures = []
    original = interceptor._StreamCapture.__init__

    def spy(self, *args, **kwargs):
        original(self, *args, **kwargs)
        captures.append(self)

    monkeypatch.setattr(interceptor._StreamCapture, "__init__", spy)
    client, consumed_first = _client_for(module_name)
    async with client:
        async with client.stream("POST", LLM_URL, json={}) as response:
            agen = response.aiter_bytes()
            await agen.__anext__()
            consumed_first.set()
            await agen.aclose()

    assert len(captured) == 1
    assert not captures[0]._chunks


@pytest.mark.asyncio
@pytest.mark.parametrize("module_name", CLIENTS)
async def test_async_abandoned_over_cap_stream_is_truncated_and_incomplete(
    captured, module_name, monkeypatch
):
    monkeypatch.setattr(interceptor, "_max_body_bytes", 40)
    module = __import__(module_name)

    class Stream(module.AsyncByteStream):
        async def __aiter__(self):
            for chunk in _openai_chunks():
                yield chunk

    transport = module.MockTransport(
        lambda request: module.Response(
            200, stream=Stream(), headers={"content-type": "text/event-stream"}
        )
    )
    async with module.AsyncClient(transport=transport) as client:
        async with client.stream("POST", LLM_URL, json={}) as response:
            agen = response.aiter_lines()
            async for line in agen:
                if "[DONE]" in line:
                    break
            await agen.aclose()

    assert len(captured) == 1
    res = captured[0][1]
    assert res["body_truncated"] is True
    assert res["stream_incomplete"] is True
