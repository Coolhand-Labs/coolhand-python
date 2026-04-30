"""Tests for requests.Session.send interception in coolhand.httpx_interceptor."""

from unittest.mock import MagicMock, patch

import pytest

import coolhand.httpx_interceptor as interceptor
from coolhand.httpx_interceptor import is_patched
from coolhand.httpx_interceptor import patch as patch_all
from coolhand.httpx_interceptor import set_handler, unpatch


@pytest.fixture(autouse=True)
def reset_state():
    """Reset interceptor globals before and after each test."""
    interceptor._patched = False
    interceptor._original_send = None
    interceptor._original_async_send = None
    interceptor._original_requests_send = None
    interceptor._handler = None
    interceptor._intercept_addresses = None
    yield
    # Restore state without relying on unpatch() to avoid real network calls
    try:
        import requests

        if interceptor._original_requests_send:
            requests.Session.send = interceptor._original_requests_send
    except ImportError:
        pass
    interceptor._patched = False
    interceptor._original_send = None
    interceptor._original_async_send = None
    interceptor._original_requests_send = None
    interceptor._handler = None
    interceptor._intercept_addresses = None


def _make_prepared_request(url, method="POST", body=None, headers=None):
    req = MagicMock()
    req.url = url
    req.method = method
    req.body = body
    req.headers = headers or {}
    return req


def _make_response(
    status_code=200, content=b'{"result": "ok"}', content_type="application/json"
):
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = {"content-type": content_type}
    resp.content = content
    return resp


def _patch_and_mock_original(fake_response=None):
    """Patch all and replace _original_requests_send with a safe mock."""
    patch_all()
    mock_original = MagicMock(return_value=fake_response or _make_response())
    interceptor._original_requests_send = mock_original
    return mock_original


class TestRequestsPatchUnpatch:
    def test_patch_sets_patched_flag(self):
        patch_all()
        assert is_patched() is True

    def test_patch_is_idempotent(self):
        patch_all()
        patch_all()
        assert is_patched() is True

    def test_unpatch_clears_flag(self):
        import requests

        patch_all()
        # Replace original with safe mock so unpatch doesn't leave real send broken
        orig = requests.Session.send
        send_func = orig.__func__ if hasattr(orig, "__func__") else orig
        interceptor._original_requests_send = send_func
        unpatch()
        assert is_patched() is False

    def test_unpatch_restores_requests_send(self):
        import requests

        original = requests.Session.send
        patch_all()
        assert requests.Session.send is not original
        unpatch()
        assert requests.Session.send is original

    def test_unpatch_resets_original_requests_send(self):
        patch_all()
        assert interceptor._original_requests_send is not None
        unpatch()
        assert interceptor._original_requests_send is None

    def test_unpatch_when_not_patched_is_noop(self):
        unpatch()  # should not raise
        assert is_patched() is False


