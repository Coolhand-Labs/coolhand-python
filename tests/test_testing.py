"""Tests for coolhand.testing (interception assertion helpers)."""

import asyncio
import threading
from unittest.mock import MagicMock

import httpx
import pytest
import requests

from coolhand import Coolhand, httpx_interceptor
from coolhand.httpx_interceptor import patch as patch_httpx
from coolhand.httpx_interceptor import set_exclude_api_patterns, set_handler, unpatch
from coolhand.testing import (
    InterceptionSnapshot,
    assert_interception_occurred,
    capture_interception_snapshot,
)

LLM_URL = "https://api.openai.com/v1/chat/completions"
NON_LLM_URL = "https://example.com/api"


def _ok(request):
    return httpx.Response(200, json={})


def _pristine_httpx_send(self, request, **kwargs):
    return httpx.Response(200, json={}, request=request)


async def _pristine_httpx_async_send(self, request, **kwargs):
    return httpx.Response(200, json={}, request=request)


def _pristine_requests_send(self, request, **kwargs):
    response = requests.Response()
    response.status_code = 200
    response._content = b"{}"
    response._content_consumed = True
    return response


@pytest.fixture
def patched(reset_global_instance, monkeypatch):
    """Patch interceptors over deterministic, network-free "original" senders."""
    monkeypatch.setattr(httpx.Client, "send", _pristine_httpx_send)
    monkeypatch.setattr(httpx.AsyncClient, "send", _pristine_httpx_async_send)
    monkeypatch.setattr(requests.Session, "send", _pristine_requests_send)
    set_handler(MagicMock())
    patch_httpx()
    yield
    unpatch()


class TestSnapshot:
    def test_snapshot_is_frozen(self, patched):
        snapshot = capture_interception_snapshot()
        assert isinstance(snapshot, InterceptionSnapshot)
        with pytest.raises(AttributeError):
            snapshot.count = 0  # type: ignore[misc]

    def test_snapshot_tracks_counter(self, patched):
        before = capture_interception_snapshot()
        assert before.count == httpx_interceptor.get_intercept_count()
        httpx.Client().get(LLM_URL)
        assert capture_interception_snapshot().count == before.count + 1


