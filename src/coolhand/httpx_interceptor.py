"""httpx interceptor for capturing API calls."""

import codecs
import contextvars
import functools
import gc
import ipaddress
import json
import logging
import os
import queue
import re
import threading
import time
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from ._body_limit import DEFAULT_MAX_BODY_BYTES, truncate_text
from .declared_context import attach_snapshot
from .types import RequestData, ResponseData

logger = logging.getLogger(__name__)

# State
_patched = False
_original_send: Callable | None = None
_original_async_send: Callable | None = None
_original_httpx2_send: Callable | None = None
_original_httpx2_async_send: Callable | None = None
_original_requests_send: Callable | None = None
_handler: Callable[[RequestData, ResponseData | None, str | None], None] | None = None
_intercept_addresses: list[str] | None = None
_exclude_api_patterns: list[str] | None = None
# Byte cap for the streaming accumulator; None/0 means unlimited.
_max_body_bytes: int | None = DEFAULT_MAX_BODY_BYTES

# Reentrancy guard: prevents the handler from firing more than once per logical
# request when the same intercepted call re-enters the public send() — e.g. a
# requests→httpx adapter chain triggers both patched_requests_send and
# patched_send for the same request, or any other code that calls self.send()
# while already inside a patched send.
_intercepting: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "coolhand_intercepting", default=False
)

# Process-wide count of requests the patched wrappers have captured. Exists so a
# host app's own tests can assert that interception really happened (see
# coolhand.testing). Never reset by unpatch(), so snapshot deltas stay valid
# across patch cycles.
_intercept_count = 0
# Serialises patch()/unpatch(): two threads racing past the "already patched" check
# would save the first thread's wrapper as the "original", and every call would then
# recurse into itself.
_patch_lock = threading.RLock()
_intercept_count_lock = threading.Lock()


def _record_interception() -> None:
    """Count one captured request. Called once per request, not per re-entry."""
    global _intercept_count
    with _intercept_count_lock:
        _intercept_count += 1


def get_intercept_count() -> int:
    """Return how many requests the httpx/requests patches have captured."""
    with _intercept_count_lock:
        return _intercept_count


# Default intercept addresses. Entries are matched against the parsed URL, never
# the raw string: bare host, host/path-prefix, :port/path-prefix, or a /path
# fragment (see _compile_entry for the grammar).
DEFAULT_INTERCEPT_ADDRESSES = [
    "api.openai.com",
    "api.anthropic.com",
    "api.elevenlabs.io",
    "generativelanguage.googleapis.com",
    "aiplatform.googleapis.com",
    "gateway.ai.cloudflare.com",
    # GitHub Models
    "models.github.ai",
    "models.inference.ai.azure.com",
    # Azure OpenAI dedicated hosts (commercial, US Gov, China/21Vianet)
    "openai.azure.com",
    "openai.azure.us",
    "openai.azure.cn",
    # Azure AI Services / Foundry multi-service hosts. Path-anchored so Speech,
    # Vision, Language and Content Safety traffic on the same host is not captured.
    # Bare "ai.azure.com" is deliberately absent — that is the Foundry portal domain.
    "cognitiveservices.azure.com/openai/",
    "cognitiveservices.azure.com/models/",
    "cognitiveservices.azure.us/openai/",
    "cognitiveservices.azure.us/models/",
    "cognitiveservices.azure.cn/openai/",
    "cognitiveservices.azure.cn/models/",
    "services.ai.azure.com/openai/",
    "services.ai.azure.com/models/",
    "services.ai.azure.us/openai/",
    "services.ai.azure.us/models/",
    # No services.ai.azure.cn equivalent — Foundry is not offered in the China cloud.
    # Azure serverless / MaaS deployments and Azure ML managed online endpoints.
    # The MaaS hosts have no sovereign-cloud variants; Azure ML does.
    "inference.ai.azure.com",
    "models.ai.azure.com",
    "inference.ml.azure.com",
    "inference.ml.azure.us",
    "openrouter.ai",
    "opencode.ai",
    "api.opencode.ai",
    # OpenAI-compatible providers the Coolhand server ingests and prices
    "api.deepseek.com",
    "api.mistral.ai",
    "api.perplexity.ai",
    "api.x.ai",
    # Cohere is path-anchored, never host-wide: the server ingests only v2 chat
    # and v1/v2 embed. Its other endpoints (v1 chat, rerank, tokenize, classify)
    # have different envelopes and would be recorded as empty successes.
    # /v1/embed-jobs also contains "/v1/embed", so it is in the deny-list.
    "api.cohere.com/v2/chat",
    "api.cohere.com/v1/embed",
    "api.cohere.com/v2/embed",
    "api.cohere.ai/v2/chat",
    "api.cohere.ai/v1/embed",
    "api.cohere.ai/v2/embed",
    # TypeSafe Jev (System One). Path-anchored: the server routes only this path.
    "api.typesafe.ai/v1/systemone",
    # Amazon Bedrock. The region label is a host wildcard (commercial, GovCloud
    # and China partitions), plus the FIPS variants.
    # "*" in a host is a single label, so "bedrock-runtime.evil.s3.amazonaws.com"
    # (an S3 bucket named "bedrock-runtime.evil") does not match.
    "bedrock-runtime.*.amazonaws.com",
    "bedrock-runtime.*.amazonaws.com.cn",
    "bedrock-runtime-fips.*.amazonaws.com",
    "bedrock-runtime.*.api.aws",
    "bedrock-runtime-fips.*.api.aws",
    # Vertex AI regional hosts are "<region>-aiplatform.googleapis.com", a hyphen
    # rather than a dot subdomain, so the bare entry above does not cover them.
    "*-aiplatform.googleapis.com",
    # Self-hosted Ollama has no fixed host, so only path identifies it, and a
    # bare "/api/chat" would capture any app's own unrelated route. Anchor to
    # Ollama's default port as well. Bare localhost is skipped by
    # _is_localhost, so these cover remote, LAN and Docker Compose hosts
    # (e.g. http://ollama:11434) on the default port.
    ":11434/api/chat",
    ":11434/api/generate",
    ":11434/api/embed",
    ":11434/api/embeddings",
    # Google RPC-style verbs behind googleapis.com (Vertex private/PSC/mTLS
    # hosts too). A bare host also matches every subdomain, so these are
    # constrained to Google hosts: an unrelated app calling e.g. TensorFlow
    # Serving's "/v1/models/foo:predict" is not captured.
    "googleapis.com/*:generateContent",
    "googleapis.com/*:streamGenerateContent",
    "googleapis.com/*:predict",
    "googleapis.com/*:streamRawPredict",
    # Vertex AI dedicated endpoints: <id>.<region>-<project>.prediction.vertexai.goog
    "prediction.vertexai.goog",
]

