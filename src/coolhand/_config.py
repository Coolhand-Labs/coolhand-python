"""Shared configuration helpers used by both client and feedback_service."""

import ssl
from email.message import Message
from typing import Any
from urllib.parse import urlparse
from urllib.request import (
    HTTPRedirectHandler,
    HTTPSHandler,
    OpenerDirector,
    Request,
    build_opener,
)

try:
    import certifi

    _ssl_context = ssl.create_default_context()
    _ssl_context.load_verify_locations(cafile=certifi.where())
except ImportError:
    _ssl_context = None

_DEFAULT_BASE_URL = "https://coolhandlabs.com"


class _RefuseRedirects(HTTPRedirectHandler):
    """Turns a 3xx into an error instead of following it.

    `base_url` is validated, but a redirect would carry the `X-API-Key` header to
    whatever host the response names. Returning `None` makes urllib surface the 3xx as
    an `HTTPError`.
    """

    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Message,
        newurl: str,
    ) -> None:
        return None


def _build_opener() -> OpenerDirector:
    """Build a urllib opener that never follows redirects.

    Shared by every code path that sends `X-API-Key` (`CoolhandClient`,
    `FeedbackService`, `TemplateService`) so none of them can be tricked into
    replaying the key to a redirect target.
    """
    return build_opener(HTTPSHandler(context=_ssl_context), _RefuseRedirects())


# Fixed timeout (seconds) for the write paths: CoolhandClient._send_one and
# FeedbackService._submit. Deliberately not configurable via Config.timeout,
# which only applies to TemplateService's read methods — see the comment on
# that field in types.py.
_WRITE_TIMEOUT_SECONDS = 10


def _normalize_base_url(url: str) -> str:
    """Validate and normalize a base_url value.

    https:// URLs are always accepted.
    http://localhost and http://127.0.0.1 are accepted for local development.
    Everything else raises ValueError.
    Trailing slashes are stripped.
    """
    url = url.rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme == "https" and parsed.hostname:
        return url
    if parsed.scheme == "http" and parsed.hostname in {
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        return url
    raise ValueError(
        f"base_url must use https:// (got {url!r}). "
        "For local development, http://localhost is allowed."
    )
