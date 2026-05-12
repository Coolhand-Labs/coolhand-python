"""Shared URL utilities."""

from urllib.parse import urlparse


def normalize_base_url(url: str | None) -> str | None:
    """Normalize and validate a base URL.

    Accepts https:// unconditionally. Accepts http:// only for localhost
    and 127.0.0.1 (local dev). Strips trailing slashes.

    Raises ValueError for any other scheme or host.
    """
    if url is None:
        return None
    url = url.rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme == "https":
        return url
    if parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1"):
        return url
    raise ValueError(
        f"Invalid base_url {url!r}: must use https://, or http:// with "
        "localhost / 127.0.0.1 for local development."
    )
