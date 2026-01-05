"""HTTP interceptor for capturing API calls - patches httpx only."""

import logging
import time
from typing import Any, Callable, Optional
from urllib.parse import urlparse

from .types import RequestData, ResponseData

logger = logging.getLogger(__name__)

# State
_patched = False
_original_send: Optional[Callable] = None
_original_async_send: Optional[Callable] = None
_handler: Optional[
    Callable[[RequestData, Optional[ResponseData], Optional[str]], None]
] = None


# Known LLM API domains to monitor
LLM_API_DOMAINS = [
    "api.openai.com",
    "api.anthropic.com",
]


def _is_localhost(url: str) -> bool:
    """Check if URL is localhost."""
    try:
        host = urlparse(url).netloc.lower()
        return any(p in host for p in ["localhost", "127.0.0.1", "0.0.0.0", "::1"])
    except Exception:
        return False


def _is_llm_api(url: str) -> bool:
    """Check if URL is a known LLM API endpoint."""
    try:
        host = urlparse(url).netloc.lower()
        return any(domain in host for domain in LLM_API_DOMAINS)
    except Exception:
        return False


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
    handler: Callable[[RequestData, Optional[ResponseData], Optional[str]], None]
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

        # Only capture LLM API requests
        if not _is_llm_api(url) or _is_localhost(url) or not _handler:
            return _original_send(self, request, **kwargs)

        start = time.time()
        req_data: RequestData = {
            "method": request.method,
            "url": url,
            "headers": dict(request.headers),
            "body": request.content.decode("utf-8") if request.content else None,
            "timestamp": start,
        }

        try:
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
            _handler(req_data, res_data, None)
            return response

        except Exception as e:
            _handler(req_data, None, str(e))
            raise

    async def patched_async_send(self, request, **kwargs):
        """Patched async send."""
        url = str(request.url)

        # Only capture LLM API requests
        if not _is_llm_api(url) or _is_localhost(url) or not _handler:
            return await _original_async_send(self, request, **kwargs)

        start = time.time()
        req_data: RequestData = {
            "method": request.method,
            "url": url,
            "headers": dict(request.headers),
            "body": request.content.decode("utf-8") if request.content else None,
            "timestamp": start,
        }

        try:
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
                        _handler(req_data, res_data, None)

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
                _handler(req_data, res_data, None)

            return response

        except Exception as e:
            _handler(req_data, None, str(e))
            raise

    # Apply patches
    httpx.Client.send = patched_send
    httpx.AsyncClient.send = patched_async_send
    _patched = True

    logger.info("Global HTTP monitoring enabled")
    return True


def unpatch() -> None:
    """Restore original httpx methods."""
    global _patched

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

    _patched = False
    logger.info("Global HTTP monitoring disabled")


def is_patched() -> bool:
    """Check if httpx is patched."""
    return _patched
