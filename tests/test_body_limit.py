"""Tests for coolhand._body_limit and its use in the client and interceptor."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from coolhand._body_limit import DEFAULT_MAX_BODY_BYTES, body_size, cap_body
from coolhand.client import CoolhandClient
from coolhand.httpx_interceptor import (
    _BoundedChunks,
    set_handler,
    set_max_body_bytes,
    unpatch,
)
from coolhand.httpx_interceptor import patch as patch_httpx

MARKER = "[truncated: "


class TestCapBody:
    def test_under_cap_unchanged(self):
        body = {"a": "b"}
        assert cap_body(body, 1000) is body
        assert cap_body("short", 1000) == "short"
        assert cap_body(b"short", 1000) == b"short"

    def test_none_body_unchanged(self):
        assert cap_body(None, 10) is None

    @pytest.mark.parametrize("cap", [None, 0])
    def test_disabled(self, cap):
        body = "x" * 5000
        assert cap_body(body, cap) is body

    def test_oversized_str(self):
        result = cap_body("x" * 100, 10)
        assert result == "x" * 10 + "[truncated: 100 bytes, kept first 10]"

    def test_oversized_bytes(self):
        result = cap_body(b"y" * 100, 10)
        assert result.startswith("y" * 10 + MARKER)

    def test_oversized_dict_becomes_string(self):
        body = {"data": "z" * 100}
        result = cap_body(body, 20)
        assert isinstance(result, str)
        assert result.startswith(json.dumps(body)[:20])
        assert f"[truncated: {body_size(body)} bytes" in result

    def test_multibyte_boundary_stays_valid_utf8(self):
        # Each "é" is 2 bytes; cap of 5 lands mid-character.
        result = cap_body("é" * 10, 5)
        kept = result.split(MARKER)[0]
        assert kept == "é" * 2
        kept.encode("utf-8")  # must not raise


class TestClientCap:
    def _client(self, **kwargs):
        return CoolhandClient(api_key="k" * 16, auto_submit=False, **kwargs)

    def test_default_cap(self, reset_global_instance):
        assert self._client().config["max_body_bytes"] == DEFAULT_MAX_BODY_BYTES

    def test_small_json_body_still_parsed(self, reset_global_instance):
        client = self._client()
        client.log_interaction(
            {
                "method": "POST",
                "url": "https://api.openai.com/v1/x",
                "body": '{"a": 1}',
            },
            {"status_code": 200, "body": '{"b": 2}'},
        )
        item = client._queue[0]
        assert item["request_body"] == {"a": 1}
        assert item["response_body"] == {"b": 2}

    def test_oversized_bodies_truncated(self, reset_global_instance):
        client = self._client(max_body_bytes=100)
        big = json.dumps({"image": "A" * 10_000})
        client.log_interaction(
            {"method": "POST", "url": "https://api.openai.com/v1/x", "body": big},
            {"status_code": 200, "body": big.encode()},
        )
        item = client._queue[0]
        for key in ("request_body", "response_body"):
            assert isinstance(item[key], str)
            assert item[key].startswith(big[:100])
            assert f"[truncated: {len(big)} bytes, kept first 100]" in item[key]

    def test_cap_disabled(self, reset_global_instance):
        client = self._client(max_body_bytes=None)
        big = "A" * 10_000
        client.log_interaction(
            {"method": "POST", "url": "https://api.openai.com/v1/x", "body": big},
            {"status_code": 200, "body": big},
        )
        assert client._queue[0]["request_body"] == big

    def test_redaction_survives_truncation(self, reset_global_instance):
        """Credentials in an oversized JSON body must not leak via the kept prefix."""
        client = self._client(max_body_bytes=2000)
        body = json.dumps(
            {
                "data_sources": [
                    {
                        "type": "azure_search",
                        "parameters": {
                            "authentication": {"type": "api_key", "key": "SECRET-KEY"}
                        },
                    }
                ],
                "padding": "P" * 50_000,
            }
        )
        client.log_interaction(
            {"method": "POST", "url": "https://x.openai.azure.com/v1", "body": body}
        )
        out = client._queue[0]["request_body"]
        assert "SECRET-KEY" not in out
        assert "[REDACTED]" in out
        assert MARKER in out

    def test_prefixed_response_not_truncated_twice(self, reset_global_instance):
        client = self._client(max_body_bytes=10)
        pre = "x" * 10 + "[truncated: 500 bytes, kept first 10]"
        client.log_interaction(
            {"method": "POST", "url": "https://api.openai.com/v1/x"},
            {"status_code": 200, "body": pre, "body_truncated": True},
        )
        assert client._queue[0]["response_body"] == pre

    def test_queued_memory_stays_bounded(self, reset_global_instance):
        cap = 1000
        client = self._client(max_body_bytes=cap)
        big = json.dumps({"image": "A" * 1_000_000})
        for _ in range(200):
            client.log_interaction(
                {"method": "POST", "url": "https://api.openai.com/v1/x", "body": big},
                {"status_code": 200, "body": big},
            )
        marker_slack = 100
        total = sum(
            len(i["request_body"]) + len(i["response_body"]) for i in client._queue
        )
        assert total <= 200 * 2 * (cap + marker_slack)


class TestBoundedChunks:
    def test_stops_storing_after_cap(self):
        acc = _BoundedChunks(10)
        for _ in range(1000):
            acc.append("abcde")
        assert acc.total == 5000
        assert acc.truncated
        assert len(acc._chunks) == 2  # only up to the cap is retained
        assert acc.joined().startswith("abcdeabcde[truncated: 5000 bytes")

    def test_under_cap_untouched(self):
        acc = _BoundedChunks(100)
        acc.append("hello")
        assert not acc.truncated
        assert acc.joined() == "hello"

    def test_unlimited(self):
        acc = _BoundedChunks(None)
        for _ in range(100):
            acc.append("abcde")
        assert not acc.truncated
        assert len(acc.joined()) == 500

    def test_empty_is_falsy(self):
        assert not _BoundedChunks(10)


class TestStreamingCap:
    @pytest.mark.asyncio
    async def test_stream_over_cap_is_truncated(self, reset_global_instance):
        captured = []
        set_handler(lambda req, res, err: captured.append(res))
        set_max_body_bytes(100)
        patch_httpx()
        try:
            import httpx

            from coolhand import httpx_interceptor

            response = MagicMock()
            response.status_code = 200
            response.headers = {"content-type": "text/event-stream"}

            async def lines():
                for i in range(500):
                    yield f"data: {i:04d}"

            response.aiter_lines = lines
            response.aiter_bytes = None
            response.aiter_text = None
            response.aiter_raw = None

            saved = httpx_interceptor._original_async_send
            httpx_interceptor._original_async_send = AsyncMock(return_value=response)
            request = MagicMock()
            request.method = "POST"
            request.url = "https://api.openai.com/v1/chat/completions"
            request.headers = {}
            request.content = b"{}"

            async with httpx.AsyncClient() as client:
                resp = await httpx.AsyncClient.send(client, request)
                async for _ in resp.aiter_lines():
                    pass
            httpx_interceptor._original_async_send = saved

            assert len(captured) == 1
            res = captured[0]
            assert res["body_truncated"] is True
            assert res["body"].startswith("data: 0000\n")
            assert "[truncated: 5500 bytes, kept first 100]" in res["body"]
        finally:
            unpatch()
