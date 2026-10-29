"""Shared plumbing for the read-only REST services.

`TemplateService`, `StepTemplateService`, `WorkloadService` and `LogService` all GET a
JSON resource with the client's **private** API key (`HelpCenterDocs` alone needs none),
raise `CoolhandAPIError` on any failure, and read pagination off response headers. That
machinery lives here once.
"""

import http.client
import json
import logging
import os
from collections.abc import Mapping
from datetime import datetime, timezone
from email.message import Message
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import OpenerDirector, Request

from ._config import (
    _DEFAULT_BASE_URL,
    DEFAULT_REQUEST_TIMEOUT_SECONDS,
    _build_opener,
    _normalize_base_url,
    _resolve_request_timeout,
)
from .declared_context import validate_key
from .types import Config, Pagination
from .version import __version__

# Every query behind these endpoints is bounded by a 10-second statement timeout
# server-side, and answers 504 when it trips. A client timeout at or below that would
# abort the connection just before the 504 arrived, turning a reportable server answer
# into an opaque network error. Shared with the write paths; see
# `_config.DEFAULT_REQUEST_TIMEOUT_SECONDS`.
DEFAULT_TIMEOUT_SECONDS = DEFAULT_REQUEST_TIMEOUT_SECONDS

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


def _by_key_url(base_url: str, endpoint: str, key: str, label: str) -> str:
    """Build `<base><endpoint>/by_key/<key>`; the route is a glob over the key.

    Each `/`-separated segment is percent-encoded (`#` -> `%23`, `:` -> `%3A`) and the
    segments are rejoined with a literal `/`, which the glob needs to see. Blank keys
    and `.`, `..` or empty segments are rejected up front: `quote` leaves dots alone, so
    they would be resolved away as path segments and retarget the request.
    """
    key = validate_key(key, f"{label}: key")
    segments = key.split("/")
    if any(segment in {".", ".."} for segment in segments):
        raise ValueError(f"{label}: key must not contain '.' or '..' path segments")
    encoded = "/".join(quote(segment, safe="") for segment in segments)
    return f"{base_url}{endpoint}/by_key/{encoded}"


def _put_json(
    opener: OpenerDirector,
    url: str,
    payload: dict[str, Any],
    *,
    api_key: str,
    timeout: float,
    label: str,
) -> tuple[int, dict[str, Any]]:
    """PUT `payload` as JSON; return `(status, body)` or raise `CoolhandAPIError`."""
    request = Request(
        url=url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-API-Key": api_key,
            "User-Agent": f"coolhand-python/{__version__}",
        },
        method="PUT",
    )
    try:
        with opener.open(request, timeout=timeout) as response:
            status = response.status
            raw = response.read().decode("utf-8")
    except HTTPError as error:
        raise CoolhandAPIError(
            f"{label} request failed ({error.code}): {_error_body(error)}",
            status=error.code,
        ) from error
    except URLError as error:
        raise CoolhandAPIError(f"{label} request failed: {error.reason}") from error
    except (OSError, http.client.HTTPException, UnicodeDecodeError) as error:
        # A read timeout, reset, truncated or undecodable body escapes `open` unwrapped.
        raise CoolhandAPIError(f"{label} request failed: {error}") from error
    except ValueError as error:
        # http.client's message for a malformed header quotes the value, which can
        # be the API key. Report the type only, and `from None` so the original is
        # not chained and printed with any traceback of this error.
        raise CoolhandAPIError(
            f"{label} request failed: {type(error).__name__}"
        ) from None

    try:
        body = json.loads(raw)
    except ValueError as error:
        raise CoolhandAPIError(
            f"{label} response was not valid JSON: {raw[:_MAX_ERROR_BODY_CHARS]}",
            status=status,
        ) from error
    if not isinstance(body, dict):
        raise CoolhandAPIError(
            f"{label} response was not a JSON object: {raw[:_MAX_ERROR_BODY_CHARS]}",
            status=status,
        )
    return status, body


def _flatten_workload(workload: Any, label: str) -> dict[str, str]:
    """Flatten `{"id": ...}` / `{"key": ...}` to `workload_id` / `workload_key`."""
    if not isinstance(workload, Mapping):
        raise ValueError(f"{label}: workload must be a mapping with an 'id' or a 'key'")
    identifier = workload.get("id")
    if identifier is not None:
        if not isinstance(identifier, str):
            raise ValueError(f"{label}: workload.id must be a string")
        if identifier.strip():
            return {"workload_id": identifier.strip()}
    if workload.get("key") is not None:
        return {"workload_key": validate_key(workload["key"], f"{label}: workload.key")}
    raise ValueError(f"{label}: workload needs an 'id' or a 'key'")


class ReadService:
    """Base for the services that GET JSON from the REST endpoints.

    Private-key by default; the public help center reads are the keyless exception.
    """

    # Names the resource in error messages ("Template request failed (404): ...").
    _resource_label = "Resource"

    # Keyless reads send no `X-API-Key` at all rather than an empty one. Off for the
    # private-key services, whose empty header is long-standing behaviour.
    _keyless = False

    def __init__(self, config: Config | None = None, **kwargs: Any) -> None:
        """Initialize the service.

        Args:
            config: Configuration dictionary. `api_key` must be the **private** key
                (optional for the keyless `HelpCenterDocs`).
            **kwargs: Override config values (api_key, base_url, silent,
                request_timeout).
        """
        self.config: Config = {
            "api_key": os.getenv("COOLHAND_API_KEY", ""),
            "base_url": os.getenv("COOLHAND_BASE_URL") or _DEFAULT_BASE_URL,
            "silent": os.getenv("COOLHAND_SILENT", "true").lower() == "true",
        }
        if config:
            self.config.update(config)
        self.config.update(kwargs)
        self.config["request_timeout"] = _resolve_request_timeout(self.config)
        self.config["base_url"] = _normalize_base_url(
            self.config.get("base_url", _DEFAULT_BASE_URL)
        )
        self._opener = _build_opener()

    @property
    def api_key(self) -> str:
        """Get the configured API key, stripped of surrounding whitespace.

        A trailing newline (common in env files / k8s secrets) makes http.client
        reject the header with a ValueError whose message contains the full key.
        """
        return (self.config.get("api_key") or "").strip()

    @property
    def silent(self) -> bool:
        """Check if silent mode is enabled."""
        return self.config.get("silent", True)

    @property
    def timeout(self) -> float:
        """Get the HTTP timeout, in seconds."""
        return self.config["request_timeout"]

    def _get_json(self, url: str) -> tuple[Any, Message]:
        """GET `url` and parse the JSON body, raising `CoolhandAPIError` on failure."""
        headers = {
            "Accept": "application/json",
            "User-Agent": f"coolhand-python/{__version__}",
        }
        if self.api_key or not self._keyless:
            headers["X-API-Key"] = self.api_key
        request = Request(url=url, headers=headers, method="GET")

        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
                response_headers = response.headers
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
        except (OSError, http.client.HTTPException, UnicodeDecodeError) as error:
            # A read timeout, reset, truncated or undecodable body escapes `open`
            # unwrapped; the contract is CoolhandAPIError for every failure.
            raise CoolhandAPIError(
                f"{self._resource_label} request failed: {error}"
            ) from error
        except ValueError as error:
            # Type only, no chained cause: the original quotes the API key (see
            # _put_json).
            raise CoolhandAPIError(
                f"{self._resource_label} request failed: {type(error).__name__}"
            ) from None

        try:
            return json.loads(raw), response_headers
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
            logging.getLogger(type(self).__module__).info(message)
