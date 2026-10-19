"""JSON-RPC interceptor for github-copilot-sdk — patches JsonRpcClient."""

import itertools
import logging
import threading
import time
from collections.abc import Callable
from typing import Any

from .types import RequestData, ResponseData

logger = logging.getLogger(__name__)

COPILOT_INTERCEPTOR_PENDING_TTL_SECONDS = 300

_patched = False
_original_request: Callable | None = None
_original_handle_message: Callable | None = None
_handler: Callable[[RequestData, ResponseData | None, str | None], None] | None = None

# Per-session FIFO queues keyed by sessionId.  Entries are pushed here BEFORE
# the await in patched_request so that _handle_message can correlate an
# assistant.message notification that arrives on the reader thread before the
# event loop schedules the awaiting coroutine's resumption.
_pre_pending: dict[str | None, list[dict[str, Any]]] = {}

# session.create params keyed by sessionId — provides system prompt and other
# session-level config that is not repeated in subsequent session.send calls.
# Stored as {"params": dict, "start": float} so _sweep_stale can evict them.
# Note: entries are evicted after COPILOT_INTERCEPTOR_PENDING_TTL_SECONDS (300s).
# Sessions longer than this TTL will have their system prompt absent from
# session.send bodies logged after eviction — a logging gap, not a functional bug.
#
# For cloud/remote sessions (CopilotClient(enable_remote_sessions=True)) the
# session id is server-assigned and absent from the session.create request, so
# entries are temporarily keyed by a "__pending__{n}" placeholder and re-keyed
# to the real sessionId once it's known from the session.create response.
_session_params: dict[str, dict[str, Any]] = {}

# Counter for temporary "__pending__{n}" keys used above; guarded by _lock.
_pending_key_counter = itertools.count()

# Model name keyed by sessionId, updated on session.model.change notifications.
# Stored as {"model": str, "start": float}; timestamp refreshes on each change.
# Same TTL caveat as _session_params applies.
_session_models: dict[str | None, dict[str, Any]] = {}

# assistant.usage payload keyed by sessionId; merged into the response body on
# assistant.message. Stored as {"data": dict, "start": float} for TTL eviction.
# Entries are consumed (popped) when the paired assistant.message arrives.
_session_usage: dict[str | None, dict[str, Any]] = {}

_lock = threading.Lock()


def _promote_system_prompt(body: dict[str, Any]) -> None:
    """Surface Copilot's nested systemMessage.content as a top-level `system`
    string field, mirroring Anthropic's body.system shape so the backend's
    existing system-prompt extractor recognizes it. Mutates body in place;
    the original systemMessage field is left untouched.
    """
    system_message = body.get("systemMessage")
    if not isinstance(system_message, dict):
        return
    content = system_message.get("content")
    if isinstance(content, str) and content:
        body["system"] = content


def _remove_from_pre_pending(session_id: str | None, entry: dict[str, Any]) -> bool:
    """Remove entry from _pre_pending by object identity. Returns True if removed.

    Caller must hold _lock.
    """
    queue = _pre_pending.get(session_id, [])
    for i, e in enumerate(queue):
        if e is entry:
            queue.pop(i)
            if not queue:
                _pre_pending.pop(session_id, None)
            return True
    return False


