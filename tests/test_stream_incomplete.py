"""Abandoned streamed responses reach the API with ``raw_request.stream_incomplete``.

Mock-transport tests (the server is not booted): a chunked mock stream goes through
the patched httpx client, the real ``CoolhandClient`` builds the interaction, and the
outgoing POST body is asserted exactly as the backend will receive it.
"""

import gc
import json
import threading
import time

import httpx
import pytest

from coolhand import httpx_interceptor
from coolhand.client import CoolhandClient

LLM_URL = "https://api.openai.com/v1/chat/completions"
SSE = (
    b'data: {"id": "chatcmpl-1", "choices": [{"delta": {"content": "ok"}}]}\n\n'
    b'data: {"id": "chatcmpl-1", "choices": [], "usage": {"total_tokens": 12}}\n\n'
    b"data: [DONE]\n\n"
)
CHUNKS = [SSE[i : i + 13] for i in range(0, len(SSE), 13)]


class _Stream(httpx.SyncByteStream):
    def __iter__(self):
        yield from CHUNKS


def _http_client():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200, stream=_Stream(), headers={"content-type": "text/event-stream"}
        )
    )
    return httpx.Client(transport=transport)


@pytest.fixture
def sdk(reset_global_instance, mock_urlopen):
    client = CoolhandClient(api_key="k" * 16, auto_submit=False, silent=True)
    httpx_interceptor.set_exclude_api_patterns(None)
    httpx_interceptor.set_handler(client.log_interaction)
    assert httpx_interceptor.patch() is True
    yield client
    httpx_interceptor.unpatch()
    httpx_interceptor.set_handler(None)


def _posted(client, mock_urlopen):
    """The ``raw_request`` of every record the client would POST, in order."""
    with client._state_lock:
        queued, client._queue = client._queue, []
    bodies = []
    for interaction in queued:
        assert client._send_one(interaction) is True
    for call in mock_urlopen.call_args_list:
        request = call.args[0]
        assert request.full_url.endswith("/api/v2/llm_request_logs")
        bodies.append(json.loads(request.data)["llm_request_log"]["raw_request"])
    return bodies


def test_stream_read_to_the_end_posts_no_marker(sdk, mock_urlopen):
    with _http_client() as http:
        with http.stream("POST", LLM_URL, json={}) as response:
            for _ in response.iter_bytes():
                pass

    (raw,) = _posted(sdk, mock_urlopen)
    assert "stream_incomplete" not in raw
    assert raw["is_streaming"] is True
    assert raw["response_body"] == SSE.decode()


def test_breaking_mid_stream_posts_the_partial_body_with_the_marker(sdk, mock_urlopen):
    with _http_client() as http:
        with http.stream("POST", LLM_URL, json={}) as response:
            for _ in response.iter_bytes():
                break

    (raw,) = _posted(sdk, mock_urlopen)
    assert raw["stream_incomplete"] is True
    assert raw["is_streaming"] is True
    assert raw["response_body"] == CHUNKS[0].decode()


def test_stopping_at_done_without_draining_posts_the_marker(sdk, mock_urlopen):
    with _http_client() as http:
        with http.stream("POST", LLM_URL, json={}) as response:
            for line in response.iter_lines():
                if line == "data: [DONE]":
                    break

    (raw,) = _posted(sdk, mock_urlopen)
    assert raw["stream_incomplete"] is True
    assert raw["response_body"].rstrip().endswith("data: [DONE]")


def test_dropped_connection_posts_the_partial_body_with_the_marker(sdk, mock_urlopen):
    class Dropped(httpx.SyncByteStream):
        def __iter__(self):
            yield CHUNKS[0]
            yield CHUNKS[1]
            raise httpx.ReadError("connection dropped")

    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200, stream=Dropped(), headers={"content-type": "text/event-stream"}
        )
    )
    with httpx.Client(transport=transport) as http:
        with http.stream("POST", LLM_URL, json={}) as response:
            with pytest.raises(httpx.ReadError):
                for _ in response.iter_bytes():
                    pass

    (raw,) = _posted(sdk, mock_urlopen)
    assert raw["stream_incomplete"] is True
    assert raw["response_body"] == (CHUNKS[0] + CHUNKS[1]).decode()


def test_abandoned_stream_posts_exactly_one_record(sdk, mock_urlopen):
    with _http_client() as http:
        with http.stream("POST", LLM_URL, json={}) as response:
            iterator = response.iter_bytes()
            next(iterator)
            iterator.close()
            iterator.close()
        response.close()

    assert len(_posted(sdk, mock_urlopen)) == 1
    assert mock_urlopen.call_count == 1


