"""Shared plumbing for the read-only REST services.

`TemplateService`, `WorkloadService` and `LogService` all GET a JSON resource with the
client's **private** API key, raise `CoolhandAPIError` on any failure, and read
pagination off response headers. That machinery lives here once.
"""

import json
import logging
import os
from datetime import datetime, timezone
from email.message import Message
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request

from ._config import _DEFAULT_BASE_URL, _build_opener, _normalize_base_url
from .types import Config, Pagination
from .version import __version__

logger = logging.getLogger(__name__)

# Every query behind these endpoints is bounded by a 10-second statement timeout
# server-side, and answers 504 when it trips. A client timeout at or below that would
# abort the connection just before the 504 arrived, turning a reportable server answer
# into an opaque network error.
DEFAULT_TIMEOUT_SECONDS = 30.0

# DEFAULT_PER_PAGE / MAX_PER_PAGE on the v2 list controllers. Mirrored here only to fill
# in pagination when a header is missing or malformed; when the headers are present
# their values are used verbatim.
_DEFAULT_PER_PAGE = 25
_MAX_PER_PAGE = 100

_MAX_ERROR_BODY_CHARS = 2000

QueryValue = bool | int | float | str | None


class CoolhandAPIError(Exception):
    """Raised by the read methods when a request yields no usable JSON body.

    `status` is the HTTP status code when the server answered, and `None` when there was
    no response at all (transport failure) or the response body was not JSON.

    The read methods raise rather than failing silently the way the write paths do
    (`create_feedback` logs and returns `None`; the auto-monitor's submission path
    behind `CoolhandClient.flush()` logs and drops): a caller has to be able to tell a
    `404` from a `504`, and the latter is an expected, retryable condition on these
    endpoints rather than a bug.
    """

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def _parse_header_int(value: str | None, fallback: int) -> int:
    """Read a non-negative integer header, treating anything else as absent.

    Stricter than `int(...)` on purpose: an empty or garbage header should fall back
    rather than raise or, worse, be coerced into a plausible-looking zero.
    """
    if value is None:
        return fallback
    trimmed = value.strip()
    if trimmed.isascii() and trimmed.isdigit():
        return int(trimmed)
    return fallback


def _query_value(value: bool | int | float | str) -> str:
    """Render a filter value for the query string.

    Booleans must go over the wire lowercase: the server reads `"false"` as false, but
    would read Python's `str(False)` -> `"False"` as true.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _query_string(filters: dict[str, QueryValue]) -> str:
    """Encode the filters that were set; `None` is omitted, `False` and `0` are sent."""
    return urlencode(
        {
            key: _query_value(value)
            for key, value in filters.items()
            if value is not None
        }
    )


def _with_query(url: str, filters: dict[str, QueryValue]) -> str:
    query = _query_string(filters)
    return f"{url}?{query}" if query else url


def _timestamp_param(value: datetime | str | None, name: str) -> str | None:
    """Serialise a `since`/`until` bound for the wire.

    A `datetime` becomes ISO8601 UTC (naive means UTC); a string is passed through for
    the server to validate. `urlencode` turns a `+hh:mm` offset in a string into `%2B`,
    so it never arrives as a space and a 422.

    Raises:
        ValueError: If `value` is neither a `datetime` nor a `str`.
    """
    if value is None or isinstance(value, str):
        return value
    if not isinstance(value, datetime):
        raise ValueError(f"{name} must be a datetime or an ISO8601 string")
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _window_filters(
    days_back: int | None,
    since: datetime | str | None,
    until: datetime | str | None,
) -> dict[str, QueryValue]:
    """The `days_back` / `since` / `until` params shared by every windowed endpoint."""
    return {
        "days_back": days_back,
        "since": _timestamp_param(since, "since"),
        "until": _timestamp_param(until, "until"),
    }


def _page_and_per(
    headers: Message,
    requested_page: int | None,
    requested_per: int | None,
) -> tuple[int, int]:
    """Current page and page size, from `X-Page` / `X-Per-Page`, else the request."""
    fallback_page = requested_page if requested_page and requested_page > 0 else 1
    if requested_per and requested_per > 0:
        fallback_per = min(requested_per, _MAX_PER_PAGE)
    else:
        fallback_per = _DEFAULT_PER_PAGE

    current_page = max(1, _parse_header_int(headers.get("X-Page"), fallback_page))
    per_page = _parse_header_int(headers.get("X-Per-Page"), fallback_per)
    return current_page, per_page


def _pagination_from_headers(
    headers: Message,
    requested_page: int | None,
    requested_per: int | None,
) -> Pagination:
    """Build `Pagination` from response headers, never from the page's own length."""
    current_page, per_page = _page_and_per(headers, requested_page, requested_per)
    # Reported as sent. The live server answers X-Total-Pages: 1 alongside
    # X-Total-Count: 0, and recomputing either from the other would contradict the
    # endpoint rather than correct it.
    total_count = _parse_header_int(headers.get("X-Total-Count"), 0)
    total_pages = _parse_header_int(headers.get("X-Total-Pages"), 0)

    return {
        "current_page": current_page,
        "per_page": per_page,
        "total_count": total_count,
        "total_pages": total_pages,
        "has_next_page": current_page < total_pages,
        "has_prev_page": current_page > 1,
    }


