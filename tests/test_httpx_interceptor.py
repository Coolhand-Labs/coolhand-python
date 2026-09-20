"""Tests for coolhand.httpx_interceptor module."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from coolhand.httpx_interceptor import (
    DEFAULT_EXCLUDE_API_PATTERNS,
    DEFAULT_INTERCEPT_ADDRESSES,
    _is_binary_content_type,
    _is_excluded,
    _is_llm_api,
    _is_localhost,
    _is_streaming_content_type,
    _read_response_body,
    _should_capture_url,
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

    def test_elevenlabs(self):
        """Detects api.elevenlabs.io."""
        assert _is_llm_api("https://api.elevenlabs.io/v1/text-to-speech") is True

    def test_github_models(self):
        """Detects models.github.ai and the deprecated models.inference.ai.azure.com."""
        assert _is_llm_api("https://models.github.ai/chat/completions") is True
        assert (
            _is_llm_api("https://models.inference.ai.azure.com/chat/completions")
            is True
        )

    def test_azure_openai_openai_azure_com(self):
        """Detects Azure OpenAI resource on openai.azure.com."""
        url = (
            "https://my-resource.openai.azure.com/openai/deployments/gpt-4"
            "/chat/completions?api-version=2024-02-15-preview"
        )
        assert _is_llm_api(url) is True

    def test_azure_openai_cognitiveservices_azure_com(self):
        """Detects Azure OpenAI resource on cognitiveservices.azure.com."""
        url = (
            "https://my-resource.cognitiveservices.azure.com/openai/deployments/gpt-4"
            "/chat/completions?api-version=2024-02-15-preview"
        )
        assert _is_llm_api(url) is True

    def test_azure_cognitiveservices_non_openai_not_matched(self):
        """Non-OpenAI Cognitive Services traffic on the same host is not captured."""
        url = "https://my-resource.cognitiveservices.azure.com/speechtotext/v3.1/transcriptions"
        assert _is_llm_api(url) is False

    def test_azure_openai_v1_api_path(self):
        """Detects the Azure OpenAI v1 API path, not just /openai/deployments."""
        url = (
            "https://my-resource.cognitiveservices.azure.com/openai/v1/responses"
            "?api-version=preview"
        )
        assert _is_llm_api(url) is True

    def test_azure_foundry_model_inference(self):
        """Detects the Foundry model inference API on services.ai.azure.com."""
        url = (
            "https://my-resource.services.ai.azure.com/models/chat/completions"
            "?api-version=2024-05-01-preview"
        )
        assert _is_llm_api(url) is True

    def test_azure_model_inference_on_cognitiveservices_host(self):
        """An AI Services resource is reachable on its cognitiveservices FQDN too."""
        url = (
            "https://my-resource.cognitiveservices.azure.com/models/chat/completions"
            "?api-version=2024-05-01-preview"
        )
        assert _is_llm_api(url) is True

    def test_azure_foundry_openai_deployment(self):
        """Detects Azure OpenAI deployments hosted on a Foundry resource."""
        url = (
            "https://my-resource.services.ai.azure.com/openai/deployments/gpt-4o"
            "/chat/completions?api-version=2024-02-15-preview"
        )
        assert _is_llm_api(url) is True

    def test_azure_serverless_inference_endpoint(self):
        """Detects serverless (MaaS) deployments on inference.ai.azure.com."""
        url = (
            "https://mistral-large-abcde.eastus2.inference.ai.azure.com"
            "/v1/chat/completions"
        )
        assert _is_llm_api(url) is True

    def test_azure_serverless_models_endpoint(self):
        """Detects serverless API deployments on models.ai.azure.com."""
        url = "https://my-deployment.eastus2.models.ai.azure.com/chat/completions"
        assert _is_llm_api(url) is True

    def test_azure_ml_managed_online_endpoint(self):
        """Detects Azure ML managed online endpoints on inference.ml.azure.com."""
        url = "https://my-endpoint.eastus2.inference.ml.azure.com/score"
        assert _is_llm_api(url) is True

    def test_azure_openai_us_government_cloud(self):
        """Detects Azure OpenAI in the US Government cloud (.azure.us)."""
        url = "https://my-resource.openai.azure.us/openai/v1/chat/completions"
        assert _is_llm_api(url) is True

    def test_azure_openai_china_cloud(self):
        """Detects Azure OpenAI in the China / 21Vianet cloud (.azure.cn)."""
        url = "https://my-resource.openai.azure.cn/openai/v1/chat/completions"
        assert _is_llm_api(url) is True

    def test_azure_foundry_non_inference_not_matched(self):
        """Non-inference Foundry traffic on the shared host is not captured."""
        assert (
            _is_llm_api("https://my-resource.services.ai.azure.com/speech/recognize")
            is False
        )
        assert (
            _is_llm_api(
                "https://my-resource.services.ai.azure.com/contentsafety/text:analyze"
            )
            is False
        )

    def test_azure_foundry_portal_not_matched(self):
        """The Foundry portal domain itself is not captured."""
        assert _is_llm_api("https://ai.azure.com/build/overview") is False

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

    def test_vertex_ai_any_path_matched(self):
        """Detects any Vertex AI URL via aiplatform.googleapis.com domain match."""
        url = (
            "https://us-central1-aiplatform.googleapis.com/v1"
            "/projects/my-project/locations/us-central1"
            "/publishers/google/models/gemini-pro:predict"
        )
        assert _is_llm_api(url) is True

    def test_vertex_ai_predict(self):
        """Detects Vertex AI :predict endpoint."""
        url = (
            "https://us-central1-aiplatform.googleapis.com/v1"
            "/projects/my-project/locations/us-central1"
            "/endpoints/123456:predict"
        )
        assert _is_llm_api(url) is True

    def test_vertex_ai_stream_raw_predict(self):
        """Detects Vertex AI :streamRawPredict endpoint."""
        url = (
            "https://us-central1-aiplatform.googleapis.com/v1"
            "/projects/my-project/locations/us-central1"
            "/endpoints/123456:streamRawPredict"
        )
        assert _is_llm_api(url) is True

    def test_vertex_ai_openai_compatible(self):
        """Detects Vertex AI OpenAI-compatible /chat/completions endpoint."""
        url = (
            "https://aiplatform.googleapis.com/v1/projects/my-project"
            "/locations/us-central1/endpoints/openapi/chat/completions"
        )
        assert _is_llm_api(url) is True

    def test_cloudflare_ai_gateway(self):
        """Detects Cloudflare AI Gateway."""
        url = (
            "https://gateway.ai.cloudflare.com/v1/my-account"
            "/my-gateway/openai/chat/completions"
        )
        assert _is_llm_api(url) is True

    def test_openrouter(self):
        """Detects OpenRouter API."""
        url = "https://openrouter.ai/api/v1/chat/completions"
        assert _is_llm_api(url) is True

    def test_opencode(self):
        """Detects OpenCode API."""
        url = "https://opencode.ai/zen/v1/chat/completions"
        assert _is_llm_api(url) is True

    @pytest.mark.parametrize(
        "url",
        [
            "https://api.deepseek.com/chat/completions",
            "https://api.mistral.ai/v1/chat/completions",
            "https://api.perplexity.ai/chat/completions",
            "https://api.x.ai/v1/chat/completions",
        ],
    )
    def test_openai_compatible_providers(self, url):
        """Detects DeepSeek, Mistral, Perplexity and xAI."""
        assert _should_capture_url(url) is True

    @pytest.mark.parametrize(
        "url",
        [
            "https://api.cohere.com/v2/chat",
            "https://api.cohere.ai/v2/chat",
            "https://api.cohere.com/v1/embed",
            "https://api.cohere.ai/v1/embed",
            "https://api.cohere.com/v2/embed",
            "https://api.cohere.ai/v2/embed",
        ],
    )
    def test_cohere_supported_paths(self, url):
        """Detects the Cohere endpoints the server ingests."""
        assert _should_capture_url(url) is True

    @pytest.mark.parametrize(
        "url",
        [
            "https://api.cohere.com/v1/chat",
            "https://api.cohere.ai/v1/chat",
            "https://api.cohere.com/v2/rerank",
            "https://api.cohere.com/v1/tokenize",
            "https://api.cohere.com/v1/classify",
            "https://api.cohere.com/v1/embed-jobs",
            "https://api.cohere.ai/v1/embed-jobs",
            "https://api.cohere.com/",
        ],
    )
    def test_cohere_unsupported_paths_not_captured(self, url):
        """Cohere is path-scoped, not host-wide."""
        assert _should_capture_url(url) is False

    def test_typesafe_jev(self):
        """Detects TypeSafe Jev (System One)."""
        assert _should_capture_url("https://api.typesafe.ai/v1/systemone") is True

    def test_typesafe_other_paths_not_captured(self):
        """TypeSafe is path-scoped to /v1/systemone."""
        assert _should_capture_url("https://api.typesafe.ai/v1/models") is False
        assert _should_capture_url("https://api.typesafe.ai/") is False

    @pytest.mark.parametrize(
        "url",
        [
            "https://bedrock-runtime.us-east-1.amazonaws.com/model/foo/converse",
            "https://bedrock-runtime.eu-west-2.amazonaws.com/model/foo/invoke",
            "https://bedrock-runtime-fips.us-gov-west-1.amazonaws.com/model/foo/converse",
        ],
    )
    def test_bedrock_runtime(self, url):
        """Detects Bedrock runtime hosts in any region."""
        assert _should_capture_url(url) is True

    def test_bedrock_other_services_not_captured(self):
        """Bedrock control-plane and agent hosts are not the runtime host."""
        assert (
            _should_capture_url(
                "https://bedrock.us-east-1.amazonaws.com/foundation-models"
            )
            is False
        )
        assert (
            _should_capture_url(
                "https://bedrock-agent-runtime.us-east-1.amazonaws.com/agents"
            )
            is False
        )

    @pytest.mark.parametrize(
        "path", ["/api/chat", "/api/generate", "/api/embed", "/api/embeddings"]
    )
    def test_ollama_on_default_port(self, path):
        """Detects Ollama paths on its default port for non-localhost hosts."""
        assert _should_capture_url("http://ollama:11434" + path) is True
        assert _should_capture_url("http://gpu-box.lan:11434" + path) is True

    def test_ollama_path_without_port_not_captured(self):
        """A bare /api/chat on an unrelated host is never captured."""
        assert _should_capture_url("https://example.com/api/chat") is False
        assert _should_capture_url("https://app.internal:8080/api/generate") is False
        assert _should_capture_url("https://example.com/api/embed") is False

    def test_ollama_other_paths_on_default_port_not_captured(self):
        """Only the four inference paths match, not the rest of Ollama's API."""
        assert _should_capture_url("http://ollama:11434/api/tags") is False
        assert _should_capture_url("http://ollama:11434/api/pull") is False

    def test_ollama_localhost_still_skipped(self):
        """Bare localhost stays uncaptured; the localhost guard is unchanged."""
        assert _should_capture_url("http://localhost:11434/api/chat") is False

    def test_all_default_addresses(self):
        """All default intercept addresses are detected."""
        for addr in DEFAULT_INTERCEPT_ADDRESSES:
            if addr.startswith(":1"):
                url = "http://ollama" + addr
            elif addr.startswith(":"):
                url = "https://example.googleapis.com/v1/models/gemini" + addr
            elif addr.startswith("//"):
                url = "https:" + addr + "us-east-1.amazonaws.com/v1/test"
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
        """Passing None back to the setter restores the defaults."""
        set_intercept_addresses(["api.custom-llm.com"])
        assert _is_llm_api("https://api.openai.com/v1/chat/completions") is False

        set_intercept_addresses(None)
        assert _is_llm_api("https://api.openai.com/v1/chat/completions") is True

    def test_malformed_list_matches_nothing_and_logs(
        self, caplog, reset_global_instance
    ):
        """A non-str entry can't silently disable matching without a trace.

        ``any()`` short-circuits, so a malformed entry only bites once matching
        reaches it — put it first to exercise the failure path.
        """
        import logging

        set_intercept_addresses([None, "api.openai.com"])  # type: ignore[list-item]
        with caplog.at_level(logging.DEBUG, logger="coolhand.httpx_interceptor"):
            assert _is_llm_api("https://api.openai.com/v1/chat/completions") is False

        assert "Failed to match URL against pattern list" in caplog.text

    def test_empty_list_matches_nothing(self, reset_global_instance):
        """Explicit empty list means capture nothing, not defaults."""
        set_intercept_addresses([])
        assert _is_llm_api("https://api.openai.com/v1/chat/completions") is False
        assert _is_llm_api("https://api.anthropic.com/v1/messages") is False


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


