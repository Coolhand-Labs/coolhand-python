"""Coolhand client for submitting API interactions."""

import atexit
import json
import logging
import os
import queue
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen

from ._config import _DEFAULT_BASE_URL, _normalize_base_url, _ssl_context
from .httpx_interceptor import DEFAULT_EXCLUDE_API_PATTERNS
from .types import Config, RequestData, ResponseData
from .version import __version__

logger = logging.getLogger(__name__)

# Bound on how many interactions may be waiting for delivery at once. Once full,
# flush() drops new items rather than blocking the caller (which may be an
# asyncio event loop thread).
_MAX_DISPATCH_QUEUE_SIZE = 1000

# How long shutdown() waits for the worker thread to drain the dispatch queue
# before giving up. Bounded so a dead/unreachable backend can't hang process exit.
_SHUTDOWN_TIMEOUT = 5.0

# Sentinel telling the worker thread to stop after finishing queued work.
_SHUTDOWN_SENTINEL = object()

# Sensitive headers to mask
SENSITIVE_HEADERS = [
    "authorization",
    "api-key",
    "x-api-key",
    "openai-api-key",
    "anthropic-api-key",
    "x-goog-api-key",
    "cookie",
    "set-cookie",
    "proxy-authorization",
    "x-amz-security-token",
    "x-amz-signature",
]

SENSITIVE_QUERY_PARAMS = {"key", "api_key", "apikey", "token", "access_token", "secret"}


def _get_default_config() -> Config:
    """Get default configuration from environment."""
    return {
        "api_key": os.getenv("COOLHAND_API_KEY") or None,
        "base_url": os.getenv("COOLHAND_BASE_URL") or _DEFAULT_BASE_URL,
        "silent": os.getenv("COOLHAND_SILENT", "true").lower() == "true",
        "auto_submit": True,
        "session_id": f"session_{int(time.time() * 1000)}",
        "exclude_api_patterns": list(DEFAULT_EXCLUDE_API_PATTERNS),
    }


def _mask_value(value: str) -> str:
    """Mask a sensitive value, keeping first/last 4 chars."""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}{'*' * (len(value) - 8)}{value[-4:]}"


def _sanitize_headers(headers: dict[str, str]) -> dict[str, str]:
    """Sanitize sensitive headers."""
    result = {}
    for key, value in headers.items():
        if any(s in key.lower() for s in SENSITIVE_HEADERS):
            result[key] = _mask_value(str(value))
        else:
            result[key] = value
    return result


def _sanitize_url(url: str) -> str:
    """Redact sensitive query parameters from URLs."""
    try:
        parsed = urlparse(url)
        if not parsed.query:
            return url
        params = parse_qs(parsed.query, keep_blank_values=True)
        redacted = False
        for param in SENSITIVE_QUERY_PARAMS:
            if param in params:
                params[param] = ["[REDACTED]"]
                redacted = True
        if not redacted:
            return url
        return urlunparse(parsed._replace(query=urlencode(params, doseq=True)))
    except Exception:
        # Fail closed: if redaction itself breaks, drop the query string rather
        # than risk forwarding an unredacted secret in it.
        return url.split("?", 1)[0]


def _parse_body(body: str | bytes | dict | None) -> str | dict | None:
    """Parse body to JSON object if possible, otherwise return as string."""
    if body is None:
        return None

    # Already a dict
    if isinstance(body, dict):
        return body

    # Convert bytes to string
    if isinstance(body, bytes):
        try:
            body = body.decode("utf-8")
        except Exception:
            return str(body)

    # Try to parse as JSON
    if isinstance(body, str):
        try:
            return json.loads(body)
        except Exception:
            return body

    return str(body)


