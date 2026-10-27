#!/usr/bin/env python3
# Usage: cd <repo-root> && uv run python examples/streaming_capture.py
"""
Offline check that sync streamed responses are captured with their token metadata.

No keys and no network: a sync httpx client streams Anthropic- and OpenAI-shaped SSE
bodies from a mock transport through the real interceptor. A sync stream used to be
logged with ``is_streaming: false`` and the placeholder body ``"[streaming]"``, so the
server could extract no tokens, output or message id from it. This exits 1 if the
captured body does not carry them, 0 otherwise.
"""

import json

import _live_check

NAME = "streaming_capture"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
OPENAI_URL = "https://api.openai.com/v1/chat/completions"

import httpx  # noqa: E402

from coolhand import httpx_interceptor  # noqa: E402


def sse(*events: tuple[str, dict]) -> bytes:
    return "".join(
        f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in events
    ).encode()


ANTHROPIC_SSE = sse(
    (
        "message_start",
        {
            "type": "message_start",
            "message": {"id": "msg_01Example", "usage": {"input_tokens": 12}},
        },
    ),
    (
        "content_block_delta",
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "ok"}},
    ),
    (
        "message_delta",
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "usage": {"output_tokens": 7},
        },
    ),
    ("message_stop", {"type": "message_stop"}),
)

OPENAI_SSE = (
    sse(
        ("", {"id": "chatcmpl-Example", "choices": [{"delta": {"content": "ok"}}]}),
        (
            "",
            {
                "id": "chatcmpl-Example",
                "choices": [],
                "usage": {
                    "prompt_tokens": 9,
                    "completion_tokens": 3,
                    "total_tokens": 12,
                },
            },
        ),
    )
    + b"data: [DONE]\n\n"
)

# (name, url, body, reader, stop_at_done). Readers cover the iterator layers SDKs use:
# the Anthropic SDK reads bytes, the OpenAI SDK reads lines and stops at ``[DONE]``
# without exhausting the stream, which is the case that used to capture nothing.
CASES = [
    ("anthropic via iter_bytes", ANTHROPIC_URL, ANTHROPIC_SSE, "iter_bytes", False),
    ("anthropic via iter_lines", ANTHROPIC_URL, ANTHROPIC_SSE, "iter_lines", False),
    ("openai via iter_lines", OPENAI_URL, OPENAI_SSE, "iter_lines", False),
    ("openai via iter_bytes", OPENAI_URL, OPENAI_SSE, "iter_bytes", False),
    ("openai stopping at [DONE]", OPENAI_URL, OPENAI_SSE, "iter_lines", True),
]


def check_anthropic(res: dict) -> None:
    events = _live_check.sse_events(res["body"])
    started = next(e["message"] for e in events if e["type"] == "message_start")
    ended = next(e for e in events if e["type"] == "message_delta")
    assert started["id"] == "msg_01Example", started
    assert started["usage"]["input_tokens"] == 12, started
    assert ended["usage"]["output_tokens"] == 7, ended


def check_openai(res: dict) -> None:
    events = _live_check.sse_events(res["body"])
    usage = next(e["usage"] for e in events if e.get("usage"))
    assert {e["id"] for e in events} == {"chatcmpl-Example"}, events
    assert usage["total_tokens"] == 12, usage


def run_case(name: str, url: str, body: bytes, reader: str, stop_at_done: bool) -> None:
    class Chunked(httpx.SyncByteStream):
        """Small chunks, like a network, so a reader can stop before the last one."""

        def __iter__(self):
            for i in range(0, len(body), 40):
                yield body[i : i + 40]

    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200, stream=Chunked(), headers={"content-type": "text/event-stream"}
        )
    )
    httpx_interceptor.set_handler(lambda *_: None)
    records = _live_check.capture_responses()
    # A real stream (stream=True), consumed incrementally like an SDK does.
    with httpx.Client(transport=transport) as client:
        with client.stream("POST", url, json={}) as response:
            for item in getattr(response, reader)():
                text = item if isinstance(item, str) else item.decode()
                if stop_at_done and "[DONE]" in text:
                    break
    try:
        res = _live_check.single_streamed_capture(name, records)
        (check_anthropic if "anthropic" in url else check_openai)(res)
    except AssertionError as e:
        _live_check.fail(name, f"token metadata missing from captured body: {e}")
    print(f"PASS {name}: id and token counts captured")


def main() -> None:
    # The interceptor alone: no Coolhand client, no network, no delivery.
    httpx_interceptor.set_exclude_api_patterns(None)
    httpx_interceptor.set_handler(lambda *_: None)
    if not httpx_interceptor.patch():
        _live_check.fail(NAME, "could not patch httpx")
    try:
        for case in CASES:
            run_case(*case)
    finally:
        httpx_interceptor.unpatch()
        httpx_interceptor.set_handler(None)
    print(f"PASS {NAME}: {len(CASES)} sync streams captured with token metadata")


if __name__ == "__main__":
    main()
