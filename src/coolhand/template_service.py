"""Read-only access to the LLM request template endpoints.

Wraps `GET /api/v2/llm_request_templates` (list + search) and
`GET /api/v2/llm_request_templates/{id}` (show). Both require the client's **private**
API key — the public key is write-only on this API and is rejected exactly like an
invalid one.

Template *mutation* stays on the MCP surface: this REST surface is read-only, with no
create/update/deprecate and no version-history sub-resource.
"""

import json
import logging
import os
from email.message import Message
from typing import Any, cast
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from ._config import _DEFAULT_BASE_URL, _normalize_base_url, _ssl_context
from .types import (
    Config,
    LlmRequestTemplateDetail,
    LlmRequestTemplateStatus,
    LlmRequestTemplateSummary,
    Pagination,
    SearchTemplatesResponse,
)
from .version import __version__

logger = logging.getLogger(__name__)

TEMPLATES_ENDPOINT = "/api/v2/llm_request_templates"

# Every query behind these endpoints is bounded by a 10-second statement timeout
# server-side, and answers 504 when it trips. A client timeout at or below that would
# abort the connection just before the 504 arrived, turning a reportable server answer
# into an opaque network error.
DEFAULT_TIMEOUT_SECONDS = 30.0

# DEFAULT_PER_PAGE / MAX_PER_PAGE on the v2 list controller. Mirrored here only to fill
# in pagination when a header is missing or malformed; when the headers are present —
# which on this endpoint is always, since it has no `include_total` opt-out — their
# values are used verbatim.
_DEFAULT_PER_PAGE = 25
_MAX_PER_PAGE = 100

_MAX_ERROR_BODY_CHARS = 2000


class CoolhandAPIError(Exception):
    """Raised by the read methods when a request yields no usable JSON body.

    `status` is the HTTP status code when the server answered, and `None` when there was
    no response at all (transport failure) or the response body was not JSON.

    The read methods raise rather than logging and returning `None` the way the write
    methods (`create_feedback`, `CoolhandClient.flush`) do: a caller has to be able to
    tell a `404` from a `504`, and the latter is an expected, retryable condition on
    these endpoints rather than a bug.
    """

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


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


def _query_value(value: bool | int | str) -> str:
    """Render a filter value for the query string.

    Booleans must go over the wire lowercase: the server reads `"false"` as false, but
    would read Python's `str(False)` -> `"False"` as true.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _pagination_from_headers(
    headers: Message,
    requested_page: int | None,
    requested_per: int | None,
) -> Pagination:
    """Build `Pagination` from response headers, never from the page's own length."""
    fallback_page = requested_page if requested_page and requested_page > 0 else 1
    if requested_per and requested_per > 0:
        fallback_per = min(requested_per, _MAX_PER_PAGE)
    else:
        fallback_per = _DEFAULT_PER_PAGE

    current_page = max(1, _parse_header_int(headers.get("X-Page"), fallback_page))
    per_page = _parse_header_int(headers.get("X-Per-Page"), fallback_per)
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


def _encode_template_id(template_id: str) -> str:
    """URL-encode a template hashid, rejecting values that would retarget the request.

    `quote` leaves `.` unescaped, so `.` and `..` would survive into the path and be
    resolved away by the server or an intermediate proxy — silently hitting the list
    route (or an unrelated path) and returning a bare array where the caller expects a
    single template.
    """
    if not isinstance(template_id, str) or not template_id.strip():
        raise ValueError("get_template: template_id must be a non-empty string")
    if template_id.strip() in {".", ".."}:
        raise ValueError(
            "get_template: template_id must not be a relative path segment"
        )
    return quote(template_id, safe="")


def _error_body(error: HTTPError) -> str:
    """Read an error response body for the message, tolerating an unreadable one."""
    try:
        return error.read().decode("utf-8", errors="replace")[:_MAX_ERROR_BODY_CHARS]
    except Exception:
        return ""


