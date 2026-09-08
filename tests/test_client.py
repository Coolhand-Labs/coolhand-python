"""Tests for coolhand.client module."""

import threading

import pytest

from coolhand._config import _normalize_base_url
from coolhand.client import (
    CoolhandClient,
    _get_default_config,
    _mask_value,
    _parse_body,
    _sanitize_headers,
    _sanitize_url,
    _to_iso8601,
    get_instance,
    initialize,
    set_instance,
)


class TestMaskValue:
    """Tests for _mask_value helper function."""

    def test_mask_short_string(self):
        """Strings <= 8 chars are fully masked."""
        assert _mask_value("abc") == "***"
        assert _mask_value("12345678") == "********"

    def test_mask_long_string(self):
        """Strings > 8 chars keep first/last 4 chars."""
        result = _mask_value("sk-1234567890abcdef")
        assert result.startswith("sk-1")
        assert result.endswith("cdef")
        assert "****" in result

    def test_mask_exactly_9_chars(self):
        """Edge case: 9 char string has 1 asterisk in middle."""
        result = _mask_value("123456789")
        assert result == "1234*6789"


class TestSanitizeHeaders:
    """Tests for _sanitize_headers function."""

    def test_masks_authorization_header(self):
        """Authorization header is masked."""
        headers = {"Authorization": "Bearer sk-secret-key-12345678"}
        result = _sanitize_headers(headers)
        assert result["Authorization"] != "Bearer sk-secret-key-12345678"
        assert "****" in result["Authorization"]

    def test_masks_api_key_header(self):
        """API key headers are masked."""
        headers = {
            "x-api-key": "secret-api-key-123456789",
            "openai-api-key": "sk-openai-key-12345678",
        }
        result = _sanitize_headers(headers)
        assert "****" in result["x-api-key"]
        assert "****" in result["openai-api-key"]

    def test_passes_normal_headers(self):
        """Non-sensitive headers pass through unchanged."""
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "test-agent",
        }
        result = _sanitize_headers(headers)
        assert result == headers

    def test_case_insensitive_matching(self):
        """Header matching is case-insensitive."""
        headers = {"AUTHORIZATION": "Bearer secret-12345678"}
        result = _sanitize_headers(headers)
        assert "****" in result["AUTHORIZATION"]

    def test_masks_goog_api_key_header(self):
        """x-goog-api-key header is masked."""
        headers = {"x-goog-api-key": "AIzaSyDEADBEEF1234567890"}
        result = _sanitize_headers(headers)
        assert "****" in result["x-goog-api-key"]
        assert "AIzaSyDEADBEEF1234567890" != result["x-goog-api-key"]


class TestSanitizeUrl:
    """Tests for _sanitize_url function."""

    def test_redacts_key_param(self):
        """Google Gemini-style key param is redacted."""
        url = (
            "https://generativelanguage.googleapis.com"
            "/v1/models:generateContent?key=AIzaSyDEADBEEF1234"
        )
        result = _sanitize_url(url)
        assert "AIzaSyDEADBEEF1234" not in result
        assert "key=%5BREDACTED%5D" in result
        assert "generativelanguage.googleapis.com" in result

    def test_redacts_api_key_param(self):
        """api_key query param is redacted."""
        url = "https://api.example.com/v1/endpoint?api_key=secret123"
        result = _sanitize_url(url)
        assert "secret123" not in result
        assert "api_key=%5BREDACTED%5D" in result

    def test_redacts_multiple_sensitive_params(self):
        """Multiple sensitive params are all redacted."""
        url = "https://api.example.com/v1?key=secret1&token=secret2"
        result = _sanitize_url(url)
        assert "secret1" not in result
        assert "secret2" not in result

    def test_preserves_non_sensitive_params(self):
        """Non-sensitive params pass through unchanged."""
        url = "https://api.example.com/v1?model=gpt-4&stream=true"
        assert _sanitize_url(url) == url

    def test_no_query_params(self):
        """URL without query params passes through unchanged."""
        url = "https://api.openai.com/v1/chat/completions"
        assert _sanitize_url(url) == url

    def test_mixed_params(self):
        """Sensitive params redacted, others preserved."""
        url = "https://api.example.com/v1?model=gemini&key=AIzaSySecret"
        result = _sanitize_url(url)
        assert "model=gemini" in result
        assert "AIzaSySecret" not in result

    def test_empty_string(self):
        """Empty string returns empty string."""
        assert _sanitize_url("") == ""

    def test_params_are_case_sensitive(self):
        """Query params are case-sensitive per URL spec."""
        url = "https://api.example.com/v1?KEY=value123"
        result = _sanitize_url(url)
        # "KEY" != "key", so it should NOT be redacted
        assert "value123" in result

    def test_fails_closed_on_internal_error(self):
        """If redaction breaks internally, the query string is dropped, not leaked."""
        from unittest.mock import patch

        url = "https://api.example.com/v1?api_key=secret123&model=gpt-4"
        with patch("coolhand.client.parse_qs", side_effect=ValueError("boom")):
            result = _sanitize_url(url)
        assert "secret123" not in result
        assert result == "https://api.example.com/v1"


class TestParseBody:
    """Tests for _parse_body function."""

    def test_parse_none(self):
        """None returns None."""
        assert _parse_body(None) is None

    def test_parse_dict(self):
        """Dict is returned as-is."""
        body = {"key": "value"}
        assert _parse_body(body) == {"key": "value"}

    def test_parse_bytes_json(self):
        """Bytes containing JSON are parsed."""
        body = b'{"key": "value"}'
        assert _parse_body(body) == {"key": "value"}

    def test_parse_bytes_plain(self):
        """Bytes that aren't JSON are returned as string."""
        body = b"plain text"
        assert _parse_body(body) == "plain text"

    def test_parse_json_string(self):
        """JSON string is parsed to dict."""
        body = '{"key": "value"}'
        assert _parse_body(body) == {"key": "value"}

    def test_parse_plain_string(self):
        """Plain string is returned as-is."""
        body = "plain text"
        assert _parse_body(body) == "plain text"

    def test_parse_bytes_invalid_utf8(self):
        """Bytes that fail UTF-8 decode return string representation."""
        # Invalid UTF-8 sequence
        body = b"\xff\xfe invalid"
        result = _parse_body(body)
        # Should return string representation since decode fails
        assert isinstance(result, str)


class TestToIso8601:
    """Tests for _to_iso8601 function."""

    def test_converts_timestamp(self):
        """Unix timestamp is converted to ISO 8601."""
        # 2024-01-01 00:00:00 UTC
        timestamp = 1704067200.0
        result = _to_iso8601(timestamp)
        assert result == "2024-01-01T00:00:00Z"

    def test_includes_milliseconds(self):
        """Fractional seconds are preserved."""
        timestamp = 1704067200.123
        result = _to_iso8601(timestamp)
        assert "2024-01-01" in result
        assert result.endswith("Z")


class TestGetDefaultConfig:
    """Tests for _get_default_config function."""

    def test_returns_config_dict(self):
        """Returns a config dictionary with expected keys."""
        config = _get_default_config()
        assert "api_key" in config
        assert "silent" in config
        assert "auto_submit" in config
        assert "session_id" in config

    def test_session_id_generated(self):
        """Session ID is generated with timestamp."""
        config = _get_default_config()
        assert config["session_id"].startswith("session_")


