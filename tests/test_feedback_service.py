"""Tests for FeedbackService."""

import json
import warnings
from unittest.mock import MagicMock, patch

import pytest

from coolhand import (
    FeedbackData,
    FeedbackService,
    create_feedback,
    get_feedback_service,
)


@pytest.fixture
def mock_feedback_urlopen():
    """Mock urllib urlopen for feedback API tests."""
    with patch("coolhand.feedback_service.urlopen") as mock:
        mock_response = MagicMock()
        mock_response.status = 201
        mock_response.read.return_value = json.dumps(
            {
                "id": "xyz789abc123",
                "llm_request_log_id": "abc123def456",
                "workload_id": "wkld789xyz123",
                "like": True,
                "explanation": "Great response",
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z",
            }
        ).encode("utf-8")
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock.return_value = mock_response
        yield mock


@pytest.fixture
def feedback_service(mock_config):
    """Create a FeedbackService instance for testing."""
    return FeedbackService(config=mock_config)


@pytest.fixture
def reset_default_service():
    """Reset the default feedback service between tests."""
    import coolhand.feedback_service as fs

    original = fs._default_service
    fs._default_service = None
    yield
    fs._default_service = original


class TestFeedbackServiceInit:
    """Test FeedbackService initialization."""

    def test_init_with_config(self, mock_config):
        """Test initialization with config dict."""
        service = FeedbackService(config=mock_config)
        assert service.api_key == "test-api-key-12345678"
        assert service.silent is True

    def test_init_with_kwargs(self):
        """Test initialization with kwargs."""
        service = FeedbackService(api_key="my-key", silent=False)
        assert service.api_key == "my-key"
        assert service.silent is False

    def test_init_with_env_vars(self, monkeypatch):
        """Test initialization from environment variables."""
        monkeypatch.setenv("COOLHAND_API_KEY", "env-key-12345")
        monkeypatch.setenv("COOLHAND_SILENT", "false")

        service = FeedbackService()
        assert service.api_key == "env-key-12345"
        assert service.silent is False


