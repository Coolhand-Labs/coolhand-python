"""Size cap for captured request/response bodies."""

import json
from typing import Any

# 1 MiB. Large enough to keep nearly all text prompts/responses whole, small
# enough that base64 images/audio can't pile up unbounded in the dispatch queue.
DEFAULT_MAX_BODY_BYTES = 1_048_576


def body_size(body: Any) -> int:
    """Size of a body in UTF-8 bytes (dicts are measured as serialized JSON)."""
    if isinstance(body, bytes):
        return len(body)
    if isinstance(body, str):
        return len(body.encode("utf-8", errors="replace"))
    return len(json.dumps(body, default=str).encode("utf-8", errors="replace"))


def truncate_text(text: str, total: int, cap: int) -> str:
    """Keep the first ``cap`` bytes of ``text`` and append a truncation marker.

    ``total`` is the original size in bytes, reported in the marker. Decoding
    with ``errors="ignore"`` drops a multibyte character split by the cut.
    """
    kept = text.encode("utf-8", errors="replace")[:cap].decode("utf-8", errors="ignore")
    return f"{kept}[truncated: {total} bytes, kept first {cap}]"


def cap_body(body: Any, cap: int | None) -> Any:
    """Return ``body`` unchanged if it fits in ``cap`` bytes, else a truncated string.

    A falsy ``cap`` (``None`` or ``0``) disables the limit.
    """
    if not cap or body is None:
        return body
    total = body_size(body)
    if total <= cap:
        return body
    if isinstance(body, bytes):
        text = body.decode("utf-8", errors="replace")
    elif isinstance(body, str):
        text = body
    else:
        text = json.dumps(body, default=str)
    return truncate_text(text, total, cap)