class TestCoolhandClient:
    """Tests for CoolhandClient class."""

    def test_init_default_config(self, reset_global_instance):
        """Client initializes with default config."""
        client = CoolhandClient()
        assert client.config["auto_submit"] is True
        assert "session_id" in client.config

    def test_init_custom_config(self, mock_config, reset_global_instance):
        """Client accepts custom config."""
        client = CoolhandClient(config=mock_config)
        assert client.config["api_key"] == "test-api-key-12345678"
        assert client.config["base_url"] == "https://test.coolhandlabs.com"

    def test_init_kwargs_override(self, reset_global_instance):
        """Kwargs override config values."""
        client = CoolhandClient(debug=True, silent=False)
        assert client.config["debug"] is True
        assert client.config["silent"] is False

    def test_session_id_property(self, reset_global_instance):
        """Session ID is accessible as property."""
        client = CoolhandClient(session_id="test-session")
        assert client.session_id == "test-session"

    def test_log_interaction_increments_count(
        self, mock_request_data, mock_response_data, reset_global_instance
    ):
        """log_interaction increments interaction count."""
        client = CoolhandClient(auto_submit=False)
        assert client._interaction_count == 0

        client.log_interaction(mock_request_data, mock_response_data)
        assert client._interaction_count == 1

        client.log_interaction(mock_request_data, mock_response_data)
        assert client._interaction_count == 2

    def test_log_interaction_creates_flat_structure(
        self, mock_request_data, mock_response_data, reset_global_instance
    ):
        """log_interaction creates flat data structure."""
        client = CoolhandClient(auto_submit=False)
        client.log_interaction(mock_request_data, mock_response_data)

        interaction = client._queue[0]
        assert "id" in interaction
        assert "timestamp" in interaction
        assert "method" in interaction
        assert "url" in interaction
        assert "headers" in interaction
        assert "request_body" in interaction
        assert "response_headers" in interaction
        assert "response_body" in interaction
        assert "status_code" in interaction
        assert "duration_ms" in interaction
        assert "completed_at" in interaction
        assert "is_streaming" in interaction

    def test_log_interaction_generates_uuid(
        self, mock_request_data, mock_response_data, reset_global_instance
    ):
        """log_interaction generates UUID for each interaction."""
        client = CoolhandClient(auto_submit=False)
        client.log_interaction(mock_request_data, mock_response_data)
        client.log_interaction(mock_request_data, mock_response_data)

        id1 = client._queue[0]["id"]
        id2 = client._queue[1]["id"]
        assert id1 != id2
        # UUID format check
        assert len(id1) == 36
        assert id1.count("-") == 4

    def test_log_interaction_iso_timestamps(
        self, mock_request_data, mock_response_data, reset_global_instance
    ):
        """log_interaction uses ISO 8601 timestamps."""
        client = CoolhandClient(auto_submit=False)
        client.log_interaction(mock_request_data, mock_response_data)

        interaction = client._queue[0]
        assert interaction["timestamp"].endswith("Z")
        assert interaction["completed_at"].endswith("Z")

    def test_log_interaction_lowercase_method(
        self, mock_request_data, mock_response_data, reset_global_instance
    ):
        """log_interaction lowercases HTTP method."""
        mock_request_data["method"] = "POST"
        client = CoolhandClient(auto_submit=False)
        client.log_interaction(mock_request_data, mock_response_data)

        assert client._queue[0]["method"] == "post"

    def test_log_interaction_duration_ms(
        self, mock_request_data, mock_response_data, reset_global_instance
    ):
        """log_interaction converts duration to milliseconds."""
        mock_response_data["duration"] = 0.5  # 500ms
        client = CoolhandClient(auto_submit=False)
        client.log_interaction(mock_request_data, mock_response_data)

        assert client._queue[0]["duration_ms"] == 500.0

    def test_log_interaction_streaming_flag(
        self, mock_request_data, mock_response_data, reset_global_instance
    ):
        """log_interaction includes is_streaming flag."""
        mock_response_data["is_streaming"] = True
        client = CoolhandClient(auto_submit=False)
        client.log_interaction(mock_request_data, mock_response_data)

        assert client._queue[0]["is_streaming"] is True

    def test_flush_clears_queue(
        self, mock_request_data, mock_response_data, reset_global_instance
    ):
        """flush clears the queue after submission."""
        client = CoolhandClient(auto_submit=False, api_key=None)
        client.log_interaction(mock_request_data, mock_response_data)
        assert len(client._queue) == 1

        client.flush()
        assert len(client._queue) == 0

    def test_flush_skips_without_api_key(self, reset_global_instance):
        """flush skips API submission when no API key is configured. This
        must not start a delivery worker or enqueue the undeliverable item:
        coolhand/__init__.py auto-constructs a Coolhand() on import, and for
        the (very common) no-key case, every captured interaction going
        through flush() must stay a true no-op — no background thread, no
        item parked in _dispatch_queue."""
        client = CoolhandClient(auto_submit=False, api_key=None)
        client._queue.append({"test": "data"})

        result = client.flush()
        assert result is True
        assert len(client._queue) == 0
        assert client._worker_thread is None
        assert client._dispatch_queue.qsize() == 0

    def test_flush_empty_queue(self, reset_global_instance):
        """flush with empty queue returns True."""
        client = CoolhandClient(auto_submit=False)
        assert client.flush() is True

    def test_get_stats(self, reset_global_instance):
        """get_stats returns expected structure."""
        client = CoolhandClient(api_key="test-key")
        stats = client.get_stats()

        assert "config" in stats
        assert stats["config"]["has_api_key"] is True

        assert "monitoring" in stats
        assert "enabled" in stats["monitoring"]

        assert "logging" in stats
        assert "session_id" in stats["logging"]
        assert "interaction_count" in stats["logging"]
        assert "queue_size" in stats["logging"]

    def test_shutdown_flushes(self, reset_global_instance):
        """shutdown calls flush."""
        client = CoolhandClient(auto_submit=False, api_key=None)
        client._queue.append({"test": "data"})

        client.shutdown()
        assert len(client._queue) == 0


class TestGlobalInstanceFunctions:
    """Tests for global instance management functions."""

    def test_get_instance_none_initially(self, reset_global_instance):
        """get_instance returns None before initialization."""
        assert get_instance() is None

    def test_set_instance(self, reset_global_instance):
        """set_instance sets the global instance."""
        client = CoolhandClient()
        set_instance(client)
        assert get_instance() is client

    def test_initialize_creates_instance(self, reset_global_instance):
        """initialize creates and returns a new instance."""
        client = initialize(api_key="test-key")
        assert client is not None
        assert get_instance() is client

    def test_initialize_idempotent(self, reset_global_instance):
        """initialize returns existing instance if already initialized."""
        client1 = initialize(api_key="key1")
        client2 = initialize(api_key="key2")
        assert client1 is client2


class TestLogInteractionEdgeCases:
    """Tests for edge cases in log_interaction."""

    def test_log_interaction_with_error_no_response(
        self, mock_request_data, reset_global_instance
    ):
        """log_interaction handles error with no response."""
        client = CoolhandClient(auto_submit=False)
        client.log_interaction(
            mock_request_data, response=None, error="Connection failed"
        )

        interaction = client._queue[0]
        assert interaction["status_code"] == 0
        assert interaction["response_body"] is None
        assert interaction["response_headers"] == {}

    def test_log_interaction_logging_when_not_silent(
        self, mock_request_data, mock_response_data, reset_global_instance, caplog
    ):
        """log_interaction logs output when silent=False."""
        import logging

        caplog.set_level(logging.INFO)

        client = CoolhandClient(auto_submit=False, silent=False)
        client.log_interaction(mock_request_data, mock_response_data)

        assert "Captured:" in caplog.text
        assert "POST" in caplog.text or "post" in caplog.text