class TestIsBinaryContentType:
    """Tests for _is_binary_content_type helper function."""

    def test_audio(self):
        """Detects audio/* content types."""
        assert _is_binary_content_type("audio/mpeg") is True
        assert _is_binary_content_type("audio/wav") is True

    def test_video(self):
        """Detects video/* content types."""
        assert _is_binary_content_type("video/mp4") is True

    def test_image(self):
        """Detects image/* content types."""
        assert _is_binary_content_type("image/png") is True
        assert _is_binary_content_type("image/jpeg") is True

    def test_octet_stream(self):
        """Detects application/octet-stream."""
        assert _is_binary_content_type("application/octet-stream") is True

    def test_bedrock_eventstream(self):
        """Bedrock binary event-stream frames are not decodable as text."""
        assert _is_binary_content_type("application/vnd.amazon.eventstream") is True

    def test_case_insensitive(self):
        """Content-Type matching is case-insensitive."""
        assert _is_binary_content_type("Audio/MPEG") is True
        assert _is_binary_content_type("Image/PNG") is True

    def test_non_binary(self):
        """Rejects text and JSON content types."""
        assert _is_binary_content_type("application/json") is False
        assert _is_binary_content_type("text/plain") is False
        assert _is_binary_content_type("text/event-stream") is False


