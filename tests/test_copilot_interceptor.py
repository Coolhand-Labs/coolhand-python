"""Tests for coolhand.copilot_interceptor module."""

import sys
import time
import types
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Helpers: build and inject a fake copilot SDK into sys.modules
# ---------------------------------------------------------------------------


def _make_fake_client_class():
    """Simulate github-copilot-sdk 0.1.x JsonRpcClient signature."""

    class FakeJsonRpcClient:
        async def request(self, method, params=None, timeout=None, **kwargs):
            return {"messageId": "msg-001"}

        def _handle_message(self, message):
            pass

    return FakeJsonRpcClient


def _make_fake_client_class_v1():
    """Simulate github-copilot-sdk 1.0.x JsonRpcClient signature.

    1.0.0 added ``on_response_inline`` as a keyword-only parameter to
    ``request()``.  Calls that pass it will raise TypeError against a wrapper
    that doesn't accept **kwargs — which is exactly the bug we're testing.
    """

    class FakeJsonRpcClientV1:
        # Track kwargs forwarded by the interceptor so tests can assert on them.
        last_kwargs: dict = {}

        async def request(
            self,
            method,
            params=None,
            timeout=None,
            *,
            on_response_inline=None,
        ):
            FakeJsonRpcClientV1.last_kwargs = {"on_response_inline": on_response_inline}
            return {"messageId": "msg-001"}

        def _handle_message(self, message):
            pass

    return FakeJsonRpcClientV1


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
        copilot_interceptor._pre_pending.clear()
        copilot_interceptor._session_params.clear()
        copilot_interceptor._session_models.clear()

    yield

    for k, v in saved.items():
        setattr(copilot_interceptor, k, v)
    with copilot_interceptor._lock:
        copilot_interceptor._pre_pending.clear()
        copilot_interceptor._session_params.clear()
        copilot_interceptor._session_models.clear()

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

    def test_unpatch_clears_state(self):
        from coolhand import copilot_interceptor

        _inject_fake_sdk()
        copilot_interceptor.patch()
        with copilot_interceptor._lock:
            copilot_interceptor._pre_pending["s1"] = [
                {"req_data": {}, "start": time.time()}
            ]
            copilot_interceptor._session_params["s1"] = {
                "params": {"systemMessage": "sys"},
                "start": time.time(),
            }
        copilot_interceptor.unpatch()
        assert copilot_interceptor._pre_pending == {}
        assert copilot_interceptor._session_params == {}

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

    def test_unpatch_graceful_when_sdk_removed_after_patch(self):
        from coolhand import copilot_interceptor

        _inject_fake_sdk()
        copilot_interceptor.patch()
        _remove_fake_sdk()

        # SDK import fails during unpatch — should not raise
        copilot_interceptor.unpatch()
        assert copilot_interceptor.is_patched() is False
        assert copilot_interceptor._pre_pending == {}
        assert copilot_interceptor._session_params == {}


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
        # Entry lives in _pre_pending until assistant.message is received;
        # messageId from the send result is not used for correlation.
        assert "s1" in copilot_interceptor._pre_pending
        entry = copilot_interceptor._pre_pending["s1"][0]
        assert entry["req_data"]["body"]["prompt"] == "hello"
        assert entry["req_data"]["body"]["sessionId"] == "s1"
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

        assert copilot_interceptor._pre_pending == {}
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
        assert copilot_interceptor._pre_pending == {}

    @pytest.mark.asyncio
    async def test_no_duplicate_handler_call_when_entry_already_consumed(self, handler):
        """If _handle_message consumes the entry (success) before the underlying
        request raises (e.g. read timeout after notification), the error path
        must not invoke the handler a second time."""
        from coolhand import copilot_interceptor

        consumed_by_handle_message = []

        class RaceClient:
            async def request(self_inner, method, params=None, timeout=None):
                # Simulate: notification arrives and is processed synchronously
                # before this coroutine raises.
                entry = copilot_interceptor._pre_pending.get("s1", [None])[0]
                if entry:
                    with copilot_interceptor._lock:
                        q = copilot_interceptor._pre_pending.get("s1", [])
                        if q:
                            consumed_by_handle_message.append(q.pop(0))
                            if not q:
                                del copilot_interceptor._pre_pending["s1"]
                raise RuntimeError("timeout after notification")

            def _handle_message(self, message):
                pass

        _inject_fake_sdk(RaceClient)
        copilot_interceptor.set_handler(handler)
        copilot_interceptor.patch()

        instance = RaceClient()
        with pytest.raises(RuntimeError, match="timeout after notification"):
            await instance.request("session.send", {"sessionId": "s1", "prompt": "hi"})

        # Entry was consumed before the raise — handler must not be called
        assert len(consumed_by_handle_message) == 1
        handler.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_pending_entry_when_handler_is_none(self):
        from coolhand import copilot_interceptor

        cls = _inject_fake_sdk()
        copilot_interceptor.patch()  # handler not set

        instance = cls()
        await instance.request("session.send", {"sessionId": "s1", "prompt": "hi"})
        assert copilot_interceptor._pre_pending == {}

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

        entry = copilot_interceptor._pre_pending["s1"][0]
        assert entry["req_data"]["headers"] == {"X-Custom": "value"}
        assert entry["req_data"]["body"]["attachments"] == [
            {"type": "file", "path": "/tmp/x"}
        ]

    def test_remove_from_pre_pending_returns_false_when_not_found(self):
        from coolhand import copilot_interceptor

        other_entry = {"req_data": {}, "start": time.time()}
        absent_entry = {"req_data": {}, "start": time.time()}
        with copilot_interceptor._lock:
            copilot_interceptor._pre_pending["s1"] = [other_entry]
            result = copilot_interceptor._remove_from_pre_pending("s1", absent_entry)

        assert result is False
        assert copilot_interceptor._pre_pending["s1"] == [other_entry]