def _sweep_stale() -> None:
    """Evict stale entries from all session caches.

    Cleans _pre_pending, _session_params, _session_models, and _session_usage.
    Runs unconditionally on every _handle_message call so entries are cleaned up
    even when a session terminates without emitting another assistant.message.
    """
    now = time.time()
    with _lock:
        stale_pre_sessions = []
        for sid, queue in list(_pre_pending.items()):
            while (
                queue
                and now - queue[0]["start"] > COPILOT_INTERCEPTOR_PENDING_TTL_SECONDS
            ):
                queue.pop(0)
                stale_pre_sessions.append(sid)
            if not queue:
                del _pre_pending[sid]

        stale_sessions = [
            k
            for k, v in _session_params.items()
            if now - v["start"] > COPILOT_INTERCEPTOR_PENDING_TTL_SECONDS
        ]
        for k in stale_sessions:
            del _session_params[k]

        stale_models = [
            k
            for k, v in _session_models.items()
            if now - v["start"] > COPILOT_INTERCEPTOR_PENDING_TTL_SECONDS
        ]
        for k in stale_models:
            del _session_models[k]

        stale_usage = [
            k
            for k, v in _session_usage.items()
            if now - v["start"] > COPILOT_INTERCEPTOR_PENDING_TTL_SECONDS
        ]
        for k in stale_usage:
            del _session_usage[k]

    if stale_pre_sessions or stale_sessions or stale_models or stale_usage:
        logger.debug(
            "Copilot interceptor: evicted %d stale pre-pending,"
            " %d stale session entries, %d stale model entries, %d stale usage entries",
            len(stale_pre_sessions),
            len(stale_sessions),
            len(stale_models),
            len(stale_usage),
        )


def set_handler(
    handler: Callable[[RequestData, ResponseData | None, str | None], None],
) -> None:
    """Set the handler for captured Copilot interactions."""
    global _handler
    _handler = handler