DEFAULT_EXCLUDE_API_PATTERNS: list[str] = json.loads(
    (Path(__file__).parent / "default_exclude_api_patterns.json").read_text()
)


def set_intercept_addresses(addresses: list[str] | None) -> None:
    """Set custom intercept addresses (hosts, host/path prefixes, ``/path`` fragments).

    An empty list disables capture entirely; ``None`` restores
    ``DEFAULT_INTERCEPT_ADDRESSES``.
    """
    global _intercept_addresses
    _warn_on_invalid_entries("intercept_addresses", addresses)
    _intercept_addresses = addresses


def set_exclude_api_patterns(patterns: list[str] | None) -> None:
    """Set URL patterns to exclude from capture (deny-list).

    Patterns use the same grammar as intercept addresses; a bare ``/path``
    fragment matches the URL path only, never the query string.

    An empty list disables exclusion entirely; ``None`` restores
    ``DEFAULT_EXCLUDE_API_PATTERNS``.
    """
    global _exclude_api_patterns
    _warn_on_invalid_entries("exclude_api_patterns", patterns)
    _exclude_api_patterns = patterns


def _warn_on_invalid_entries(name: str, entries: list[str] | None) -> None:
    """Warn at configuration time about a malformed pattern list.

    A non-list container (a bare ``str`` iterates per character; a one-shot
    iterator is consumed by the first match) or a non-``str`` entry makes URL
    matching misbehave at request time, where failures are only logged at debug
    level. Surfacing it here means a bad config is noticed when
    ``Coolhand(...)`` is constructed. Also flags ``str`` entries containing
    ``?`` or ``#``, which cannot match a query string or fragment. Never
    raises: monitoring must not break the host application.
    """
    if entries is None:
        return
    if not isinstance(entries, (list, tuple)):
        logger.warning(
            "Invalid %s: expected a list of str, got %s.", name, type(entries).__name__
        )
        return
    invalid = sorted({type(e).__name__ for e in entries if not isinstance(e, str)})
    if invalid:
        logger.warning(
            "Invalid %s entries (expected str, got %s). URL matching against "
            "this list may fail at request time, in which case the request is "
            "not captured.",
            name,
            ", ".join(invalid),
        )
    # Matching runs on host, port and path only, so an entry carrying a query
    # string or fragment cannot match one. "*" is a real glob and is not flagged.
    unmatchable = [e for e in entries if isinstance(e, str) and ("?" in e or "#" in e)]
    if unmatchable:
        logger.warning(
            "%s entries containing '?' or '#' cannot match a query string or "
            "fragment: patterns are matched against the URL host, port and "
            "path ('?' is not a wildcard; use '*'). Affected entries (text "
            "from the first '?' or '#' omitted): %s",
            name,
            # A query string is where secrets live (?api_key=...); keep it out of logs.
            ", ".join(
                repr(re.split(r"[?#]", e, maxsplit=1)[0] + "...") for e in unmatchable
            ),
        )


def set_max_body_bytes(max_bytes: int | None) -> None:
    """Set the byte cap for captured streaming response bodies.

    ``None`` or 0 disables the cap.
    """
    global _max_body_bytes
    _max_body_bytes = max_bytes


class _BoundedChunks:
    """Accumulates streamed text, retaining at most ``cap`` bytes.

    Keeps counting the total so the truncation marker can report the full size,
    but stops storing once the cap is reached so memory stays bounded however
    long the stream runs. ``cap`` of ``None`` or 0 means unlimited.
    """

    def __init__(self, cap: int | None) -> None:
        self._cap = cap
        self._chunks: list[str] = []
        self._stored = 0
        self.total = 0

    def append(self, text: str) -> None:
        size = len(text.encode("utf-8", errors="replace"))
        self.total += size
        if self._cap and self._stored >= self._cap:
            return
        self._chunks.append(text)
        self._stored += size

    @property
    def truncated(self) -> bool:
        return bool(self._cap) and self.total > self._cap

    def __bool__(self) -> bool:
        return bool(self._chunks)

    def clear(self) -> None:
        self._chunks = []

    def joined(self) -> str:
        text = "".join(self._chunks)
        if self.truncated:
            return truncate_text(text, self.total, self._cap or 0)
        return text


