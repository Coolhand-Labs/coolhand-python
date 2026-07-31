"""httpx interceptor for capturing API calls."""

import contextvars
import json
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .types import RequestData, ResponseData

logger = logging.getLogger(__name__)

# State
_patched = False
_original_send: Callable | None = None
_original_async_send: Callable | None = None
_original_requests_send: Callable | None = None
_handler: Callable[[RequestData, ResponseData | None, str | None], None] | None = None
_intercept_addresses: list[str] | None = None
_exclude_api_patterns: list[str] | None = None

# Reentrancy guard: prevents the handler from firing more than once per logical
# request when the same intercepted call re-enters the public send() — e.g. a
# requests→httpx adapter chain triggers both patched_requests_send and
# patched_send for the same request, or any other code that calls self.send()
# while already inside a patched send.
_intercepting: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "coolhand_intercepting", default=False
)


# Default intercept addresses (domains and path substrings)
DEFAULT_INTERCEPT_ADDRESSES = [
    "api.openai.com",
    "api.anthropic.com",
    "generativelanguage.googleapis.com",
    "aiplatform.googleapis.com",
    "gateway.ai.cloudflare.com",
    "models.github.ai",
    "models.inference.ai.azure.com",
    "openrouter.ai",
    ":generateContent",
    ":streamGenerateContent",
    ":predict",
    ":streamRawPredict",
]

DEFAULT_EXCLUDE_API_PATTERNS: list[str] = json.loads(
    (Path(__file__).parent / "default_exclude_api_patterns.json").read_text()
)


def set_intercept_addresses(addresses: list[str]) -> None:
    """Set custom intercept addresses (domains and/or path substrings)."""
    global _intercept_addresses
    _intercept_addresses = addresses


def set_exclude_api_patterns(patterns: list[str]) -> None:
    """Set URL substring patterns to exclude from capture (deny-list)."""
    global _exclude_api_patterns
    _exclude_api_patterns = patterns


def _is_localhost(url: str) -> bool:
    """Check if URL is localhost."""
    try:
        host = urlparse(url).netloc.lower()
        return any(p in host for p in ["localhost", "127.0.0.1", "0.0.0.0", "::1"])
    except Exception:
        return False


def _is_llm_api(url: str) -> bool:
    """Check if URL matches any intercept address (substring match)."""
    try:
        addresses = _intercept_addresses or DEFAULT_INTERCEPT_ADDRESSES
        return any(addr in url for addr in addresses)
    except Exception:
        return False


def _is_excluded(url: str) -> bool:
    """Check if URL matches any exclude pattern (deny-list after allow-list)."""
    try:
        patterns = (
            _exclude_api_patterns
            if _exclude_api_patterns is not None
            else DEFAULT_EXCLUDE_API_PATTERNS
        )
        return any(p in url for p in patterns)
    except Exception:
        # Fail closed: if the exclude check itself breaks, treat the URL as
        # excluded rather than risk capturing traffic the deny-list exists to skip.
        return True


def _is_streaming_content_type(content_type: str) -> bool:
    """Check if content type indicates streaming."""
    return "text/event-stream" in content_type or "application/x-ndjson" in content_type


def _read_response_body(response: Any) -> Any:
    """Safely read response body."""
    try:
        content_type = response.headers.get("content-type", "")
        if _is_streaming_content_type(content_type):
            return "[streaming]"

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


def patch() -> bool:
    """Patch httpx to intercept requests."""
    global _patched, _original_send, _original_async_send

    if _patched:
        return True

    try:
        import httpx
    except ImportError:
        logger.debug("httpx not available")
        return False

    # Save originals
    _original_send = httpx.Client.send
    _original_async_send = httpx.AsyncClient.send

    def patched_send(self, request, **kwargs):
        """Patched sync send."""
        url = str(request.url)

        # Only capture LLM API requests; skip if already intercepting (reentrancy
        # guard prevents double-logging when the same intercepted call re-enters
        # the public send(), e.g. via a requests→httpx adapter chain).
        if (
            not _is_llm_api(url)
            or _is_localhost(url)
            or _is_excluded(url)
            or not _handler
            or _intercepting.get()
        ):
            return _original_send(self, request, **kwargs)

        token = _intercepting.set(True)
        start = time.time()
        req_data: RequestData | None = None

        try:
            req_data = {
                "method": request.method,
                "url": url,
                "headers": dict(request.headers),
                "body": (
                    request.content.decode("utf-8", errors="replace")
                    if request.content
                    else None
                ),
                "timestamp": start,
            }
            response = _original_send(self, request, **kwargs)
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

    async def patched_async_send(self, request, **kwargs):
        """Patched async send."""
        url = str(request.url)

        # Only capture LLM API requests; skip if already intercepting (reentrancy
        # guard prevents double-logging when the same intercepted call re-enters
        # the public send(), e.g. via a requests→httpx adapter chain).
        if (
            not _is_llm_api(url)
            or _is_localhost(url)
            or _is_excluded(url)
            or not _handler
            or _intercepting.get()
        ):
            return await _original_async_send(self, request, **kwargs)

        token = _intercepting.set(True)
        start = time.time()
        req_data: RequestData | None = None

        try:
            req_data = {
                "method": request.method,
                "url": url,
                "headers": dict(request.headers),
                "body": (
                    request.content.decode("utf-8", errors="replace")
                    if request.content
                    else None
                ),
                "timestamp": start,
            }
            response = await _original_async_send(self, request, **kwargs)
            duration = time.time() - start

            # Check for streaming response
            content_type = response.headers.get("content-type", "")
            is_streaming = _is_streaming_content_type(content_type)

            if is_streaming:
                # Wrap streaming methods to capture content
                captured_chunks = []
                content_sent = [False]  # Use list to allow mutation in closures

                def send_captured():
                    if not content_sent[0] and captured_chunks:
                        content_sent[0] = True
                        res_data: ResponseData = {
                            "status_code": response.status_code,
                            "headers": dict(response.headers),
                            "body": "".join(captured_chunks),
                            "timestamp": time.time(),
                            "duration": time.time() - start,
                            "is_streaming": True,
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

    # Apply httpx patches
    httpx.Client.send = patched_send
    httpx.AsyncClient.send = patched_async_send

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
            if (
                not _is_llm_api(url)
                or _is_localhost(url)
                or _is_excluded(url)
                or not _handler
                or _intercepting.get()
            ):
                return _original_requests_send(self, request, **kwargs)

            token = _intercepting.set(True)
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
                response = _original_requests_send(self, request, **kwargs)
                duration = time.time() - start

                res_body: Any = None
                is_streaming = False
                try:
                    content_type = response.headers.get("content-type", "")
                    is_streaming = _is_streaming_content_type(content_type)
                    if is_streaming:
                        res_body = "[streaming]"
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
    """Restore original httpx methods."""
    global _patched, _original_requests_send, _exclude_api_patterns

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
        import requests as _requests_lib

        if _original_requests_send:
            _requests_lib.Session.send = _original_requests_send
    except ImportError:
        pass

    _original_requests_send = None
    _exclude_api_patterns = None
    _patched = False
    logger.info("Global HTTP monitoring disabled")


def is_patched() -> bool:
    """Check if httpx is patched."""
    return _patched
