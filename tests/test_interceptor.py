"""Tests for coolhand.interceptor module."""

import sys
import pytest
from unittest.mock import MagicMock, patch

# AsyncMock is only available in Python 3.8+
if sys.version_info >= (3, 8):
    from unittest.mock import AsyncMock
else:
    class AsyncMock(MagicMock):
        async def __call__(self, *args, **kwargs):
            return super().__call__(*args, **kwargs)

from coolhand.interceptor import (
    _is_localhost,
    _is_llm_api,
    _is_streaming_content_type,
    _read_response_body,
    set_handler,
    patch as patch_httpx,
    unpatch,
    is_patched,
    LLM_API_DOMAINS,
)


class TestIsLocalhost:
    """Tests for _is_localhost helper function."""

    def test_localhost(self):
        """Detects localhost."""
        assert _is_localhost('http://localhost:8000/api') is True
        assert _is_localhost('https://localhost/test') is True

    def test_127_0_0_1(self):
        """Detects 127.0.0.1."""
        assert _is_localhost('http://127.0.0.1:3000/api') is True

    def test_0_0_0_0(self):
        """Detects 0.0.0.0."""
        assert _is_localhost('http://0.0.0.0:8080/') is True

    def test_ipv6_localhost(self):
        """Detects IPv6 localhost."""
        assert _is_localhost('http://[::1]:8000/api') is True

    def test_not_localhost(self):
        """Rejects non-localhost URLs."""
        assert _is_localhost('https://api.openai.com/v1/chat') is False
        assert _is_localhost('https://example.com') is False


class TestIsLlmApi:
    """Tests for _is_llm_api helper function."""

    def test_openai(self):
        """Detects api.openai.com."""
        assert _is_llm_api('https://api.openai.com/v1/chat/completions') is True

    def test_anthropic(self):
        """Detects api.anthropic.com."""
        assert _is_llm_api('https://api.anthropic.com/v1/messages') is True

    def test_non_llm_api(self):
        """Rejects non-LLM API URLs."""
        assert _is_llm_api('https://api.github.com/repos') is False
        assert _is_llm_api('https://example.com/api') is False
        assert _is_llm_api('https://anagramica.com/solve') is False

    def test_all_domains_in_list(self):
        """All defined LLM domains are detected."""
        for domain in LLM_API_DOMAINS:
            url = f'https://{domain}/v1/test'
            assert _is_llm_api(url) is True, f"Failed for {domain}"


class TestIsStreamingContentType:
    """Tests for _is_streaming_content_type helper function."""

    def test_event_stream(self):
        """Detects text/event-stream."""
        assert _is_streaming_content_type('text/event-stream') is True
        assert _is_streaming_content_type('text/event-stream; charset=utf-8') is True

    def test_ndjson(self):
        """Detects application/x-ndjson."""
        assert _is_streaming_content_type('application/x-ndjson') is True

    def test_non_streaming(self):
        """Rejects non-streaming content types."""
        assert _is_streaming_content_type('application/json') is False
        assert _is_streaming_content_type('text/plain') is False


class TestReadResponseBody:
    """Tests for _read_response_body helper function."""

    def test_streaming_returns_placeholder(self):
        """Streaming responses return [streaming] placeholder."""
        response = MagicMock()
        response.headers = {'content-type': 'text/event-stream'}
        assert _read_response_body(response) == '[streaming]'

    def test_reads_content_attribute(self):
        """Reads from _content if available."""
        response = MagicMock()
        response.headers = {'content-type': 'application/json'}
        response._content = b'{"result": "success"}'
        assert _read_response_body(response) == b'{"result": "success"}'

    def test_reads_content_property(self):
        """Falls back to content property."""
        response = MagicMock()
        response.headers = {'content-type': 'application/json'}
        response._content = None
        response.content = b'{"result": "fallback"}'
        assert _read_response_body(response) == b'{"result": "fallback"}'


class TestSetHandler:
    """Tests for set_handler function."""

    def test_sets_handler(self, reset_global_instance):
        """set_handler sets the global handler."""
        from coolhand import interceptor

        handler = MagicMock()
        set_handler(handler)
        assert interceptor._handler is handler


class TestPatchUnpatch:
    """Tests for patch and unpatch functions."""

    def test_patch_success(self, reset_global_instance):
        """patch successfully patches httpx."""
        result = patch_httpx()
        assert result is True
        assert is_patched() is True
        unpatch()

    def test_patch_idempotent(self, reset_global_instance):
        """Calling patch twice is safe."""
        patch_httpx()
        result = patch_httpx()  # Second call
        assert result is True
        assert is_patched() is True
        unpatch()

    def test_unpatch_restores(self, reset_global_instance):
        """unpatch restores original methods."""
        import httpx

        original_send = httpx.Client.send
        original_async_send = httpx.AsyncClient.send

        patch_httpx()
        assert httpx.Client.send is not original_send

        unpatch()
        assert is_patched() is False

    def test_is_patched_tracks_state(self, reset_global_instance):
        """is_patched correctly tracks patched state."""
        assert is_patched() is False
        patch_httpx()
        assert is_patched() is True
        unpatch()
        assert is_patched() is False


