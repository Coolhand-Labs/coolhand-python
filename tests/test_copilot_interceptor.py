"""Tests for coolhand.copilot_interceptor module."""

import sys
import time
import types
from unittest.mock import MagicMock

import pytest

if sys.version_info >= (3, 8):
    from unittest.mock import AsyncMock
else:

    class AsyncMock(MagicMock):
        async def __call__(self, *args, **kwargs):
            return super().__call__(*args, **kwargs)


# ---------------------------------------------------------------------------
# Helpers: build and inject a fake copilot SDK into sys.modules
# ---------------------------------------------------------------------------


def _make_fake_client_class():
    class FakeJsonRpcClient:
        async def request(self, method, params=None, timeout=None):
            return {"messageId": "msg-001"}

        def _handle_message(self, message):
            pass

    return FakeJsonRpcClient


def _inject_fake_sdk(client_cls=None):
    """Inject minimal fake copilot._jsonrpc into sys.modules. Returns the class."""
    if client_cls is None:
        client_cls = _make_fake_client_class()

    copilot_mod = types.ModuleType("copilot")
    jsonrpc_mod = types.ModuleType("copilot._jsonrpc")
    jsonrpc_mod.JsonRpcClient = client_cls

    sys.modules["copilot"] = copilot_mod
    sys.modules["copilot._jsonrpc"] = jsonrpc_mod
    # Ensure legacy path does not accidentally resolve
    sys.modules.pop("copilot.jsonrpc", None)

    return client_cls


def _remove_fake_sdk():
    for key in ["copilot", "copilot._jsonrpc", "copilot.jsonrpc"]:
        sys.modules.pop(key, None)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_copilot_interceptor():
    """Reset copilot_interceptor module state before and after each test."""
    from coolhand import copilot_interceptor

    saved = {
        "_patched": copilot_interceptor._patched,
        "_original_request": copilot_interceptor._original_request,
        "_original_handle_message": copilot_interceptor._original_handle_message,
        "_handler": copilot_interceptor._handler,
    }
    copilot_interceptor._patched = False
    copilot_interceptor._original_request = None
    copilot_interceptor._original_handle_message = None
    copilot_interceptor._handler = None
    with copilot_interceptor._lock:
        copilot_interceptor._pending.clear()
        copilot_interceptor._pre_pending.clear()

    yield

    for k, v in saved.items():
        setattr(copilot_interceptor, k, v)
    with copilot_interceptor._lock:
        copilot_interceptor._pending.clear()
        copilot_interceptor._pre_pending.clear()

    _remove_fake_sdk()


@pytest.fixture
def handler():
    return MagicMock()


# ---------------------------------------------------------------------------
# TestPatchUnpatch
# ---------------------------------------------------------------------------


class TestPatchUnpatch:
    def test_returns_false_when_sdk_missing(self):
        from coolhand import copilot_interceptor

        _remove_fake_sdk()
        assert copilot_interceptor.patch() is False
        assert copilot_interceptor.is_patched() is False

    def test_returns_true_when_sdk_present(self):
        from coolhand import copilot_interceptor

        _inject_fake_sdk()
        assert copilot_interceptor.patch() is True
        assert copilot_interceptor.is_patched() is True

    def test_patch_is_idempotent(self):
        from coolhand import copilot_interceptor

        cls = _inject_fake_sdk()
        original_request = cls.request
        copilot_interceptor.patch()
        first_patched = cls.request
        copilot_interceptor.patch()
        assert cls.request is first_patched
        assert copilot_interceptor._original_request is original_request

    def test_unpatch_restores_methods(self):
        from coolhand import copilot_interceptor

        cls = _inject_fake_sdk()
        original_request = cls.request
        original_handle = cls._handle_message
        copilot_interceptor.patch()
        copilot_interceptor.unpatch()
        assert cls.request is original_request
        assert cls._handle_message is original_handle
        assert copilot_interceptor.is_patched() is False

    def test_unpatch_clears_pending(self):
        from coolhand import copilot_interceptor

        _inject_fake_sdk()
        copilot_interceptor.patch()
        with copilot_interceptor._lock:
            copilot_interceptor._pending[("s1", "m1")] = {
                "req_data": {},
                "start": time.time(),
            }
            copilot_interceptor._pre_pending["s1"] = [
                {"req_data": {}, "start": time.time()}
            ]
        copilot_interceptor.unpatch()
        assert copilot_interceptor._pending == {}
        assert copilot_interceptor._pre_pending == {}

    def test_unpatch_when_not_patched_is_noop(self):
        from coolhand import copilot_interceptor

        # Should not raise
        copilot_interceptor.unpatch()
        assert copilot_interceptor.is_patched() is False

    def test_falls_back_to_legacy_import(self):
        from coolhand import copilot_interceptor

        cls = _make_fake_client_class()
        copilot_mod = types.ModuleType("copilot")
        legacy_mod = types.ModuleType("copilot.jsonrpc")
        legacy_mod.JsonRpcClient = cls
        sys.modules["copilot"] = copilot_mod
        sys.modules["copilot.jsonrpc"] = legacy_mod
        sys.modules.pop("copilot._jsonrpc", None)

        assert copilot_interceptor.patch() is True
        assert copilot_interceptor.is_patched() is True