def patch() -> bool:
    """Patch JsonRpcClient to intercept github-copilot-sdk interactions."""
    global _patched, _original_request, _original_handle_message

    if _patched:
        return True

    try:
        from copilot._jsonrpc import JsonRpcClient
    except ImportError:
        try:
            from copilot.jsonrpc import JsonRpcClient  # type: ignore[no-redef]
        except ImportError:
            logger.debug("github-copilot-sdk not available")
            return False

    _original_request = JsonRpcClient.request
    _original_handle_message = JsonRpcClient._handle_message

    async def patched_request(self, method, params=None, timeout=None, **kwargs):
        if not _handler:
            return await _original_request(self, method, params, timeout, **kwargs)

        if method == "session.create":
            p_create = params or {}
            session_id = p_create.get("sessionId")
            if session_id:
                storage_key = session_id
            else:
                with _lock:
                    storage_key = f"__pending__{next(_pending_key_counter)}"
                logger.debug(
                    "Copilot interceptor: session.create has no sessionId"
                    " (cloud/remote session) — buffering under %s pending"
                    " server-assigned id",
                    storage_key,
                )
            with _lock:
                _session_params[storage_key] = {
                    "params": dict(p_create),
                    "start": time.time(),
                }

            result = await _original_request(self, method, params, timeout, **kwargs)

            resolved_session_id = session_id or (
                result.get("sessionId") if isinstance(result, dict) else None
            )
            if resolved_session_id and resolved_session_id != storage_key:
                with _lock:
                    entry = _session_params.pop(storage_key, None)
                    if entry is not None:
                        _session_params[resolved_session_id] = entry

            if resolved_session_id and isinstance(result, dict):
                model = result.get("model") or result.get("modelId")
                if model:
                    with _lock:
                        _session_models[resolved_session_id] = {
                            "model": model,
                            "start": time.time(),
                        }
                    logger.debug(
                        "Copilot interceptor: session.create response"
                        " — model=%s session=%s",
                        model,
                        resolved_session_id,
                    )
                else:
                    logger.debug(
                        "Copilot interceptor: session.create response keys: %s",
                        list(result.keys()) if result else [],
                    )
            return result

        if method != "session.send":
            return await _original_request(self, method, params, timeout, **kwargs)

        start = time.time()
        p = params or {}
        session_id = p.get("sessionId")
        with _lock:
            session_ctx = dict(_session_params.get(session_id, {}).get("params", {}))
        body = {**session_ctx, **dict(p)}
        _promote_system_prompt(body)
        req_data: RequestData = {
            "method": "POST",
            "url": "copilot://session.send",
            "headers": p.get("requestHeaders") or {},
            "body": body,
            "timestamp": start,
        }

        # Push to _pre_pending BEFORE the await.  The reader thread can call
        # _handle_message synchronously between resolving the send-response
        # Future and the event loop scheduling this coroutine's resumption.
        # Storing here ensures _handle_message finds the entry at that moment.
        entry: dict[str, Any] = {"req_data": req_data, "start": start}
        with _lock:
            _pre_pending.setdefault(session_id, []).append(entry)

        try:
            result = await _original_request(self, method, params, timeout, **kwargs)
        except Exception as e:
            with _lock:
                still_owned = _remove_from_pre_pending(session_id, entry)
            if still_owned:
                _handler(req_data, None, str(e))
            raise

        return result

    def patched_handle_message(self, message):
        _original_handle_message(self, message)
        if not _handler:
            return
        _sweep_stale()
        try:
            if (
                "method" in message
                and "id" not in message
                and message.get("method") == "session.event"
            ):
                msg_params = message.get("params", {})
                session_id = msg_params.get("sessionId")
                event = msg_params.get("event", {})
                event_type = event.get("type")
                logger.debug(
                    "Copilot interceptor: session.event type=%s session=%s",
                    event_type,
                    session_id,
                )
                if event_type == "session.model_change":
                    model = event.get("data", {}).get("newModel")
                    if model and session_id:
                        with _lock:
                            _session_models[session_id] = {
                                "model": model,
                                "start": time.time(),
                            }
                    else:
                        logger.debug(
                            "Copilot interceptor: session.model_change"
                            " missing model or sessionId — data: %s",
                            event.get("data"),
                        )
                elif event_type == "assistant.usage":
                    usage_data = event.get("data", {})
                    if session_id and usage_data:
                        with _lock:
                            _session_usage[session_id] = {
                                "data": dict(usage_data),
                                "start": time.time(),
                            }
                elif event_type == "assistant.message":
                    data = event.get("data", {})
                    with _lock:
                        queue = _pre_pending.get(session_id, [])
                        if queue:
                            pending = queue.pop(0)
                            if not queue:
                                del _pre_pending[session_id]
                        else:
                            pending = None
                        cached_model = _session_models.get(session_id, {}).get("model")
                        cached_usage = _session_usage.pop(session_id, {}).get(
                            "data", {}
                        )
                    if pending:
                        end = time.time()
                        res_data: ResponseData = {
                            "status_code": 200,
                            "headers": {},
                            "body": {
                                **data,
                                **cached_usage,
                                "sessionId": session_id,
                                "model": data.get("model") or cached_model,
                            },
                            "timestamp": end,
                            "duration": end - pending["start"],
                            "is_streaming": False,
                        }
                        try:
                            _handler(pending["req_data"], res_data, None)
                        except Exception as e:
                            logger.warning("Copilot handler error: %s", e)
        except Exception as e:
            logger.warning("Copilot message intercept error: %s", e)

    JsonRpcClient.request = patched_request
    JsonRpcClient._handle_message = patched_handle_message
    _patched = True
    logger.info("github-copilot-sdk monitoring enabled")
    return True


def unpatch() -> None:
    """Restore original JsonRpcClient methods."""
    global _patched

    if not _patched:
        return

    try:
        from copilot._jsonrpc import JsonRpcClient
    except ImportError:
        try:
            from copilot.jsonrpc import JsonRpcClient  # type: ignore[no-redef]
        except ImportError:
            JsonRpcClient = None  # type: ignore[assignment]

    if JsonRpcClient is not None:
        if _original_request:
            JsonRpcClient.request = _original_request
        if _original_handle_message:
            JsonRpcClient._handle_message = _original_handle_message

    with _lock:
        _pre_pending.clear()
        _session_params.clear()
        _session_models.clear()
        _session_usage.clear()

    _patched = False
    logger.info("github-copilot-sdk monitoring disabled")


def is_patched() -> bool:
    """Check if JsonRpcClient is patched."""
    return _patched