class TestSendOne:
    """Tests for _send_one, the blocking per-interaction API submission call
    used by the background worker (see TestBackgroundDispatch for the
    non-blocking flush() contract)."""

    def test_send_one_successful_submission(self, reset_global_instance, mock_urlopen):
        """_send_one returns True on a successful submission."""
        client = CoolhandClient(auto_submit=False, api_key="real-api-key-12345")
        interaction = {
            "id": "test-id",
            "method": "post",
            "url": "https://api.openai.com/v1/chat",
            "timestamp": "2024-01-01T00:00:00Z",
        }

        assert client._send_one(interaction) is True
        mock_urlopen.assert_called_once()

    def test_send_one_logs_success_when_not_silent(
        self, reset_global_instance, mock_urlopen, caplog
    ):
        """A successful submission is logged when silent=False — the old
        flush() logged a "Successfully submitted N/M" summary; delivery
        moving to the background worker (one item at a time, no natural
        batch boundary) shouldn't mean a non-silent caller loses positive
        confirmation that anything was ever delivered."""
        import logging

        caplog.set_level(logging.INFO)

        client = CoolhandClient(
            auto_submit=False, api_key="real-api-key-12345", silent=False
        )
        interaction = {
            "id": "test-id",
            "method": "post",
            "url": "https://api.openai.com/v1/chat",
        }

        assert client._send_one(interaction) is True
        # Correlatable, not just the internal id: the "Captured:" log line
        # elsewhere has no id, so method+url is what ties the two together.
        assert "test-id" in caplog.text
        assert "post" in caplog.text
        assert "https://api.openai.com/v1/chat" in caplog.text

    def test_send_one_treats_any_2xx_status_as_success(self, reset_global_instance):
        """A 2xx status other than exactly 200/201 (e.g. 202 Accepted) still
        counts as a successful submission."""
        from unittest.mock import MagicMock, patch

        client = CoolhandClient(auto_submit=False, api_key="real-api-key-12345")
        mock_resp = MagicMock()
        mock_resp.status = 202
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("coolhand.client.urlopen", return_value=mock_resp):
            result = client._send_one({"id": "x", "method": "post", "url": "test"})

        assert result is True

    def test_send_one_no_api_key_returns_false(self, reset_global_instance):
        """_send_one returns False without a request when no API key is set."""
        client = CoolhandClient(auto_submit=False, api_key=None)
        assert client._send_one({"id": "test-id"}) is False

    def test_send_one_http_error(self, reset_global_instance, caplog):
        """_send_one handles HTTP errors gracefully."""
        import logging
        from unittest.mock import patch
        from urllib.error import HTTPError

        caplog.set_level(logging.WARNING)

        client = CoolhandClient(auto_submit=False, api_key="real-api-key-12345")
        interaction = {"id": "test-id", "method": "post", "url": "test"}

        with patch("coolhand.client.urlopen") as mock:
            mock.side_effect = HTTPError(
                url="https://coolhandlabs.com/api/v2/llm_request_logs",
                code=500,
                msg="Internal Server Error",
                hdrs={},
                fp=None,
            )
            result = client._send_one(interaction)

        assert result is False
        assert "Failed to submit interaction" in caplog.text

    def test_send_one_url_error(self, reset_global_instance, caplog):
        """_send_one handles URL errors gracefully."""
        import logging
        from unittest.mock import patch
        from urllib.error import URLError

        caplog.set_level(logging.WARNING)

        client = CoolhandClient(auto_submit=False, api_key="real-api-key-12345")
        interaction = {"id": "test-id", "method": "post", "url": "test"}

        with patch("coolhand.client.urlopen") as mock:
            mock.side_effect = URLError("Connection refused")
            result = client._send_one(interaction)

        assert result is False
        assert "Failed to submit interaction" in caplog.text

    def test_send_one_unexpected_error(self, reset_global_instance, caplog):
        """_send_one handles unexpected errors gracefully."""
        import logging
        from unittest.mock import patch

        caplog.set_level(logging.WARNING)

        client = CoolhandClient(auto_submit=False, api_key="real-api-key-12345")
        interaction = {"id": "test-id", "method": "post", "url": "test"}

        with patch("coolhand.client.urlopen") as mock:
            mock.side_effect = RuntimeError("Unexpected error")
            result = client._send_one(interaction)

        assert result is False
        assert "Unexpected error submitting interaction" in caplog.text

    def test_send_one_passes_ssl_context_to_urlopen(self, reset_global_instance):
        """_send_one passes _ssl_context as context= argument to urlopen."""
        import ssl
        from unittest.mock import MagicMock, patch

        sentinel_ctx = ssl.create_default_context()
        client = CoolhandClient(auto_submit=False, api_key="real-api-key-12345")
        interaction = {
            "id": "test-id",
            "method": "post",
            "url": "https://api.openai.com/v1/chat",
            "timestamp": "2024-01-01T00:00:00Z",
        }

        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.status = 200

        with (
            patch("coolhand.client._ssl_context", sentinel_ctx),
            patch("coolhand.client.urlopen", return_value=mock_resp) as mock_open,
        ):
            client._send_one(interaction)
            _, kwargs = mock_open.call_args
            assert kwargs.get("context") is sentinel_ctx

    def test_send_one_ssl_context_none_when_certifi_missing(
        self, reset_global_instance
    ):
        """_send_one passes context=None to urlopen when certifi is unavailable."""
        from unittest.mock import MagicMock, patch

        client = CoolhandClient(auto_submit=False, api_key="real-api-key-12345")
        interaction = {
            "id": "test-id",
            "method": "post",
            "url": "https://api.openai.com/v1/chat",
            "timestamp": "2024-01-01T00:00:00Z",
        }

        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.status = 200

        with (
            patch("coolhand.client._ssl_context", None),
            patch("coolhand.client.urlopen", return_value=mock_resp) as mock_open,
        ):
            client._send_one(interaction)
            _, kwargs = mock_open.call_args
            assert kwargs.get("context") is None

    def test_log_interaction_with_auto_submit(
        self, mock_request_data, mock_response_data, reset_global_instance
    ):
        """log_interaction triggers flush when auto_submit=True."""
        from unittest.mock import patch

        client = CoolhandClient(auto_submit=True, api_key="demo-key")

        with patch.object(client, "flush") as mock_flush:
            mock_flush.return_value = True
            client.log_interaction(mock_request_data, mock_response_data)
            mock_flush.assert_called_once()

        # flush() was mocked above, so the interaction is still sitting in
        # _queue unflushed; clear it so nothing lingers to be delivered for
        # real if this client's (test-session-patched) atexit hook ever runs.
        client._queue.clear()


class _HangingUrlopen:
    """Callable urlopen stand-in that blocks until released. `entered` fires
    the moment it's reached, so a test can prove the worker actually got
    there before doing anything else; `release` lets it go. Used to verify
    flush()/log_interaction() return before delivery completes, while making
    sure a test's `with patch(...)` block only tears down after the worker
    is confirmed done draining — never leaving a window for it to fall
    through to the real urlopen once the patch is gone."""

    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.call_count = 0

    def __call__(self, *args, **kwargs):
        self.call_count += 1
        self.entered.set()
        self.release.wait(timeout=5)
        raise TimeoutError("test backend never responded")


@pytest.fixture
def hanging_urlopen():
    return _HangingUrlopen()