class TestCreateFeedback:
    """Test FeedbackService.create_feedback method."""

    def test_create_feedback_success(self, feedback_service, mock_feedback_urlopen):
        """Test successful feedback creation."""
        feedback: FeedbackData = {
            "llm_request_log_id": 12345,
            "sentiment": "like",
            "explanation": "Great response",
        }

        result = feedback_service.create_feedback(feedback)

        assert result is not None
        assert result["id"] == "xyz789abc123"
        assert result["llm_request_log_id"] == "abc123def456"
        assert result["workload_id"] == "wkld789xyz123"
        assert result["like"] is True

        # Verify API was called
        mock_feedback_urlopen.assert_called_once()

    def test_create_feedback_with_revised_output(
        self, feedback_service, mock_feedback_urlopen
    ):
        """Test feedback with revised output."""
        feedback: FeedbackData = {
            "llm_request_log_id": 12345,
            "sentiment": "dislike",
            "explanation": "Incorrect information",
            "revised_output": "The correct answer is 42.",
        }

        result = feedback_service.create_feedback(feedback)
        assert result is not None

        # Verify payload includes revised_output
        call_args = mock_feedback_urlopen.call_args
        request = call_args[0][0]
        payload = json.loads(request.data.decode("utf-8"))
        assert (
            payload["llm_request_log_feedback"]["revised_output"]
            == "The correct answer is 42."
        )

    def test_create_feedback_with_fuzzy_match(
        self, feedback_service, mock_feedback_urlopen
    ):
        """Test feedback using original_output for fuzzy matching."""
        feedback: FeedbackData = {
            "original_output": "The capital of France is London.",
            "sentiment": "dislike",
            "revised_output": "The capital of France is Paris.",
        }

        result = feedback_service.create_feedback(feedback)
        assert result is not None

    def test_create_feedback_with_provider_id(
        self, feedback_service, mock_feedback_urlopen
    ):
        """Test feedback using llm_provider_unique_id."""
        feedback: FeedbackData = {
            "llm_provider_unique_id": "req-abc123",
            "sentiment": "like",
        }

        result = feedback_service.create_feedback(feedback)
        assert result is not None

    def test_create_feedback_like_true_converts_to_sentiment_like(
        self, feedback_service, mock_feedback_urlopen
    ):
        """like=True is converted to sentiment='like' in the payload and warns."""
        with pytest.warns(DeprecationWarning, match="'like' is deprecated"):
            feedback_service.create_feedback(
                {"llm_request_log_id": 12345, "like": True}
            )

        payload = json.loads(mock_feedback_urlopen.call_args[0][0].data.decode("utf-8"))
        fb = payload["llm_request_log_feedback"]
        assert fb["sentiment"] == "like"
        assert "like" not in fb  # deprecated field stripped from wire payload

    def test_create_feedback_like_false_converts_to_sentiment_dislike(
        self, feedback_service, mock_feedback_urlopen
    ):
        """like=False is converted to sentiment='dislike' in the payload and warns."""
        with pytest.warns(DeprecationWarning, match="'like' is deprecated"):
            feedback_service.create_feedback(
                {"llm_request_log_id": 12345, "like": False}
            )

        payload = json.loads(mock_feedback_urlopen.call_args[0][0].data.decode("utf-8"))
        fb = payload["llm_request_log_feedback"]
        assert fb["sentiment"] == "dislike"
        assert "like" not in fb  # deprecated field stripped from wire payload

    def test_create_feedback_explicit_sentiment_not_overridden_by_like(
        self, feedback_service, mock_feedback_urlopen
    ):
        """Explicit sentiment takes precedence; like is stripped from wire payload."""
        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            feedback_service.create_feedback(
                {"llm_request_log_id": 12345, "like": True, "sentiment": "neutral"}
            )

        fb = json.loads(mock_feedback_urlopen.call_args[0][0].data.decode("utf-8"))[
            "llm_request_log_feedback"
        ]
        assert fb["sentiment"] == "neutral"
        assert "like" not in fb  # like stripped even when sentiment is already set

    def test_create_feedback_like_with_sentiment_no_warning(
        self, feedback_service, mock_feedback_urlopen
    ):
        """Test that passing both 'like' and 'sentiment' does not warn."""
        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            feedback_service.create_feedback(
                {"llm_request_log_id": 12345, "like": True, "sentiment": "like"}
            )

    def test_create_feedback_without_like_succeeds(
        self, feedback_service, mock_feedback_urlopen
    ):
        """Test that missing 'like' does not raise an error."""
        feedback: FeedbackData = {
            "llm_request_log_id": 12345,
            "explanation": "Some explanation",
        }

        result = feedback_service.create_feedback(feedback)
        assert result is not None
        mock_feedback_urlopen.assert_called_once()

    def test_create_feedback_with_sentiment(
        self, feedback_service, mock_feedback_urlopen
    ):
        """Test feedback using sentiment string instead of like bool."""
        feedback: FeedbackData = {
            "llm_request_log_id": 12345,
            "sentiment": "dislike",
            "explanation": "Response was incorrect",
        }

        result = feedback_service.create_feedback(feedback)
        assert result is not None

        call_args = mock_feedback_urlopen.call_args
        request = call_args[0][0]
        payload = json.loads(request.data.decode("utf-8"))
        assert payload["llm_request_log_feedback"]["sentiment"] == "dislike"
        assert "like" not in payload["llm_request_log_feedback"]

    def test_create_feedback_with_workload_hashid(
        self, feedback_service, mock_feedback_urlopen
    ):
        """Test feedback with workload_hashid passes through payload."""
        feedback: FeedbackData = {
            "llm_request_log_id": 12345,
            "sentiment": "like",
            "workload_hashid": "abc123",
        }

        feedback_service.create_feedback(feedback)

        call_args = mock_feedback_urlopen.call_args
        request = call_args[0][0]
        payload = json.loads(request.data.decode("utf-8"))
        assert payload["llm_request_log_feedback"]["workload_hashid"] == "abc123"

    def test_create_feedback_with_creator_type(
        self, feedback_service, mock_feedback_urlopen
    ):
        """Test feedback with creator_type passes through payload."""
        feedback: FeedbackData = {
            "llm_request_log_id": 12345,
            "sentiment": "like",
            "creator_type": "agent",
        }

        feedback_service.create_feedback(feedback)

        call_args = mock_feedback_urlopen.call_args
        request = call_args[0][0]
        payload = json.loads(request.data.decode("utf-8"))
        assert payload["llm_request_log_feedback"]["creator_type"] == "agent"

    def test_create_feedback_no_api_key_returns_none(self, mock_feedback_urlopen):
        """Test that missing API key returns None without calling API."""
        service = FeedbackService(api_key="", silent=True)
        feedback: FeedbackData = {
            "llm_request_log_id": 12345,
            "sentiment": "like",
        }

        result = service.create_feedback(feedback)
        assert result is None
        mock_feedback_urlopen.assert_not_called()

    def test_create_feedback_includes_collector(
        self, feedback_service, mock_feedback_urlopen
    ):
        """Test that collector string is added to payload."""
        feedback: FeedbackData = {
            "llm_request_log_id": 12345,
            "sentiment": "like",
        }

        feedback_service.create_feedback(feedback)

        # Verify collector is in payload
        call_args = mock_feedback_urlopen.call_args
        request = call_args[0][0]
        payload = json.loads(request.data.decode("utf-8"))
        collector = payload["llm_request_log_feedback"]["collector"]
        assert "coolhand-python" in collector
        assert "manual" in collector

    def test_create_feedback_does_not_override_caller_collector(
        self, feedback_service, mock_feedback_urlopen
    ):
        """Test that a caller-supplied collector is not overridden by the SDK."""
        feedback: FeedbackData = {
            "llm_request_log_id": 12345,
            "sentiment": "like",
            "collector": "my-custom-collector",
        }

        feedback_service.create_feedback(feedback)

        call_args = mock_feedback_urlopen.call_args
        request = call_args[0][0]
        payload = json.loads(request.data.decode("utf-8"))
        assert payload["llm_request_log_feedback"]["collector"] == "my-custom-collector"

    def test_create_feedback_warns_no_matching_field(
        self, feedback_service, mock_feedback_urlopen, caplog
    ):
        """Test warning when no matching field is provided."""
        import logging

        caplog.set_level(logging.WARNING)

        feedback: FeedbackData = {
            "sentiment": "like",
            "explanation": "Good response",
        }

        feedback_service.create_feedback(feedback)

        assert "No matching field provided" in caplog.text


