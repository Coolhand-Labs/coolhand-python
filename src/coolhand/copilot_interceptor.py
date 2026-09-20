"""JSON-RPC interceptor for github-copilot-sdk — patches JsonRpcClient."""

import asyncio
import logging
import threading
import time
from collections.abc import Callable
from typing import Any

from .types import RequestData, ResponseData

logger = logging.getLogger(__name__)

COPILOT_INTERCEPTOR_PENDING_TTL_SECONDS = 300
# How long to wait for an assistant.message notification after the session.send
# RPC ack before logging the interaction as an error.  Covers cases where the
# copilot model rejects or drops the request without emitting a notification.
COPILOT_INTERCEPTOR_FALLBACK_TIMEOUT = 60.0

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
_session_params: dict[str | None, dict[str, Any]] = {}

# Model name keyed by sessionId, updated on session.model.change notifications.
# Stored as {"model": str, "start": float}; timestamp refreshes on each change.
# Same TTL caveat as _session_params applies.
_session_models: dict[str | None, dict[str, Any]] = {}

# assistant.usage payload keyed by sessionId; merged into the response body on
# assistant.message. Stored as {"data": dict, "start": float} for TTL eviction.
# Entries are consumed (popped) when the paired assistant.message arrives.
_session_usage: dict[str | None, dict[str, Any]] = {}