class TestBackgroundDispatch:
    """Tests for the bounded dispatch queue + background worker: flush() must
    hand off interactions for delivery without blocking the calling thread
    (which may own an asyncio event loop), and shutdown() must not hang if the
    backend is unreachable."""

    def test_flush_returns_immediately_when_backend_is_slow(
        self,
        mock_request_data,
        mock_response_data,
        reset_global_instance,
        hanging_urlopen,
    ):
        """flush() must not block the caller even if urlopen never returns."""
        import time
        from unittest.mock import patch

        client = CoolhandClient(auto_submit=False, api_key="real-api-key-12345")
        client.log_interaction(mock_request_data, mock_response_data)

        with patch("coolhand.client.urlopen", side_effect=hanging_urlopen):
            start = time.monotonic()
            client.flush()
            elapsed = time.monotonic() - start

            # Wait for the worker to actually reach the mock, then let it finish
            # and join it, all while the patch is still active — otherwise the
            # worker could still be about to call urlopen after this `with`
            # block restores the real one, hitting the live Coolhand API.
            assert hanging_urlopen.entered.wait(timeout=2), (
                "worker never reached the mocked urlopen"
            )
            hanging_urlopen.release.set()
            # shutdown() sends the sentinel so the worker actually exits (a
            # bare join() with nothing to stop it would just time out without
            # proving anything); still inside the patch, so it can't fall
            # through to the real urlopen.
            client.shutdown()
            assert client._worker_thread is None

        assert elapsed < 0.5
        assert hanging_urlopen.call_count == 1

    def test_log_interaction_returns_immediately_when_backend_is_slow(
        self,
        mock_request_data,
        mock_response_data,
        reset_global_instance,
        hanging_urlopen,
    ):
        """auto_submit must not block the caller even if urlopen hangs."""
        import time
        from unittest.mock import patch

        client = CoolhandClient(auto_submit=True, api_key="real-api-key-12345")

        with patch("coolhand.client.urlopen", side_effect=hanging_urlopen):
            start = time.monotonic()
            client.log_interaction(mock_request_data, mock_response_data)
            elapsed = time.monotonic() - start

            assert hanging_urlopen.entered.wait(timeout=2), (
                "worker never reached the mocked urlopen"
            )
            hanging_urlopen.release.set()
            # shutdown() sends the sentinel so the worker actually exits (a
            # bare join() with nothing to stop it would just time out without
            # proving anything); still inside the patch, so it can't fall
            # through to the real urlopen.
            client.shutdown()
            assert client._worker_thread is None

        assert elapsed < 0.5
        assert hanging_urlopen.call_count == 1

    @pytest.mark.asyncio
    async def test_log_interaction_does_not_block_the_event_loop(
        self,
        mock_request_data,
        mock_response_data,
        reset_global_instance,
        hanging_urlopen,
    ):
        """This is the exact scenario behind the reported bug: httpx's async
        interceptor calls log_interaction() synchronously (not awaited, since
        it isn't a coroutine) from inside the coroutine that patches
        httpx.AsyncClient.send. That must not stall the event loop — other
        coroutines need to keep making progress even while delivery is slow.
        The sync-thread "returns immediately" tests above prove flush() is
        non-blocking on the calling thread, but not specifically that an
        asyncio event loop stays responsive, which is what was actually
        reported."""
        import asyncio
        from unittest.mock import patch

        canary_ran = False

        async def canary():
            nonlocal canary_ran
            await asyncio.sleep(0.05)
            canary_ran = True

        client = CoolhandClient(auto_submit=True, api_key="real-api-key-12345")

        with patch("coolhand.client.urlopen", side_effect=hanging_urlopen):
            canary_task = asyncio.create_task(canary())
            # Synchronous call, exactly like httpx_interceptor's
            # patched_async_send invoking the handler — never awaited.
            client.log_interaction(mock_request_data, mock_response_data)
            await asyncio.sleep(0.15)
            assert canary_ran  # event loop kept running while delivery was "slow"

            # Confirm the worker actually reached the mock before letting it
            # go and joining it, all while the patch is still active — see
            # _HangingUrlopen's docstring for why this matters.
            assert hanging_urlopen.entered.wait(timeout=2), (
                "worker never reached the mocked urlopen"
            )
            hanging_urlopen.release.set()
            client.shutdown()
            await canary_task

        assert client._worker_thread is None

    def test_flush_drops_and_counts_when_dispatch_queue_full(
        self, reset_global_instance
    ):
        """Items are dropped (not blocked) once the dispatch queue is full."""
        from unittest.mock import patch

        from coolhand import client as client_module

        with patch.object(client_module, "_MAX_DISPATCH_QUEUE_SIZE", 2):
            client = CoolhandClient(auto_submit=False, api_key="real-api-key-12345")

        # Prevent the worker from draining the queue so backpressure is
        # observable. flush() calls _ensure_worker_locked() directly (not
        # the public _ensure_worker() wrapper), so that's what must be
        # patched here — patching _ensure_worker would silently do nothing
        # and let a real worker thread start, hitting the real API.
        with patch.object(client, "_ensure_worker_locked", return_value=True):
            for i in range(5):
                client._queue.append(
                    {"id": f"item-{i}", "method": "post", "url": "test"}
                )

            result = client.flush()

        assert result is False
        assert client._dropped_count == 3
        stats = client.get_stats()
        assert stats["logging"]["dropped_count"] == 3
        assert stats["logging"]["pending_delivery"] == 2
        assert len(client._queue) == 0

    def test_shutdown_drains_worker_within_timeout(
        self, reset_global_instance, mock_urlopen
    ):
        """shutdown() waits for the worker to deliver queued interactions."""
        client = CoolhandClient(auto_submit=False, api_key="real-api-key-12345")
        for i in range(3):
            client._queue.append({"id": f"item-{i}", "method": "post", "url": "test"})

        client.shutdown()

        assert mock_urlopen.call_count == 3
        assert len(client._queue) == 0

    def test_shutdown_drains_a_stale_sentinel_with_no_worker(
        self, reset_global_instance, mock_urlopen
    ):
        """shutdown()'s narrow-race guard (start a worker if the dispatch
        queue is non-empty but no worker exists) is load-bearing, not just
        cheap insurance: without it, a leftover sentinel from an earlier
        timed-out shutdown() call would sit in the dispatch queue forever
        and pending_delivery would permanently over-report by one."""
        from coolhand.client import _SHUTDOWN_SENTINEL

        client = CoolhandClient(auto_submit=False, api_key="k")
        assert client._worker_thread is None

        # Simulate the state left behind by an earlier shutdown() call whose
        # worker committed to exit in the Thread.is_alive() staleness window
        # (see pending_delivery's comment in get_stats()) — a sentinel stuck
        # in the queue with no worker left to consume it.
        client._dispatch_queue.put(_SHUTDOWN_SENTINEL)

        client.shutdown()

        assert client._dispatch_queue.qsize() == 0
        assert client.get_stats()["logging"]["pending_delivery"] == 0
        assert client._worker_thread is None

    def test_shutdown_swallows_queue_full_when_putting_sentinel(
        self, reset_global_instance, hanging_urlopen
    ):
        """If the dispatch queue is completely full when shutdown() tries to
        enqueue the sentinel, the resulting queue.Full must be swallowed
        rather than propagating — shutdown() must not raise just because
        the backend is badly backed up; it still waits out its budget on
        the join instead."""
        from unittest.mock import patch

        from coolhand import client as client_module
        from coolhand.client import _SHUTDOWN_SENTINEL

        with (
            patch.object(client_module, "_MAX_DISPATCH_QUEUE_SIZE", 1),
            patch.object(client_module, "_SHUTDOWN_TIMEOUT", 0.1),
        ):
            client = CoolhandClient(auto_submit=False, api_key="k")

            with patch("coolhand.client.urlopen", side_effect=hanging_urlopen):
                client._queue.append({"id": "first", "method": "post", "url": "test"})
                client.flush()  # worker takes "first" immediately, blocks

                assert hanging_urlopen.entered.wait(timeout=2), (
                    "worker never reached the mocked urlopen"
                )

                # Fill the now-empty (capacity 1) queue so shutdown()'s
                # sentinel put has nowhere to go.
                client._dispatch_queue.put_nowait(
                    {"id": "second", "method": "post", "url": "test"}
                )

                client.shutdown()  # must not raise queue.Full

                # Pin that the queue was genuinely full when the sentinel
                # put was attempted — just "second" is queued, the sentinel
                # never made it in — and that the atexit hook was correctly
                # left armed since a worker is still running.
                assert client._dispatch_queue.qsize() == 1
                assert client._worker_thread is not None

                # Clean up: release the block, then hand the worker a fresh
                # sentinel once it's made room, so it exits — still inside
                # the patch, so it can never fall through to the real
                # urlopen.
                hanging_urlopen.release.set()
                client._dispatch_queue.put(_SHUTDOWN_SENTINEL, timeout=2)
                worker = client._worker_thread
                if worker is not None:
                    worker.join(timeout=2)

    def test_shutdown_does_not_hang_when_backend_unreachable(
        self, reset_global_instance
    ):
        """shutdown() must return promptly even if the backend never responds."""
        import threading
        import time
        from unittest.mock import patch

        from coolhand import client as client_module

        entered = threading.Event()
        block_forever = threading.Event()

        def hanging_urlopen(*args, **kwargs):
            entered.set()
            block_forever.wait()  # released explicitly below
            raise TimeoutError("unreachable")

        client = CoolhandClient(auto_submit=False, api_key="real-api-key-12345")
        client._queue.append({"id": "test-id", "method": "post", "url": "test"})

        with (
            patch.object(client_module, "_SHUTDOWN_TIMEOUT", 0.2),
            patch("coolhand.client.urlopen", side_effect=hanging_urlopen),
        ):
            start = time.monotonic()
            client.shutdown()
            elapsed = time.monotonic() - start

            # Confirm (and wait out, if needed) the worker actually reaching the
            # mock before letting it go and joining it — while the patch is
            # still active, so it can never fall through to the real urlopen.
            assert entered.wait(timeout=2), "worker never reached the mocked urlopen"
            # Captured now (still guaranteed alive, blocked in the mock)
            # rather than read fresh after join() below — _worker_loop
            # clears client._worker_thread to None as it exits, so a fresh
            # read at that point could already be None.
            worker = client._worker_thread
            block_forever.set()
            # shutdown() already enqueued the sentinel before giving up on its
            # own join (it's a blocking put with spare queue capacity, so it
            # succeeds regardless of the patched _SHUTDOWN_TIMEOUT); once
            # unblocked, the worker picks it up and exits on its own.
            worker.join(timeout=2)
            assert not worker.is_alive()
            assert client._worker_thread is None

        assert elapsed < 1.0

    def test_worker_loop_drains_items_behind_a_stale_sentinel(
        self, reset_global_instance, mock_urlopen
    ):
        """A worker that receives the shutdown sentinel must still deliver
        whatever is already queued behind it (and swallow any further stray
        sentinels) instead of exiting immediately and stranding them. This
        can happen because shutdown() is safe to call more than once, and a
        call whose join times out while the worker is still busy leaves its
        sentinel behind — a second such call could enqueue another one
        before the worker ever gets free."""
        import threading

        from coolhand.client import _SHUTDOWN_SENTINEL

        client = CoolhandClient(auto_submit=False, api_key="real-api-key-12345")

        client._dispatch_queue.put(_SHUTDOWN_SENTINEL)
        client._dispatch_queue.put({"id": "a", "method": "post", "url": "test"})
        client._dispatch_queue.put(_SHUTDOWN_SENTINEL)
        client._dispatch_queue.put({"id": "b", "method": "post", "url": "test"})

        worker = threading.Thread(target=client._worker_loop, daemon=True)
        worker.start()
        worker.join(timeout=2)

        assert not worker.is_alive()
        assert mock_urlopen.call_count == 2

    def test_worker_loop_continues_when_item_lands_during_exit_check(
        self, reset_global_instance, mock_urlopen
    ):
        """If something lands on the dispatch queue in the narrow window
        between the drain loop finishing (queue.Empty raised) and the
        locked emptiness check that follows, the worker must keep running
        and consume it instead of exiting and stranding it — the `continue`
        branch this pins. Deterministic: the injection happens synchronously
        inside the worker's own drain loop, so no second thread or real
        race is needed."""
        import queue

        from coolhand.client import _SHUTDOWN_SENTINEL

        client = CoolhandClient(auto_submit=False, api_key="k")

        original_get_nowait = client._dispatch_queue.get_nowait
        injected = {"done": False}

        def spying_get_nowait():
            try:
                return original_get_nowait()
            except queue.Empty:
                if not injected["done"]:
                    injected["done"] = True
                    # Simulate a concurrent flush() landing an item (and a
                    # second sentinel, so the worker can still exit cleanly
                    # afterward) right as the drain loop concludes there's
                    # nothing left.
                    client._dispatch_queue.put_nowait(
                        {"id": "late", "method": "post", "url": "test"}
                    )
                    client._dispatch_queue.put_nowait(_SHUTDOWN_SENTINEL)
                raise

        client._dispatch_queue.get_nowait = spying_get_nowait
        client._dispatch_queue.put(_SHUTDOWN_SENTINEL)

        worker = threading.Thread(target=client._worker_loop, daemon=True)
        worker.start()
        worker.join(timeout=2)

        assert not worker.is_alive()
        assert mock_urlopen.call_count == 1  # "late" was delivered, not stranded

    def test_deliver_exception_does_not_kill_the_worker(
        self, reset_global_instance, mock_urlopen
    ):
        """A single bad item must not silently kill delivery for every
        interaction still queued behind it. _send_one is total today (it
        catches everything internally), but _deliver's own guard is what
        protects the worker loop if that ever changes (a subclass override,
        a monkeypatch) — this pins that guard's behavior directly rather
        than relying on _send_one never raising."""
        from unittest.mock import patch

        original_send_one = CoolhandClient._send_one
        call_ids = []

        def flaky_send_one(self, interaction):
            call_ids.append(interaction["id"])
            if interaction["id"] == "bad":
                raise RuntimeError("boom")
            return original_send_one(self, interaction)

        client = CoolhandClient(auto_submit=False, api_key="real-api-key-12345")

        with patch.object(CoolhandClient, "_send_one", flaky_send_one):
            client._queue.append({"id": "bad", "method": "post", "url": "test"})
            client._queue.append({"id": "good", "method": "post", "url": "test"})
            client.shutdown()

        assert call_ids == ["bad", "good"]
        assert client._worker_thread is None
        assert mock_urlopen.call_count == 1  # only "good" reached the real send path
        assert client._delivery_failure_count == 1

    def test_delivery_failure_count_increments_on_failed_delivery(
        self, reset_global_instance
    ):
        """A failed POST (non-2xx status, network error, etc.) increments
        delivery_failure_count, distinct from dropped_count — this is the
        observability signal for e.g. a bad API key, where nothing is ever
        dropped for lack of queue capacity but every delivery still fails."""
        from unittest.mock import MagicMock, patch

        client = CoolhandClient(auto_submit=False, api_key="bad-key")
        mock_resp = MagicMock()
        mock_resp.status = 401
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("coolhand.client.urlopen", return_value=mock_resp):
            client._queue.append({"id": "x", "method": "post", "url": "test"})
            client.shutdown()

        stats = client.get_stats()
        assert stats["logging"]["delivery_failure_count"] == 1
        assert stats["logging"]["dropped_count"] == 0

    def test_concurrent_log_interaction_delivers_each_item_exactly_once(
        self, mock_request_data, mock_response_data, reset_global_instance, mock_urlopen
    ):
        """Concurrent log_interaction() calls — as happen when the sync and
        async httpx interceptors, `requests`, and the Copilot JSON-RPC reader
        thread can all auto-submit from different threads — must not race on
        the internal queue swap and duplicate or drop interactions."""
        import threading

        client = CoolhandClient(auto_submit=True, api_key="real-api-key-12345")

        def worker():
            client.log_interaction(mock_request_data, mock_response_data)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        client.shutdown()

        assert mock_urlopen.call_count == 20
        assert client._interaction_count == 20

    def test_flush_dispatches_successfully_with_api_key(self, reset_global_instance):
        """flush() returns True and hands items to the dispatch queue when an
        API key is configured and nothing is dropped."""
        from unittest.mock import patch

        client = CoolhandClient(auto_submit=False, api_key="real-api-key-12345")

        # Prevent the worker from draining the queue so it's observable
        # below. flush() calls _ensure_worker_locked() directly (not the
        # public _ensure_worker() wrapper), so that's what must be patched
        # here — patching _ensure_worker would silently do nothing and let
        # a real worker thread start, hitting the real API.
        with patch.object(client, "_ensure_worker_locked", return_value=True):
            client._queue.append({"id": "x", "method": "post", "url": "test"})
            result = client.flush()

        assert result is True
        assert client._dispatch_queue.qsize() == 1
        assert len(client._queue) == 0

    def test_send_one_logs_warning_on_non_2xx_status(
        self, reset_global_instance, caplog
    ):
        """_send_one returns False and logs a warning on a non-2xx status,
        so an auth failure (401/403) isn't silently swallowed."""
        import logging
        from unittest.mock import MagicMock, patch

        caplog.set_level(logging.WARNING)

        client = CoolhandClient(auto_submit=False, api_key="bad-key")
        mock_resp = MagicMock()
        mock_resp.status = 401
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("coolhand.client.urlopen", return_value=mock_resp):
            result = client._send_one({"id": "x", "method": "post", "url": "test"})

        assert result is False
        assert "401" in caplog.text

    def test_shutdown_is_idempotent(self, reset_global_instance, mock_urlopen):
        """Calling shutdown() twice does not raise and is a safe no-op the
        second time (e.g. an explicit shutdown() followed by the atexit hook)."""
        client = CoolhandClient(auto_submit=False, api_key="real-api-key-12345")
        client._queue.append({"id": "x", "method": "post", "url": "test"})

        client.shutdown()
        client.shutdown()  # must not raise

        assert mock_urlopen.call_count == 1

    def test_flush_after_shutdown_still_delivers(
        self, reset_global_instance, mock_urlopen
    ):
        """shutdown() is not a permanent stop switch: flush() afterwards still
        works by starting a fresh worker, so monitoring doesn't silently die
        forever if shutdown() is ever called mid-program rather than right
        before process exit (the CHANGELOG explicitly points callers at
        shutdown() as a delivery barrier, which wouldn't be safe otherwise).
        Delivers through a real worker both times (rather than shutting down
        an already-empty client) so this actually exercises "worker exited,
        a later call starts a fresh one," not just an empty no-op. Identity
        is captured via which thread actually called _send_one, rather than
        reading client._worker_thread after the fact — _worker_loop clears
        that to None as it exits, so by the time shutdown() returns it's
        already None again either way."""
        import threading
        from unittest.mock import patch

        original_send_one = CoolhandClient._send_one
        delivering_threads = []

        def recording_send_one(self, interaction):
            delivering_threads.append(threading.current_thread())
            return original_send_one(self, interaction)

        client = CoolhandClient(auto_submit=False, api_key="real-api-key-12345")

        with patch.object(CoolhandClient, "_send_one", recording_send_one):
            client._queue.append({"id": "first", "method": "post", "url": "test"})
            client.shutdown()

            client._queue.append({"id": "second", "method": "post", "url": "test"})
            client.shutdown()

        assert len(delivering_threads) == 2
        assert delivering_threads[0] is not delivering_threads[1]
        assert client._worker_thread is None
        assert mock_urlopen.call_count == 2
        assert len(client._queue) == 0

    def test_worker_loop_clears_worker_thread_on_exit(
        self, reset_global_instance, mock_urlopen
    ):
        """_worker_loop clears self._worker_thread to None as soon as it
        commits to exiting, rather than callers relying on
        Thread.is_alive() — which only flips once the OS finishes tearing
        the thread down, a narrow but real window where a concurrent
        _ensure_worker() could see a thread that's technically still
        "alive" but will never consume anything else, and skip starting a
        replacement."""
        from coolhand.client import _SHUTDOWN_SENTINEL

        client = CoolhandClient(auto_submit=False, api_key="k")
        client._queue.append({"id": "x", "method": "post", "url": "test"})
        client.flush()

        worker = client._worker_thread
        assert worker is not None

        client._dispatch_queue.put(_SHUTDOWN_SENTINEL)
        worker.join(timeout=2)

        assert client._worker_thread is None

    def test_flush_after_worker_committed_to_exit_still_delivers(
        self, reset_global_instance, mock_urlopen
    ):
        """Regression test: a worker that has drained everything and is
        about to decide whether to exit must not let a concurrent flush()
        see it as "available" (Thread.is_alive() stays True until the OS
        finishes tearing the thread down) and skip starting a replacement,
        only for the worker to then exit anyway — stranding the item with
        no consumer. Forces the exact interleaving deterministically (via a
        monkeypatched Queue.empty pausing the worker mid-decision) rather
        than relying on real-time scheduling luck to reproduce it."""
        import threading

        from coolhand.client import _SHUTDOWN_SENTINEL

        client = CoolhandClient(auto_submit=False, api_key="k")

        worker_at_exit_check = threading.Event()
        let_worker_proceed = threading.Event()
        original_empty = client._dispatch_queue.empty

        def spying_empty():
            if threading.current_thread() is client._worker_thread:
                worker_at_exit_check.set()
                let_worker_proceed.wait(timeout=2)
            return original_empty()

        client._dispatch_queue.empty = spying_empty

        client._queue.append({"id": "first", "method": "post", "url": "test"})
        client.flush()  # starts the worker, delivers "first"

        # Enqueue the sentinel directly (bypassing shutdown(), which would
        # itself try to re-acquire _worker_lock for its own final decision
        # and deadlock against the worker paused below) to drive the worker
        # into its exit-check, where it'll pause.
        client._dispatch_queue.put(_SHUTDOWN_SENTINEL)
        assert worker_at_exit_check.wait(timeout=2)

        client._queue.append({"id": "second", "method": "post", "url": "test"})
        flush_done = threading.Event()

        def do_flush():
            client.flush()
            flush_done.set()

        t = threading.Thread(target=do_flush)
        t.start()

        # The worker is paused *inside* its locked exit-check, holding
        # _worker_lock. If flush() shares that lock correctly, this
        # concurrent flush() call must block on it rather than proceeding —
        # proving the two are mutually exclusive.
        assert not flush_done.wait(timeout=0.2)

        let_worker_proceed.set()
        t.join(timeout=2)
        assert flush_done.is_set()

        client.shutdown()
        assert mock_urlopen.call_count == 2  # both delivered, neither stranded

    def test_ensure_worker_reuses_live_worker_across_flushes(
        self, reset_global_instance, mock_urlopen
    ):
        """A single flush() shouldn't spin up a new thread per call — the
        same worker thread should be reused as long as it's still alive."""
        import threading
        from unittest.mock import patch

        # Keep the first item's delivery parked so the worker stays alive
        # (and thus reusable) across both flush() calls below.
        hold = threading.Event()
        proceed = threading.Event()
        original_send_one = CoolhandClient._send_one

        def blocking_then_normal_send_one(self, interaction):
            if interaction["id"] == "first":
                hold.set()
                proceed.wait(timeout=2)
            return original_send_one(self, interaction)

        client = CoolhandClient(auto_submit=False, api_key="real-api-key-12345")

        with patch.object(CoolhandClient, "_send_one", blocking_then_normal_send_one):
            client._queue.append({"id": "first", "method": "post", "url": "test"})
            client.flush()
            assert hold.wait(timeout=2), "worker never started processing"
            first_worker = client._worker_thread

            client._queue.append({"id": "second", "method": "post", "url": "test"})
            client.flush()
            second_worker = client._worker_thread

            proceed.set()
            client.shutdown()  # sends the sentinel so the worker actually exits
            assert not first_worker.is_alive()

        assert first_worker is second_worker
        assert mock_urlopen.call_count == 2

    def test_ensure_worker_public_entry_starts_a_worker(
        self, reset_global_instance, mock_urlopen
    ):
        """_ensure_worker() (the public entry point flush() itself doesn't
        use, but shutdown()'s narrow-race guard does) must acquire
        _worker_lock and start a worker when none is running yet, not just
        report availability."""
        from coolhand.client import _SHUTDOWN_SENTINEL

        client = CoolhandClient(auto_submit=False, api_key="k")
        assert client._worker_thread is None

        assert client._ensure_worker() is True
        assert client._worker_thread is not None
        assert client._worker_thread.is_alive()

        worker = client._worker_thread
        client._dispatch_queue.put(_SHUTDOWN_SENTINEL)
        worker.join(timeout=2)

    def test_ensure_worker_handles_thread_start_runtime_error(
        self, reset_global_instance
    ):
        """flush() must not raise if Thread.start() fails (e.g. because the
        interpreter has begun shutting down), and must report the item as
        dropped rather than falsely claiming it was queued."""
        from unittest.mock import patch

        client = CoolhandClient(auto_submit=False, api_key="real-api-key-12345")

        with patch(
            "threading.Thread.start",
            side_effect=RuntimeError("can't create new thread"),
        ):
            client._queue.append({"id": "x", "method": "post", "url": "test"})
            result = client.flush()  # must not raise

        assert result is False
        assert client._worker_thread is None
        assert client._dropped_count == 1