class TestFeedbackServiceHTTPErrors:
    """Test FeedbackService error handling."""

    def test_http_error_returns_none(self, feedback_service):
        """Test that HTTP errors return None."""
        from urllib.error import HTTPError

        with patch("coolhand.feedback_service.urlopen") as mock:
            mock.side_effect = HTTPError(
                url="https://coolhandlabs.com/api/v2/llm_request_log_feedbacks",
                code=500,
                msg="Internal Server Error",
                hdrs={},
                fp=None,
            )

            feedback: FeedbackData = {
                "llm_request_log_id": 12345,
                "sentiment": "like",
            }

            result = feedback_service.create_feedback(feedback)
            assert result is None

    def test_url_error_returns_none(self, feedback_service):
        """Test that URL errors return None."""
        from urllib.error import URLError

        with patch("coolhand.feedback_service.urlopen") as mock:
            mock.side_effect = URLError("Connection refused")

            feedback: FeedbackData = {
                "llm_request_log_id": 12345,
                "sentiment": "like",
            }

            result = feedback_service.create_feedback(feedback)
            assert result is None

    def test_create_feedback_passes_ssl_context_to_urlopen(self, feedback_service):
        """create_feedback passes _ssl_context as context= argument to urlopen."""
        import ssl
        from unittest.mock import MagicMock

        sentinel_ctx = ssl.create_default_context()
        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.status = 201
        mock_resp.read.return_value = b'{"id": 1}'

        feedback: FeedbackData = {"llm_request_log_id": 12345, "sentiment": "like"}

        with (
            patch("coolhand.feedback_service._ssl_context", sentinel_ctx),
            patch(
                "coolhand.feedback_service.urlopen", return_value=mock_resp
            ) as mock_open,
        ):
            feedback_service.create_feedback(feedback)
            _, kwargs = mock_open.call_args
            assert kwargs.get("context") is sentinel_ctx

    def test_create_feedback_ssl_context_none_when_certifi_missing(
        self, feedback_service
    ):
        """create_feedback passes context=None to urlopen when certifi unavailable."""
        from unittest.mock import MagicMock

        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.status = 201
        mock_resp.read.return_value = b'{"id": 1}'

        feedback: FeedbackData = {"llm_request_log_id": 12345, "sentiment": "like"}

        with (
            patch("coolhand.feedback_service._ssl_context", None),
            patch(
                "coolhand.feedback_service.urlopen", return_value=mock_resp
            ) as mock_open,
        ):
            feedback_service.create_feedback(feedback)
            _, kwargs = mock_open.call_args
            assert kwargs.get("context") is None