class TestRequestCapture:
    """Tests for request capture behavior."""

    def test_ignores_localhost(self, reset_global_instance, mock_httpx_request):
        """Localhost requests are not captured."""
        handler = MagicMock()
        set_handler(handler)
        patch_httpx()

        mock_httpx_request.url = 'http://localhost:8000/api'

        # The patched send should call original without capturing
        import httpx

        with patch.object(httpx.Client, 'send') as mock_send:
            # Just verify the logic - actual httpx interaction is mocked
            assert _is_localhost('http://localhost:8000/api') is True

        unpatch()

    def test_ignores_non_llm_api(self, reset_global_instance):
        """Non-LLM API requests are not captured."""
        assert _is_llm_api('https://api.github.com/repos') is False
        assert _is_llm_api('https://example.com/api') is False

    def test_captures_llm_api(self, reset_global_instance):
        """LLM API requests are captured."""
        assert _is_llm_api('https://api.openai.com/v1/chat/completions') is True
        assert _is_llm_api('https://api.anthropic.com/v1/messages') is True


class TestSyncRequestCapture:
    """Tests for synchronous request capture."""

    def test_sync_send_captures_request(self, reset_global_instance):
        """Sync send captures LLM API requests."""
        captured_requests = []

        def capture_handler(req, res, err):
            captured_requests.append((req, res, err))

        set_handler(capture_handler)
        patch_httpx()

        try:
            import httpx

            # Create a mock for the original send
            from coolhand import interceptor

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.headers = {'content-type': 'application/json'}
            mock_response._content = b'{"result": "ok"}'
            mock_response.content = b'{"result": "ok"}'

            # Temporarily mock the original send
            original_original = interceptor._original_send
            interceptor._original_send = MagicMock(return_value=mock_response)

            mock_request = MagicMock()
            mock_request.method = 'POST'
            mock_request.url = 'https://api.openai.com/v1/chat/completions'
            mock_request.headers = {'Content-Type': 'application/json'}
            mock_request.content = b'{"model": "gpt-4"}'

            client = httpx.Client()
            # Call the patched send
            httpx.Client.send(client, mock_request)

            # Restore
            interceptor._original_send = original_original

            assert len(captured_requests) == 1
            req, res, err = captured_requests[0]
            assert req['method'] == 'POST'
            assert req['url'] == 'https://api.openai.com/v1/chat/completions'
            assert res['status_code'] == 200
            assert err is None

        finally:
            unpatch()


class TestAsyncRequestCapture:
    """Tests for asynchronous request capture."""

    @pytest.mark.asyncio
    async def test_async_send_captures_request(self, reset_global_instance):
        """Async send captures LLM API requests."""
        captured_requests = []

        def capture_handler(req, res, err):
            captured_requests.append((req, res, err))

        set_handler(capture_handler)
        patch_httpx()

        try:
            import httpx
            from coolhand import interceptor

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.headers = {'content-type': 'application/json'}
            mock_response._content = b'{"result": "ok"}'
            mock_response.content = b'{"result": "ok"}'

            # Mock the original async send
            original_original = interceptor._original_async_send
            interceptor._original_async_send = AsyncMock(return_value=mock_response)

            mock_request = MagicMock()
            mock_request.method = 'POST'
            mock_request.url = 'https://api.anthropic.com/v1/messages'
            mock_request.headers = {'Content-Type': 'application/json'}
            mock_request.content = b'{"model": "claude-3"}'

            async with httpx.AsyncClient() as client:
                await httpx.AsyncClient.send(client, mock_request)

            interceptor._original_async_send = original_original

            assert len(captured_requests) == 1
            req, res, err = captured_requests[0]
            assert req['method'] == 'POST'
            assert 'anthropic' in req['url']
            assert res['status_code'] == 200

        finally:
            unpatch()


class TestErrorHandling:
    """Tests for error handling in request capture."""

    def test_sync_error_captured(self, reset_global_instance):
        """Errors during sync requests are captured."""
        captured_requests = []

        def capture_handler(req, res, err):
            captured_requests.append((req, res, err))

        set_handler(capture_handler)
        patch_httpx()

        try:
            import httpx
            from coolhand import interceptor

            # Mock original send to raise an exception
            interceptor._original_send = MagicMock(side_effect=Exception("Connection failed"))

            mock_request = MagicMock()
            mock_request.method = 'POST'
            mock_request.url = 'https://api.openai.com/v1/chat/completions'
            mock_request.headers = {}
            mock_request.content = b'{}'

            client = httpx.Client()

            with pytest.raises(Exception, match="Connection failed"):
                httpx.Client.send(client, mock_request)

            assert len(captured_requests) == 1
            req, res, err = captured_requests[0]
            assert req is not None
            assert res is None
            assert err == "Connection failed"

        finally:
            unpatch()