_Matcher = Callable[[str, int | None, str], bool]

_PORT_ONLY = re.compile(r":(\d+)(/.*)?")
_HOST_PORT = re.compile(r"(.*):(\d+)")
_DEFAULT_PORTS = {"http": 80, "https": 443}


def _glob_host_match(pattern: str, host: str) -> bool:
    """Match ``host`` against a glob where ``*`` is one label (never a ``.``).

    Tracks the set of reachable pattern positions per character, so cost is
    O(len(host) * len(pattern)) with no regex backtracking, however many
    ``*`` an entry has.
    """

    def skip_stars(positions: set[int]) -> set[int]:
        out = set(positions)
        for i in positions:
            while i < len(pattern) and pattern[i] == "*":
                i += 1
                out.add(i)
        return out

    states = skip_stars({0})
    for ch in host:
        states = skip_stars(
            {
                i if pattern[i] == "*" else i + 1
                for i in states
                if i < len(pattern)
                and (pattern[i] == ch or (pattern[i] == "*" and ch != "."))
            }
        )
        if not states:
            return False
    return len(pattern) in states


def _glob_path_match(pattern: str, path: str) -> bool:
    """Match the whole ``path`` against a glob where ``*`` matches anything.

    Greedy leftmost search of the literal segments between the stars: linear
    in practice and polynomial at worst, with no regex backtracking.
    """
    first, *middle, last = pattern.split("*")  # pattern always contains a "*"
    end = len(path) - len(last)
    if end < len(first) or not path.startswith(first) or not path.endswith(last):
        return False
    pos = len(first)
    for segment in middle:
        found = path.find(segment, pos, end)
        if found < 0:
            return False
        pos = found + len(segment)
    return True


@functools.lru_cache(maxsize=512)
def _compile_entry(entry: str) -> _Matcher:
    """Compile an entry into a ``(host, port, path) -> bool`` matcher.

    Shapes: ``host`` (the host or any subdomain), ``host/path`` (path prefix),
    ``host:8443`` (explicit port, default ports are implied by the scheme),
    ``:11434/api/chat`` (any host on that port), ``/fragment`` (substring of the
    path) and ``[::1]:8080`` (IPv6 hosts need brackets). ``*`` is a glob: in a
    host it matches within one label (never a ``.``), and a globbed host must
    match the whole host (no implicit subdomains, unlike a bare host); in a
    path it matches anything, and a globbed path must match the whole path. A
    leading ``scheme://`` or ``//`` is ignored. Entries cannot carry a query
    string or fragment, and an entry that reduces to nothing (``""``, ``"."``, ``"[]"``)
    matches nothing rather than everything.
    """
    if not isinstance(entry, str):
        raise TypeError(f"pattern entries must be str, got {type(entry).__name__}")
    entry = re.sub(r"^(?:[a-zA-Z][a-zA-Z0-9+.-]*:)?//", "", entry.strip())
    host: str | None = None
    port: int | None = None
    path: str | None
    is_fragment = entry.startswith("/")
    if is_fragment:
        path = entry
    elif m := _PORT_ONLY.fullmatch(entry):
        port, path = int(m.group(1)), m.group(2)
    else:
        host, slash, rest = entry.partition("/")
        path = slash + rest if slash else None
        if m := _HOST_PORT.fullmatch(host):
            host, port = m.group(1), int(m.group(2))
        host = host.strip("[]").lower().rstrip(".") or None
        if host is not None:
            # "a**b" matches exactly what "a*b" does; collapsing keeps the glob
            # matcher linear however many stars an entry has.
            host = re.sub(r"\*+", "*", host)

    if host is None and port is None and path is None:
        logger.debug("Ignoring empty pattern entry %r", entry)
        return lambda *_: False

    host_ok: Callable[[str], bool] | None = None
    if host is not None:
        if "*" in host:
            host_glob = host
            host_ok = lambda h: _glob_host_match(host_glob, h)  # noqa: E731
        else:
            suffix = "." + host
            host_ok = lambda h: h == host or h.endswith(suffix)  # noqa: E731

    path_ok: Callable[[str], bool] | None = None
    if path is not None:
        if "*" in path:
            # A fragment glob is a whole-path glob padded with "*" on both sides.
            path_glob = f"*{path}*" if is_fragment else path
            path_ok = lambda p: _glob_path_match(path_glob, p)  # noqa: E731
        elif is_fragment:
            path_ok = lambda p: path in p  # noqa: E731
        else:
            path_ok = lambda p: p.startswith(path)  # noqa: E731

    def matches(url_host: str, url_port: int | None, url_path: str) -> bool:
        return (
            (host_ok is None or (bool(url_host) and host_ok(url_host)))
            and (port is None or url_port == port)
            and (path_ok is None or path_ok(url_path))
        )

    return matches


