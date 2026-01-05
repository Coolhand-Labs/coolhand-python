"""Tests for coolhand.types module."""

from coolhand.types import Config, RequestData, ResponseData


class TestRequestData:
    """Tests for RequestData TypedDict."""

    def test_request_data_can_be_created(self):
        """RequestData can be instantiated with expected fields."""
        data: RequestData = {
            "method": "POST",
            "url": "https://api.openai.com/v1/chat/completions",
            "headers": {"Content-Type": "application/json"},
            "body": {"model": "gpt-4"},
            "timestamp": 1234567890.0,
        }
        assert data["method"] == "POST"
        assert data["url"] == "https://api.openai.com/v1/chat/completions"
        assert data["headers"] == {"Content-Type": "application/json"}
        assert data["body"] == {"model": "gpt-4"}
        assert data["timestamp"] == 1234567890.0

    def test_request_data_partial(self):
        """RequestData can be created with partial fields (total=False)."""
        data: RequestData = {
            "method": "GET",
            "url": "https://api.anthropic.com/v1/messages",
        }
        assert data["method"] == "GET"
        assert "headers" not in data


class TestResponseData:
    """Tests for ResponseData TypedDict."""

    def test_response_data_can_be_created(self):
        """ResponseData can be instantiated with expected fields."""
        data: ResponseData = {
            "status_code": 200,
            "headers": {"Content-Type": "application/json"},
            "body": {"choices": []},
            "timestamp": 1234567890.0,
            "duration": 0.5,
            "is_streaming": False,
        }
        assert data["status_code"] == 200
        assert data["headers"] == {"Content-Type": "application/json"}
        assert data["body"] == {"choices": []}
        assert data["timestamp"] == 1234567890.0
        assert data["duration"] == 0.5
        assert data["is_streaming"] is False

    def test_response_data_partial(self):
        """ResponseData can be created with partial fields."""
        data: ResponseData = {
            "status_code": 500,
        }
        assert data["status_code"] == 500
        assert "body" not in data


class TestConfig:
    """Tests for Config TypedDict."""

    def test_config_can_be_created(self):
        """Config can be instantiated with expected fields."""
        data: Config = {
            "api_key": "test-key",
            "base_url": "https://coolhandlabs.com",
            "debug": True,
            "silent": False,
            "auto_submit": True,
            "session_id": "session-123",
        }
        assert data["api_key"] == "test-key"
        assert data["base_url"] == "https://coolhandlabs.com"
        assert data["debug"] is True
        assert data["silent"] is False
        assert data["auto_submit"] is True
        assert data["session_id"] == "session-123"

    def test_config_partial(self):
        """Config can be created with partial fields."""
        data: Config = {
            "api_key": "my-key",
        }
        assert data["api_key"] == "my-key"
        assert "base_url" not in data
