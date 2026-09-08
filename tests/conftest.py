"""Pytest fixtures for Coolhand tests."""

import os
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Module-level (not a fixture): runs the moment conftest.py is imported, which
# pytest guarantees happens before any test module is collected/imported. This
# must run that early to protect `coolhand`'s own module-level auto-init
# (`coolhand/__init__.py`, `if get_instance() is None: _instance = Coolhand()`)
# — a fixture would only apply once tests start running, well after that
# auto-init has already executed with whatever the developer's real shell
# environment happens to contain. Without this, a developer with a real
# COOLHAND_API_KEY exported locally would have the test suite's auto-created
# global instance capture and deliver real interactions to production.
# Distinct from COOLHAND_LIVE_API_KEY / COOLHAND_LIVE_BASE_URL, which gate the
# separate opt-in `tests/live/` suite and are untouched here.
os.environ.pop("COOLHAND_API_KEY", None)
os.environ.pop("COOLHAND_BASE_URL", None)


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
    original_exclude_api_patterns = httpx_interceptor._exclude_api_patterns

    # Reset before test
    client._instance = None
    httpx_interceptor._patched = False
    httpx_interceptor._handler = None
    httpx_interceptor._intercept_addresses = None
    httpx_interceptor._exclude_api_patterns = None

    yield

    # Reset after test
    client._instance = original_instance
    httpx_interceptor._patched = original_patched
    httpx_interceptor._handler = original_handler
    httpx_interceptor._intercept_addresses = original_intercept_addresses
    httpx_interceptor._exclude_api_patterns = original_exclude_api_patterns

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


@pytest.fixture(autouse=True, scope="session")
def _no_real_atexit_registration():
    """Prevent any CoolhandClient constructed *during test execution* from
    registering a *real* atexit handler.

    CoolhandClient.__init__ registers atexit.register(self.shutdown) so a
    process exit still attempts delivery. Without this fixture, any test that
    constructs a client and leaves an interaction in its queue (e.g. by
    mocking flush() without cleaning up afterward) would fire a genuine
    background HTTP POST to the production Coolhand API when the pytest
    process itself exits. This complements, but does not replace, the
    module-level COOLHAND_API_KEY clearing above: this fixture only applies
    once tests start running, so it cannot protect `coolhand`'s own
    module-level auto-init (`coolhand/__init__.py`), which runs at import
    time — before any fixture, session-scoped or not, can take effect. That
    path is closed by clearing the env var early enough that the auto-created
    instance never has an api_key to submit with. Individual tests that need
    to assert on the registration call (e.g.
    test_constructor_registers_atexit_shutdown) patch
    "coolhand.client.atexit.register" locally, which layers on top of this
    and is unaffected by it.

    Note: since `coolhand.client` does `import atexit` (not `from atexit
    import register`), this patches the attribute on the real `atexit`
    module — it no-ops `atexit.register` process-wide for the whole test
    session, not just for coolhand. Nothing in this suite currently
    registers its own atexit cleanup during test execution, but keep that in
    mind before adding one. (A scoped alternative — replacing
    `coolhand.client`'s own `atexit` reference with a stand-in object — was
    tried and reverted: `tests/test_init.py::test_coolhand_registers_atexit`
    patches the real `atexit.register` directly and asserts on it, which
    only works because `coolhand.client.atexit` really is the same module
    object; a stand-in would silently stop that test from testing anything.)
    """
    with patch("coolhand.client.atexit.register"):
        yield


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