class TemplateService:
    """Read the LLM request templates your logs are matched against.

    Example:
        >>> from coolhand import TemplateService
        >>> service = TemplateService(api_key="your-private-api-key")
        >>> result = service.search_templates(status="published")
        >>> detail = service.get_template(result["templates"][0]["id"])
    """

    def __init__(self, config: Config | None = None, **kwargs: Any) -> None:
        """Initialize the template service.

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
        self._opener = build_opener(
            HTTPSHandler(context=_ssl_context), _RefuseRedirects()
        )

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

    def search_templates(
        self,
        *,
        search: str | None = None,
        workload_id: str | None = None,
        status: LlmRequestTemplateStatus | None = None,
        include_deprecated: bool | None = None,
        include_system: bool | None = None,
        page: int | None = None,
        per: int | None = None,
    ) -> SearchTemplatesResponse:
        """List templates, optionally filtered.

        Search is the `search` *parameter* on the list endpoint, not a route of its own,
        which is why this is one method rather than a separate list/search pair.

        This is **not** a port of the `search_templates` MCP tool and does not match its
        numbers: `log_count` here excludes evals and synthetic logs, and templates whose
        workload has been archived are returned rather than hidden.

        There is deliberately no `client_id`: the client is always derived from the
        authenticating API key and cannot be supplied by the caller.

        Args:
            search: Case-insensitive *literal* substring match on the template name.
                `%` and `_` are escaped server-side so they match themselves — do not
                escape them again here.
            workload_id: Workload hashid. One that does not decode, or that belongs to
                another client, returns 422 rather than an empty list.
            status: One of "draft", "published", "failure". Any other non-empty value
                returns 422.
            include_deprecated: Include templates with a non-null `deprecated_at`.
                Defaults to false server-side.
            include_system: Include the "Unmatched" / "Ignored API Calls" buckets every
                client is created with. Defaults to false server-side, which is why a
                client with no templates of its own gets an empty list, not those two
                rows.
            page: Page number, 1-based.
            per: Page size (default 25, max 100, both enforced server-side).

        Returns:
            A dict with `templates` (newest first) and `pagination`, the latter read
            from the response headers rather than computed from the rows returned.

        Raises:
            CoolhandAPIError: On a non-2xx response, with `status` set — `401` for a
                missing/invalid/public key, `422` for an unrecognized `status` or an
                undecodable/foreign `workload_id`, and `504` when the `log_count`
                aggregate exceeds the server's statement timeout. A `504` is retryable:
                narrow with `workload_id`, `search` or a smaller `per` and try again.
                Also raised, with `status` left `None`, on a transport failure or a body
                that is not the JSON array this endpoint returns.
        """
        filters: dict[str, bool | int | str | None] = {
            "search": search,
            "workload_id": workload_id,
            "status": status,
            "include_deprecated": include_deprecated,
            "include_system": include_system,
            "page": page,
            # `per_page` is accepted on the wire as an alias with the same bounds, but
            # one knob is enough and sending both invites disagreement.
            "per": per,
        }
        query = urlencode(
            {
                key: _query_value(value)
                for key, value in filters.items()
                if value is not None
            }
        )

        url = f"{self.config['base_url']}{TEMPLATES_ENDPOINT}"
        if query:
            url = f"{url}?{query}"

        body, headers = self._get_json(url)
        if not isinstance(body, list):
            raise CoolhandAPIError(
                "Template list response was not a JSON array: "
                f"{str(body)[:_MAX_ERROR_BODY_CHARS]}"
            )

        templates: list[LlmRequestTemplateSummary] = body
        self._log(f"Fetched {len(templates)} template(s)")
        return {
            "templates": templates,
            "pagination": _pagination_from_headers(headers, page, per),
        }

    def get_template(self, template_id: str) -> LlmRequestTemplateDetail:
        """Get a single template by hashid, including both prompt patterns.

        Unlike `search_templates`, this applies no filtering beyond client ownership: a
        deprecated or system template is reachable by id with no opt-in flag, since
        inspecting one of those is the usual reason to fetch a template directly.

        Args:
            template_id: The template hashid, i.e. the `id` field from
                `search_templates`.

        Returns:
            The template, with `user_prompt_pattern` and `system_prompt_pattern` — the
            full untruncated regexes `search_templates` omits — present as keys even
            when null.

        Raises:
            ValueError: If `template_id` is blank, not a string, or a relative path
                segment.
            CoolhandAPIError: On a non-2xx response, with `status` set — `404` for an
                unknown id *or* one belonging to another client (existence is not
                disclosed, so this is never a `403`), and `504` on the same `log_count`
                timeout `search_templates` describes, which fetching the "Unmatched"
                bucket by id can trip on its own. Also raised, with `status` left
                `None`, on a transport failure or a non-JSON-object body.
        """
        encoded_id = _encode_template_id(template_id)
        url = f"{self.config['base_url']}{TEMPLATES_ENDPOINT}/{encoded_id}"

        body, _headers = self._get_json(url)
        if not isinstance(body, dict):
            raise CoolhandAPIError(
                "Template response was not a JSON object: "
                f"{str(body)[:_MAX_ERROR_BODY_CHARS]}"
            )

        template = cast(LlmRequestTemplateDetail, body)
        self._log(f"Fetched template {template.get('id', 'unknown')}")
        return template

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
                f"Template request failed ({error.code}): {_error_body(error)}",
                status=error.code,
            ) from error
        except URLError as error:
            raise CoolhandAPIError(
                f"Template request failed: {error.reason}"
            ) from error

        try:
            return json.loads(raw), headers
        except ValueError as error:
            raise CoolhandAPIError(
                f"Template response was not valid JSON: {raw[:_MAX_ERROR_BODY_CHARS]}"
            ) from error

    def _log(self, message: str) -> None:
        """Log a message if not in silent mode."""
        if not self.silent:
            logger.info(message)


_default_service: TemplateService | None = None


def get_template_service(
    config: Config | None = None, **kwargs: Any
) -> TemplateService:
    """Get a template service instance.

    If no config is provided and a default service exists, returns the default.
    Otherwise creates a new service with the provided config.

    Args:
        config: Optional configuration dictionary.
        **kwargs: Override config values.

    Returns:
        TemplateService instance.
    """
    global _default_service

    if config is None and not kwargs and _default_service is not None:
        return _default_service

    service = TemplateService(config, **kwargs)

    if _default_service is None:
        _default_service = service

    return service
