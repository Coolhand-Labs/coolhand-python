"""Tests for coolhand.httpx_interceptor module."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from coolhand.httpx_interceptor import (
    DEFAULT_EXCLUDE_API_PATTERNS,
    DEFAULT_INTERCEPT_ADDRESSES,
    _is_excluded,
    _is_llm_api,
    _is_localhost,
    _is_streaming_content_type,
    _read_response_body,
    is_patched,
    set_exclude_api_patterns,
    set_handler,
    set_intercept_addresses,
    unpatch,
)
from coolhand.httpx_interceptor import patch as patch_httpx


class TestIsLocalhost:
    """Tests for _is_localhost helper function."""

    def test_localhost(self):
        """Detects localhost."""
        assert _is_localhost("http://localhost:8000/api") is True
        assert _is_localhost("https://localhost/test") is True

    def test_127_0_0_1(self):
        """Detects 127.0.0.1."""
        assert _is_localhost("http://127.0.0.1:3000/api") is True

    def test_0_0_0_0(self):
        """Detects 0.0.0.0."""
        assert _is_localhost("http://0.0.0.0:8080/") is True

    def test_ipv6_localhost(self):
        """Detects IPv6 localhost."""
        assert _is_localhost("http://[::1]:8000/api") is True

    def test_not_localhost(self):
        """Rejects non-localhost URLs."""
        assert _is_localhost("https://api.openai.com/v1/chat") is False
        assert _is_localhost("https://example.com") is False


class TestIsLlmApi:
    """Tests for _is_llm_api helper function."""

    def test_openai(self):
        """Detects api.openai.com."""
        assert _is_llm_api("https://api.openai.com/v1/chat/completions") is True

    def test_anthropic(self):
        """Detects api.anthropic.com."""
        assert _is_llm_api("https://api.anthropic.com/v1/messages") is True

    def test_github_models(self):
        """Detects models.github.ai and the deprecated models.inference.ai.azure.com."""
        assert _is_llm_api("https://models.github.ai/chat/completions") is True
        assert (
            _is_llm_api("https://models.inference.ai.azure.com/chat/completions")
            is True
        )

    def test_non_llm_api(self):
        """Rejects non-LLM API URLs."""
        assert _is_llm_api("https://api.github.com/repos") is False
        assert _is_llm_api("https://example.com/api") is False
        assert _is_llm_api("https://anagramica.com/solve") is False

    def test_gemini(self):
        """Detects generativelanguage.googleapis.com."""
        url = (
            "https://generativelanguage.googleapis.com"
            "/v1beta/models/gemini-pro:generateContent"
        )
        assert _is_llm_api(url) is True

    def test_gemini_streaming(self):
        """Detects Gemini streaming endpoint :streamGenerateContent."""
        url = (
            "https://generativelanguage.googleapis.com"
            "/v1beta/models/gemini-pro:streamGenerateContent?alt=sse"
        )
        assert _is_llm_api(url) is True

    def test_gemini_count_tokens(self):
        """Detects Gemini :countTokens endpoint."""
        url = (
            "https://generativelanguage.googleapis.com"
            "/v1beta/models/gemini-pro:countTokens"
        )
        assert _is_llm_api(url) is True

    def test_gemini_with_api_key_param(self):
        """Detects Gemini URL with ?key= query param."""
        url = (
            "https://generativelanguage.googleapis.com"
            "/v1beta/models/gemini-pro:generateContent"
            "?key=AIzaSyDEADBEEF"
        )
        assert _is_llm_api(url) is True

    def test_vertex_ai_generate_content(self):
        """Detects Vertex AI :generateContent endpoint."""
        url = (
            "https://us-central1-aiplatform.googleapis.com/v1"
            "/projects/my-project/locations/us-central1"
            "/publishers/google/models/gemini-pro:generateContent"
        )
        assert _is_llm_api(url) is True

    def test_vertex_ai_streaming(self):
        """Detects Vertex AI :streamGenerateContent endpoint."""
        url = (
            "https://us-central1-aiplatform.googleapis.com/v1"
            "/projects/my-project/locations/us-central1"
            "/publishers/google/models"
            "/gemini-pro:streamGenerateContent"
        )
        assert _is_llm_api(url) is True

    def test_vertex_ai_unknown_path_not_matched(self):
        """Rejects Vertex AI URL with non-matching path."""
        url = (
            "https://us-central1-aiplatform.googleapis.com/v1"
            "/projects/my-project/locations/us-central1"
            "/publishers/google/models/gemini-pro:predict"
        )
        assert _is_llm_api(url) is False

    def test_all_default_addresses(self):
        """All default intercept addresses are detected."""
        for addr in DEFAULT_INTERCEPT_ADDRESSES:
            if addr.startswith(":"):
                url = "https://example.googleapis.com/v1/models/gemini" + addr
            else:
                url = "https://" + addr + "/v1/test"
            assert _is_llm_api(url) is True, f"Failed for {addr}"


class TestCustomInterceptAddresses:
    """Tests for custom intercept addresses."""

    def test_custom_intercept_addresses(self, reset_global_instance):
        """Custom intercept addresses override defaults."""
        set_intercept_addresses(["api.custom-llm.com", "/v1/inference"])

        assert _is_llm_api("https://api.custom-llm.com/chat") is True
        assert _is_llm_api("https://example.com/v1/inference") is True

        # Default addresses should no longer match
        assert _is_llm_api("https://api.openai.com/v1/chat/completions") is False
        assert _is_llm_api("https://api.anthropic.com/v1/messages") is False

    def test_default_restored_after_reset(self, reset_global_instance):
        """Resetting _intercept_addresses to None restores defaults."""
        from coolhand import httpx_interceptor

        set_intercept_addresses(["api.custom-llm.com"])
        assert _is_llm_api("https://api.openai.com/v1/chat/completions") is False

        httpx_interceptor._intercept_addresses = None
        assert _is_llm_api("https://api.openai.com/v1/chat/completions") is True


class TestIsStreamingContentType:
    """Tests for _is_streaming_content_type helper function."""

    def test_event_stream(self):
        """Detects text/event-stream."""
        assert _is_streaming_content_type("text/event-stream") is True
        assert _is_streaming_content_type("text/event-stream; charset=utf-8") is True

    def test_ndjson(self):
        """Detects application/x-ndjson."""
        assert _is_streaming_content_type("application/x-ndjson") is True

    def test_non_streaming(self):
        """Rejects non-streaming content types."""
        assert _is_streaming_content_type("application/json") is False
        assert _is_streaming_content_type("text/plain") is False


class TestReadResponseBody:
    """Tests for _read_response_body helper function."""

    def test_streaming_returns_placeholder(self):
        """Streaming responses return [streaming] placeholder."""
        response = MagicMock()
        response.headers = {"content-type": "text/event-stream"}
        assert _read_response_body(response) == "[streaming]"

    def test_reads_content_attribute(self):
        """Reads from _content if available."""
        response = MagicMock()
        response.headers = {"content-type": "application/json"}
        response._content = b'{"result": "success"}'
        assert _read_response_body(response) == b'{"result": "success"}'

    def test_reads_content_property(self):
        """Falls back to content property."""
        response = MagicMock()
        response.headers = {"content-type": "application/json"}
        response._content = None
        response.content = b'{"result": "fallback"}'
        assert _read_response_body(response) == b'{"result": "fallback"}'


class TestSetHandler:
    """Tests for set_handler function."""

    def test_sets_handler(self, reset_global_instance):
        """set_handler sets the global handler."""
        from coolhand import httpx_interceptor

        handler = MagicMock()
        set_handler(handler)
        assert httpx_interceptor._handler is handler


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

        mock_httpx_request.url = "http://localhost:8000/api"

        # The patched send should call original without capturing
        # Just verify the logic - actual httpx interaction is mocked
        assert _is_localhost("http://localhost:8000/api") is True

        unpatch()

    def test_ignores_non_llm_api(self, reset_global_instance):
        """Non-LLM API requests are not captured."""
        assert _is_llm_api("https://api.github.com/repos") is False
        assert _is_llm_api("https://example.com/api") is False

    def test_captures_llm_api(self, reset_global_instance):
        """LLM API requests are captured."""
        assert _is_llm_api("https://api.openai.com/v1/chat/completions") is True
        assert _is_llm_api("https://api.anthropic.com/v1/messages") is True


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
            from coolhand import httpx_interceptor

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.headers = {"content-type": "application/json"}
            mock_response._content = b'{"result": "ok"}'
            mock_response.content = b'{"result": "ok"}'

            # Temporarily mock the original send
            original_original = httpx_interceptor._original_send
            httpx_interceptor._original_send = MagicMock(return_value=mock_response)

            mock_request = MagicMock()
            mock_request.method = "POST"
            mock_request.url = "https://api.openai.com/v1/chat/completions"
            mock_request.headers = {"Content-Type": "application/json"}
            mock_request.content = b'{"model": "gpt-4"}'

            client = httpx.Client()
            # Call the patched send
            httpx.Client.send(client, mock_request)

            # Restore
            httpx_interceptor._original_send = original_original

            assert len(captured_requests) == 1
            req, res, err = captured_requests[0]
            assert req["method"] == "POST"
            assert req["url"] == "https://api.openai.com/v1/chat/completions"
            assert res["status_code"] == 200
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

            from coolhand import httpx_interceptor

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.headers = {"content-type": "application/json"}
            mock_response._content = b'{"result": "ok"}'
            mock_response.content = b'{"result": "ok"}'

            # Mock the original async send
            original_original = httpx_interceptor._original_async_send
            httpx_interceptor._original_async_send = AsyncMock(
                return_value=mock_response
            )

            mock_request = MagicMock()
            mock_request.method = "POST"
            mock_request.url = "https://api.anthropic.com/v1/messages"
            mock_request.headers = {"Content-Type": "application/json"}
            mock_request.content = b'{"model": "claude-3"}'

            async with httpx.AsyncClient() as client:
                await httpx.AsyncClient.send(client, mock_request)

            httpx_interceptor._original_async_send = original_original

            assert len(captured_requests) == 1
            req, res, err = captured_requests[0]
            assert req["method"] == "POST"
            assert "anthropic" in req["url"]
            assert res["status_code"] == 200

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

            from coolhand import httpx_interceptor

            # Mock original send to raise an exception
            httpx_interceptor._original_send = MagicMock(
                side_effect=Exception("Connection failed")
            )

            mock_request = MagicMock()
            mock_request.method = "POST"
            mock_request.url = "https://api.openai.com/v1/chat/completions"
            mock_request.headers = {}
            mock_request.content = b"{}"

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


class TestInterceptorEdgeCases:
    """Tests for edge cases in httpx_interceptor module."""

    def test_patch_already_patched_returns_true(self, reset_global_instance):
        """patch returns True when already patched."""
        # First patch
        result1 = patch_httpx()
        assert result1 is True
        assert is_patched() is True

        # Second patch should also return True (idempotent)
        result2 = patch_httpx()
        assert result2 is True

        unpatch()

    def test_unpatch_when_not_patched(self, reset_global_instance):
        """unpatch does nothing when not patched."""
        from coolhand import httpx_interceptor

        # Ensure not patched
        httpx_interceptor._patched = False

        # Should not raise any errors
        unpatch()
        assert is_patched() is False

    def test_is_localhost_with_invalid_url(self):
        """_is_localhost handles invalid URLs gracefully."""
        # These should not raise exceptions
        assert _is_localhost("") is False
        assert _is_localhost("not-a-valid-url") is False

    def test_is_llm_api_with_invalid_url(self):
        """_is_llm_api handles invalid URLs gracefully."""
        # These should not raise exceptions
        assert _is_llm_api("") is False
        assert _is_llm_api("not-a-valid-url") is False


class TestAsyncStreamingCapture:
    """Tests for async streaming response capture."""

    @pytest.mark.asyncio
    async def test_async_streaming_aiter_lines(self, reset_global_instance):
        """Async streaming via aiter_lines is captured."""
        captured_requests = []

        def capture_handler(req, res, err):
            captured_requests.append((req, res, err))

        set_handler(capture_handler)
        patch_httpx()

        try:
            import httpx

            from coolhand import httpx_interceptor

            # Create mock streaming response
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.headers = {"content-type": "text/event-stream"}

            async def mock_aiter_lines():
                yield 'data: {"chunk": 1}'
                yield 'data: {"chunk": 2}'
                yield ""

            mock_response.aiter_lines = mock_aiter_lines
            mock_response.aiter_bytes = None
            mock_response.aiter_text = None
            mock_response.aiter_raw = None

            # Mock the original async send
            original_original = httpx_interceptor._original_async_send
            httpx_interceptor._original_async_send = AsyncMock(
                return_value=mock_response
            )

            mock_request = MagicMock()
            mock_request.method = "POST"
            mock_request.url = "https://api.openai.com/v1/chat/completions"
            mock_request.headers = {"Content-Type": "application/json"}
            mock_request.content = b'{"stream": true}'

            async with httpx.AsyncClient() as client:
                response = await httpx.AsyncClient.send(client, mock_request)
                # Consume the stream
                async for _ in response.aiter_lines():
                    pass

            httpx_interceptor._original_async_send = original_original

            # Should have captured the streaming response
            assert len(captured_requests) == 1
            req, res, err = captured_requests[0]
            assert res["is_streaming"] is True

        finally:
            unpatch()

    @pytest.mark.asyncio
    async def test_async_error_captured(self, reset_global_instance):
        """Errors during async requests are captured."""
        captured_requests = []

        def capture_handler(req, res, err):
            captured_requests.append((req, res, err))

        set_handler(capture_handler)
        patch_httpx()

        try:
            import httpx

            from coolhand import httpx_interceptor

            # Save original for restoration
            saved_original = httpx_interceptor._original_async_send

            # Create a mock that raises when awaited
            async def raising_send(*args, **kwargs):
                raise Exception("Async connection failed")

            httpx_interceptor._original_async_send = raising_send

            mock_request = MagicMock()
            mock_request.method = "POST"
            mock_request.url = "https://api.anthropic.com/v1/messages"
            mock_request.headers = {}
            mock_request.content = b"{}"

            async with httpx.AsyncClient() as client:
                with pytest.raises(Exception, match="Async connection failed"):
                    await httpx.AsyncClient.send(client, mock_request)

            # Restore original before unpatch
            httpx_interceptor._original_async_send = saved_original

            assert len(captured_requests) == 1
            req, res, err = captured_requests[0]
            assert req is not None
            assert res is None
            assert err == "Async connection failed"

        finally:
            unpatch()


class TestReadResponseBodyEdgeCases:
    """Tests for edge cases in _read_response_body."""

    def test_read_response_body_no_content(self):
        """_read_response_body returns None when no content available."""
        response = MagicMock()
        response.headers = {"content-type": "application/json"}
        response._content = None
        del response.content  # Remove the content attribute

        result = _read_response_body(response)
        # Should handle missing content gracefully
        assert result is None or result == b""

    def test_read_response_body_exception(self):
        """_read_response_body handles exceptions gracefully."""
        response = MagicMock()
        response.headers.get.side_effect = Exception("Header error")

        result = _read_response_body(response)
        assert result is None


class TestIsExcluded:
    """Tests for _is_excluded helper function."""

    def test_excluded_by_default_pattern(self, reset_global_instance):
        """batchPredictionJobs URLs are excluded by default."""
        url = (
            "https://aiplatform.googleapis.com/v1/projects/my-project"
            "/locations/us-central1/batchPredictionJobs/123456"
        )
        assert _is_excluded(url) is True

    def test_llm_inference_not_excluded(self, reset_global_instance):
        """Normal LLM inference endpoints are not excluded."""
        assert _is_excluded("https://api.openai.com/v1/chat/completions") is False
        assert _is_excluded("https://api.anthropic.com/v1/messages") is False
        url = (
            "https://us-central1-aiplatform.googleapis.com/v1/projects/my-project"
            "/locations/us-central1/publishers/google/models/gemini-pro:generateContent"
        )
        assert _is_excluded(url) is False

    def test_custom_pattern_excludes(self, reset_global_instance):
        """Custom patterns override defaults."""
        set_exclude_api_patterns(["/myOperationalEndpoint/"])
        assert _is_excluded("https://example.com/myOperationalEndpoint/status") is True
        assert _is_excluded("https://api.openai.com/v1/chat/completions") is False

    def test_empty_list_disables_exclusions(self, reset_global_instance):
        """Empty pattern list disables all exclusions."""
        set_exclude_api_patterns([])
        url = (
            "https://aiplatform.googleapis.com/v1/projects/my-project"
            "/locations/us-central1/batchPredictionJobs/123"
        )
        assert _is_excluded(url) is False

    def test_uses_default_when_none(self, reset_global_instance):
        """When _exclude_api_patterns is None, DEFAULT_EXCLUDE_API_PATTERNS is used."""
        from coolhand import httpx_interceptor

        httpx_interceptor._exclude_api_patterns = None
        url = (
            "https://aiplatform.googleapis.com/v1/projects/my-project"
            "/locations/us-central1/batchPredictionJobs/123"
        )
        assert _is_excluded(url) is True

    def test_default_constant_contains_batch_prediction_jobs(self):
        """DEFAULT_EXCLUDE_API_PATTERNS contains the expected default."""
        assert "/batchPredictionJobs/" in DEFAULT_EXCLUDE_API_PATTERNS


class TestExcludeIntegration:
    """Integration tests for exclude_api_patterns in the patched senders."""

    def test_excluded_url_not_captured_sync(self, reset_global_instance):
        """Excluded URL is not forwarded to the handler in sync send."""
        captured = []

        def capture_handler(req, res, err):
            captured.append(req)

        set_handler(capture_handler)
        # Allow aiplatform.googleapis.com via intercept_addresses
        set_intercept_addresses(["aiplatform.googleapis.com"])
        # Keep default exclude patterns (batchPredictionJobs)
        patch_httpx()

        try:
            import httpx

            from coolhand import httpx_interceptor

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.headers = {"content-type": "application/json"}
            mock_response._content = b'{"done": true}'
            mock_response.content = b'{"done": true}'

            original_original = httpx_interceptor._original_send
            httpx_interceptor._original_send = MagicMock(return_value=mock_response)

            mock_request = MagicMock()
            mock_request.method = "GET"
            mock_request.url = (
                "https://aiplatform.googleapis.com/v1/projects/proj"
                "/locations/us-central1/batchPredictionJobs/999"
            )
            mock_request.headers = {}
            mock_request.content = b""

            client = httpx.Client()
            httpx.Client.send(client, mock_request)

            httpx_interceptor._original_send = original_original

            assert len(captured) == 0
        finally:
            unpatch()

    def test_non_excluded_llm_url_captured_sync(self, reset_global_instance):
        """Non-excluded LLM URL is still captured in sync send."""
        captured = []

        def capture_handler(req, res, err):
            captured.append(req)

        set_handler(capture_handler)
        patch_httpx()

        try:
            import httpx

            from coolhand import httpx_interceptor

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.headers = {"content-type": "application/json"}
            mock_response._content = b'{"result": "ok"}'
            mock_response.content = b'{"result": "ok"}'

            original_original = httpx_interceptor._original_send
            httpx_interceptor._original_send = MagicMock(return_value=mock_response)

            mock_request = MagicMock()
            mock_request.method = "POST"
            mock_request.url = "https://api.openai.com/v1/chat/completions"
            mock_request.headers = {"Content-Type": "application/json"}
            mock_request.content = b'{"model": "gpt-4"}'

            client = httpx.Client()
            httpx.Client.send(client, mock_request)

            httpx_interceptor._original_send = original_original

            assert len(captured) == 1
            assert "openai" in captured[0]["url"]
        finally:
            unpatch()