def _matches_any(
    url: str,
    override: list[str] | None,
    defaults: list[str],
    *,
    on_error: bool,
) -> bool:
    """Match a URL's host, port and path against an override list or defaults.

    Matching runs on the parsed URL (host, port, percent-decoded path), not the
    raw string, so query text, userinfo and lookalike hosts cannot trigger or
    dodge an entry. ``None`` means "no override, use defaults"; an empty list
    means "match nothing" and is deliberately distinct from ``None``.
    ``on_error`` is the result when matching raises (e.g. a non-str entry);
    callers choose the value that means "do not capture" for their list
    (``False`` for the allow-list, ``True`` for the deny-list). Logged at debug
    to avoid per-request spam; the user-facing trace is the warning from
    ``_warn_on_invalid_entries``.
    """
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").rstrip(".")
        try:
            port = parts.port or _DEFAULT_PORTS.get(parts.scheme)
        except ValueError:
            port = None
        path = unquote(parts.path)
        return any(
            _compile_entry(entry)(host, port, path)
            for entry in (defaults if override is None else override)
        )
    except Exception:
        logger.debug("Failed to match URL against pattern list", exc_info=True)
        return on_error


def _is_localhost(url: str) -> bool:
    """Check if the URL's host (not userinfo, path or lookalike) is loopback."""
    try:
        host = (urlsplit(url).hostname or "").rstrip(".")
        if not host:
            return False
        if host == "localhost" or host.endswith(".localhost"):
            return True
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:  # an ordinary hostname, not an IP literal
            return False
        # Older CPython does not unwrap IPv4-mapped IPv6 (::ffff:127.0.0.1) itself.
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        return ip.is_loopback or ip.is_unspecified
    except Exception:
        return False


def _is_llm_api(url: str) -> bool:
    """Check if URL matches any intercept address (host/port/path match)."""
    return _matches_any(
        url, _intercept_addresses, DEFAULT_INTERCEPT_ADDRESSES, on_error=False
    )


def _is_excluded(url: str) -> bool:
    """Check if URL matches any exclude pattern (deny-list after allow-list)."""
    return _matches_any(
        url, _exclude_api_patterns, DEFAULT_EXCLUDE_API_PATTERNS, on_error=True
    )


def _read_request_body(request: Any) -> str | None:
    """Best-effort request body for logging; never raises.

    ``request.content`` raises ``httpx.RequestNotRead`` for streamed bodies
    (multipart uploads, generator bodies). Capturing must not break the call.
    """
    try:
        content = request.content
        return content.decode("utf-8", errors="replace") if content else None
    except Exception:
        return "[unreadable body]"


def _should_capture_url(url: str) -> bool:
    """Allow-listed, not localhost, and not deny-listed."""
    return _is_llm_api(url) and not _is_localhost(url) and not _is_excluded(url)


def _is_streaming_content_type(content_type: str) -> bool:
    """Check if content type indicates streaming."""
    return "text/event-stream" in content_type or "application/x-ndjson" in content_type


def _is_binary_content_type(content_type: str) -> bool:
    """Check if content type indicates binary data (audio, video, image, etc.)."""
    # Bedrock streams use AWS binary event-stream framing, not text.
    binary_prefixes = (
        "audio/",
        "video/",
        "image/",
        "application/octet-stream",
        "application/vnd.amazon.eventstream",
    )
    content_type = content_type.lower()
    return any(content_type.startswith(p) for p in binary_prefixes)


def _read_response_body(response: Any) -> Any:
    """Safely read response body."""
    try:
        content_type = response.headers.get("content-type", "")
        if _is_streaming_content_type(content_type):
            return "[streaming]"
        if _is_binary_content_type(content_type):
            return "[binary]"

        if hasattr(response, "_content") and response._content:
            return response._content
        if hasattr(response, "content"):
            return response.content
    except Exception:
        pass
    return None


# --- Abandoned-stream reporter -------------------------------------------------
#
# A stream the consumer never finishes is reported from its generator's ``finally``.
# When that generator is garbage (the OpenAI SDK's ``Stream`` and its generator form a
# reference cycle, so this is the normal case), the cyclic GC runs that ``finally`` on
# whatever thread happened to trigger the collection, at any eval-breaker check. If
# that thread is inside ``CoolhandClient.flush()`` holding the non-reentrant
# ``_worker_lock``, running the handler (which calls ``flush()``) would block on a lock
# the same thread already holds, forever. So a finalizer run by the GC never calls the
# handler: it puts the record on a ``SimpleQueue`` (a C-level put, no Python lock) and
# a daemon thread, started from ``set_handler()``/``patch()`` and never from a
# finalizer, hands it to the handler. Finalization by refcount (``break``, ``close()``,
# ``aclose()``, exhaustion) is deterministic and still reports synchronously.

_AbandonedRecord = tuple[RequestData, ResponseData]
# Items: a record; ``None`` (wakes the reporter so it sees its stop flag); or an
# ``Event`` barrier that the reporter sets once everything queued before it is done.
_abandoned: "queue.SimpleQueue[_AbandonedRecord | threading.Event | None]" = (
    queue.SimpleQueue()
)
_reporter: threading.Thread | None = None
_reporter_stop: threading.Event | None = None
# Lock order: _patch_lock -> _reporter_lock -> (handler's) client locks, taken in
# that order by everything that starts or stops the reporter. Neither of the first
# two is ever taken from a finalizer.
_reporter_lock = threading.RLock()
# True on a thread while the cyclic GC is running on it, i.e. while any finalizer
# that fires on that thread is a GC finalizer. Thread-local so another thread's
# ordinary send is never deferred by a collection running elsewhere. The callback
# that maintains it stays registered for the process lifetime once installed: it is
# a trivial attribute store, and removing it on unpatch() would let a late finalizer
# of a pre-unpatch stream call the handler from inside the GC again.
_gc_state = threading.local()
# How long unpatch()/set_handler(None)/flush_abandoned() wait for an in-flight
# handler call.
_REPORTER_JOIN_TIMEOUT = 2.0


