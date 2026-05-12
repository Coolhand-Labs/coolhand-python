"""Shared configuration helpers."""

from urllib.parse import urlparse


def validate_base_url(url: str) -> str:
    """Normalize and validate base_url.

    Strips trailing slash. Accepts https:// for any host, or http://
    restricted to localhost/127.0.0.1 for local development.
    """
    url = url.rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme == "https":
        return url
    if parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1"):
        return url
    raise ValueError(
        f"base_url must use https:// (or http://localhost for local dev), got: {url!r}"
    )
