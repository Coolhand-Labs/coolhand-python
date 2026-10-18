"""Shared configuration helpers used by both client and feedback_service."""

import ssl
from collections.abc import Mapping
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


# Default timeout (seconds) for every call to the Coolhand API. Matches coolhand-node's
# `requestTimeoutMs` default (30000). It sits above the 10-second server-side statement
# timeout on the template endpoints, so a 504 from the server arrives as a response
# instead of the client aborting the connection just before it.
DEFAULT_REQUEST_TIMEOUT_SECONDS = 30.0

# Upper bound (about 68 years). Larger finite values make `urlopen` raise OverflowError
# on the first request, which the delivery path swallows; reject them up front instead.
# Same bound coolhand-node applies to `requestTimeoutMs`, which is 2^31-1 there too.
_MAX_REQUEST_TIMEOUT_SECONDS = 2**31 - 1


def _resolve_request_timeout(config: Mapping[str, Any]) -> float:
    """Resolve and validate the timeout, in seconds, for calls to the Coolhand API.

    `request_timeout` wins; the older `timeout` spelling is honoured only when it is
    absent. Neither set (or `None`) gives `DEFAULT_REQUEST_TIMEOUT_SECONDS`. Raises
    ValueError for anything that isn't a finite number greater than zero and at most
    `_MAX_REQUEST_TIMEOUT_SECONDS`, so a bad value fails at construction instead of
    surfacing as an opaque network error on the first request.
    """
    name = "request_timeout"
    value = config.get(name)
    if value is None:
        name = "timeout"
        value = config.get(name)
    if value is None:
        return DEFAULT_REQUEST_TIMEOUT_SECONDS
    # bool is an int subclass; `request_timeout=True` is a mistake, not one second.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(
            f"{name} must be a positive number of seconds (got {value!r})."
        )
    # One chained comparison on purpose: it is exact for any int (math.isfinite would
    # raise OverflowError on 10**400) and False for NaN, so NaN and inf fail it too.
    if not 0 < value <= _MAX_REQUEST_TIMEOUT_SECONDS:
        raise ValueError(
            f"{name} must be a positive number of seconds, at most "
            f"{_MAX_REQUEST_TIMEOUT_SECONDS} (got {value!r})."
        )
    return float(value)


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
