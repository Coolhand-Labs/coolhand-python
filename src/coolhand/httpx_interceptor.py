"""httpx interceptor for capturing API calls."""

import contextvars
import functools
import ipaddress
import json
import logging
import re
import threading
import time
from collections.abc import Callable
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
    ``Coolhand(...)`` is constructed. Never raises: monitoring must not break
    the host application.
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
    string, and an entry that reduces to nothing (``""``, ``"."``, ``"[]"``)
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


def set_handler(
    handler: Callable[[RequestData, ResponseData | None, str | None], None],
) -> None:
    """Set the handler for captured requests."""
    global _handler
    _handler = handler


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
            duration = time.time() - start

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
            duration = time.time() - start

            # Check for streaming response
            content_type = response.headers.get("content-type", "")
            is_streaming = _is_streaming_content_type(content_type)

            if is_streaming:
                # Wrap streaming methods to capture content
                captured_chunks = _BoundedChunks(_max_body_bytes)
                content_sent = [False]  # Use list to allow mutation in closures

                def send_captured():
                    if not content_sent[0] and captured_chunks:
                        content_sent[0] = True
                        res_data: ResponseData = {
                            "status_code": response.status_code,
                            "headers": dict(response.headers),
                            "body": captured_chunks.joined(),
                            "timestamp": time.time(),
                            "duration": time.time() - start,
                            "is_streaming": True,
                            "body_truncated": captured_chunks.truncated,
                        }
                        try:
                            _handler(req_data, res_data, None)
                        except Exception:
                            logger.debug("Handler error in streaming", exc_info=True)

                # Wrap aiter_bytes
                if hasattr(response, "aiter_bytes"):
                    orig_aiter_bytes = response.aiter_bytes

                    async def capturing_aiter_bytes(chunk_size=1024):
                        async for chunk in orig_aiter_bytes(chunk_size):
                            if chunk:
                                captured_chunks.append(
                                    chunk.decode("utf-8", errors="replace")
                                )
                            yield chunk
                        send_captured()

                    response.aiter_bytes = capturing_aiter_bytes

                # Wrap aiter_lines (used by OpenAI for SSE)
                if hasattr(response, "aiter_lines"):
                    orig_aiter_lines = response.aiter_lines

                    async def capturing_aiter_lines():
                        async for line in orig_aiter_lines():
                            if line:
                                captured_chunks.append(line + "\n")
                            yield line
                        send_captured()

                    response.aiter_lines = capturing_aiter_lines

                # Wrap aiter_text
                if hasattr(response, "aiter_text"):
                    orig_aiter_text = response.aiter_text

                    async def capturing_aiter_text():
                        async for text in orig_aiter_text():
                            if text:
                                captured_chunks.append(text)
                            yield text
                        send_captured()

                    response.aiter_text = capturing_aiter_text

                # Wrap aiter_raw (lowest level)
                if hasattr(response, "aiter_raw"):
                    orig_aiter_raw = response.aiter_raw

                    async def capturing_aiter_raw(chunk_size=1024):
                        async for chunk in orig_aiter_raw(chunk_size):
                            if chunk:
                                captured_chunks.append(
                                    chunk.decode("utf-8", errors="replace")
                                )
                            yield chunk
                        send_captured()

                    response.aiter_raw = capturing_aiter_raw
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
        _original_send = httpx.Client.send
        _original_async_send = httpx.AsyncClient.send
        httpx.Client.send = _make_patched_send(lambda: _original_send)
        httpx.AsyncClient.send = _make_patched_async_send(lambda: _original_async_send)
    if httpx2 is not None:
        _original_httpx2_send = httpx2.Client.send
        _original_httpx2_async_send = httpx2.AsyncClient.send
        httpx2.Client.send = _make_patched_send(lambda: _original_httpx2_send)
        httpx2.AsyncClient.send = _make_patched_async_send(
            lambda: _original_httpx2_async_send
        )

    # Optionally patch requests.Session.send
    global _original_requests_send
    try:
        import requests as _requests_lib

        _original_requests_send = _requests_lib.Session.send

        def patched_requests_send(self, request, **kwargs):
            url = str(request.url or "")

            # Skip if already intercepting — prevents double-logging when the
            # same call re-enters the public send(), e.g. via a requests→httpx
            # adapter chain triggering both this handler and patched_send.
            if not _should_capture_url(url) or not _handler or _intercepting.get():
                return _original_requests_send(self, request, **kwargs)

            token = _intercepting.set(True)
            _record_interception()
            start = time.time()
            req_data: RequestData | None = None

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
                response = _original_requests_send(self, request, **kwargs)
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
                if req_data is not None:
                    try:
                        _handler(req_data, None, str(e))
                    except Exception:
                        logger.debug("Handler error on error path", exc_info=True)
                raise
            finally:
                _intercepting.reset(token)

        _requests_lib.Session.send = patched_requests_send
    except ImportError:
        pass

    _patched = True

    logger.info("Global HTTP monitoring enabled")
    return True


def unpatch() -> None:
    """Restore original httpx / httpx2 methods."""
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
    logger.info("Global HTTP monitoring disabled")


def has_handler() -> bool:
    """Check if a capture handler is set (patched requests pass through without one)."""
    return bool(_handler)


def is_patched() -> bool:
    """Check if httpx / httpx2 are patched."""
    return _patched