def _track_gc(phase: str, info: dict[str, int]) -> None:
    try:
        _gc_state.active = phase == "start"
    except Exception:  # interpreter shutdown: module globals may be gone
        pass


def _in_gc() -> bool:
    # At interpreter shutdown ``_gc_state`` may already be None, which reads as False:
    # a finalizer then reports synchronously, as it did before the reporter existed.
    return getattr(_gc_state, "active", False)


def _deliver_abandoned(record: _AbandonedRecord) -> None:
    """Hand one record to the handler. A handler cleared by teardown drops it."""
    handler = _handler
    if handler is None:
        return
    try:
        handler(record[0], record[1], None)
    except Exception:
        logger.debug("Handler error in abandoned-stream report", exc_info=True)


def _handle_queued(item: "_AbandonedRecord | threading.Event | None") -> None:
    if isinstance(item, threading.Event):
        item.set()
    elif item is not None:
        _deliver_abandoned(item)


def _reporter_loop(stop: threading.Event) -> None:
    while not stop.is_set():
        _handle_queued(_abandoned.get())


def _drain_abandoned(deliver: bool) -> None:
    """Empty the queue on the calling thread, delivering the records or not."""
    while True:
        try:
            item = _abandoned.get_nowait()
        except queue.Empty:
            return
        if deliver:
            _handle_queued(item)
        elif isinstance(item, threading.Event):
            item.set()


def _reporter_alive() -> bool:
    thread = _reporter  # read once: a teardown may clear it concurrently
    return thread is not None and thread.is_alive()


def flush_abandoned() -> None:
    """Deliver abandoned-stream records that are queued or in flight.

    Called from ``CoolhandClient.shutdown()`` so they are not lost at exit. Waits
    (bounded by ``_REPORTER_JOIN_TIMEOUT``) for the reporter to get through everything
    queued ahead of a barrier, then delivers any remainder on the calling thread. Must
    not be called from a GC finalizer.
    """
    thread = _reporter
    me = threading.current_thread()
    if thread is not None and thread is not me and thread.is_alive():
        done = threading.Event()
        _abandoned.put(done)
        deadline = time.monotonic() + _REPORTER_JOIN_TIMEOUT
        while time.monotonic() < deadline:
            if done.wait(0.02):
                return
            if not thread.is_alive():  # stopped after the check above
                break
    _drain_abandoned(deliver=True)


def _stop_reporter_locked() -> None:
    """Stop the reporter, deliver what it left behind, and drop the rest."""
    global _reporter, _reporter_stop
    thread, stop = _reporter, _reporter_stop
    _reporter = _reporter_stop = None  # finalizers stop queueing from here on
    if thread is not None and stop is not None and thread.is_alive():
        stop.set()
        _abandoned.put(None)
        if thread is not threading.current_thread():
            thread.join(_REPORTER_JOIN_TIMEOUT)
    _drain_abandoned(deliver=True)
    _drain_abandoned(deliver=False)


def _sync_reporter() -> None:
    """Run the reporter exactly while capture is active (patched, with a handler).

    Idempotent; called after every state change (``set_handler``, ``patch``,
    ``unpatch``) and when a capture begins, so a forked child, or a reporter that
    died, gets a fresh thread.
    """
    global _reporter, _reporter_stop
    with _patch_lock, _reporter_lock:
        if not (_patched and _handler is not None):
            if _reporter is not None:
                _stop_reporter_locked()
            return
        if _reporter_alive():
            return
        # Anything queued while no reporter ran is stale (a finalizer racing a
        # teardown, or what a reporter that died abnormally left): drop it rather
        # than deliver it after the fact.
        _drain_abandoned(deliver=False)
        if _track_gc not in gc.callbacks:
            gc.callbacks.append(_track_gc)
        stop = threading.Event()
        thread = threading.Thread(
            target=_reporter_loop,
            args=(stop,),
            name="coolhand-abandoned-streams",
            daemon=True,
        )
        try:
            thread.start()
        except RuntimeError:  # interpreter is exiting
            return
        _reporter, _reporter_stop = thread, stop


def _reset_reporter_after_fork() -> None:
    """A forked child has no reporter thread, may inherit a lock another parent
    thread held or its queue blocked, and must not report the parent's queued records
    (the parent delivers them). The next capture starts its own reporter (see
    ``_StreamCapture.__init__``); a stream inherited from the parent that is
    finalized in the child before then is dropped rather than reported twice."""
    global _reporter, _reporter_stop, _reporter_lock, _abandoned, _patch_lock
    _reporter = _reporter_stop = None
    _reporter_lock = threading.RLock()
    # _sync_reporter takes this one too, on a child's first capture.
    _patch_lock = threading.RLock()
    # Replaced, not drained: the parent's reporter was blocked inside get() at fork
    # time, which can leave the inherited queue's internal lock held forever.
    _abandoned = queue.SimpleQueue()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_reporter_after_fork)


def set_handler(
    handler: Callable[[RequestData, ResponseData | None, str | None], None] | None,
) -> None:
    """Set the handler for captured requests (``None`` stops reporting)."""
    global _handler
    if handler is None:
        with _patch_lock, _reporter_lock:
            # Report what is already queued while the old handler is still set.
            if _reporter is not None:
                _stop_reporter_locked()
    _handler = handler
    _sync_reporter()