def _to_iso8601(timestamp: float) -> str:
    """Convert Unix timestamp to ISO 8601 string."""
    return (
        datetime.fromtimestamp(timestamp, tz=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


class CoolhandClient:
    """Simple client for submitting interactions to Coolhand."""

    def __init__(self, config: Config | None = None, **kwargs):
        """Initialize the client."""
        self.config = _get_default_config()
        if config:
            self.config.update(config)
        self.config.update(kwargs)
        self.config["base_url"] = _normalize_base_url(
            self.config.get("base_url", _DEFAULT_BASE_URL)
        )

        self._queue: list[dict[str, Any]] = []
        self._interaction_count = 0

        # Delivery of queued interactions happens on a background worker thread so
        # that flush() never blocks the calling thread (which may own an asyncio
        # event loop) on network I/O. See _ensure_worker()/_worker_loop().
        self._dispatch_queue: queue.Queue[Any] = queue.Queue(
            maxsize=_MAX_DISPATCH_QUEUE_SIZE
        )
        self._dropped_count = 0
        self._delivery_failure_count = 0
        self._worker_thread: threading.Thread | None = None
        self._worker_lock = threading.Lock()
        # Guards self._queue (the swap-and-clear in flush() — without it,
        # two concurrent flush() calls could both read the same list before
        # either replaces it, delivering every queued interaction twice) and
        # the plain counters (_interaction_count, _dropped_count,
        # _delivery_failure_count), all reachable from multiple threads.
        self._state_lock = threading.Lock()

        if not self.config.get("silent"):
            logging.basicConfig(level=logging.INFO)

        # Ensure queued interactions still get a delivery attempt on normal process
        # exit, even for a bare CoolhandClient() not wrapped by the Coolhand subclass.
        atexit.register(self.shutdown)

    @property
    def session_id(self) -> str:
        return self.config.get("session_id", "")

    def log_interaction(
        self,
        request: RequestData,
        response: ResponseData | None = None,
        error: str | None = None,
    ) -> None:
        """Log an API interaction in flat format matching Ruby/Node SDKs."""
        # Get timestamps
        req_timestamp = request.get("timestamp", time.time())
        res_timestamp = (
            response.get("timestamp", time.time()) if response else time.time()
        )
        duration_seconds = response.get("duration", 0.0) if response else 0.0

        # Build flat interaction data (matching Ruby/Node format)
        interaction = {
            "id": str(uuid.uuid4()),
            "timestamp": _to_iso8601(req_timestamp),
            "method": request.get("method", "").lower(),
            "url": _sanitize_url(request.get("url", "")),
            "headers": _sanitize_headers(request.get("headers", {})),
            "request_body": _parse_body(request.get("body")),
            "response_headers": (
                _sanitize_headers(response.get("headers", {})) if response else {}
            ),
            "response_body": _parse_body(response.get("body")) if response else None,
            "status_code": response.get("status_code", 0) if response else 0,
            "duration_ms": round(duration_seconds * 1000, 2),
            "completed_at": _to_iso8601(res_timestamp),
            "is_streaming": response.get("is_streaming", False) if response else False,
        }

        # Debug output when not silent
        if not self.config.get("silent"):
            url = _sanitize_url(request.get("url", "unknown"))
            method = request.get("method", "unknown")
            status = response.get("status_code") if response else "error"
            logger.info(f"Captured: {method} {url} -> {status}")

        # Locked so a concurrent flush() can't swap self._queue out for a new
        # list between this reading the attribute and the append landing —
        # which would silently strand the interaction on the discarded list.
        # _interaction_count is incremented here too since it's the same
        # kind of plain read-modify-write, reachable from the same
        # concurrent callers. Released before flush() below, which takes the
        # same lock itself.
        with self._state_lock:
            self._interaction_count += 1
            self._queue.append(interaction)

        # Auto-submit if enabled
        if self.config.get("auto_submit"):
            self.flush()

    def _send_one(self, interaction: dict[str, Any]) -> bool:
        """POST a single interaction to the Coolhand API. Blocking; runs on the
        worker thread (or, in tests, is called directly)."""
        api_key = self.config.get("api_key")
        if not api_key:
            return False

        try:
            payload = {
                "llm_request_log": {
                    "raw_request": interaction,
                    "collector": f"coolhand-python-{__version__}-auto-monitor",
                }
            }

            request = Request(
                url=f"{self.config['base_url']}/api/v2/llm_request_logs",
                data=json.dumps(payload, default=str).encode("utf-8"),
                headers={
                    "X-API-Key": api_key,
                    "Content-Type": "application/json",
                    "User-Agent": f"coolhand-python/{__version__}",
                },
                method="POST",
            )

            with urlopen(request, context=_ssl_context, timeout=10) as resp:
                if 200 <= resp.status < 300:
                    return True
                logger.warning(
                    f"Unexpected status submitting interaction: {resp.status}"
                )
                return False

        except (HTTPError, URLError) as e:
            logger.warning(f"Failed to submit interaction: {e}")
            return False
        except Exception as e:
            logger.warning(f"Unexpected error submitting interaction: {e}")
            return False

    def _ensure_worker(self) -> bool:
        """Start the background delivery thread if it isn't already running.

        Returns False if no worker is running and one could not be started
        (e.g. interpreter shutdown has begun), so callers can treat pending
        items as undeliverable rather than reporting them as queued.

        Public entry point: acquires _worker_lock itself. flush() instead
        calls _ensure_worker_locked() directly, holding _worker_lock across
        both the availability check *and* the enqueue that follows — see
        that method's docstring for why the two must be atomic together.
        """
        if self._worker_thread is not None and self._worker_thread.is_alive():
            return True
        with self._worker_lock:
            return self._ensure_worker_locked()

    def _ensure_worker_locked(self) -> bool:
        """Body of _ensure_worker(). Caller must already hold _worker_lock."""
        if self._worker_thread is not None and self._worker_thread.is_alive():
            return True
        worker = threading.Thread(target=self._worker_loop, daemon=True)
        try:
            worker.start()
        except RuntimeError:
            # Can't create new threads once interpreter shutdown has begun
            # (e.g. a late atexit-triggered flush). Nothing to deliver to.
            logger.debug("Could not start delivery worker; interpreter is exiting")
            return False
        self._worker_thread = worker
        # (Re-)arm the atexit safety net so a final drain is attempted at
        # real process exit. unregister-then-register guarantees exactly
        # one entry regardless of whether __init__'s original
        # registration is still in place or an earlier shutdown() call
        # already removed it — atexit.unregister on an unregistered
        # callable is a documented no-op, so this is safe either way.
        atexit.unregister(self.shutdown)
        atexit.register(self.shutdown)
        return True

    def _worker_loop(self) -> None:
        """Deliver queued interactions to Coolhand until told to stop.

        On the sentinel, drains and delivers whatever else is already
        buffered before exiting (silently discarding any further sentinels
        found along the way) rather than exiting immediately. A shutdown()
        call that times out while this worker is still busy can leave its
        sentinel behind for a while, and shutdown() may be called more than
        once while the same worker stays busy, potentially queuing more than
        one sentinel — without draining, whichever sentinel this worker
        reaches first would strand every real item still behind it, and any
        second sentinel would go on to kill the *next* worker before it does
        any work at all.

        Committing to exit is itself guarded by _worker_lock, checked
        against the dispatch queue being genuinely empty at that moment —
        the same lock flush() holds across both its "is a worker available"
        check and the put_nowait() that follows (see
        _ensure_worker_locked()). Without sharing that lock, a flush() could
        see this worker as available (Thread.is_alive() is still True right
        up until the thread function returns) in the same narrow window
        this method has already decided to exit, enqueueing an item no
        worker will ever consume.
        """
        while True:
            item = self._dispatch_queue.get()
            if item is _SHUTDOWN_SENTINEL:
                while True:
                    try:
                        item = self._dispatch_queue.get_nowait()
                    except queue.Empty:
                        break
                    if item is not _SHUTDOWN_SENTINEL:
                        self._deliver(item)
                with self._worker_lock:
                    if not self._dispatch_queue.empty():
                        # Something was enqueued (holding this same lock,
                        # per _ensure_worker_locked()) between the drain
                        # above finishing and this check — keep running
                        # rather than exiting out from under it.
                        continue
                    if self._worker_thread is threading.current_thread():
                        self._worker_thread = None
                    return
            self._deliver(item)

    def _deliver(self, interaction: dict[str, Any]) -> None:
        """Call _send_one, guarding the worker loop against it ever raising
        (it's total today, but a subclass override or monkeypatch could
        change that) — a single bad item must not silently kill delivery
        for every interaction still queued behind it."""
        try:
            delivered = self._send_one(interaction)
        except Exception:
            logger.exception("Unexpected error delivering interaction")
            delivered = False

        if not delivered:
            # Distinct from dropped_count: a drop means the item never made
            # it onto the dispatch queue (full, or no worker); this means it
            # did, but the POST itself failed (bad API key, non-2xx status,
            # network error) — without this, e.g. every request 401ing would
            # report dropped_count: 0 and read as "everything delivered".
            with self._state_lock:
                self._delivery_failure_count += 1

    def flush(self) -> bool:
        """Hand off queued interactions for background delivery to Coolhand.

        This never performs network I/O itself: it moves interactions onto a
        bounded dispatch queue that a worker thread drains, so callers on the
        critical path (including an asyncio event loop thread) are not blocked
        on the HTTP round trip. Safe to call again after shutdown() — a fresh
        worker is started as needed. Returns False if any item had to be
        dropped, either because the dispatch queue was full or because no
        worker could be started to receive it. If no API key is configured,
        items are discarded (not counted as dropped) and this still returns
        True — there's nowhere to submit them, so it's not a delivery
        failure; get_stats()["config"]["has_api_key"] tells you why nothing
        is ever being sent.
        """
        with self._state_lock:
            if not self._queue:
                return True
            items, self._queue = self._queue, []

        if not self.config.get("api_key"):
            logger.debug("No API key configured, skipping submission")
            return True

        # Held across both the availability check and every put_nowait()
        # below — not just the check — so a worker that's mid-exit (see
        # _worker_loop's own use of this lock) can't commit to exiting
        # between "a worker is available" and the item actually landing in
        # the queue. put_nowait() never blocks, so holding the lock across
        # it is cheap.
        with self._worker_lock:
            worker_available = self._ensure_worker_locked()

            if not worker_available:
                dropped = len(items)
            else:
                dropped = 0
                for interaction in items:
                    try:
                        self._dispatch_queue.put_nowait(interaction)
                    except queue.Full:
                        dropped += 1

        if dropped:
            # Locked: flush() is called concurrently from multiple interceptor
            # threads, and this is a plain read-modify-write.
            with self._state_lock:
                self._dropped_count += dropped
            logger.warning(
                f"Dropped {dropped}/{len(items)} interactions "
                "(dispatch queue full or delivery worker unavailable)"
            )

        return dropped == 0

    def get_stats(self) -> dict[str, Any]:
        """Get client statistics."""
        # Check if httpx is patched
        patched_libs = []
        try:
            from . import httpx_interceptor

            if httpx_interceptor.is_patched():
                patched_libs += ["httpx.Client.send", "httpx.AsyncClient.send"]
        except Exception:
            pass
        try:
            from . import copilot_interceptor

            if copilot_interceptor.is_patched():
                patched_libs += [
                    "JsonRpcClient.request",
                    "JsonRpcClient._handle_message",
                ]
        except Exception:
            pass

        return {
            "config": {
                "has_api_key": bool(self.config.get("api_key")),
            },
            "monitoring": {
                "enabled": True,
                "patched_libraries": patched_libs,
            },
            "logging": {
                "session_id": self.session_id,
                "interaction_count": self._interaction_count,
                "queue_size": len(self._queue),
                "dropped_count": self._dropped_count,
                # Distinct from dropped_count: this counts items that made it
                # onto the dispatch queue but whose POST failed (bad API key,
                # non-2xx status, network error).
                "delivery_failure_count": self._delivery_failure_count,
                # Upper bound: briefly includes the internal shutdown
                # sentinel while a shutdown() call is in flight.
                "pending_delivery": self._dispatch_queue.qsize(),
            },
        }

    def shutdown(self) -> None:
        """Flush and cleanup.

        Waits up to _SHUTDOWN_TIMEOUT for the background worker to deliver any
        interactions still in flight, then returns regardless — an unreachable
        backend must not hang process exit (this is called from atexit). Not a
        permanent stop switch: monitoring keeps working afterwards, and a later
        flush() simply starts a fresh worker if needed (e.g. if shutdown() was
        called mid-program rather than right before process exit).
        """
        logger.info("Shutting down Coolhand...")
        self.flush()

        # Guards a narrow race: something enqueued directly onto the dispatch
        # queue while the worker that would have drained it was exiting, so
        # self._worker_thread still points at a now-dead thread. Not reachable
        # through flush() alone (which always calls _ensure_worker() itself
        # before enqueueing), but cheap to close off here too.
        if not self._dispatch_queue.empty():
            self._ensure_worker()

        worker = self._worker_thread
        if worker is not None and worker.is_alive():
            deadline = time.monotonic() + _SHUTDOWN_TIMEOUT
            try:
                # Blocking (not put_nowait): if the queue is full, give the
                # worker the shutdown budget to make room rather than losing
                # the sentinel outright and leaving the worker running past
                # shutdown() with an undelivered backlog.
                self._dispatch_queue.put(_SHUTDOWN_SENTINEL, timeout=_SHUTDOWN_TIMEOUT)
            except queue.Full:
                pass
            worker.join(timeout=max(0.0, deadline - time.monotonic()))

        # Explicit shutdown() calls shouldn't keep the client (and its queues)
        # alive in the atexit registry for the rest of the process — but only
        # once we're actually done: if a worker is still running (timed out
        # against a slow/unreachable backend, or a concurrent flush() from
        # another interceptor thread started a fresh one in the meantime) or
        # the dispatch queue still has something in it, leave the hook armed
        # so a final attempt still happens at real process exit.
        # self._worker_thread being None is the authoritative "no active
        # worker" signal — _worker_loop clears it itself right before
        # returning (see its docstring), rather than this relying on
        # Thread.is_alive(), which only flips after the OS finishes tearing
        # the thread down and would otherwise leave a brief window where a
        # worker that's committed to exiting still reports "alive". The
        # whole check-then-act sequence is held under _worker_lock — the
        # same lock _ensure_worker() and _worker_loop's own cleanup use — so
        # this can't interleave with either: it either completes first (and
        # a subsequent _ensure_worker() re-arms the hook for its new
        # worker), or it runs after one of them and correctly sees the
        # up-to-date state. Harmless no-op if this fires again from atexit
        # itself.
        with self._worker_lock:
            if self._worker_thread is None and self._dispatch_queue.empty():
                atexit.unregister(self.shutdown)

        logger.info("Coolhand shutdown complete")


# Global instance
_instance: CoolhandClient | None = None


def get_instance() -> CoolhandClient | None:
    """Get the global client instance."""
    return _instance


def set_instance(instance: CoolhandClient) -> None:
    """Set the global client instance."""
    global _instance
    _instance = instance


def initialize(config: Config | None = None, **kwargs) -> CoolhandClient:
    """Initialize the global client."""
    global _instance
    if _instance is None:
        _instance = CoolhandClient(config, **kwargs)
    return _instance