class TestReadResponseBody:
    """Tests for _read_response_body helper function."""

    def test_streaming_returns_placeholder(self):
        """Streaming responses return [streaming] placeholder."""
        response = MagicMock()
        response.headers = {"content-type": "text/event-stream"}
        assert _read_response_body(response) == "[streaming]"

    def test_binary_returns_placeholder(self):
        """Binary responses return [binary] placeholder instead of raw bytes."""
        response = MagicMock()
        response.headers = {"content-type": "audio/mpeg"}
        assert _read_response_body(response) == "[binary]"

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
            saved_original_async_send = httpx_interceptor._original_async_send
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

            httpx_interceptor._original_async_send = saved_original_async_send

            # Should have captured the streaming response
            assert len(captured_requests) == 1
            req, res, err = captured_requests[0]
            assert res["is_streaming"] is True
            assert res["body"] == 'data: {"chunk": 1}\ndata: {"chunk": 2}\n'

        finally:
            unpatch()

    @pytest.mark.parametrize(
        ("attr_name", "takes_chunk_size", "chunks"),
        [
            ("aiter_bytes", True, [b"chunk1", b"chunk2"]),
            ("aiter_text", False, ["chunk1", "chunk2"]),
            ("aiter_raw", True, [b"chunk1", b"chunk2"]),
        ],
    )
    @pytest.mark.asyncio
    async def test_async_streaming_captures_via_aiter_variant(
        self, reset_global_instance, attr_name, takes_chunk_size, chunks
    ):
        """Async streaming is captured regardless of which aiter_* method is drained."""
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

            if takes_chunk_size:

                async def mock_aiter(chunk_size=1024):
                    for chunk in chunks:
                        yield chunk
            else:

                async def mock_aiter():
                    for chunk in chunks:
                        yield chunk

            for name in ("aiter_bytes", "aiter_lines", "aiter_text", "aiter_raw"):
                setattr(mock_response, name, mock_aiter if name == attr_name else None)

            # Mock the original async send
            saved_original_async_send = httpx_interceptor._original_async_send
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
                # Consume the stream via the variant under test
                async for _ in getattr(response, attr_name)():
                    pass

            httpx_interceptor._original_async_send = saved_original_async_send

            # Should have captured the streaming response
            assert len(captured_requests) == 1
            req, res, err = captured_requests[0]
            assert res["is_streaming"] is True
            assert res["body"] == "chunk1chunk2"

        finally:
            unpatch()

    @pytest.mark.asyncio
    async def test_async_streaming_empty_chunks_not_captured(
        self, reset_global_instance
    ):
        """An immediately-exhausted stream never fires the handler.

        Documents existing behavior in httpx_interceptor.send_captured(), which
        only invokes the handler `if not content_sent[0] and captured_chunks`
        — an empty stream leaves captured_chunks empty, so the request/response
        pair is silently dropped rather than reported with an empty body.
        """
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
            mock_response.headers = {"content-type": "text/event-stream"}

            async def mock_aiter_bytes(chunk_size=1024):
                for chunk in ():
                    yield chunk

            mock_response.aiter_bytes = mock_aiter_bytes
            mock_response.aiter_lines = None
            mock_response.aiter_text = None
            mock_response.aiter_raw = None

            saved_original_async_send = httpx_interceptor._original_async_send
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
                async for _ in response.aiter_bytes():
                    pass

            httpx_interceptor._original_async_send = saved_original_async_send

            assert captured_requests == []

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
        """Passing None back to the setter restores DEFAULT_EXCLUDE_API_PATTERNS."""
        set_exclude_api_patterns(["/somethingElse/"])
        set_exclude_api_patterns(None)
        url = (
            "https://aiplatform.googleapis.com/v1/projects/my-project"
            "/locations/us-central1/batchPredictionJobs/123"
        )
        assert _is_excluded(url) is True

    def test_default_constant_contains_batch_prediction_jobs(self):
        """DEFAULT_EXCLUDE_API_PATTERNS contains the expected default."""
        assert "/batchPredictionJobs/" in DEFAULT_EXCLUDE_API_PATTERNS

    def test_default_patterns_exclude_non_llm_vertex_paths(self, reset_global_instance):
        """Common non-LLM Vertex AI paths are excluded by default."""
        non_llm_paths = [
            "/datasets/",
            "/trainingPipelines/",
            "/pipelineJobs/",
            "/customJobs/",
            "/featurestores/",
            "/tensorboards/",
            "/metadataStores/",
            "/modelDeploymentMonitoringJobs/",
        ]
        base = "https://aiplatform.googleapis.com/v1/projects/proj/locations/us"
        for path in non_llm_paths:
            url = base + path + "123"
            assert _is_excluded(url) is True, f"Expected {path!r} to be excluded"

    def test_default_patterns_exclude_non_inference_azure_openai_paths(
        self, reset_global_instance
    ):
        """Azure OpenAI management paths are excluded, mirroring the Vertex list."""
        base = "https://my-resource.openai.azure.com"
        for url in [
            # Legacy deployments-era paths...
            f"{base}/openai/files?api-version=2024-10-21",
            f"{base}/openai/fine_tuning/jobs?api-version=2024-10-21",
            f"{base}/openai/batches?api-version=2024-10-21",
            # ...and their v1 API twins, which the legacy patterns do not match.
            f"{base}/openai/v1/files",
            f"{base}/openai/v1/fine_tuning/jobs",
            f"{base}/openai/v1/batches",
            # Model listing is control plane, not inference.
            f"{base}/openai/models?api-version=2024-10-21",
            f"{base}/openai/v1/models",
        ]:
            assert _is_excluded(url) is True, f"Expected {url!r} to be excluded"

        # Inference is still captured, on both the dedicated and Foundry hosts.
        for inference in [
            f"{base}/openai/v1/chat/completions",
            "https://my-resource.services.ai.azure.com/models/chat/completions",
        ]:
            assert _is_llm_api(inference) is True
            assert _is_excluded(inference) is False


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