def _tag(wrapper: Callable, original: Callable) -> Callable:
    """Record the real function behind one of our wrappers."""
    wrapper._coolhand_original = original  # type: ignore[attr-defined]
    return wrapper


def _unwrap(func: Callable) -> Callable:
    """Return the real function behind a Coolhand wrapper, or ``func`` itself.

    Saving one of our own wrappers as the "original" would make it call itself.
    That can happen if patch() runs while the class is already patched but the
    module state was reset (a test fixture, or a concurrent race).
    """
    # Look in __dict__ only: getattr would let a Mock invent the attribute.
    return getattr(func, "__dict__", {}).get("_coolhand_original", func)


# httpx implements lines on text on bytes on raw, each looked up on the response
# instance (sync as ``iter_*``, async as ``aiter_*``). Every one is wrapped, since
# a consumer may call any of them. The converter turns one item into the text
# appended to the captured body; ``None`` means a bytes layer, decoded by the
# capture's incremental decoder so a character split across chunks survives.
_STREAM_METHODS: tuple[tuple[str, Callable[[Any], str] | None], ...] = (
    ("bytes", None),
    ("lines", lambda line: line + "\n"),
    ("text", str),
    ("raw", None),
)


class _StreamCapture:
    """Accumulates one streamed response and reports it to the handler once.

    Shared by the sync and async ``send`` wrappers. Capture happens only at the
    outermost iterator layer that is running (``active``), or every chunk would be
    recorded once per layer. A stream is complete only if the wrapped iterator was
    exhausted. Anything else that captured data (``break``, ``close()``, a cancelled
    or garbage-collected generator, a transport error, an SDK that stops at
    ``data: [DONE]`` without draining the iterator) is sent as it stood, marked
    ``stream_incomplete``. The first outcome wins, so a late finalizer cannot send a
    second record. Nothing here may raise into the host's stream.
    """

    def __init__(self, response: Any, req_data: RequestData, start: float) -> None:
        self._response = response
        self._req_data = req_data
        self._start = start
        self._chunks = _BoundedChunks(_max_body_bytes)
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._sent = False
        self._lock = threading.Lock()
        self.active = False
        if not _reporter_alive():
            _sync_reporter()  # first capture in a forked child, or a dead reporter

    def add(self, item: Any, to_text: Callable[[Any], str] | None = None) -> None:
        if self._sent:
            return
        try:
            text = to_text(item) if to_text else self._decoder.decode(item)
            self._chunks.append(text)
        except Exception:
            logger.debug("Streaming capture error", exc_info=True)

    def send(self, incomplete: bool = False) -> None:
        in_gc = _in_gc()
        with self._lock:
            if self._sent or not self._chunks:
                return
            self._sent = True
        try:
            if not incomplete:
                self._chunks.append(self._decoder.decode(b"", final=True))
            res_data: ResponseData = {
                "status_code": self._response.status_code,
                "headers": dict(self._response.headers),
                "body": self._chunks.joined(),
                "timestamp": time.time(),
                "duration": time.time() - self._start,
                "is_streaming": True,
                "body_truncated": self._chunks.truncated,
            }
            if incomplete:
                res_data["stream_incomplete"] = True
            if in_gc:
                # Finalized by the cyclic GC, possibly while this very thread holds
                # a lock the handler needs: hand off, never call the handler here.
                # No logging either, it takes locks too. With no reporter running
                # (after teardown) the record is dropped.
                if _reporter is not None and _handler is not None:
                    _abandoned.put((self._req_data, res_data))
            elif _handler:
                _handler(self._req_data, res_data, None)
        except Exception:
            if not in_gc:
                logger.debug("Handler error in streaming", exc_info=True)
        finally:
            self._chunks.clear()

    def wrap_sync(
        self, orig: Callable, to_text: Callable[[Any], str] | None
    ) -> Callable:
        def capturing(*args: Any, **kwargs: Any) -> Iterator[Any]:
            if self.active:
                yield from orig(*args, **kwargs)
                return
            self.active = True
            exhausted = False
            try:
                for item in orig(*args, **kwargs):
                    if item:
                        self.add(item, to_text)
                    yield item
                exhausted = True
            finally:
                self.active = False
                self.send(incomplete=not exhausted)

        return capturing

    def wrap_async(
        self, orig: Callable, to_text: Callable[[Any], str] | None
    ) -> Callable:
        async def capturing(*args: Any, **kwargs: Any) -> AsyncIterator[Any]:
            if self.active:
                async for item in orig(*args, **kwargs):
                    yield item
                return
            self.active = True
            exhausted = False
            try:
                async for item in orig(*args, **kwargs):
                    if item:
                        self.add(item, to_text)
                    yield item
                exhausted = True
            finally:
                self.active = False
                self.send(incomplete=not exhausted)

        return capturing

    def install(self, prefix: str, wrap: Callable) -> None:
        """Wrap the response's ``<prefix>bytes/lines/text/raw`` to capture them.

        Arguments (e.g. ``chunk_size``) are forwarded untouched so httpx's own
        buffering, and the host's streaming latency, is unchanged.
        """
        for suffix, to_text in _STREAM_METHODS:
            name = prefix + suffix
            if hasattr(self._response, name):
                setattr(
                    self._response, name, wrap(getattr(self._response, name), to_text)
                )

    def start(self, prefix: str, wrap: Callable) -> None:
        """Begin capturing a streaming response, or report it now if already read.

        ``send(stream=False)`` reads the whole body inside ``send()``, so there is
        nothing left to iterate and iterating it later is not an abandon.
        """
        content = getattr(self._response, "_content", None)
        if isinstance(content, bytes):
            self.add(content)
            self.send()
        else:
            self.install(prefix, wrap)