# ---------------------------------------------------------------------------
# TestSessionCreate
# ---------------------------------------------------------------------------


class TestSessionCreate:
    @pytest.mark.asyncio
    async def test_session_create_stores_params(self, handler):
        from coolhand import copilot_interceptor

        cls = _inject_fake_sdk()
        copilot_interceptor.set_handler(handler)
        copilot_interceptor.patch()

        instance = cls()
        await instance.request(
            "session.create",
            {
                "sessionId": "s1",
                "systemMessage": {"content": "You are a helpful assistant."},
                "model": "gpt-4o",
            },
        )

        assert "s1" in copilot_interceptor._session_params
        stored = copilot_interceptor._session_params["s1"]["params"]
        assert stored["systemMessage"] == {"content": "You are a helpful assistant."}
        assert stored["model"] == "gpt-4o"
        handler.assert_not_called()

    @pytest.mark.asyncio
    async def test_session_create_skipped_when_no_handler(self):
        from coolhand import copilot_interceptor

        cls = _inject_fake_sdk()
        copilot_interceptor.patch()  # no handler

        instance = cls()
        await instance.request(
            "session.create",
            {"sessionId": "s1", "systemMessage": {"content": "sys"}},
        )

        assert copilot_interceptor._session_params == {}

    @pytest.mark.asyncio
    async def test_session_send_merges_session_create_params(self, handler):
        from coolhand import copilot_interceptor

        cls = _inject_fake_sdk()
        copilot_interceptor.set_handler(handler)
        copilot_interceptor.patch()

        instance = cls()
        await instance.request(
            "session.create",
            {
                "sessionId": "s1",
                "systemMessage": {"content": "You are a music expert."},
                "model": "gpt-4o",
            },
        )
        await instance.request(
            "session.send",
            {"sessionId": "s1", "prompt": "hello"},
        )

        entry = copilot_interceptor._pre_pending["s1"][0]
        body = entry["req_data"]["body"]
        assert body["prompt"] == "hello"
        assert body["systemMessage"] == {"content": "You are a music expert."}
        assert body["model"] == "gpt-4o"

    @pytest.mark.asyncio
    async def test_session_send_params_take_precedence(self, handler):
        from coolhand import copilot_interceptor

        cls = _inject_fake_sdk()
        copilot_interceptor.set_handler(handler)
        copilot_interceptor.patch()

        instance = cls()
        await instance.request(
            "session.create",
            {"sessionId": "s1", "model": "gpt-4o"},
        )
        await instance.request(
            "session.send",
            {"sessionId": "s1", "prompt": "hi", "model": "gpt-4o-mini"},
        )

        body = copilot_interceptor._pre_pending["s1"][0]["req_data"]["body"]
        assert body["model"] == "gpt-4o-mini"

    @pytest.mark.asyncio
    async def test_unpatch_clears_session_params(self, handler):
        from coolhand import copilot_interceptor

        _inject_fake_sdk()
        copilot_interceptor.set_handler(handler)
        copilot_interceptor.patch()
        with copilot_interceptor._lock:
            copilot_interceptor._session_params["s1"] = {
                "params": {"systemMessage": "sys"},
                "start": time.time(),
            }
        copilot_interceptor.unpatch()
        assert copilot_interceptor._session_params == {}

    @pytest.mark.asyncio
    async def test_session_create_without_session_id_is_noop(self, handler):
        from coolhand import copilot_interceptor

        cls = _inject_fake_sdk()
        copilot_interceptor.set_handler(handler)
        copilot_interceptor.patch()

        instance = cls()
        await instance.request(
            "session.create",
            {"systemMessage": {"content": "sys"}},  # no sessionId
        )

        assert copilot_interceptor._session_params == {}
        handler.assert_not_called()