class TestRequestsCapture:
    def test_non_llm_url_passes_through(self):
        import requests

        captured = []
        set_handler(lambda req, res, err: captured.append((req, res, err)))
        mock_original = _patch_and_mock_original()

        session = requests.Session()
        req = _make_prepared_request("https://api.github.com/repos")
        session.send(req)

        assert captured == []
        mock_original.assert_called_once()

    def test_localhost_passes_through(self):
        import requests

        captured = []
        set_handler(lambda req, res, err: captured.append((req, res, err)))
        mock_original = _patch_and_mock_original()

        session = requests.Session()
        req = _make_prepared_request("http://localhost:8080/v1/chat/completions")
        session.send(req)

        assert captured == []
        mock_original.assert_called_once()

    def test_no_handler_passes_through(self):
        import requests

        patch_all()
        fake_response = _make_response()
        mock_original = MagicMock(return_value=fake_response)
        interceptor._original_requests_send = mock_original
        # no set_handler call

        session = requests.Session()
        req = _make_prepared_request("https://api.openai.com/v1/chat/completions")
        result = session.send(req)

        assert result is fake_response
        assert mock_original.call_count == 1

    def test_llm_url_captured(self):
        import requests

        captured = []
        set_handler(lambda req, res, err: captured.append((req, res, err)))
        fake_response = _make_response(status_code=200, content=b'{"id":"123"}')
        _patch_and_mock_original(fake_response)

        session = requests.Session()
        req = _make_prepared_request(
            "https://models.inference.ai.azure.com/chat/completions",
            body=b'{"messages":[]}',
        )
        session.send(req)

        assert len(captured) == 1
        req_data, res_data, error = captured[0]
        assert (
            req_data["url"] == "https://models.inference.ai.azure.com/chat/completions"
        )
        assert req_data["method"] == "POST"
        assert req_data["body"] == '{"messages":[]}'
        assert res_data["status_code"] == 200
        assert res_data["body"] == '{"id":"123"}'  # decoded string, not bytes
        assert isinstance(res_data["body"], str)
        assert error is None

    def test_openai_url_captured(self):
        import requests

        captured = []
        set_handler(lambda req, res, err: captured.append((req, res, err)))
        _patch_and_mock_original()

        session = requests.Session()
        req = _make_prepared_request("https://api.openai.com/v1/chat/completions")
        session.send(req)

        assert len(captured) == 1
        assert captured[0][0]["url"] == "https://api.openai.com/v1/chat/completions"

    def test_bytes_body_decoded(self):
        import requests

        captured = []
        set_handler(lambda req, res, err: captured.append((req, res, err)))
        _patch_and_mock_original()

        session = requests.Session()
        req = _make_prepared_request(
            "https://api.openai.com/v1/chat/completions",
            body=b'{"model":"gpt-4"}',
        )
        session.send(req)

        assert len(captured) == 1
        assert captured[0][0]["body"] == '{"model":"gpt-4"}'

    def test_string_body_preserved(self):
        import requests

        captured = []
        set_handler(lambda req, res, err: captured.append((req, res, err)))
        _patch_and_mock_original()

        session = requests.Session()
        req = _make_prepared_request(
            "https://api.openai.com/v1/chat/completions",
            body='{"model":"gpt-4"}',
        )
        session.send(req)

        assert len(captured) == 1
        assert captured[0][0]["body"] == '{"model":"gpt-4"}'

    def test_none_body_preserved(self):
        import requests

        captured = []
        set_handler(lambda req, res, err: captured.append((req, res, err)))
        _patch_and_mock_original()

        session = requests.Session()
        req = _make_prepared_request(
            "https://api.openai.com/v1/chat/completions", body=None
        )
        session.send(req)

        assert len(captured) == 1
        assert captured[0][0]["body"] is None

    def test_streaming_content_type_sets_body_placeholder(self):
        import requests

        captured = []
        set_handler(lambda req, res, err: captured.append((req, res, err)))
        fake_response = _make_response(
            content_type="text/event-stream", content=b"data: ...\n"
        )
        _patch_and_mock_original(fake_response)

        session = requests.Session()
        req = _make_prepared_request("https://api.openai.com/v1/chat/completions")
        session.send(req)

        assert len(captured) == 1
        assert captured[0][1]["body"] == "[streaming]"
        assert captured[0][1]["is_streaming"] is True

    def test_response_fields_populated(self):
        import requests

        captured = []
        set_handler(lambda req, res, err: captured.append((req, res, err)))
        fake_response = _make_response(status_code=201, content=b'{"created": true}')
        _patch_and_mock_original(fake_response)

        session = requests.Session()
        req = _make_prepared_request("https://api.anthropic.com/v1/messages")
        session.send(req)

        assert len(captured) == 1
        res_data = captured[0][1]
        assert res_data["status_code"] == 201
        assert res_data["is_streaming"] is False
        assert res_data["duration"] >= 0
        assert res_data["timestamp"] > 0


class TestRequestsErrorHandling:
    def test_exception_calls_handler_and_reraises(self):
        import requests

        captured = []
        set_handler(lambda req, res, err: captured.append((req, res, err)))
        patch_all()
        interceptor._original_requests_send = MagicMock(
            side_effect=ConnectionError("network error")
        )

        session = requests.Session()
        req = _make_prepared_request("https://api.openai.com/v1/chat/completions")

        with pytest.raises(ConnectionError, match="network error"):
            session.send(req)

        assert len(captured) == 1
        req_data, res_data, error = captured[0]
        assert res_data is None
        assert "network error" in error

    def test_exception_not_captured_for_non_llm_url(self):
        import requests

        captured = []
        set_handler(lambda req, res, err: captured.append((req, res, err)))
        patch_all()
        interceptor._original_requests_send = MagicMock(
            side_effect=ConnectionError("network error")
        )

        session = requests.Session()
        req = _make_prepared_request("https://api.github.com/repos")

        with pytest.raises(ConnectionError):
            session.send(req)

        assert captured == []


class TestRequestsImportError:
    def test_patch_succeeds_without_requests(self):
        """patch() succeeds when requests is not installed."""
        import builtins

        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "requests":
                raise ImportError("No module named 'requests'")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            result = patch_all()

        assert result is True
        assert is_patched() is True

    def test_unpatch_succeeds_without_requests(self):
        """unpatch() succeeds when requests is not installed."""
        patch_all()

        import builtins

        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "requests":
                raise ImportError("No module named 'requests'")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            unpatch()  # should not raise

        assert is_patched() is False