def _make_patched_send(get_original: Callable[[], Callable]) -> Callable:
    """Build the sync ``send`` wrapper; ``get_original`` returns the real ``send``."""

    def patched_send(self, request, **kwargs):
        """Patched sync send."""
        url = str(request.url)

        # Only capture LLM API requests; skip if already intercepting (reentrancy
        # guard prevents double-logging when the same intercepted call re-enters
        # the public send(), e.g. via a requests→httpx adapter chain).
        if not _should_capture_url(url) or not _handler or _intercepting.get():
            return get_original()(self, request, **kwargs)

        token = _intercepting.set(True)
        _record_interception()
        start = time.time()
        req_data: RequestData | None = None
        response_received = False

        try:
            req_data = {
                "method": request.method,
                "url": url,
                "headers": dict(request.headers),
                "body": _read_request_body(request),
                "timestamp": start,
            }
            attach_snapshot(req_data)
            response = get_original()(self, request, **kwargs)
            response_received = True
            duration = time.time() - start

            if _is_streaming_content_type(response.headers.get("content-type", "")):
                capture = _StreamCapture(response, req_data, start)
                capture.start("iter_", capture.wrap_sync)
                return response

            res_data: ResponseData = {
                "status_code": response.status_code,
                "headers": dict(response.headers),
                "body": _read_response_body(response),
                "timestamp": time.time(),
                "duration": duration,
                "is_streaming": False,
            }
            try:
                _handler(req_data, res_data, None)
            except Exception:
                logger.debug("Handler error on success path", exc_info=True)
            return response

        except Exception as e:
            if response_received:
                logger.debug("Response capture error", exc_info=True)
                return response
            if req_data is not None:
                try:
                    _handler(req_data, None, str(e))
                except Exception:
                    logger.debug("Handler error on error path", exc_info=True)
            raise
        finally:
            _intercepting.reset(token)

    return patched_send


def _make_patched_async_send(get_original: Callable[[], Callable]) -> Callable:
    """Build the async ``send`` wrapper; ``get_original`` returns the real ``send``."""

    async def patched_async_send(self, request, **kwargs):
        """Patched async send."""
        url = str(request.url)

        # Only capture LLM API requests; skip if already intercepting (reentrancy
        # guard prevents double-logging when the same intercepted call re-enters
        # the public send(), e.g. via a requests→httpx adapter chain).
        if not _should_capture_url(url) or not _handler or _intercepting.get():
            return await get_original()(self, request, **kwargs)

        token = _intercepting.set(True)
        _record_interception()
        start = time.time()
        req_data: RequestData | None = None
        response_received = False

        try:
            req_data = {
                "method": request.method,
                "url": url,
                "headers": dict(request.headers),
                "body": _read_request_body(request),
                "timestamp": start,
            }
            attach_snapshot(req_data)
            response = await get_original()(self, request, **kwargs)
            response_received = True
            duration = time.time() - start

            # Check for streaming response
            content_type = response.headers.get("content-type", "")
            is_streaming = _is_streaming_content_type(content_type)

            if is_streaming:
                # Wrap streaming methods to capture content
                capture = _StreamCapture(response, req_data, start)
                capture.start("aiter_", capture.wrap_async)
            else:
                # Non-streaming: send immediately
                res_data: ResponseData = {
                    "status_code": response.status_code,
                    "headers": dict(response.headers),
                    "body": _read_response_body(response),
                    "timestamp": time.time(),
                    "duration": duration,
                    "is_streaming": False,
                }
                try:
                    _handler(req_data, res_data, None)
                except Exception:
                    logger.debug("Handler error on success path", exc_info=True)

            return response

        except Exception as e:
            if response_received:
                logger.debug("Response capture error", exc_info=True)
                return response
            if req_data is not None:
                try:
                    _handler(req_data, None, str(e))
                except Exception:
                    logger.debug("Handler error on error path", exc_info=True)
            raise
        finally:
            _intercepting.reset(token)

    return patched_async_send


def patch() -> bool:
    """Patch httpx (and httpx2, when installed) to intercept requests."""
    with _patch_lock:
        return _patch_locked()


