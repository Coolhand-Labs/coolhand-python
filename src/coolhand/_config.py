"""Shared configuration helpers used by both client and feedback_service."""

import ssl
from urllib.parse import urlparse

try:
    import certifi

    _ssl_context = ssl.create_default_context()
    _ssl_context.load_verify_locations(cafile=certifi.where())
except ImportError:
    _ssl_context = None

_DEFAULT_BASE_URL = "https://coolhandlabs.com"


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