class TestReentrancyGuard:
    """Tests that the reentrancy guard prevents double-logging."""

    def test_no_double_log_on_sync_recursive_send(self, reset_global_instance):
        """Handler fires exactly once even when _original_send calls self.send() again
        (simulates httpx redirect handling that recurses through patched_send)."""
        import httpx

        from coolhand import httpx_interceptor

        handler = MagicMock()
        set_handler(handler)
        patch_httpx()

        try:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.headers = {"content-type": "application/json"}
            mock_response._content = b'{"id": "chatcmpl-1"}'
            mock_response.content = b'{"id": "chatcmpl-1"}'

            call_count = [0]

            def recursive_original_send(self, request, **kwargs):
                call_count[0] += 1
                if call_count[0] == 1:
                    # Simulate a redirect: triggers patched_send again
                    httpx.Client.send(self, request)
                return mock_response

            original_original = httpx_interceptor._original_send
            httpx_interceptor._original_send = recursive_original_send

            mock_request = MagicMock()
            mock_request.method = "POST"
            mock_request.url = "https://api.openai.com/v1/chat/completions"
            mock_request.headers = {"Content-Type": "application/json"}
            mock_request.content = b'{"model": "gpt-4"}'

            client = httpx.Client()
            httpx.Client.send(client, mock_request)

            httpx_interceptor._original_send = original_original

            # Handler must fire exactly once despite the inner self.send() call
            assert handler.call_count == 1
        finally:
            unpatch()

    def test_no_double_log_on_async_recursive_send(self, reset_global_instance):
        """Async handler fires exactly once even when _original_async_send recurses."""
        import asyncio

        import httpx

        from coolhand import httpx_interceptor

        handler = MagicMock()
        set_handler(handler)
        patch_httpx()

        try:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.headers = {"content-type": "application/json"}
            mock_response._content = b'{"id": "chatcmpl-2"}'
            mock_response.content = b'{"id": "chatcmpl-2"}'

            call_count = [0]

            async def recursive_original_async_send(self, request, **kwargs):
                call_count[0] += 1
                if call_count[0] == 1:
                    await httpx.AsyncClient.send(self, request)
                return mock_response

            original_original = httpx_interceptor._original_async_send
            httpx_interceptor._original_async_send = recursive_original_async_send

            mock_request = MagicMock()
            mock_request.method = "POST"
            mock_request.url = "https://api.openai.com/v1/chat/completions"
            mock_request.headers = {"Content-Type": "application/json"}
            mock_request.content = b'{"model": "gpt-4"}'

            async def run():
                client = httpx.AsyncClient()
                await httpx.AsyncClient.send(client, mock_request)

            asyncio.run(run())

            httpx_interceptor._original_async_send = original_original

            assert handler.call_count == 1
        finally:
            unpatch()

    def test_handler_exception_does_not_cause_double_log(self, reset_global_instance):
        """If the handler raises on the success path, the error-path handler does not
        fire again — the original response is still returned to the caller."""
        import httpx

        from coolhand import httpx_interceptor

        call_count = [0]

        def flaky_handler(req, res, err):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("handler blew up")

        set_handler(flaky_handler)
        patch_httpx()

        try:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.headers = {"content-type": "application/json"}
            mock_response._content = b'{"ok": true}'
            mock_response.content = b'{"ok": true}'

            original_original = httpx_interceptor._original_send
            httpx_interceptor._original_send = MagicMock(return_value=mock_response)

            mock_request = MagicMock()
            mock_request.method = "POST"
            mock_request.url = "https://api.openai.com/v1/chat/completions"
            mock_request.headers = {"Content-Type": "application/json"}
            mock_request.content = b'{"model": "gpt-4"}'

            client = httpx.Client()
            result = httpx.Client.send(client, mock_request)

            httpx_interceptor._original_send = original_original

            # Handler raised on the first (success) call; must not be called a
            # second time via the error path — total invocations must be 1.
            assert call_count[0] == 1
            # The original response must still reach the caller
            assert result is mock_response
        finally:
            unpatch()