class TestAssertInterceptionOccurred:
    def test_passes_for_sync_httpx(self, patched):
        before = capture_interception_snapshot()
        httpx.Client().get(LLM_URL)
        assert_interception_occurred(before)

    def test_passes_for_async_httpx(self, patched):
        async def call():
            async with httpx.AsyncClient() as client:
                await client.get(LLM_URL)

        before = capture_interception_snapshot()
        asyncio.run(call())
        assert_interception_occurred(before)

    def test_passes_for_requests(self, patched):
        before = capture_interception_snapshot()
        requests.Session().get(LLM_URL)
        assert_interception_occurred(before)

    def test_passes_when_request_errors(self, patched, monkeypatch):
        def failing_send(self, request, **kwargs):
            raise httpx.ConnectError("boom")

        unpatch()
        monkeypatch.setattr(httpx.Client, "send", failing_send)
        patch_httpx()

        before = capture_interception_snapshot()
        with pytest.raises(httpx.ConnectError):
            httpx.Client().get(LLM_URL)
        assert_interception_occurred(before)

    def test_fails_for_non_llm_url(self, patched):
        before = capture_interception_snapshot()
        httpx.Client().get(NON_LLM_URL)
        with pytest.raises(AssertionError, match="did not intercept"):
            assert_interception_occurred(before)

    def test_fails_for_localhost_url(self, patched):
        before = capture_interception_snapshot()
        httpx.Client().get("http://localhost:8000/v1/chat/completions")
        with pytest.raises(AssertionError, match="did not intercept"):
            assert_interception_occurred(before)

    def test_fails_for_excluded_url(self, patched):
        set_exclude_api_patterns(["/v1/chat/completions"])
        before = capture_interception_snapshot()
        httpx.Client().get(LLM_URL)
        with pytest.raises(AssertionError, match="did not intercept"):
            assert_interception_occurred(before)

    def test_fails_when_no_handler_set(self, patched):
        """Patched requests pass through uncounted when no handler is installed."""
        httpx_interceptor._handler = None
        before = capture_interception_snapshot()
        httpx.Client().get(LLM_URL)
        with pytest.raises(AssertionError, match="No handler is set"):
            assert_interception_occurred(before)

    def test_passes_through_coolhand_instance(
        self, reset_global_instance, mock_config, monkeypatch
    ):
        """End to end: a real Coolhand instance installs the handler and counts."""
        # reset_global_instance clears _patched without unpatching, so wrap
        # pristine senders rather than the already-patched import-time ones.
        monkeypatch.setattr(httpx.Client, "send", _pristine_httpx_send)
        monkeypatch.setattr(requests.Session, "send", _pristine_requests_send)
        instance = Coolhand(config=mock_config)
        try:
            before = capture_interception_snapshot()
            httpx.Client(transport=httpx.MockTransport(_ok)).get(LLM_URL)
            assert_interception_occurred(before)

            instance.stop_monitoring()
            before = capture_interception_snapshot()
            httpx.Client(transport=httpx.MockTransport(_ok)).get(LLM_URL)
            with pytest.raises(AssertionError, match="Monitoring is not active"):
                assert_interception_occurred(before)
        finally:
            instance.stop_monitoring()

    def test_fails_for_stale_send_reference(self, patched):
        """The real bypass: a send reference captured before patching."""
        stale_send = _pristine_httpx_send
        client = httpx.Client()

        before = capture_interception_snapshot()
        stale_send(client, client.build_request("GET", LLM_URL))
        with pytest.raises(AssertionError, match="did not intercept"):
            assert_interception_occurred(before)

        # The live lookup through the class is intercepted.
        before = capture_interception_snapshot()
        client.get(LLM_URL)
        assert_interception_occurred(before)

    def test_failure_message_includes_custom_message(self, patched):
        before = capture_interception_snapshot()
        with pytest.raises(AssertionError) as excinfo:
            assert_interception_occurred(before, "openai client bypassed Coolhand")
        assert str(excinfo.value).startswith(
            "openai client bypassed Coolhand: Coolhand did not intercept"
        )

    def test_failure_message_flags_inactive_monitoring(self, patched):
        unpatch()
        before = capture_interception_snapshot()
        with pytest.raises(AssertionError, match="Monitoring is not active"):
            assert_interception_occurred(before)

    def test_failure_message_omits_inactive_note_when_patched(self, patched):
        before = capture_interception_snapshot()
        with pytest.raises(AssertionError) as excinfo:
            assert_interception_occurred(before)
        assert "Monitoring is not active" not in str(excinfo.value)


class TestCounter:
    def test_unpatch_does_not_reset_counter(self, patched):
        httpx.Client().get(LLM_URL)
        count = httpx_interceptor.get_intercept_count()
        assert count >= 1
        unpatch()
        assert httpx_interceptor.get_intercept_count() == count

    def test_requests_to_httpx_chain_counts_once(self, patched, monkeypatch):
        """A requests send that re-enters httpx is one logical request."""

        def chaining_send(self, request, **kwargs):
            httpx.Client().get(request.url)
            return _pristine_requests_send(self, request)

        unpatch()
        monkeypatch.setattr(requests.Session, "send", chaining_send)
        patch_httpx()

        before = httpx_interceptor.get_intercept_count()
        requests.Session().get(LLM_URL)
        assert httpx_interceptor.get_intercept_count() - before == 1

    def test_concurrent_increments_are_not_lost(self, patched):
        threads, per_thread = 8, 25

        def hammer():
            client = httpx.Client()
            for _ in range(per_thread):
                client.get(LLM_URL)

        before = httpx_interceptor.get_intercept_count()
        workers = [threading.Thread(target=hammer) for _ in range(threads)]
        for w in workers:
            w.start()
        for w in workers:
            w.join()
        assert httpx_interceptor.get_intercept_count() - before == threads * per_thread