# ---------------------------------------------------------------------------
# TestSessionModelChange
# ---------------------------------------------------------------------------


def _model_change_notification(session_id, model):
    return {
        "method": "session.event",
        "params": {
            "sessionId": session_id,
            "event": {
                "type": "session.model_change",
                "data": {"newModel": model},
            },
        },
    }


class TestSessionModelChange:
    def test_model_change_stores_model(self, handler):
        from coolhand import copilot_interceptor

        cls = _inject_fake_sdk()
        copilot_interceptor.set_handler(handler)
        copilot_interceptor.patch()

        instance = cls()
        instance._handle_message(_model_change_notification("s1", "gpt-4o"))

        assert "s1" in copilot_interceptor._session_models
        assert copilot_interceptor._session_models["s1"]["model"] == "gpt-4o"
        handler.assert_not_called()

    def test_model_change_updates_on_subsequent_change(self, handler):
        from coolhand import copilot_interceptor

        cls = _inject_fake_sdk()
        copilot_interceptor.set_handler(handler)
        copilot_interceptor.patch()

        instance = cls()
        instance._handle_message(_model_change_notification("s1", "gpt-4o"))
        instance._handle_message(_model_change_notification("s1", "o3"))

        assert copilot_interceptor._session_models["s1"]["model"] == "o3"

    def test_cached_model_included_in_response_body(self, handler):
        from coolhand import copilot_interceptor

        cls = _inject_fake_sdk()
        copilot_interceptor.set_handler(handler)
        copilot_interceptor.patch()

        instance = cls()
        instance._handle_message(_model_change_notification("s1", "gpt-4o"))

        # Prime a pending entry then fire assistant.message
        start = time.time() - 0.1
        req_data = {
            "method": "POST",
            "url": "copilot://session.send",
            "headers": {},
            "body": {"prompt": "hi", "sessionId": "s1"},
            "timestamp": start,
        }
        with copilot_interceptor._lock:
            copilot_interceptor._pre_pending.setdefault("s1", []).append(
                {"req_data": req_data, "start": start}
            )

        instance._handle_message(
            _assistant_message_notification("s1", "msg-001", "hello")
        )

        handler.assert_called_once()
        _, res_data, _ = handler.call_args[0]
        assert res_data["body"]["model"] == "gpt-4o"

    def test_data_model_takes_precedence_over_cache(self, handler):
        from coolhand import copilot_interceptor

        cls = _inject_fake_sdk()
        copilot_interceptor.set_handler(handler)
        copilot_interceptor.patch()

        instance = cls()
        instance._handle_message(_model_change_notification("s1", "gpt-4o"))

        start = time.time() - 0.1
        req_data = {
            "method": "POST",
            "url": "copilot://session.send",
            "headers": {},
            "body": {"prompt": "hi", "sessionId": "s1"},
            "timestamp": start,
        }
        with copilot_interceptor._lock:
            copilot_interceptor._pre_pending.setdefault("s1", []).append(
                {"req_data": req_data, "start": start}
            )

        # Notification carries its own model value
        msg = _assistant_message_notification("s1", "msg-001", "hello")
        msg["params"]["event"]["data"]["model"] = "o3-mini"
        instance._handle_message(msg)

        _, res_data, _ = handler.call_args[0]
        assert res_data["body"]["model"] == "o3-mini"

    def test_model_change_missing_model_field_is_noop(self, handler):
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
                    "event": {"type": "session.model_change", "data": {}},
                },
            }
        )

        assert copilot_interceptor._session_models == {}
        handler.assert_not_called()

    def test_stale_model_entries_evicted(self, handler):
        from coolhand import copilot_interceptor

        cls = _inject_fake_sdk()
        copilot_interceptor.set_handler(handler)
        copilot_interceptor.patch()

        with copilot_interceptor._lock:
            copilot_interceptor._session_models["s-old"] = {
                "model": "gpt-4o",
                "start": time.time() - 400,
            }

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

        assert "s-old" not in copilot_interceptor._session_models

    def test_unpatch_clears_session_models(self, handler):
        from coolhand import copilot_interceptor

        _inject_fake_sdk()
        copilot_interceptor.set_handler(handler)
        copilot_interceptor.patch()

        with copilot_interceptor._lock:
            copilot_interceptor._session_models["s1"] = {
                "model": "gpt-4o",
                "start": time.time(),
            }
        copilot_interceptor.unpatch()
        assert copilot_interceptor._session_models == {}


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
    def _prime_pending(self, copilot_interceptor, session_id, prompt="hello"):
        start = time.time() - 0.1
        req_data = {
            "method": "POST",
            "url": "copilot://session.send",
            "headers": {},
            "body": {"prompt": prompt, "sessionId": session_id},
            "timestamp": start,
        }
        with copilot_interceptor._lock:
            copilot_interceptor._pre_pending.setdefault(session_id, []).append(
                {"req_data": req_data, "start": start}
            )

    def test_handler_called_with_correct_data(self, handler):
        from coolhand import copilot_interceptor

        cls = _inject_fake_sdk()
        copilot_interceptor.set_handler(handler)
        copilot_interceptor.patch()
        self._prime_pending(copilot_interceptor, "s1", "hello")

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
        assert res_data["body"]["messageId"] == "msg-001"
        assert res_data["body"]["sessionId"] == "s1"
        assert res_data["body"]["model"] == "gpt-4o"
        assert res_data["body"]["outputTokens"] == 42
        assert res_data["duration"] > 0
        assert res_data["is_streaming"] is False

    def test_pending_entry_removed_after_match(self, handler):
        from coolhand import copilot_interceptor

        cls = _inject_fake_sdk()
        copilot_interceptor.set_handler(handler)
        copilot_interceptor.patch()
        self._prime_pending(copilot_interceptor, "s1")

        instance = cls()
        instance._handle_message(
            _assistant_message_notification("s1", "msg-001", "world")
        )

        assert "s1" not in copilot_interceptor._pre_pending

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
        self._prime_pending(copilot_interceptor, "s1")

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

    def test_no_pending_entry_skips_handler_but_original_called(self, handler):
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
        self._prime_pending(copilot_interceptor, "s1")

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
            "body": {"prompt": "hello", "sessionId": "s1"},
            "timestamp": start,
        }
        entry = {"req_data": req_data, "start": start}
        with copilot_interceptor._lock:
            copilot_interceptor._pre_pending.setdefault("s1", []).append(entry)
        # _pre_pending has the entry; _handle_message must find it there

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
            copilot_interceptor._pre_pending["s-stale"] = [
                {"req_data": {}, "start": stale_start}
            ]

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

        assert "s-stale" not in copilot_interceptor._pre_pending
        handler.assert_not_called()

    def test_stale_entries_evicted_silently(self, handler):
        from coolhand import copilot_interceptor

        cls = _inject_fake_sdk()
        copilot_interceptor.set_handler(handler)
        copilot_interceptor.patch()

        # Insert a stale pre-pending entry alongside a live one
        stale_start = time.time() - 400
        with copilot_interceptor._lock:
            copilot_interceptor._pre_pending["s-stale"] = [
                {"req_data": {}, "start": stale_start}
            ]

        self._prime_pending(copilot_interceptor, "s1")

        instance = cls()
        instance._handle_message(
            _assistant_message_notification("s1", "msg-001", "world")
        )

        # Stale entry gone, handler called once (not for stale entry)
        assert "s-stale" not in copilot_interceptor._pre_pending
        handler.assert_called_once()

    def test_stale_pre_pending_entries_evicted(self, handler):
        from coolhand import copilot_interceptor

        cls = _inject_fake_sdk()
        copilot_interceptor.set_handler(handler)
        copilot_interceptor.patch()

        stale_start = time.time() - 400
        with copilot_interceptor._lock:
            copilot_interceptor._pre_pending["s-stale"] = [
                {"req_data": {}, "start": stale_start}
            ]

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

        assert "s-stale" not in copilot_interceptor._pre_pending
        handler.assert_not_called()

    def test_stale_session_params_evicted(self, handler):
        from coolhand import copilot_interceptor

        cls = _inject_fake_sdk()
        copilot_interceptor.set_handler(handler)
        copilot_interceptor.patch()

        stale_start = time.time() - 400
        with copilot_interceptor._lock:
            copilot_interceptor._session_params["s-old"] = {
                "params": {"systemMessage": "sys"},
                "start": stale_start,
            }

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

        assert "s-old" not in copilot_interceptor._session_params
        handler.assert_not_called()

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
        assert res_data["body"]["outputTokens"] == 5
        assert res_data["status_code"] == 200
        assert res_data["duration"] > 0
        assert copilot_interceptor._pre_pending == {}

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
        assert copilot_interceptor._pre_pending == {}


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


