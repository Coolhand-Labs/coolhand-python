"""Tests for FeedbackService."""

import json
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
                "id": 123,
                "llm_request_log_id": 12345,
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
            "like": True,
            "explanation": "Great response",
        }

        result = feedback_service.create_feedback(feedback)

        assert result is not None
        assert result["id"] == 123
        assert result["llm_request_log_id"] == 12345
        assert result["like"] is True

        # Verify API was called
        mock_feedback_urlopen.assert_called_once()

    def test_create_feedback_with_revised_output(
        self, feedback_service, mock_feedback_urlopen
    ):
        """Test feedback with revised output."""
        feedback: FeedbackData = {
            "llm_request_log_id": 12345,
            "like": False,
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
            "like": False,
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
            "like": True,
        }

        result = feedback_service.create_feedback(feedback)
        assert result is not None

    def test_create_feedback_missing_like_raises_error(self, feedback_service):
        """Test that missing 'like' field raises ValueError."""
        feedback = {
            "llm_request_log_id": 12345,
            "explanation": "Some explanation",
        }

        with pytest.raises(ValueError, match="'like' field is required"):
            feedback_service.create_feedback(feedback)

    def test_create_feedback_no_api_key_returns_none(self, mock_feedback_urlopen):
        """Test that missing API key returns None without calling API."""
        service = FeedbackService(api_key="", silent=True)
        feedback: FeedbackData = {
            "llm_request_log_id": 12345,
            "like": True,
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
            "like": True,
        }

        feedback_service.create_feedback(feedback)

        # Verify collector is in payload
        call_args = mock_feedback_urlopen.call_args
        request = call_args[0][0]
        payload = json.loads(request.data.decode("utf-8"))
        collector = payload["llm_request_log_feedback"]["collector"]
        assert "coolhand-python" in collector
        assert "manual" in collector

    def test_create_feedback_warns_no_matching_field(
        self, feedback_service, mock_feedback_urlopen, caplog
    ):
        """Test warning when no matching field is provided."""
        import logging

        caplog.set_level(logging.WARNING)

        feedback: FeedbackData = {
            "like": True,
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
                "like": True,
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
                "like": True,
            }

            result = feedback_service.create_feedback(feedback)
            assert result is None


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
            "like": True,
        }

        result = create_feedback(feedback, api_key=mock_config["api_key"])
        assert result is not None


class TestCoolhandIntegration:
    """Test FeedbackService integration with main Coolhand class."""

    def test_coolhand_has_feedback_service(self, reset_global_instance, mock_config):
        """Test Coolhand instance has feedback_service property."""
        from coolhand import Coolhand

        with patch("coolhand.interceptor.patch"):
            instance = Coolhand(config=mock_config)

        assert hasattr(instance, "feedback_service")
        assert isinstance(instance.feedback_service, FeedbackService)

    def test_coolhand_create_feedback(
        self, reset_global_instance, mock_config, mock_feedback_urlopen
    ):
        """Test Coolhand.create_feedback method."""
        from coolhand import Coolhand

        with patch("coolhand.interceptor.patch"):
            instance = Coolhand(config=mock_config)

        feedback: FeedbackData = {
            "llm_request_log_id": 12345,
            "like": True,
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
                "like": True,
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
                "like": True,
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