class TestClientLifecycle:
    """Tests for construction/shutdown wiring shared by every CoolhandClient,
    not just the Coolhand subclass."""

    def test_constructor_registers_atexit_shutdown(self, reset_global_instance):
        """A bare CoolhandClient() (not just the Coolhand subclass) registers
        its shutdown with atexit, so queued interactions still get a delivery
        attempt on normal process exit."""
        from unittest.mock import call, patch

        # Counts the specific call rather than asserting the mock's *total*
        # call count is exactly one: coolhand.client.atexit is the real,
        # process-global atexit module, so third-party code (e.g. coverage's
        # pure-Python tracer registering its own atexit hook when a new
        # thread starts tracing) can call atexit.register during this same
        # window and would otherwise make this test flaky.
        with patch("coolhand.client.atexit.register") as mock_register:
            client = CoolhandClient(auto_submit=False, api_key="k")

        assert mock_register.call_args_list.count(call(client.shutdown)) == 1

    def test_shutdown_unregisters_atexit(self, reset_global_instance, mock_urlopen):
        """shutdown() unregisters its own atexit hook, so an explicitly
        shut-down client isn't kept alive in the atexit registry for the
        rest of the process."""
        from unittest.mock import call, patch

        client = CoolhandClient(auto_submit=False, api_key="k")

        # Counts the specific call rather than the mock's total call count —
        # see test_constructor_registers_atexit_shutdown for why.
        with patch("coolhand.client.atexit.unregister") as mock_unregister:
            client.shutdown()

        assert mock_unregister.call_args_list.count(call(client.shutdown)) == 1

    def test_flush_after_shutdown_reregisters_atexit_exactly_once(
        self, reset_global_instance, mock_urlopen
    ):
        """A fresh worker started by flush() after shutdown() re-arms the
        atexit safety net exactly once — not left permanently unregistered
        (losing the final drain on real exit), and not double-registered
        (which would run shutdown() twice, and could start a second worker
        thread during interpreter shutdown)."""
        from unittest.mock import call, patch

        client = CoolhandClient(auto_submit=False, api_key="k")
        client.shutdown()  # unregisters the atexit hook __init__ registered

        # Counts the specific call rather than the mock's total call count —
        # see test_constructor_registers_atexit_shutdown for why.
        with patch("coolhand.client.atexit.register") as mock_register:
            client._queue.append({"id": "x", "method": "post", "url": "test"})
            client.shutdown()  # flush() starts a fresh worker, re-registering

        assert mock_register.call_args_list.count(call(client.shutdown)) == 1

    def test_shutdown_keeps_atexit_armed_if_worker_still_alive(
        self, reset_global_instance, hanging_urlopen
    ):
        """If the worker doesn't finish within the shutdown budget, the
        atexit hook must stay registered so a final drain is still attempted
        at real process exit. Regression test for a bug where shutdown()
        unconditionally unregistered even when the worker was still busy,
        permanently losing the atexit safety net for that client."""
        from unittest.mock import call, patch

        from coolhand import client as client_module

        client = CoolhandClient(auto_submit=False, api_key="k")

        with (
            patch.object(client_module, "_SHUTDOWN_TIMEOUT", 0.1),
            patch("coolhand.client.urlopen", side_effect=hanging_urlopen),
            patch("coolhand.client.atexit.unregister") as mock_unregister,
        ):
            client._queue.append({"id": "x", "method": "post", "url": "test"})
            client.shutdown()

            # Confirm (waiting out any scheduling delay if needed) that the
            # worker actually reached the mock before checking/asserting
            # anything else, and — critically — before letting it go and
            # joining it, all while the patch is still active. Without this,
            # a slow-scheduled worker could still be short of the urlopen
            # call when the `with` block exits, later falling through to the
            # real urlopen once the patch is torn down.
            assert hanging_urlopen.entered.wait(timeout=2), (
                "worker never reached the mocked urlopen"
            )
            # Captured now (still guaranteed alive, blocked in the mock)
            # rather than read fresh after join() below — _worker_loop
            # clears client._worker_thread to None as it exits, so a fresh
            # read at that point could already be None.
            worker = client._worker_thread
            assert worker.is_alive()
            # Exactly one call is expected — from _ensure_worker() pairing
            # unregister-then-register when it started this fresh worker
            # a moment earlier. shutdown()'s own end-of-method unregister
            # must NOT fire a second time while the worker is still alive.
            # Counts the specific call rather than the mock's total call
            # count — see test_constructor_registers_atexit_shutdown for why.
            assert mock_unregister.call_args_list.count(call(client.shutdown)) == 1

            hanging_urlopen.release.set()
            worker.join(timeout=2)
            assert not worker.is_alive()
            assert client._worker_thread is None

    def test_shutdown_holds_worker_lock_across_unregister_decision(
        self, reset_global_instance
    ):
        """shutdown()'s atexit-unregister decision must hold _worker_lock
        across the whole check-then-act sequence, not just a snapshot read —
        otherwise a concurrent _ensure_worker() re-registering a fresh
        worker in that window could have its registration silently
        unregistered right back out. Regression test for a TOCTOU where the
        lock was released before the decision was acted on; verified
        directly (rather than via a real thread race, which wouldn't
        reliably reproduce) by spying on the dispatch queue check the
        decision makes and confirming a non-blocking acquire of the same
        lock from this thread fails at that point."""
        client = CoolhandClient(auto_submit=False, api_key="k")

        original_empty = client._dispatch_queue.empty
        observed_acquirable = []

        def spying_empty():
            # threading.Lock is non-reentrant: a non-blocking acquire from
            # this same thread only succeeds if nothing (including this
            # very call stack) currently holds it.
            acquired = client._worker_lock.acquire(blocking=False)
            observed_acquirable.append(acquired)
            if acquired:
                client._worker_lock.release()
            return original_empty()

        client._dispatch_queue.empty = spying_empty

        client.shutdown()

        # The final call (inside the locked decision) must find the lock
        # already held. An earlier, unrelated call (the narrow-race guard
        # before the join/wait logic) is intentionally outside the lock.
        assert observed_acquirable[-1] is False