class TestNewProviderSenderBehavior:
    """Sender-level behaviour for the DeepSeek/Cohere/Bedrock/Ollama additions."""

    def test_bedrock_eventstream_recorded_as_binary_sync(self, reset_global_instance):
        """A Bedrock eventstream response reaches the handler as "[binary]"."""
        captured = []
        set_handler(lambda req, res, err: captured.append(res))
        patch_httpx()

        try:
            import httpx

            from coolhand import httpx_interceptor

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.headers = {
                "content-type": "application/vnd.amazon.eventstream"
            }
            mock_response._content = b"\x00\x00\x00\x2a\x00\x00\x00\x1dframed"
            mock_response.content = mock_response._content

            original_send = httpx_interceptor._original_send
            httpx_interceptor._original_send = MagicMock(return_value=mock_response)
            try:
                mock_request = MagicMock()
                mock_request.method = "POST"
                mock_request.url = (
                    "https://bedrock-runtime.us-east-1.amazonaws.com"
                    "/model/anthropic.claude/converse-stream"
                )
                mock_request.headers = {}
                mock_request.content = b"{}"
                httpx.Client.send(httpx.Client(), mock_request)
            finally:
                httpx_interceptor._original_send = original_send

            assert len(captured) == 1
            assert captured[0]["body"] == "[binary]"
        finally:
            unpatch()

    def test_overriding_exclude_patterns_recaptures_cohere_embed_jobs(
        self, reset_global_instance
    ):
        """Replacing the deny-list drops the default Cohere embed-jobs guard."""
        url = "https://api.cohere.com/v1/embed-jobs"
        assert _should_capture_url(url) is False

        set_exclude_api_patterns([])
        assert _should_capture_url(url) is True