class TestModuleLevelFunctions:
    """Test module-level convenience functions."""

    def test_get_feedback_service_creates_instance(
        self, mock_config, reset_default_service
    ):
        """Test get_feedback_service creates a new instance."""
        service = get_feedback_service(config=mock_config)
        assert isinstance(service, FeedbackService)
        assert service.api_key == mock_config["api_key"]

    def test_get_feedback_service_returns_default(
        self, mock_config, reset_default_service
    ):
        """Test get_feedback_service returns default when no config."""
        # Create initial service
        service1 = get_feedback_service(config=mock_config)

        # Should return same instance
        service2 = get_feedback_service()
        assert service1 is service2

    def test_create_feedback_function(
        self, mock_config, mock_feedback_urlopen, reset_default_service
    ):
        """Test module-level create_feedback function."""
        feedback: FeedbackData = {
            "llm_request_log_id": 12345,
            "sentiment": "like",
        }

        result = create_feedback(feedback, api_key=mock_config["api_key"])
        assert result is not None


class TestCoolhandIntegration:
    """Test FeedbackService integration with main Coolhand class."""

    def test_coolhand_has_feedback_service(self, reset_global_instance, mock_config):
        """Test Coolhand instance has feedback_service property."""
        from coolhand import Coolhand

        with patch("coolhand.httpx_interceptor.patch"):
            instance = Coolhand(config=mock_config)

        assert hasattr(instance, "feedback_service")
        assert isinstance(instance.feedback_service, FeedbackService)

    def test_coolhand_create_feedback(
        self, reset_global_instance, mock_config, mock_feedback_urlopen
    ):
        """Test Coolhand.create_feedback method."""
        from coolhand import Coolhand

        with patch("coolhand.httpx_interceptor.patch"):
            instance = Coolhand(config=mock_config)

        feedback: FeedbackData = {
            "llm_request_log_id": 12345,
            "sentiment": "like",
        }

        result = instance.create_feedback(feedback)
        assert result is not None