# ---------------------------------------------------------------------------
# TestRequestInterception
# ---------------------------------------------------------------------------


class TestRequestInterception:
    @pytest.mark.asyncio
    async def test_pre_pending_populated_before_await(self, handler):
        from coolhand import copilot_interceptor

        cls = _inject_fake_sdk()
        copilot_interceptor.set_handler(handler)
        copilot_interceptor.patch()

        # Intercept just after the pre_pending push, before the await returns
        pushed_before_await = []
        original_orig = copilot_interceptor._original_request

        async def slow_original(self_inner, method, params=None, timeout=None):
            # By the time we enter here, the pre_pending push has already happened
            pushed_before_await.append(
                list(copilot_interceptor._pre_pending.get("s1", []))
            )
            return await original_orig(self_inner, method, params, timeout)

        copilot_interceptor._original_request = slow_original

        instance = cls()
        await instance.request("session.send", {"sessionId": "s1", "prompt": "hi"})

        assert len(pushed_before_await) == 1
        assert len(pushed_before_await[0]) == 1

    @pytest.mark.asyncio
    async def test_stores_pending_entry_for_session_send(self, handler):
        from coolhand import copilot_interceptor

        cls = _inject_fake_sdk()
        copilot_interceptor.set_handler(handler)
        copilot_interceptor.patch()

        instance = cls()
        result = await instance.request(
            "session.send",
            {"sessionId": "s1", "prompt": "hello", "mode": "immediate"},
        )

        assert result == {"messageId": "msg-001"}
        assert ("s1", "msg-001") in copilot_interceptor._pending
        entry = copilot_interceptor._pending[("s1", "msg-001")]
        assert entry["req_data"]["body"]["prompt"] == "hello"
        assert entry["req_data"]["body"]["session_id"] == "s1"
        assert entry["req_data"]["body"]["mode"] == "immediate"
        assert entry["req_data"]["url"] == "copilot://session.send"
        assert entry["req_data"]["method"] == "POST"

    @pytest.mark.asyncio
    async def test_passthrough_for_non_session_send(self, handler):
        from coolhand import copilot_interceptor

        cls = _inject_fake_sdk()
        copilot_interceptor.set_handler(handler)
        copilot_interceptor.patch()

        instance = cls()
        await instance.request("models.list", {})

        assert copilot_interceptor._pending == {}
        handler.assert_not_called()

    @pytest.mark.asyncio
    async def test_calls_handler_on_exception_and_reraises(self, handler):
        from coolhand import copilot_interceptor

        class ErrorClient:
            async def request(self, method, params=None, timeout=None):
                raise RuntimeError("connection failed")

            def _handle_message(self, message):
                pass

        _inject_fake_sdk(ErrorClient)
        copilot_interceptor.set_handler(handler)
        copilot_interceptor.patch()

        instance = ErrorClient()
        with pytest.raises(RuntimeError, match="connection failed"):
            await instance.request("session.send", {"sessionId": "s1", "prompt": "hi"})

        handler.assert_called_once()
        req_data, res_data, err = handler.call_args[0]
        assert res_data is None
        assert "connection failed" in err
        assert copilot_interceptor._pending == {}

    @pytest.mark.asyncio
    async def test_no_pending_entry_when_handler_is_none(self):
        from coolhand import copilot_interceptor

        cls = _inject_fake_sdk()
        copilot_interceptor.patch()  # handler not set

        instance = cls()
        await instance.request("session.send", {"sessionId": "s1", "prompt": "hi"})
        assert copilot_interceptor._pending == {}

    @pytest.mark.asyncio
    async def test_captures_request_headers_and_attachments(self, handler):
        from coolhand import copilot_interceptor

        cls = _inject_fake_sdk()
        copilot_interceptor.set_handler(handler)
        copilot_interceptor.patch()

        instance = cls()
        await instance.request(
            "session.send",
            {
                "sessionId": "s1",
                "prompt": "hello",
                "attachments": [{"type": "file", "path": "/tmp/x"}],
                "requestHeaders": {"X-Custom": "value"},
            },
        )

        entry = copilot_interceptor._pending[("s1", "msg-001")]
        assert entry["req_data"]["headers"] == {"X-Custom": "value"}
        assert entry["req_data"]["body"]["attachments"] == [
            {"type": "file", "path": "/tmp/x"}
        ]


