"""JSON-RPC interceptor for github-copilot-sdk — patches JsonRpcClient."""

import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from .types import RequestData, ResponseData

logger = logging.getLogger(__name__)

COPILOT_INTERCEPTOR_PENDING_TTL_SECONDS = 300

_patched = False
_original_request: Optional[Callable] = None
_original_handle_message: Optional[Callable] = None
_handler: Optional[
    Callable[[RequestData, Optional[ResponseData], Optional[str]], None]
] = None

# Per-session FIFO queues keyed by sessionId.  Entries are pushed here BEFORE
# the await in patched_request so that _handle_message can correlate an
# assistant.message notification that arrives on the reader thread before the
# event loop schedules the awaiting coroutine's resumption.
_pre_pending: Dict[Optional[str], List[Dict[str, Any]]] = {}

# session.create params keyed by sessionId — provides system prompt and other
# session-level config that is not repeated in subsequent session.send calls.
# Stored as {"params": dict, "start": float} so _sweep_stale can evict them.
# Note: entries are evicted after COPILOT_INTERCEPTOR_PENDING_TTL_SECONDS (300s).
# Sessions longer than this TTL will have their system prompt absent from
# session.send bodies logged after eviction — a logging gap, not a functional bug.
_session_params: Dict[Optional[str], Dict[str, Any]] = {}

_lock = threading.Lock()


def _remove_from_pre_pending(session_id: Optional[str], entry: Dict[str, Any]) -> bool:
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
    """Evict stale entries from _pre_pending and _session_params.

    Runs unconditionally on every _handle_message call so entries are cleaned
    up even when a session terminates without emitting another assistant.message.
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

    if stale_pre_sessions or stale_sessions:
        logger.debug(
            "Copilot interceptor: evicted %d stale pre-pending,"
            " %d stale session entries",
            len(stale_pre_sessions),
            len(stale_sessions),
        )


def set_handler(
    handler: Callable[[RequestData, Optional[ResponseData], Optional[str]], None],
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

    async def patched_request(self, method, params=None, timeout=None):
        if not _handler:
            return await _original_request(self, method, params, timeout)

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
            return await _original_request(self, method, params, timeout)

        if method != "session.send":
            return await _original_request(self, method, params, timeout)

        start = time.time()
        p = params or {}
        session_id = p.get("sessionId")
        with _lock:
            session_ctx = dict(_session_params.get(session_id, {}).get("params", {}))
        req_data: RequestData = {
            "method": "POST",
            "url": "copilot://session.send",
            "headers": p.get("requestHeaders") or {},
            "body": {**session_ctx, **dict(p)},
            "timestamp": start,
        }

        # Push to _pre_pending BEFORE the await.  The reader thread can call
        # _handle_message synchronously between resolving the send-response
        # Future and the event loop scheduling this coroutine's resumption.
        # Storing here ensures _handle_message finds the entry at that moment.
        entry: Dict[str, Any] = {"req_data": req_data, "start": start}
        with _lock:
            _pre_pending.setdefault(session_id, []).append(entry)

        try:
            result = await _original_request(self, method, params, timeout)
        except Exception as e:
            with _lock:
                _remove_from_pre_pending(session_id, entry)
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
                event = msg_params.get("event", {})
                if event.get("type") == "assistant.message":
                    data = event.get("data", {})
                    session_id = msg_params.get("sessionId")
                    with _lock:
                        queue = _pre_pending.get(session_id, [])
                        if queue:
                            pending = queue.pop(0)
                            if not queue:
                                del _pre_pending[session_id]
                        else:
                            pending = None
                    if pending:
                        end = time.time()
                        res_data: ResponseData = {
                            "status_code": 200,
                            "headers": {},
                            "body": {**data, "sessionId": session_id},
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

    _patched = False
    logger.info("github-copilot-sdk monitoring disabled")


def is_patched() -> bool:
    """Check if JsonRpcClient is patched."""
    return _patched