class TestFeedbackServiceLogging:
    """Tests for FeedbackService logging behavior."""

    def test_log_feedback_info_when_not_silent(self, mock_config, caplog):
        """_log_feedback_info logs details when silent=False."""
        import logging

        caplog.set_level(logging.INFO)

        mock_config["silent"] = False
        service = FeedbackService(config=mock_config)

        feedback: FeedbackData = {
            "llm_request_log_id": 12345,
            "like": True,
            "explanation": "This is a great response that helped me understand.",
            "revised_output": "Corrected version",
        }

        service._log_feedback_info(feedback)

        assert "Creating feedback for LLM Request Log ID: 12345" in caplog.text
        assert "thumbs up" in caplog.text
        assert "Explanation:" in caplog.text
        assert "Includes revised output" in caplog.text

    def test_log_feedback_info_truncates_long_explanation(self, mock_config, caplog):
        """_log_feedback_info truncates explanations over 100 chars."""
        import logging

        caplog.set_level(logging.INFO)

        mock_config["silent"] = False
        service = FeedbackService(config=mock_config)

        long_explanation = "x" * 150
        feedback: FeedbackData = {
            "llm_request_log_id": 12345,
            "like": False,
            "explanation": long_explanation,
        }

        service._log_feedback_info(feedback)

        assert "..." in caplog.text
        # Should not contain the full 150 char explanation
        assert long_explanation not in caplog.text

    def test_log_feedback_info_like_auto_conversion_logs_sentiment_string(
        self, mock_config, mock_feedback_urlopen, caplog
    ):
        """When like is auto-converted, the log reflects the sent sentiment string."""
        import logging

        caplog.set_level(logging.INFO)
        mock_config["silent"] = False
        service = FeedbackService(config=mock_config)

        with pytest.warns(DeprecationWarning):
            service.create_feedback({"llm_request_log_id": 12345, "like": True})

        assert "Sentiment: like" in caplog.text
        assert "thumbs up" not in caplog.text

    def test_log_feedback_info_without_like_uses_sentiment(self, mock_config, caplog):
        """_log_feedback_info uses sentiment string when like is absent."""
        import logging

        caplog.set_level(logging.INFO)

        mock_config["silent"] = False
        service = FeedbackService(config=mock_config)

        feedback: FeedbackData = {
            "llm_request_log_id": 12345,
            "sentiment": "neutral",
        }

        service._log_feedback_info(feedback)

        assert "neutral" in caplog.text
        assert "thumbs down" not in caplog.text

    def test_log_feedback_info_without_like_or_sentiment(self, mock_config, caplog):
        """_log_feedback_info logs 'unknown' when neither like nor sentiment present."""
        import logging

        caplog.set_level(logging.INFO)

        mock_config["silent"] = False
        service = FeedbackService(config=mock_config)

        feedback: FeedbackData = {
            "llm_request_log_id": 12345,
        }

        service._log_feedback_info(feedback)

        assert "unknown" in caplog.text
        assert "thumbs down" not in caplog.text

    def test_log_feedback_info_silent_mode(self, mock_config, caplog):
        """_log_feedback_info does nothing when silent=True."""
        import logging

        caplog.set_level(logging.INFO)

        mock_config["silent"] = True
        service = FeedbackService(config=mock_config)

        feedback: FeedbackData = {
            "llm_request_log_id": 12345,
            "like": True,
        }

        service._log_feedback_info(feedback)

        assert "Creating feedback" not in caplog.text


