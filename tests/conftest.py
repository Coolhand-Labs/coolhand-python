"""Pytest fixtures for Coolhand tests."""

import sys
import time
from unittest.mock import MagicMock, patch

import pytest

# AsyncMock is only available in Python 3.8+
if sys.version_info >= (3, 8):
    from unittest.mock import AsyncMock
else:
    # Create a simple AsyncMock for Python 3.7
    class AsyncMock(MagicMock):
        async def __call__(self, *args, **kwargs):
            return super().__call__(*args, **kwargs)


@pytest.fixture
def mock_config():
    """Return a mock configuration dict."""
    return {
        "api_key": "test-api-key-12345678",
        "base_url": "https://test.coolhandlabs.com",
        "debug": True,
        "silent": True,
        "auto_submit": False,
        "session_id": "test-session-123",
        "send_heartbeat": False,
    }


@pytest.fixture
def mock_request_data():
    """Return mock request data."""
    return {
        "method": "POST",
        "url": "https://api.openai.com/v1/chat/completions",
        "headers": {
            "Content-Type": "application/json",
            "Authorization": "Bearer sk-test-key-1234567890",
        },
        "body": {
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "Hello"}],
        },
        "timestamp": time.time(),
    }


@pytest.fixture
def mock_response_data():
    """Return mock response data."""
    return {
        "status_code": 200,
        "headers": {
            "Content-Type": "application/json",
            "X-Request-Id": "req-12345",
        },
        "body": {
            "id": "chatcmpl-123",
            "choices": [{"message": {"content": "Hello!"}}],
        },
        "timestamp": time.time(),
        "duration": 0.5,
        "is_streaming": False,
    }


@pytest.fixture
def reset_global_instance():
    """Reset global instance before and after each test."""
    from coolhand import client, httpx_interceptor

    # Save original state
    original_instance = client._instance
    original_patched = httpx_interceptor._patched
    original_handler = httpx_interceptor._handler
    original_intercept_addresses = httpx_interceptor._intercept_addresses

    # Reset before test
    client._instance = None
    httpx_interceptor._patched = False
    httpx_interceptor._handler = None
    httpx_interceptor._intercept_addresses = None

    yield

    # Reset after test
    client._instance = original_instance
    httpx_interceptor._patched = original_patched
    httpx_interceptor._handler = original_handler
    httpx_interceptor._intercept_addresses = original_intercept_addresses

    # Unpatch if patched during test
    if httpx_interceptor._patched:
        httpx_interceptor.unpatch()


@pytest.fixture
def mock_httpx_client():
    """Create a mock httpx client for testing."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.headers = {"content-type": "application/json"}
    mock_response._content = b'{"result": "success"}'
    mock_response.content = b'{"result": "success"}'
    mock_client.send.return_value = mock_response
    return mock_client


@pytest.fixture
def mock_httpx_async_client():
    """Create a mock async httpx client for testing."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.headers = {"content-type": "application/json"}
    mock_response._content = b'{"result": "success"}'
    mock_response.content = b'{"result": "success"}'

    async_mock = AsyncMock(return_value=mock_response)
    mock_client.send = async_mock
    return mock_client


@pytest.fixture
def mock_httpx_request():
    """Create a mock httpx request."""
    mock_request = MagicMock()
    mock_request.method = "POST"
    mock_request.url = "https://api.openai.com/v1/chat/completions"
    mock_request.headers = {"Content-Type": "application/json"}
    mock_request.content = b'{"model": "gpt-4"}'
    return mock_request


@pytest.fixture
def mock_urlopen():
    """Mock urllib urlopen for API submission tests."""
    with patch("coolhand.client.urlopen") as mock:
        mock_response = MagicMock()
        mock_response.status = 201
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock.return_value = mock_response
        yield mock