# ---------------------------------------------------------------------------
# TestKwargsForwarding — SDK 1.0 compatibility: on_response_inline forwarding
# ---------------------------------------------------------------------------


class TestKwargsForwarding:
    """Verify that patched_request forwards **kwargs to _original_request.

    github-copilot-sdk 1.0 added the keyword-only parameter on_response_inline
    to JsonRpcClient.request. The patch must forward it (and any future kwargs)
    transparently so callers don't receive a TypeError.
    """

    def _make_recording_client(self):
        """Fake client that records the kwargs passed to request()."""
        recorded = []

        class RecordingClient:
            async def request(self_inner, method, params=None, timeout=None, **kwargs):
                recorded.append(kwargs)
                return {"messageId": "msg-001"}

            def _handle_message(self_inner, message):
                pass

        return RecordingClient, recorded

    @pytest.mark.asyncio
    async def test_kwargs_forwarded_no_handler(self):
        from coolhand import copilot_interceptor

        cls, recorded = self._make_recording_client()
        _inject_fake_sdk(cls)
        copilot_interceptor.patch()

        sentinel = object()
        instance = cls()
        await instance.request("session.send", {}, on_response_inline=sentinel)

        assert len(recorded) == 1
        assert recorded[0].get("on_response_inline") is sentinel

    @pytest.mark.asyncio
    async def test_kwargs_forwarded_session_create(self, handler):
        from coolhand import copilot_interceptor

        cls, recorded = self._make_recording_client()
        _inject_fake_sdk(cls)
        copilot_interceptor.set_handler(handler)
        copilot_interceptor.patch()

        sentinel = object()
        instance = cls()
        await instance.request(
            "session.create",
            {"sessionId": "s1"},
            on_response_inline=sentinel,
        )

        assert len(recorded) == 1
        assert recorded[0].get("on_response_inline") is sentinel

    @pytest.mark.asyncio
    async def test_kwargs_forwarded_other_method(self, handler):
        from coolhand import copilot_interceptor

        cls, recorded = self._make_recording_client()
        _inject_fake_sdk(cls)
        copilot_interceptor.set_handler(handler)
        copilot_interceptor.patch()

        sentinel = object()
        instance = cls()
        await instance.request("models.list", {}, on_response_inline=sentinel)

        assert len(recorded) == 1
        assert recorded[0].get("on_response_inline") is sentinel

    @pytest.mark.asyncio
    async def test_kwargs_forwarded_session_send(self, handler):
        from coolhand import copilot_interceptor

        cls, recorded = self._make_recording_client()
        _inject_fake_sdk(cls)
        copilot_interceptor.set_handler(handler)
        copilot_interceptor.patch()

        sentinel = object()
        instance = cls()
        await instance.request(
            "session.send",
            {"sessionId": "s1", "prompt": "hi"},
            on_response_inline=sentinel,
        )

        assert len(recorded) == 1
        assert recorded[0].get("on_response_inline") is sentinel
        assert "s1" in copilot_interceptor._pre_pending