def _encode_path_id(value: str, name: str, method: str) -> str:
    """URL-encode a hashid for a path segment, rejecting values that retarget it.

    `quote` leaves `.` unescaped, so `.` and `..` would survive into the path and be
    resolved away by the server or an intermediate proxy — silently hitting the list
    route (or an unrelated path) and returning a bare array where the caller expects a
    single record.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{method}: {name} must be a non-empty string")
    if value.strip() in {".", ".."}:
        raise ValueError(f"{method}: {name} must not be a relative path segment")
    return quote(value, safe="")


def _error_body(error: HTTPError) -> str:
    """Read an error response body for the message, tolerating an unreadable one."""
    try:
        return error.read().decode("utf-8", errors="replace")[:_MAX_ERROR_BODY_CHARS]
    except Exception:
        return ""


class ReadService:
    """Base for the services that GET JSON from the private-key REST endpoints."""

    # Names the resource in error messages ("Template request failed (404): ...").
    _resource_label = "Resource"

    def __init__(self, config: Config | None = None, **kwargs: Any) -> None:
        """Initialize the service.

        Args:
            config: Configuration dictionary. `api_key` must be the **private** key.
            **kwargs: Override config values (api_key, base_url, silent, timeout).
        """
        self.config: Config = {
            "api_key": os.getenv("COOLHAND_API_KEY", ""),
            "base_url": os.getenv("COOLHAND_BASE_URL") or _DEFAULT_BASE_URL,
            "silent": os.getenv("COOLHAND_SILENT", "true").lower() == "true",
            "timeout": DEFAULT_TIMEOUT_SECONDS,
        }
        if config:
            self.config.update(config)
        self.config.update(kwargs)
        self.config["base_url"] = _normalize_base_url(
            self.config.get("base_url", _DEFAULT_BASE_URL)
        )
        self._opener = _build_opener()

    @property
    def api_key(self) -> str:
        """Get the configured API key."""
        return self.config.get("api_key") or ""

    @property
    def silent(self) -> bool:
        """Check if silent mode is enabled."""
        return self.config.get("silent", True)

    @property
    def timeout(self) -> float:
        """Get the HTTP timeout, in seconds, used by the read methods."""
        return self.config.get("timeout", DEFAULT_TIMEOUT_SECONDS)

    def _get_json(self, url: str) -> tuple[Any, Message]:
        """GET `url` and parse the JSON body, raising `CoolhandAPIError` on failure."""
        request = Request(
            url=url,
            headers={
                "Accept": "application/json",
                "X-API-Key": self.api_key,
                "User-Agent": f"coolhand-python/{__version__}",
            },
            method="GET",
        )

        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
                headers = response.headers
        except HTTPError as error:
            raise CoolhandAPIError(
                f"{self._resource_label} request failed ({error.code}): "
                f"{_error_body(error)}",
                status=error.code,
            ) from error
        except URLError as error:
            raise CoolhandAPIError(
                f"{self._resource_label} request failed: {error.reason}"
            ) from error

        try:
            return json.loads(raw), headers
        except ValueError as error:
            raise CoolhandAPIError(
                f"{self._resource_label} response was not valid JSON: "
                f"{raw[:_MAX_ERROR_BODY_CHARS]}"
            ) from error

    def _get_json_array(self, url: str, what: str) -> tuple[list[Any], Message]:
        """`_get_json` for a list endpoint; `what` names it in the error message."""
        body, headers = self._get_json(url)
        if not isinstance(body, list):
            raise CoolhandAPIError(
                f"{what} response was not a JSON array: "
                f"{str(body)[:_MAX_ERROR_BODY_CHARS]}"
            )
        return body, headers

    def _get_json_object(self, url: str, what: str) -> tuple[dict[str, Any], Message]:
        """`_get_json` for a show endpoint; `what` names it in the error message."""
        body, headers = self._get_json(url)
        if not isinstance(body, dict):
            raise CoolhandAPIError(
                f"{what} response was not a JSON object: "
                f"{str(body)[:_MAX_ERROR_BODY_CHARS]}"
            )
        return body, headers

    def _log(self, message: str) -> None:
        """Log a message if not in silent mode."""
        if not self.silent:
            logger.info(message)