def test_over_cap_abandoned_stream_marks_both_independently(sdk, mock_urlopen):
    sdk.config["max_body_bytes"] = 30
    httpx_interceptor.set_max_body_bytes(30)
    with _http_client() as http:
        with http.stream("POST", LLM_URL, json={}) as response:
            for _ in response.iter_bytes():
                pass
    seen = 0
    with _http_client() as http:
        with http.stream("POST", LLM_URL, json={}) as response:
            for chunk in response.iter_bytes():
                seen += len(chunk)
                if seen > 30:
                    break

    complete, abandoned = _posted(sdk, mock_urlopen)
    assert "[truncated: " in complete["response_body"]
    assert "stream_incomplete" not in complete
    assert abandoned["response_body"].count("[truncated: ") == 1
    assert abandoned["stream_incomplete"] is True


@pytest.mark.parametrize("value", [False, "true", 1, None])
def test_only_a_strict_true_is_forwarded(sdk, value):
    sdk.log_interaction(
        {"method": "POST", "url": LLM_URL},
        {"status_code": 200, "body": "x", "stream_incomplete": value},
    )
    assert "stream_incomplete" not in sdk._queue[0]


def test_client_forwards_stream_incomplete_true_beside_is_streaming(sdk):
    sdk.log_interaction(
        {"method": "POST", "url": LLM_URL},
        {
            "status_code": 200,
            "body": "x",
            "is_streaming": True,
            "stream_incomplete": True,
        },
    )
    assert sdk._queue[0]["stream_incomplete"] is True
    assert sdk._queue[0]["is_streaming"] is True


def test_error_without_a_response_never_carries_the_marker(sdk):
    sdk.log_interaction({"method": "POST", "url": LLM_URL}, None, "boom")
    assert "stream_incomplete" not in sdk._queue[0]


class _Cycle:
    """Holds a stream iterator in a reference cycle, as the OpenAI SDK's ``Stream``
    does, so only the cyclic garbage collector can finalize it."""

    def __init__(self, iterator):
        self.iterator = iterator
        self.me = self


def _abandon_in_a_cycle(http):
    """Start a stream and abandon it so that only ``gc.collect()`` can finalize it."""
    with http.stream("POST", LLM_URL, json={}) as response:
        iterator = response.iter_bytes()
        next(iterator)
        _Cycle(iterator)


def _run_with_timeout(target, timeout=5):
    """Run ``target`` on a thread; return it so the caller can see if it hung."""
    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(timeout)
    return thread


def test_abandoned_stream_finalized_while_flush_holds_worker_lock(sdk, mock_urlopen):
    """Issue #248: GC can finalize an abandoned stream on the very thread that is
    inside ``flush()`` holding the non-reentrant ``_worker_lock``. The finalizer
    must not take that lock (it used to, via the handler's own ``flush()``)."""
    seed = {"method": "POST", "url": LLM_URL}
    sdk.log_interaction(seed, {"status_code": 200, "body": "seed"})
    sdk.config["auto_submit"] = True

    real_ensure = sdk._ensure_worker_locked

    def ensure_worker_then_collect():
        # Runs inside flush() with _worker_lock held.
        gc.collect()
        return real_ensure()

    sdk._ensure_worker_locked = ensure_worker_then_collect

    gc.disable()  # nothing may finalize the stream before the forced collection
    try:
        with _http_client() as http:
            _abandon_in_a_cycle(http)
        flusher = _run_with_timeout(sdk.flush)
        hung = flusher.is_alive()
        if hung:
            sdk._worker_lock.release()  # unblock the stuck thread so it can exit
            flusher.join(5)
    finally:
        gc.enable()
        sdk._ensure_worker_locked = real_ensure

    assert not hung, "finalizing an abandoned stream deadlocked on _worker_lock"

    httpx_interceptor.flush_abandoned()
    sdk.shutdown()
    bodies = [json.loads(c.args[0].data) for c in mock_urlopen.call_args_list]
    raws = [b["llm_request_log"]["raw_request"] for b in bodies]
    streamed = [r for r in raws if r.get("is_streaming")]
    assert len(streamed) == 1
    assert streamed[0]["stream_incomplete"] is True
    assert streamed[0]["response_body"] == CHUNKS[0].decode()


def _reporter_threads():
    return [t for t in threading.enumerate() if t.name == "coolhand-abandoned-streams"]