class TestFeedbackServiceEdgeCases:
    """Tests for edge cases in FeedbackService."""

    def test_create_feedback_unexpected_exception(self, feedback_service, caplog):
        """create_feedback handles unexpected exceptions gracefully."""
        import logging

        caplog.set_level(logging.WARNING)

        with patch("coolhand.feedback_service.urlopen") as mock:
            mock.side_effect = RuntimeError("Unexpected error")

            feedback: FeedbackData = {
                "llm_request_log_id": 12345,
                "sentiment": "like",
            }

            result = feedback_service.create_feedback(feedback)
            assert result is None
            assert "Unexpected error submitting feedback" in caplog.text

    def test_create_feedback_non_success_status_code(self, feedback_service, caplog):
        """create_feedback handles non-200/201 status codes."""
        import logging

        caplog.set_level(logging.WARNING)

        with patch("coolhand.feedback_service.urlopen") as mock:
            mock_response = MagicMock()
            mock_response.status = 400
            mock_response.__enter__ = MagicMock(return_value=mock_response)
            mock_response.__exit__ = MagicMock(return_value=False)
            mock.return_value = mock_response

            feedback: FeedbackData = {
                "llm_request_log_id": 12345,
                "sentiment": "like",
            }

            result = feedback_service.create_feedback(feedback)
            assert result is None
            assert "Unexpected status code: 400" in caplog.text

    def test_get_collector_string_format(self, mock_config):
        """_get_collector_string returns expected format."""
        service = FeedbackService(config=mock_config)
        collector = service._get_collector_string()

        assert "coolhand-python" in collector
        assert "manual" in collector

    def test_get_feedback_service_new_when_config_provided(
        self, mock_config, reset_default_service
    ):
        """get_feedback_service creates new service when config provided."""
        # Create initial default service
        service1 = get_feedback_service(api_key="first-key")

        # Create new service with different config
        service2 = get_feedback_service(config=mock_config)

        # Should be different instances
        assert service1 is not service2
        assert service2.api_key == mock_config["api_key"]


class TestFeedbackServiceBaseUrl:
    """Tests for base_url configuration in FeedbackService."""

    def test_default_base_url(self):
        """No config → defaults to coolhandlabs.com."""
        service = FeedbackService(api_key="key")
        assert service.config["base_url"] == "https://coolhandlabs.com"

    def test_base_url_from_constructor_kwarg(self):
        """base_url kwarg is used."""
        service = FeedbackService(api_key="key", base_url="https://custom.example.com")
        assert service.config["base_url"] == "https://custom.example.com"

    def test_base_url_from_config_dict(self):
        """base_url in config dict is used."""
        service = FeedbackService(
            config={"api_key": "key", "base_url": "https://custom.example.com"}
        )
        assert service.config["base_url"] == "https://custom.example.com"

    def test_base_url_trailing_slash_normalized(self):
        """Trailing slash is stripped."""
        service = FeedbackService(api_key="key", base_url="https://example.com/")
        assert service.config["base_url"] == "https://example.com"

    def test_base_url_from_env_var(self, monkeypatch):
        """COOLHAND_BASE_URL env var is picked up."""
        monkeypatch.setenv("COOLHAND_BASE_URL", "https://self-hosted.example.com")
        service = FeedbackService(api_key="key")
        assert service.config["base_url"] == "https://self-hosted.example.com"

    def test_constructor_overrides_env(self, monkeypatch):
        """Explicit base_url overrides COOLHAND_BASE_URL."""
        monkeypatch.setenv("COOLHAND_BASE_URL", "https://env.example.com")
        service = FeedbackService(
            api_key="key", base_url="https://explicit.example.com"
        )
        assert service.config["base_url"] == "https://explicit.example.com"

    def test_invalid_base_url_raises(self):
        """Non-https base_url raises ValueError."""
        import pytest

        with pytest.raises(ValueError, match="must use https://"):
            FeedbackService(api_key="key", base_url="http://evil.example.com")

    def test_localhost_base_url_allowed(self):
        """http://localhost is allowed for local dev."""
        service = FeedbackService(api_key="key", base_url="http://localhost:4000")
        assert service.config["base_url"] == "http://localhost:4000"

    def test_create_feedback_posts_to_custom_base_url(self, mock_feedback_urlopen):
        """create_feedback POSTs to the configured base_url."""
        service = FeedbackService(
            api_key="test-key-12345678",
            base_url="https://self-hosted.example.com",
        )
        feedback: FeedbackData = {"llm_request_log_id": 1, "sentiment": "like"}
        service.create_feedback(feedback)

        call_args = mock_feedback_urlopen.call_args
        request = call_args[0][0]
        assert "self-hosted.example.com" in request.full_url
