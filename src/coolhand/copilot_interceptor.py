"""JSON-RPC interceptor for github-copilot-sdk — patches JsonRpcClient."""

import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from .types import RequestData, ResponseData

logger = logging.getLogger(__name__)

COPILOT_INTERCEPTOR_PENDING_TTL_SECONDS = 300

_patched = False
_original_request: Optional[Callable] = None
_original_handle_message: Optional[Callable] = None
_handler: Optional[
    Callable[[RequestData, Optional[ResponseData], Optional[str]], None]
] = None

# Primary pending store keyed by (sessionId, messageId) — normal path.
_pending: Dict[Tuple[Optional[str], str], Dict[str, Any]] = {}
# Pre-pending queues keyed by sessionId.  Entries are pushed here BEFORE the
# await in patched_request so that _handle_message can correlate a notification
# that arrives before the coroutine resumes (the read thread can dispatch a
# notification synchronously between resolving the send-response Future and the
# event loop scheduling the awaiting coroutine).
_pre_pending: Dict[Optional[str], List[Dict[str, Any]]] = {}

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
    """Evict stale entries from _pending and _pre_pending.

    Runs unconditionally on every _handle_message call so entries are cleaned
    up even when a session terminates without emitting another assistant.message.
    """
    now = time.time()
    with _lock:
        stale_main = [
            k
            for k, v in _pending.items()
            if now - v["start"] > COPILOT_INTERCEPTOR_PENDING_TTL_SECONDS
        ]
        for k in stale_main:
            del _pending[k]

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

    if stale_main or stale_pre_sessions:
        logger.debug(
            "Copilot interceptor: evicted %d stale pending,"
            " %d stale pre-pending entries",
            len(stale_main),
            len(stale_pre_sessions),
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
        if method != "session.send" or not _handler:
            return await _original_request(self, method, params, timeout)

        start = time.time()
        p = params or {}
        session_id = p.get("sessionId")
        req_data: RequestData = {
            "method": "POST",
            "url": "copilot://session.send",
            "headers": p.get("requestHeaders") or {},
            "body": {"messages": [{"role": "user", "content": p.get("prompt")}]},
            "timestamp": start,
        }

        # Push to _pre_pending BEFORE the await.  The read thread can call
        # _handle_message synchronously between resolving the send-response
        # Future and the event loop scheduling this coroutine's resumption.
        # Storing here ensures _handle_message finds the entry in that race.
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
                    msg_id = data.get("messageId")
                    session_id = msg_params.get("sessionId")
                    key = (session_id, msg_id)
                    with _lock:
                        pending = _pending.pop(key, None)
                        if pending is None:
                            # Race path: notification arrived before patched_request
                            # stored in _pending.  Take the first (oldest) entry for
                            # this session from _pre_pending (FIFO per session).
                            queue = _pre_pending.get(session_id, [])
                            if queue:
                                pending = queue.pop(0)
                                if not queue:
                                    del _pre_pending[session_id]
                    if pending:
                        end = time.time()
                        raw_tokens = data.get("outputTokens")
                        output_tokens = (
                            raw_tokens if isinstance(raw_tokens, int) else None
                        )
                        res_data: ResponseData = {
                            "status_code": 200,
                            "headers": {},
                            "body": {
                                "id": msg_id,
                                "model": data.get("model"),
                                "choices": [
                                    {
                                        "message": {
                                            "role": "assistant",
                                            "content": data.get("content"),
                                        }
                                    }
                                ],
                                "usage": {
                                    "completion_tokens": output_tokens,
                                    "prompt_tokens": None,
                                },
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
        _pending.clear()
        _pre_pending.clear()

    _patched = False
    logger.info("github-copilot-sdk monitoring disabled")


def is_patched() -> bool:
    """Check if JsonRpcClient is patched."""
    return _patched