# ---------------------------------------------------------------------------
# TestSDKVersionCompatibility
#
# Verifies that the patched_request wrapper correctly forwards unknown keyword
# arguments (specifically on_response_inline, added in github-copilot-sdk 1.0)
# to the original method, and that both SDK generations produce identical logs.
# ---------------------------------------------------------------------------


class TestSDKVersionCompatibility:
    @pytest.mark.asyncio
    async def test_v1_session_create_with_on_response_inline_does_not_raise(
        self, handler
    ):
        """patched_request must not raise TypeError when called with on_response_inline.

        Copilot SDK 1.0 passes on_response_inline on every session.create call.
        Without **kwargs forwarding in patched_request this raises TypeError.
        """
        from coolhand import copilot_interceptor

        cls = _inject_fake_sdk(_make_fake_client_class_v1())
        copilot_interceptor.set_handler(handler)
        copilot_interceptor.patch()

        instance = cls()
        await instance.request(
            "session.create",
            {"sessionId": "s1", "systemMessage": {"content": "sys"}},
            on_response_inline=lambda x: None,
        )

        handler.assert_not_called()  # session.create never calls handler directly

    @pytest.mark.asyncio
    async def test_v1_session_send_with_extra_kwargs_does_not_raise(self, handler):
        """Extra kwargs on session.send are forwarded without error."""
        from coolhand import copilot_interceptor

        cls = _inject_fake_sdk(_make_fake_client_class_v1())
        copilot_interceptor.set_handler(handler)
        copilot_interceptor.patch()

        instance = cls()
        await instance.request(
            "session.send",
            {"sessionId": "s1", "prompt": "hello"},
            on_response_inline=lambda x: None,
        )

        assert "s1" in copilot_interceptor._pre_pending

    @pytest.mark.asyncio
    async def test_v1_on_response_inline_forwarded_to_original(self, handler):
        """The on_response_inline callback reaches the original request method."""
        from coolhand import copilot_interceptor

        cls = _inject_fake_sdk(_make_fake_client_class_v1())
        copilot_interceptor.set_handler(handler)
        copilot_interceptor.patch()

        sentinel = object()
        instance = cls()
        await instance.request(
            "session.create",
            {"sessionId": "s1"},
            on_response_inline=sentinel,
        )

        assert cls.last_kwargs["on_response_inline"] is sentinel

    @pytest.mark.asyncio
    async def test_v1_no_handler_passthrough_forwards_kwargs(self):
        """Fast-path (no handler) also forwards kwargs to the original method."""
        from coolhand import copilot_interceptor

        cls = _inject_fake_sdk(_make_fake_client_class_v1())
        copilot_interceptor.patch()  # no handler set

        sentinel = object()
        instance = cls()
        await instance.request(
            "session.create",
            {"sessionId": "s1"},
            on_response_inline=sentinel,
        )

        assert cls.last_kwargs["on_response_inline"] is sentinel

    @pytest.mark.asyncio
    async def test_v1_error_path_forwards_kwargs(self, handler):
        """kwargs are forwarded even when the original raises, and handler is called."""
        from coolhand import copilot_interceptor

        received_kwargs = {}

        class ErrorClientV1:
            async def request(
                self, method, params=None, timeout=None, *, on_response_inline=None
            ):
                received_kwargs["on_response_inline"] = on_response_inline
                raise RuntimeError("rpc failed")

            def _handle_message(self, message):
                pass

        _inject_fake_sdk(ErrorClientV1)
        copilot_interceptor.set_handler(handler)
        copilot_interceptor.patch()

        sentinel = object()
        instance = ErrorClientV1()
        with pytest.raises(RuntimeError, match="rpc failed"):
            await instance.request(
                "session.send",
                {"sessionId": "s1", "prompt": "hi"},
                on_response_inline=sentinel,
            )

        assert received_kwargs["on_response_inline"] is sentinel
        handler.assert_called_once()
        _, res_data, err = handler.call_args[0]
        assert res_data is None
        assert "rpc failed" in err

    @pytest.mark.asyncio
    async def test_v0_and_v1_produce_identical_logs(self):
        """Both SDK generations must emit structurally identical request/response logs.

        The on_response_inline kwarg is an SDK-internal callback; it must not
        leak into or alter the captured log data.
        """
        from coolhand import copilot_interceptor

        def _reset_interceptor():
            copilot_interceptor._patched = False
            copilot_interceptor._original_request = None
            copilot_interceptor._original_handle_message = None
            copilot_interceptor._handler = None
            with copilot_interceptor._lock:
                copilot_interceptor._pre_pending.clear()
                copilot_interceptor._session_params.clear()
                copilot_interceptor._session_models.clear()

        # --- v0 (0.1.x) run ---
        _reset_interceptor()
        captured_v0: dict = {}

        def capture_v0(req, res, err):
            captured_v0["req"] = req
            captured_v0["res"] = res

        cls_v0 = _inject_fake_sdk(_make_fake_client_class())
        copilot_interceptor.set_handler(capture_v0)
        copilot_interceptor.patch()

        inst_v0 = cls_v0()
        await inst_v0.request(
            "session.create",
            {"sessionId": "s1", "systemMessage": {"content": "sys"}, "model": "gpt-4o"},
        )
        await inst_v0.request("session.send", {"sessionId": "s1", "prompt": "hello"})
        inst_v0._handle_message(
            _assistant_message_notification("s1", "msg-001", "world", model="gpt-4o")
        )

        _remove_fake_sdk()

        # --- v1 (1.0.x) run ---
        _reset_interceptor()
        captured_v1: dict = {}

        def capture_v1(req, res, err):
            captured_v1["req"] = req
            captured_v1["res"] = res

        cls_v1 = _inject_fake_sdk(_make_fake_client_class_v1())
        copilot_interceptor.set_handler(capture_v1)
        copilot_interceptor.patch()

        inst_v1 = cls_v1()
        # SDK 1.0 passes on_response_inline on session.create
        await inst_v1.request(
            "session.create",
            {"sessionId": "s1", "systemMessage": {"content": "sys"}, "model": "gpt-4o"},
            on_response_inline=lambda x: None,
        )
        await inst_v1.request("session.send", {"sessionId": "s1", "prompt": "hello"})
        inst_v1._handle_message(
            _assistant_message_notification("s1", "msg-001", "world", model="gpt-4o")
        )

        req_v0 = captured_v0["req"]
        req_v1 = captured_v1["req"]
        res_v0 = captured_v0["res"]
        res_v1 = captured_v1["res"]

        assert req_v0["url"] == req_v1["url"]
        assert req_v0["method"] == req_v1["method"]
        assert req_v0["body"]["prompt"] == req_v1["body"]["prompt"]
        assert req_v0["body"]["sessionId"] == req_v1["body"]["sessionId"]
        assert req_v0["body"]["systemMessage"] == req_v1["body"]["systemMessage"]
        assert req_v0["body"]["model"] == req_v1["body"]["model"]
        assert res_v0["status_code"] == res_v1["status_code"]
        assert res_v0["body"]["content"] == res_v1["body"]["content"]
        assert res_v0["body"]["messageId"] == res_v1["body"]["messageId"]
        assert res_v0["body"]["sessionId"] == res_v1["body"]["sessionId"]
        assert res_v0["body"]["model"] == res_v1["body"]["model"]
        assert res_v0["is_streaming"] == res_v1["is_streaming"]