def _wait_for(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


@pytest.fixture
def recording(reset_global_instance):
    """A bare handler that records which thread each report arrived on."""
    calls = []

    def handler(req, res, err):
        calls.append((threading.current_thread(), res))

    httpx_interceptor.set_exclude_api_patterns(None)
    httpx_interceptor.set_handler(handler)
    assert httpx_interceptor.patch() is True
    yield calls
    httpx_interceptor.unpatch()
    httpx_interceptor.set_handler(None)


def test_gc_finalized_stream_is_reported_once_from_the_reporter_thread(recording):
    gc.disable()
    try:
        with _http_client() as http:
            _abandon_in_a_cycle(http)
        assert recording == []
        gc.collect()
    finally:
        gc.enable()

    assert _wait_for(lambda: len(recording) == 1)
    ((thread, res),) = recording
    assert thread.name == "coolhand-abandoned-streams"
    assert thread is not threading.current_thread()
    assert res["stream_incomplete"] is True
    assert res["body"] == CHUNKS[0].decode()
    gc.collect()
    time.sleep(0.05)
    assert len(recording) == 1


def test_consumer_paths_still_report_synchronously_on_the_calling_thread(recording):
    with _http_client() as http:
        with http.stream("POST", LLM_URL, json={}) as response:
            for _ in response.iter_bytes():
                break
        assert len(recording) == 1  # no waiting: delivered before the loop returned

        with http.stream("POST", LLM_URL, json={}) as response:
            iterator = response.iter_bytes()
            next(iterator)
            iterator.close()
        assert len(recording) == 2

        with http.stream("POST", LLM_URL, json={}) as response:
            for _ in response.iter_bytes():
                pass
        assert len(recording) == 3

    assert {thread for thread, _ in recording} == {threading.current_thread()}


def test_gc_finalized_stream_closed_first_posts_exactly_one_record(recording):
    gc.disable()
    try:
        with _http_client() as http:
            with http.stream("POST", LLM_URL, json={}) as response:
                iterator = response.iter_bytes()
                next(iterator)
                cycle = _Cycle(iterator)
                iterator.close()
        del iterator, response, cycle
        gc.collect()
    finally:
        gc.enable()
    time.sleep(0.05)
    assert len(recording) == 1


def test_unpatch_and_set_handler_none_stop_the_reporter_and_deliver_nothing_after(
    reset_global_instance,
):
    calls = []
    httpx_interceptor.set_exclude_api_patterns(None)
    for teardown in (
        httpx_interceptor.unpatch,
        lambda: httpx_interceptor.set_handler(None),
    ):
        calls.clear()
        httpx_interceptor.set_handler(lambda req, res, err: calls.append(res))
        assert httpx_interceptor.patch() is True
        assert len(_reporter_threads()) == 1
        gc.disable()
        try:
            with _http_client() as http:
                _abandon_in_a_cycle(http)
            teardown()
            assert _reporter_threads() == []
            gc.collect()
        finally:
            gc.enable()
        time.sleep(0.05)
        assert calls == []
        httpx_interceptor.unpatch()
        httpx_interceptor.set_handler(None)
    assert _reporter_threads() == []


def test_unpatch_delivers_a_record_still_queued_for_the_reporter(
    reset_global_instance, monkeypatch
):
    monkeypatch.setattr(httpx_interceptor, "_REPORTER_JOIN_TIMEOUT", 0.05)
    calls = []
    release = threading.Event()

    def handler(req, res, err):
        calls.append(res)
        if len(calls) == 1:
            release.wait(5)  # hold the reporter thread inside the handler

    httpx_interceptor.set_exclude_api_patterns(None)
    httpx_interceptor.set_handler(handler)
    assert httpx_interceptor.patch() is True
    try:
        gc.disable()
        try:
            with _http_client() as http:
                _abandon_in_a_cycle(http)
                _abandon_in_a_cycle(http)
            gc.collect()
        finally:
            gc.enable()
        assert _wait_for(lambda: len(calls) == 1)
        httpx_interceptor.unpatch()  # drains the second record itself
        assert len(calls) == 2
    finally:
        release.set()
        httpx_interceptor.set_handler(None)
    assert _wait_for(lambda: _reporter_threads() == [])


def test_shutdown_delivers_abandoned_records_the_reporter_has_not_reported(
    sdk, mock_urlopen
):
    release = threading.Event()
    seen = []

    def stalled(req, res, err):
        seen.append(res)
        release.wait(5)
        sdk.log_interaction(req, res, err)

    httpx_interceptor.set_handler(stalled)
    try:
        gc.disable()
        try:
            with _http_client() as http:
                _abandon_in_a_cycle(http)
                _abandon_in_a_cycle(http)
            gc.collect()
        finally:
            gc.enable()
        assert _wait_for(lambda: len(seen) == 1)  # reporter is stuck on the first
        sdk.config["auto_submit"] = False
        httpx_interceptor.set_handler(sdk.log_interaction)
        sdk.shutdown()  # drains the second one on this thread
        assert len(sdk._queue) == 0
        assert mock_urlopen.call_count == 1
    finally:
        release.set()
