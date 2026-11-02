"""Abandoned streamed responses reach the API with ``raw_request.stream_incomplete``.

Mock-transport tests (the server is not booted): a chunked mock stream goes through
the patched httpx client, the real ``CoolhandClient`` builds the interaction, and the
outgoing POST body is asserted exactly as the backend will receive it.
"""

import json

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