class TestGeminiCapture:
    """Tests for Gemini API capture and sanitization."""

    def test_gemini_url_sanitization(self):
        """Gemini ?key=API_KEY query param is redacted."""
        url = (
            "https://generativelanguage.googleapis.com"
            "/v1beta/models/gemini-pro:generateContent?key=AIzaSyDEADBEEF1234"
        )
        result = _sanitize_url(url)
        assert "AIzaSyDEADBEEF1234" not in result
        assert "generativelanguage.googleapis.com" in result

    def test_gemini_response_parsed_as_json(self):
        """Gemini JSON response is parsed correctly via _parse_body."""
        body = b'{"candidates": [{"content": {"parts": [{"text": "Hello"}]}}]}'
        result = _parse_body(body)
        assert isinstance(result, dict)
        assert "candidates" in result
        assert result["candidates"][0]["content"]["parts"][0]["text"] == "Hello"


class TestNormalizeBaseUrl:
    """Tests for _normalize_base_url helper."""

    def test_accepts_https(self):
        assert _normalize_base_url("https://example.com") == "https://example.com"

    def test_accepts_https_with_path(self):
        assert (
            _normalize_base_url("https://feedback.example.com")
            == "https://feedback.example.com"
        )

    def test_strips_trailing_slash(self):
        assert _normalize_base_url("https://example.com/") == "https://example.com"

    def test_strips_multiple_trailing_slashes(self):
        assert _normalize_base_url("https://example.com///") == "https://example.com"

    def test_accepts_http_localhost(self):
        assert _normalize_base_url("http://localhost:8080") == "http://localhost:8080"

    def test_accepts_http_127(self):
        assert _normalize_base_url("http://127.0.0.1:3000") == "http://127.0.0.1:3000"

    def test_rejects_plain_http(self):
        import pytest

        with pytest.raises(ValueError, match="must use https://"):
            _normalize_base_url("http://example.com")

    def test_rejects_ftp(self):
        import pytest

        with pytest.raises(ValueError, match="must use https://"):
            _normalize_base_url("ftp://example.com")

    def test_rejects_empty_string(self):
        import pytest

        with pytest.raises(ValueError, match="must use https://"):
            _normalize_base_url("")

    def test_rejects_localhost_subdomain_spoof(self):
        """http://localhost.attacker.com must not pass the localhost check."""
        import pytest

        with pytest.raises(ValueError, match="must use https://"):
            _normalize_base_url("http://localhost.attacker.com")

    def test_rejects_127_subdomain_spoof(self):
        """http://127.0.0.1.attacker.com must not pass the 127.0.0.1 check."""
        import pytest

        with pytest.raises(ValueError, match="must use https://"):
            _normalize_base_url("http://127.0.0.1.attacker.com")

    def test_rejects_localhost_at_spoof(self):
        """http://localhost@attacker.com userinfo bypass is rejected."""
        import pytest

        with pytest.raises(ValueError, match="must use https://"):
            _normalize_base_url("http://localhost@attacker.com")

    def test_rejects_localhostevil_no_separator(self):
        """http://localhostevil.com has no separator — must be rejected."""
        import pytest

        with pytest.raises(ValueError, match="must use https://"):
            _normalize_base_url("http://localhostevil.com")

    def test_rejects_https_no_hostname(self):
        """https:// with no hostname is rejected (would produce broken URLs)."""
        import pytest

        with pytest.raises(ValueError, match="must use https://"):
            _normalize_base_url("https://")

    def test_rejects_https_scheme_only(self):
        """https: with no slashes or hostname is rejected."""
        import pytest

        with pytest.raises(ValueError, match="must use https://"):
            _normalize_base_url("https:")

    def test_rejects_ws_scheme(self):
        """ws:// is rejected — only https and loopback http are allowed."""
        import pytest

        with pytest.raises(ValueError, match="must use https://"):
            _normalize_base_url("ws://example.com")

    def test_accepts_http_ipv6_loopback(self):
        """http://[::1] is accepted as a loopback address for local dev."""
        assert _normalize_base_url("http://[::1]:8080") == "http://[::1]:8080"