# ---------------------------------------------------------------------------
# TestHandleMessageInterception
# ---------------------------------------------------------------------------


def _assistant_message_notification(
    session_id, message_id, content, model=None, output_tokens=None
):
    return {
        "method": "session.event",
        "params": {
            "sessionId": session_id,
            "event": {
                "type": "assistant.message",
                "data": {
                    "messageId": message_id,
                    "content": content,
                    "model": model,
                    "outputTokens": output_tokens,
                },
            },
        },
    }


class TestHandleMessageInterception:
    def _prime_pending(
        self, copilot_interceptor, session_id, message_id, prompt="hello"
    ):
        start = time.time() - 0.1
        req_data = {
            "method": "POST",
            "url": "copilot://session.send",
            "headers": {},
            "body": {"prompt": prompt, "session_id": session_id},
            "timestamp": start,
        }
        with copilot_interceptor._lock:
            copilot_interceptor._pending[(session_id, message_id)] = {
                "req_data": req_data,
                "start": start,
            }

    def test_handler_called_with_correct_data(self, handler):
        from coolhand import copilot_interceptor

        cls = _inject_fake_sdk()
        copilot_interceptor.set_handler(handler)
        copilot_interceptor.patch()
        self._prime_pending(copilot_interceptor, "s1", "msg-001", "hello")

        instance = cls()
        instance._handle_message(
            _assistant_message_notification(
                "s1", "msg-001", "world", model="gpt-4o", output_tokens=42
            )
        )

        handler.assert_called_once()
        req_data, res_data, err = handler.call_args[0]
        assert err is None
        assert req_data["url"] == "copilot://session.send"
        assert req_data["body"]["prompt"] == "hello"
        assert res_data["status_code"] == 200
        assert res_data["body"]["content"] == "world"
        assert res_data["body"]["message_id"] == "msg-001"
        assert res_data["body"]["session_id"] == "s1"
        assert res_data["body"]["model"] == "gpt-4o"
        assert res_data["body"]["output_tokens"] == 42
        assert res_data["duration"] > 0
        assert res_data["is_streaming"] is False

    def test_pending_entry_removed_after_match(self, handler):
        from coolhand import copilot_interceptor

        cls = _inject_fake_sdk()
        copilot_interceptor.set_handler(handler)
        copilot_interceptor.patch()
        self._prime_pending(copilot_interceptor, "s1", "msg-001")

        instance = cls()
        instance._handle_message(
            _assistant_message_notification("s1", "msg-001", "world")
        )

        assert ("s1", "msg-001") not in copilot_interceptor._pending

    def test_original_always_called_first(self, handler):
        from coolhand import copilot_interceptor

        call_order = []

        class OrderedClient:
            def request(self, method, params=None, timeout=None):
                pass

            def _handle_message(self, message):
                call_order.append("original")

        _inject_fake_sdk(OrderedClient)
        original_handler = MagicMock(
            side_effect=lambda *a: call_order.append("coolhand")
        )
        copilot_interceptor.set_handler(original_handler)
        copilot_interceptor.patch()
        self._prime_pending(copilot_interceptor, "s1", "msg-001")

        instance = OrderedClient()
        instance._handle_message(
            _assistant_message_notification("s1", "msg-001", "world")
        )

        assert call_order == ["original", "coolhand"]

    def test_non_session_event_method_skips_handler(self, handler):
        from coolhand import copilot_interceptor

        cls = _inject_fake_sdk()
        copilot_interceptor.set_handler(handler)
        copilot_interceptor.patch()

        instance = cls()
        instance._handle_message({"method": "session.lifecycle", "params": {}})
        handler.assert_not_called()

    def test_non_assistant_event_type_skips_handler(self, handler):
        from coolhand import copilot_interceptor

        cls = _inject_fake_sdk()
        copilot_interceptor.set_handler(handler)
        copilot_interceptor.patch()

        instance = cls()
        instance._handle_message(
            {
                "method": "session.event",
                "params": {
                    "sessionId": "s1",
                    "event": {"type": "session.idle", "data": {}},
                },
            }
        )
        handler.assert_not_called()

    def test_response_message_with_id_skips_handler(self, handler):
        from coolhand import copilot_interceptor

        cls = _inject_fake_sdk()
        copilot_interceptor.set_handler(handler)
        copilot_interceptor.patch()

        instance = cls()
        instance._handle_message(
            {
                "id": "rpc-uuid-123",
                "result": {"messageId": "msg-001"},
            }
        )
        handler.assert_not_called()

    def test_unknown_message_id_skips_handler_but_original_called(self, handler):
        from coolhand import copilot_interceptor

        original_called = []

        class TrackingClient:
            async def request(self, method, params=None, timeout=None):
                return {}

            def _handle_message(self, message):
                original_called.append(True)

        _inject_fake_sdk(TrackingClient)
        copilot_interceptor.set_handler(handler)
        copilot_interceptor.patch()

        instance = TrackingClient()
        instance._handle_message(
            _assistant_message_notification("s1", "unknown-id", "world")
        )

        handler.assert_not_called()
        assert original_called == [True]

    def test_handler_exception_does_not_break_dispatch(self, handler):
        from coolhand import copilot_interceptor

        original_called = []

        class TrackingClient:
            async def request(self, method, params=None, timeout=None):
                return {}

            def _handle_message(self, message):
                original_called.append(True)

        _inject_fake_sdk(TrackingClient)
        handler.side_effect = RuntimeError("boom")
        copilot_interceptor.set_handler(handler)
        copilot_interceptor.patch()
        self._prime_pending(copilot_interceptor, "s1", "msg-001")

        instance = TrackingClient()
        # Should not raise despite handler error
        instance._handle_message(
            _assistant_message_notification("s1", "msg-001", "world")
        )

        assert original_called == [True]

    def test_race_notification_before_patched_request_resumes(self, handler):
        """Notification arrives (via _handle_message) before patched_request stores
        the entry in _pending.  The entry lives in _pre_pending at that moment and
        must be found there so the interaction is not silently dropped."""
        from coolhand import copilot_interceptor

        cls = _inject_fake_sdk()
        copilot_interceptor.set_handler(handler)
        copilot_interceptor.patch()

        # Manually simulate the pre-await state: push to _pre_pending directly
        start = time.time() - 0.05
        req_data = {
            "method": "POST",
            "url": "copilot://session.send",
            "headers": {},
            "body": {"prompt": "hello", "session_id": "s1"},
            "timestamp": start,
        }
        entry = {"req_data": req_data, "start": start}
        with copilot_interceptor._lock:
            copilot_interceptor._pre_pending.setdefault("s1", []).append(entry)
        # _pending is empty — simulates the race window

        instance = cls()
        instance._handle_message(
            _assistant_message_notification("s1", "msg-001", "response")
        )

        # Handler must have been called despite no _pending entry
        handler.assert_called_once()
        req_d, res_d, err = handler.call_args[0]
        assert err is None
        assert req_d["body"]["prompt"] == "hello"
        assert res_d["body"]["content"] == "response"
        # pre_pending entry was consumed
        assert "s1" not in copilot_interceptor._pre_pending

    def test_stale_entries_evicted_on_any_notification(self, handler):
        """Stale cleanup runs unconditionally on every _handle_message call,
        not only when an assistant.message event is processed."""
        from coolhand import copilot_interceptor

        cls = _inject_fake_sdk()
        copilot_interceptor.set_handler(handler)
        copilot_interceptor.patch()

        stale_start = time.time() - 400
        with copilot_interceptor._lock:
            copilot_interceptor._pending[("s-stale", "stale-id")] = {
                "req_data": {},
                "start": stale_start,
            }

        instance = cls()
        # Send a session.idle notification — NOT assistant.message
        instance._handle_message(
            {
                "method": "session.event",
                "params": {
                    "sessionId": "s1",
                    "event": {"type": "session.idle", "data": {}},
                },
            }
        )

        assert ("s-stale", "stale-id") not in copilot_interceptor._pending
        handler.assert_not_called()

    def test_stale_entries_evicted_silently(self, handler):
        from coolhand import copilot_interceptor

        cls = _inject_fake_sdk()
        copilot_interceptor.set_handler(handler)
        copilot_interceptor.patch()

        # Insert a stale entry (started 400s ago)
        stale_start = time.time() - 400
        with copilot_interceptor._lock:
            copilot_interceptor._pending[("s-stale", "stale-id")] = {
                "req_data": {},
                "start": stale_start,
            }

        self._prime_pending(copilot_interceptor, "s1", "msg-001")

        instance = cls()
        instance._handle_message(
            _assistant_message_notification("s1", "msg-001", "world")
        )

        # Stale entry gone, handler called once (not for stale entry)
        assert ("s-stale", "stale-id") not in copilot_interceptor._pending
        handler.assert_called_once()

    def test_malformed_event_does_not_raise(self, handler):
        from coolhand import copilot_interceptor

        cls = _inject_fake_sdk()
        copilot_interceptor.set_handler(handler)
        copilot_interceptor.patch()

        instance = cls()
        # Missing nested keys — should not raise
        instance._handle_message({"method": "session.event", "params": None})
        handler.assert_not_called()


