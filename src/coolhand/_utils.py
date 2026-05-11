"""Shared internal utilities."""

from urllib.parse import urlparse


def _validate_base_url(url: str) -> str:
    """Validate and normalize a base URL.

    Accepts https:// (any non-empty hostname) or http:// on localhost/127.0.0.1
    only. Trailing slashes are stripped.
    """
    url = url.rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme == "https" and parsed.hostname:
        return url
    if parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1"):
        return url
    raise ValueError(
        f"Invalid base_url '{url}': must use https:// "
        "(or http://localhost / http://127.0.0.1 for local dev)"
    )