class TestBaseUrlConfig:
    """Tests for base_url in CoolhandClient."""

    def test_default_base_url(self, reset_global_instance):
        """No config → defaults to coolhandlabs.com."""
        client = CoolhandClient(auto_submit=False)
        assert client.config["base_url"] == "https://coolhandlabs.com"

    def test_base_url_from_constructor(self, reset_global_instance):
        """Explicit base_url in constructor is used."""
        client = CoolhandClient(
            base_url="https://feedback.example.com", auto_submit=False
        )
        assert client.config["base_url"] == "https://feedback.example.com"

    def test_base_url_from_config_dict(self, reset_global_instance):
        """base_url in config dict is used."""
        client = CoolhandClient(
            config={"base_url": "https://custom.example.com"}, auto_submit=False
        )
        assert client.config["base_url"] == "https://custom.example.com"

    def test_base_url_trailing_slash_normalized(self, reset_global_instance):
        """Trailing slash is stripped during init."""
        client = CoolhandClient(base_url="https://example.com/", auto_submit=False)
        assert client.config["base_url"] == "https://example.com"

    def test_base_url_from_env_var(self, monkeypatch, reset_global_instance):
        """COOLHAND_BASE_URL env var is picked up."""
        monkeypatch.setenv("COOLHAND_BASE_URL", "https://self-hosted.example.com")
        client = CoolhandClient(auto_submit=False)
        assert client.config["base_url"] == "https://self-hosted.example.com"

    def test_constructor_base_url_overrides_env(
        self, monkeypatch, reset_global_instance
    ):
        """Explicit base_url kwarg overrides env var."""
        monkeypatch.setenv("COOLHAND_BASE_URL", "https://env.example.com")
        client = CoolhandClient(
            base_url="https://explicit.example.com", auto_submit=False
        )
        assert client.config["base_url"] == "https://explicit.example.com"

    def test_invalid_base_url_raises(self, reset_global_instance):
        """Non-https base_url raises ValueError."""
        import pytest

        with pytest.raises(ValueError, match="must use https://"):
            CoolhandClient(base_url="http://evil.example.com", auto_submit=False)

    def test_localhost_base_url_allowed(self, reset_global_instance):
        """http://localhost is allowed for local dev."""
        client = CoolhandClient(base_url="http://localhost:4000", auto_submit=False)
        assert client.config["base_url"] == "http://localhost:4000"

    def test_get_default_config_includes_base_url(self, monkeypatch):
        """_get_default_config includes base_url from env."""
        monkeypatch.setenv("COOLHAND_BASE_URL", "https://env.example.com")
        cfg = _get_default_config()
        assert cfg["base_url"] == "https://env.example.com"

    def test_get_default_config_base_url_default(self, monkeypatch):
        """_get_default_config defaults to coolhandlabs.com."""
        monkeypatch.delenv("COOLHAND_BASE_URL", raising=False)
        cfg = _get_default_config()
        assert cfg["base_url"] == "https://coolhandlabs.com"

    def test_flush_uses_config_base_url(self, reset_global_instance):
        """_send_one POSTs to the configured base_url, not hardcoded default."""
        from unittest.mock import MagicMock, patch

        client = CoolhandClient(
            api_key="test-key-12345678",
            base_url="https://self-hosted.example.com",
            auto_submit=False,
        )

        with patch("coolhand.client.urlopen") as mock_open:
            mock_resp = MagicMock()
            mock_resp.status = 201
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_open.return_value = mock_resp
            client._send_one({"id": "x", "method": "post", "url": "test"})

        call_args = mock_open.call_args
        request = call_args[0][0]
        assert "self-hosted.example.com" in request.full_url