def _patch_locked() -> bool:
    global _patched, _original_send, _original_async_send
    global _original_httpx2_send, _original_httpx2_async_send

    if _patched:
        return True

    try:
        import httpx
    except ImportError:
        httpx = None
        logger.debug("httpx not available")
    try:
        # The OpenAI / Anthropic / pydantic-ai SDKs moved to this separate fork of
        # httpx; its clients never touch httpx.Client.send, so it needs its own patch.
        import httpx2
    except ImportError:
        httpx2 = None
        logger.debug("httpx2 not available")

    if httpx is None and httpx2 is None:
        return False

    if httpx is not None:
        _original_send = _unwrap(httpx.Client.send)
        _original_async_send = _unwrap(httpx.AsyncClient.send)
        httpx.Client.send = _tag(
            _make_patched_send(lambda: _original_send), _original_send
        )
        httpx.AsyncClient.send = _tag(
            _make_patched_async_send(lambda: _original_async_send),
            _original_async_send,
        )
    if httpx2 is None:
        # Forget originals saved by an earlier patch() that did see httpx2.
        _original_httpx2_send = _original_httpx2_async_send = None
    else:
        _original_httpx2_send = _unwrap(httpx2.Client.send)
        _original_httpx2_async_send = _unwrap(httpx2.AsyncClient.send)
        httpx2.Client.send = _tag(
            _make_patched_send(lambda: _original_httpx2_send), _original_httpx2_send
        )
        httpx2.AsyncClient.send = _tag(
            _make_patched_async_send(lambda: _original_httpx2_async_send),
            _original_httpx2_async_send,
        )

    # Optionally patch requests.Session.send
    global _original_requests_send
    try:
        import requests as _requests_lib

        # Bound locally: unpatch() clears the module global, but a library that
        # captured this wrapper while it was installed (mock/record tools wrap
        # Session.send) may still call it afterwards.
        real_requests_send = _original_requests_send = _unwrap(
            _requests_lib.Session.send
        )

        def send_original():
            return _original_requests_send or real_requests_send

        def patched_requests_send(self, request, **kwargs):
            url = str(request.url or "")

            # Skip if already intercepting — prevents double-logging when the
            # same call re-enters the public send(), e.g. via a requests→httpx
            # adapter chain triggering both this handler and patched_send.
            if not _should_capture_url(url) or not _handler or _intercepting.get():
                return send_original()(self, request, **kwargs)

            token = _intercepting.set(True)
            _record_interception()
            start = time.time()
            req_data: RequestData | None = None
            response_received = False

            try:
                body: str | bytes | None = request.body
                if isinstance(body, bytes):
                    body = body.decode("utf-8", errors="replace")

                req_data = {
                    "method": request.method or "",
                    "url": url,
                    "headers": dict(request.headers or {}),
                    "body": body,
                    "timestamp": start,
                }
                attach_snapshot(req_data)
                response = send_original()(self, request, **kwargs)
                response_received = True
                duration = time.time() - start

                res_body: Any = None
                is_streaming = False
                try:
                    content_type = response.headers.get("content-type", "")
                    # Touching response.content on a stream=True response would
                    # buffer the whole body and defeat the caller's streaming.
                    # Session.send falls back to self.stream when no kwarg is given.
                    stream_requested = kwargs.get("stream", self.stream)
                    is_streaming = bool(stream_requested) or _is_streaming_content_type(
                        content_type
                    )
                    if is_streaming:
                        res_body = "[streaming]"
                    elif _is_binary_content_type(content_type):
                        res_body = "[binary]"
                    else:
                        res_body = response.content.decode("utf-8", errors="replace")
                except Exception:
                    pass

                res_data: ResponseData = {
                    "status_code": response.status_code,
                    "headers": dict(response.headers),
                    "body": res_body,
                    "timestamp": time.time(),
                    "duration": duration,
                    "is_streaming": is_streaming,
                }
                try:
                    _handler(req_data, res_data, None)
                except Exception:
                    logger.debug("Handler error on success path", exc_info=True)
                return response

            except Exception as e:
                if response_received:
                    logger.debug("Response capture error", exc_info=True)
                    return response
                if req_data is not None:
                    try:
                        _handler(req_data, None, str(e))
                    except Exception:
                        logger.debug("Handler error on error path", exc_info=True)
                raise
            finally:
                _intercepting.reset(token)

        _requests_lib.Session.send = _tag(patched_requests_send, real_requests_send)
    except ImportError:
        pass

    _patched = True
    _sync_reporter()

    logger.info("Global HTTP monitoring enabled")
    return True


def unpatch() -> None:
    """Restore original httpx / httpx2 methods."""
    with _patch_lock:
        _unpatch_locked()


def _unpatch_locked() -> None:
    global _patched, _original_requests_send, _exclude_api_patterns, _max_body_bytes

    if not _patched:
        return

    try:
        import httpx

        if _original_send:
            httpx.Client.send = _original_send
        if _original_async_send:
            httpx.AsyncClient.send = _original_async_send
    except ImportError:
        pass

    try:
        import httpx2

        if _original_httpx2_send:
            httpx2.Client.send = _original_httpx2_send
        if _original_httpx2_async_send:
            httpx2.AsyncClient.send = _original_httpx2_async_send
    except ImportError:
        pass

    try:
        import requests as _requests_lib

        if _original_requests_send:
            _requests_lib.Session.send = _original_requests_send
    except ImportError:
        pass

    _original_requests_send = None
    _exclude_api_patterns = None
    _max_body_bytes = DEFAULT_MAX_BODY_BYTES
    _patched = False
    _sync_reporter()
    logger.info("Global HTTP monitoring disabled")


def has_handler() -> bool:
    """Check if a capture handler is set (patched requests pass through without one)."""
    return bool(_handler)


def patched_targets() -> list[str]:
    """Names of the ``send`` methods currently wrapped (for diagnostics)."""
    if not _patched:
        return []
    targets = []
    if _original_send:
        targets += ["httpx.Client.send", "httpx.AsyncClient.send"]
    if _original_httpx2_send:
        targets += ["httpx2.Client.send", "httpx2.AsyncClient.send"]
    return targets


def is_patched() -> bool:
    """Check if httpx / httpx2 are patched."""
    return _patched