# ---------------------------------------------------------------------------
# TestEndToEnd
# ---------------------------------------------------------------------------


class TestEndToEnd:
    @pytest.mark.asyncio
    async def test_full_roundtrip(self, handler):
        from coolhand import copilot_interceptor

        cls = _inject_fake_sdk()
        copilot_interceptor.set_handler(handler)
        copilot_interceptor.patch()

        instance = cls()
        await instance.request(
            "session.send",
            {"sessionId": "s1", "prompt": "what is 2+2?"},
        )
        instance._handle_message(
            _assistant_message_notification("s1", "msg-001", "4", output_tokens=5)
        )

        handler.assert_called_once()
        req_data, res_data, err = handler.call_args[0]
        assert err is None
        assert req_data["body"]["prompt"] == "what is 2+2?"
        assert res_data["body"]["content"] == "4"
        assert res_data["body"]["output_tokens"] == 5
        assert res_data["status_code"] == 200
        assert res_data["duration"] > 0
        assert copilot_interceptor._pending == {}

    @pytest.mark.asyncio
    async def test_multiple_concurrent_sessions(self, handler):
        from coolhand import copilot_interceptor

        cls = _inject_fake_sdk()

        # Two different sessions with different messageIds
        async def fake_request(self, method, params=None, timeout=None):
            sid = (params or {}).get("sessionId")
            return {"messageId": f"msg-{sid}"}

        cls.request = fake_request
        copilot_interceptor.set_handler(handler)
        copilot_interceptor.patch()

        i1 = cls()
        i2 = cls()
        await i1.request("session.send", {"sessionId": "sA", "prompt": "ping"})
        await i2.request("session.send", {"sessionId": "sB", "prompt": "pong"})

        i1._handle_message(_assistant_message_notification("sA", "msg-sA", "reply-A"))
        i2._handle_message(_assistant_message_notification("sB", "msg-sB", "reply-B"))

        assert handler.call_count == 2
        contents = {c[0][1]["body"]["content"] for c in handler.call_args_list}
        assert contents == {"reply-A", "reply-B"}
        assert copilot_interceptor._pending == {}


# ---------------------------------------------------------------------------
# TestGetStats
# ---------------------------------------------------------------------------


class TestGetStats:
    def test_includes_copilot_entries_when_patched(self):
        from coolhand import copilot_interceptor
        from coolhand.client import CoolhandClient

        _inject_fake_sdk()
        copilot_interceptor.patch()

        client = CoolhandClient(
            {"api_key": "test-key", "silent": True, "auto_submit": False}
        )
        stats = client.get_stats()
        libs = stats["monitoring"]["patched_libraries"]
        assert "JsonRpcClient.request" in libs
        assert "JsonRpcClient._handle_message" in libs

    def test_excludes_copilot_entries_when_not_patched(self):
        from coolhand.client import CoolhandClient

        client = CoolhandClient(
            {"api_key": "test-key", "silent": True, "auto_submit": False}
        )
        stats = client.get_stats()
        libs = stats["monitoring"]["patched_libraries"]
        assert "JsonRpcClient.request" not in libs
        assert "JsonRpcClient._handle_message" not in libs