# ---------------------------------------------------------------------------
# TestNewEventTypes
#
# Verifies that event types added in github-copilot-sdk 1.0 pass through the
# _handle_message interceptor without errors and without spuriously calling the
# handler (they are not assistant.message completions).
# ---------------------------------------------------------------------------


class TestNewEventTypes:
    def _make_v1_notification(self, session_id, event_type, data=None):
        return {
            "method": "session.event",
            "params": {
                "sessionId": session_id,
                "event": {
                    "type": event_type,
                    "data": data or {},
                },
            },
        }

    def test_assistant_reasoning_event_does_not_call_handler(self, handler):
        """assistant.reasoning is a new 1.0 event type; interceptor must ignore it."""
        from coolhand import copilot_interceptor

        cls = _inject_fake_sdk()
        copilot_interceptor.set_handler(handler)
        copilot_interceptor.patch()

        instance = cls()
        instance._handle_message(
            self._make_v1_notification(
                "s1", "assistant.reasoning", {"content": "thinking step"}
            )
        )

        handler.assert_not_called()

    def test_assistant_reasoning_delta_does_not_call_handler(self, handler):
        """assistant.reasoning.delta (streaming chunk) must not trigger handler."""
        from coolhand import copilot_interceptor

        cls = _inject_fake_sdk()
        copilot_interceptor.set_handler(handler)
        copilot_interceptor.patch()

        instance = cls()
        instance._handle_message(
            self._make_v1_notification(
                "s1", "assistant.reasoning.delta", {"delta": "..."}
            )
        )

        handler.assert_not_called()

    def test_assistant_message_delta_does_not_call_handler(self, handler):
        """assistant.message.delta (streaming chunk) must not trigger handler."""
        from coolhand import copilot_interceptor

        cls = _inject_fake_sdk()
        copilot_interceptor.set_handler(handler)
        copilot_interceptor.patch()

        instance = cls()
        instance._handle_message(
            self._make_v1_notification(
                "s1", "assistant.message.delta", {"delta": "partial"}
            )
        )

        handler.assert_not_called()

    def test_unknown_event_type_does_not_raise(self, handler):
        """Any future unknown event type must not raise."""
        from coolhand import copilot_interceptor

        cls = _inject_fake_sdk()
        copilot_interceptor.set_handler(handler)
        copilot_interceptor.patch()

        instance = cls()
        instance._handle_message(
            self._make_v1_notification("s1", "future.unknown.event.type", {})
        )

        handler.assert_not_called()

    def test_external_tool_requested_does_not_call_handler(self, handler):
        """external_tool.requested is a new v3-protocol event in SDK 1.0."""
        from coolhand import copilot_interceptor

        cls = _inject_fake_sdk()
        copilot_interceptor.set_handler(handler)
        copilot_interceptor.patch()

        instance = cls()
        instance._handle_message(
            self._make_v1_notification(
                "s1", "external_tool.requested", {"toolName": "bash", "input": "ls"}
            )
        )

        handler.assert_not_called()

    def test_new_event_types_do_not_corrupt_pending_state(self, handler):
        """Interleaved new-event-type notifications must not drain _pre_pending."""
        from coolhand import copilot_interceptor

        cls = _inject_fake_sdk()
        copilot_interceptor.set_handler(handler)
        copilot_interceptor.patch()

        start = time.time() - 0.05
        req_data = {
            "method": "POST",
            "url": "copilot://session.send",
            "headers": {},
            "body": {"prompt": "hi", "sessionId": "s1"},
            "timestamp": start,
        }
        with copilot_interceptor._lock:
            copilot_interceptor._pre_pending.setdefault("s1", []).append(
                {"req_data": req_data, "start": start}
            )

        instance = cls()
        noise_events = (
            "assistant.reasoning",
            "assistant.message.delta",
            "session.idle",
        )
        for event_type in noise_events:
            instance._handle_message(self._make_v1_notification("s1", event_type, {}))

        assert "s1" in copilot_interceptor._pre_pending
        handler.assert_not_called()

        instance._handle_message(
            _assistant_message_notification("s1", "msg-001", "done")
        )

        handler.assert_called_once()
        assert "s1" not in copilot_interceptor._pre_pending