_lock = threading.Lock()


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
    Stale pre-pending entries are logged via the handler so requests that never
    received an assistant.message notification are not silently dropped.
    """
    now = time.time()
    stale_entries: list[dict[str, Any]] = []
    with _lock:
        for sid, queue in list(_pre_pending.items()):
            while (
                queue
                and now - queue[0]["start"] > COPILOT_INTERCEPTOR_PENDING_TTL_SECONDS
            ):
                stale_entries.append(queue.pop(0))
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

    for entry in stale_entries:
        if not entry.get("logged"):
            entry["logged"] = True
            _log_no_response(entry)

    if stale_entries or stale_sessions or stale_models or stale_usage:
        logger.debug(
            "Copilot interceptor: evicted %d stale pre-pending,"
            " %d stale session entries, %d stale model entries, %d stale usage entries",
            len(stale_entries),
            len(stale_sessions),
            len(stale_models),
            len(stale_usage),
        )


def _log_no_response(entry: dict[str, Any]) -> None:
    """Report a pre-pending entry whose assistant.message never (yet) arrived.

    Shared by _sweep_stale and _fallback_log so the error string and
    handler-call/exception-guard aren't duplicated between the two callers.
    """
    if not _handler:
        return
    try:
        _handler(entry["req_data"], None, "no assistant.message event received")
    except Exception as e:
        logger.warning("Copilot handler error on stale entry: %s", e)


def _cancel_timer(entry: dict[str, Any]) -> None:
    """Best-effort cancellation of a pre-pending entry's fallback timer.

    Uses call_soon_threadsafe because the caller (patched_handle_message, or
    unpatch()) may run on the SDK's reader thread rather than the event-loop
    thread that owns the TimerHandle. A closed loop (RuntimeError) is treated
    as a no-op — if the fallback still fires, _fallback_log's identity/logged
    check makes it harmless.
    """
    timer = entry.get("timer")
    timer_loop = entry.get("loop")
    if timer is not None and timer_loop is not None:
        try:
            timer_loop.call_soon_threadsafe(timer.cancel)
        except RuntimeError:
            pass


def _fallback_log(session_id: str | None, entry: dict[str, Any]) -> None:
    """Scheduled by call_later after COPILOT_INTERCEPTOR_FALLBACK_TIMEOUT seconds.

    Unlike _sweep_stale, this does NOT evict the entry from _pre_pending: the
    real assistant.message may still be a legitimately slow response that
    arrives after this fires, and _handle_message must still be able to find
    and deliver it (see the "assistant.message" branch below, which cancels
    this entry's timer and pops it normally whenever it does arrive). This
    only reports the not-yet-answered request as an error; entry["logged"]
    guards against _sweep_stale reporting the same entry again later.
    """
    with _lock:
        queue = _pre_pending.get(session_id, [])
        still_pending = any(e is entry for e in queue) and not entry.get("logged")
        if still_pending:
            entry["logged"] = True
    if still_pending:
        _log_no_response(entry)


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
                with _lock:
                    _session_params[session_id] = {
                        "params": dict(p_create),
                        "start": time.time(),
                    }
            else:
                logger.debug(
                    "Copilot interceptor: session.create has no sessionId,"
                    " system prompt will not be captured"
                )
            result = await _original_request(self, method, params, timeout, **kwargs)
            if session_id and isinstance(result, dict):
                model = result.get("model") or result.get("modelId")
                if model:
                    with _lock:
                        _session_models[session_id] = {
                            "model": model,
                            "start": time.time(),
                        }
                    logger.debug(
                        "Copilot interceptor: session.create response"
                        " — model=%s session=%s",
                        model,
                        session_id,
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
        req_data: RequestData = {
            "method": "POST",
            "url": "copilot://session.send",
            "headers": p.get("requestHeaders") or {},
            # requestHeaders is already carried (and masked) in "headers"; leaving
            # it in the body would ship Authorization/X-API-Key values unmasked.
            "body": {
                k: v
                for k, v in {**session_ctx, **dict(p)}.items()
                if k != "requestHeaders"
            },
            "timestamp": start,
        }

        # Push to _pre_pending BEFORE the await.  The reader thread can call
        # _handle_message synchronously between resolving the send-response
        # Future and the event loop scheduling this coroutine's resumption.
        # Storing here ensures _handle_message finds the entry at that moment.
        entry: dict[str, Any] = {
            "req_data": req_data,
            "start": start,
            "timer": None,
            "logged": False,
        }
        with _lock:
            _pre_pending.setdefault(session_id, []).append(entry)

        try:
            result = await _original_request(self, method, params, timeout, **kwargs)
        except Exception as e:
            with _lock:
                still_owned = _remove_from_pre_pending(session_id, entry)
            if still_owned:
                try:
                    _handler(req_data, None, str(e))
                except Exception:
                    logger.debug("Copilot handler error on error path", exc_info=True)
            raise

        # Schedule a fallback in case assistant.message never arrives (e.g. the
        # model rejected the request silently) — unless the reader thread
        # already delivered it via _handle_message while this coroutine was
        # suspended in the await above (the same pre-existing race the
        # exception branch above guards against with _remove_from_pre_pending).
        with _lock:
            still_pending = any(e is entry for e in _pre_pending.get(session_id, []))
        if still_pending:
            # patched_request only runs as an awaited coroutine, so a running
            # loop is always present here. The loop is stashed on the entry
            # too, since _handle_message may cancel the timer from the SDK's
            # reader thread and TimerHandle.cancel() is only safe to call from
            # the loop's own thread.
            loop = asyncio.get_running_loop()
            entry["loop"] = loop
            entry["timer"] = loop.call_later(
                COPILOT_INTERCEPTOR_FALLBACK_TIMEOUT,
                _fallback_log,
                session_id,
                entry,
            )

        return result

    def patched_handle_message(self, message):
        _original_handle_message(self, message)
        if not _handler:
            return
        try:
            _sweep_stale()
        except Exception:
            logger.debug("Copilot stale sweep failed", exc_info=True)
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
                        _cancel_timer(pending)
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
        for queue in _pre_pending.values():
            for entry in queue:
                _cancel_timer(entry)
        _pre_pending.clear()
        _session_params.clear()
        _session_models.clear()
        _session_usage.clear()

    _patched = False
    logger.info("github-copilot-sdk monitoring disabled")


def is_patched() -> bool:
    """Check if JsonRpcClient is patched."""
    return _patched
