"""Tests for coolhand.client module."""

from coolhand.client import (
    CoolhandClient,
    _get_default_config,
    _mask_value,
    _parse_body,
    _sanitize_headers,
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
        client = CoolhandClient(auto_submit=False, api_key="demo-key")
        client.log_interaction(mock_request_data, mock_response_data)
        assert len(client._queue) == 1

        client.flush()
        assert len(client._queue) == 0

    def test_flush_skips_demo_key(self, reset_global_instance):
        """flush skips API submission with demo-key."""
        client = CoolhandClient(auto_submit=False, api_key="demo-key")
        client._queue.append({"test": "data"})

        result = client.flush()
        assert result is True
        assert len(client._queue) == 0

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
        client = CoolhandClient(auto_submit=False, api_key="demo-key")
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


class TestFlushAPISubmission:
    """Tests for flush API submission behavior."""

    def test_flush_successful_submission(self, reset_global_instance, mock_urlopen):
        """flush successfully submits to API."""
        client = CoolhandClient(auto_submit=False, api_key="real-api-key-12345")
        client._queue.append(
            {
                "id": "test-id",
                "method": "post",
                "url": "https://api.openai.com/v1/chat",
                "timestamp": "2024-01-01T00:00:00Z",
            }
        )

        client.flush()
        # Queue should be cleared after submission
        assert len(client._queue) == 0
        mock_urlopen.assert_called_once()

    def test_flush_http_error(self, reset_global_instance, caplog):
        """flush handles HTTP errors gracefully."""
        import logging
        from unittest.mock import patch
        from urllib.error import HTTPError

        caplog.set_level(logging.WARNING)

        client = CoolhandClient(auto_submit=False, api_key="real-api-key-12345")
        client._queue.append({"id": "test-id", "method": "post", "url": "test"})

        with patch("coolhand.client.urlopen") as mock:
            mock.side_effect = HTTPError(
                url="https://coolhandlabs.com/api/v2/llm_request_logs",
                code=500,
                msg="Internal Server Error",
                hdrs={},
                fp=None,
            )
            client.flush()

        assert "Failed to submit interaction" in caplog.text
        assert len(client._queue) == 0  # Queue still cleared

    def test_flush_url_error(self, reset_global_instance, caplog):
        """flush handles URL errors gracefully."""
        import logging
        from unittest.mock import patch
        from urllib.error import URLError

        caplog.set_level(logging.WARNING)

        client = CoolhandClient(auto_submit=False, api_key="real-api-key-12345")
        client._queue.append({"id": "test-id", "method": "post", "url": "test"})

        with patch("coolhand.client.urlopen") as mock:
            mock.side_effect = URLError("Connection refused")
            client.flush()

        assert "Failed to submit interaction" in caplog.text
        assert len(client._queue) == 0

    def test_flush_unexpected_error(self, reset_global_instance, caplog):
        """flush handles unexpected errors gracefully."""
        import logging
        from unittest.mock import patch

        caplog.set_level(logging.WARNING)

        client = CoolhandClient(auto_submit=False, api_key="real-api-key-12345")
        client._queue.append({"id": "test-id", "method": "post", "url": "test"})

        with patch("coolhand.client.urlopen") as mock:
            mock.side_effect = RuntimeError("Unexpected error")
            client.flush()

        assert "Unexpected error submitting interaction" in caplog.text
        assert len(client._queue) == 0

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
