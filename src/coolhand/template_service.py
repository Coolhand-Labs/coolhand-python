"""Read-only access to the LLM request template endpoints.

Wraps `GET /api/v2/llm_request_templates` (list + search) and
`GET /api/v2/llm_request_templates/{id}` (show). Both require the client's **private**
API key — the public key is write-only on this API and is rejected exactly like an
invalid one.

Template *mutation* stays on the MCP surface: this REST surface is read-only, with no
create/update/deprecate and no version-history sub-resource.
"""

from datetime import datetime
from typing import Any, cast

from ._read_service import (
    DEFAULT_TIMEOUT_SECONDS,
    CoolhandAPIError,
    QueryValue,
    ReadService,
    _encode_path_id,
    _pagination_from_headers,
    _window_filters,
    _with_query,
)
from .types import (
    Config,
    LlmRequestTemplateDetail,
    LlmRequestTemplateStatus,
    LlmRequestTemplateSummary,
    SearchTemplatesResponse,
)

TEMPLATES_ENDPOINT = "/api/v2/llm_request_templates"

__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "TEMPLATES_ENDPOINT",
    "CoolhandAPIError",
    "TemplateService",
    "get_template_service",
]


class TemplateService(ReadService):
    """Read the LLM request templates your logs are matched against.

    Example:
        >>> from coolhand import TemplateService
        >>> service = TemplateService(api_key="your-private-api-key")
        >>> result = service.search_templates(status="published")
        >>> detail = service.get_template(result["templates"][0]["id"])
    """

    _resource_label = "Template"

    def search_templates(
        self,
        *,
        search: str | None = None,
        workload_id: str | None = None,
        status: LlmRequestTemplateStatus | None = None,
        include_deprecated: bool | None = None,
        include_system: bool | None = None,
        include_metrics: bool | None = None,
        days_back: int | None = None,
        since: datetime | str | None = None,
        until: datetime | str | None = None,
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
            include_metrics: Add a `metrics` object (cost and performance, from the
                same SQL as the dashboard) to each template. Off by default.
            days_back: Rolling metrics window in days ending now (server default 28,
                max 365). Ignored when `since` is given.
            since: Metrics window start, inclusive. A `datetime` (naive means UTC) or an
                ISO8601 string; a string without an offset is UTC and a date alone is
                midnight UTC. Overrides `days_back`, which then comes back `None` in
                `metrics`.
            until: Metrics window end, exclusive; defaults to now. Same formats as
                `since`. With only `until`, the window is `days_back` long ending there.
            page: Page number, 1-based.
            per: Page size (default 25, max 100, both enforced server-side).

        Returns:
            A dict with `templates` (newest first) and `pagination`, the latter read
            from the response headers rather than computed from the rows returned.

        Raises:
            ValueError: If `since` or `until` is neither a `datetime` nor a string,
                before any request is made.
            CoolhandAPIError: On a non-2xx response, with `status` set — `401` for a
                missing/invalid/public key, `422` for an unrecognized `status` or an
                undecodable/foreign `workload_id`, a malformed or inverted
                `since`/`until` window or one over 365 days (only checked with
                `include_metrics`), and `504` when the `log_count`
                aggregate exceeds the server's statement timeout. A `504` is retryable:
                narrow with `workload_id`, `search` or a smaller `per` and try again.
                Also raised, with `status` left `None`, on a transport failure or a body
                that is not the JSON array this endpoint returns.
        """
        filters: dict[str, QueryValue] = {
            "search": search,
            "workload_id": workload_id,
            "status": status,
            "include_deprecated": include_deprecated,
            "include_system": include_system,
            "include_metrics": include_metrics,
            **_window_filters(days_back, since, until),
            "page": page,
            # `per_page` is accepted on the wire as an alias with the same bounds, but
            # one knob is enough and sending both invites disagreement.
            "per": per,
        }
        url = _with_query(f"{self.config['base_url']}{TEMPLATES_ENDPOINT}", filters)

        body, headers = self._get_json_array(url, "Template list")

        templates: list[LlmRequestTemplateSummary] = body
        self._log(f"Fetched {len(templates)} template(s)")
        return {
            "templates": templates,
            "pagination": _pagination_from_headers(headers, page, per),
        }

    def get_template(
        self,
        template_id: str,
        *,
        include_metrics: bool | None = None,
        days_back: int | None = None,
        since: datetime | str | None = None,
        until: datetime | str | None = None,
    ) -> LlmRequestTemplateDetail:
        """Get a single template by hashid, including both prompt patterns.

        Unlike `search_templates`, this applies no filtering beyond client ownership: a
        deprecated or system template is reachable by id with no opt-in flag, since
        inspecting one of those is the usual reason to fetch a template directly.

        Args:
            template_id: The template hashid, i.e. the `id` field from
                `search_templates`.
            include_metrics: Server default is true. Pass `False` to omit `metrics`,
                which also skips validating the window.
            days_back: Rolling window in days for `metrics`. Ignored when `since` is
                given.
            since: Metrics window start, inclusive; a `datetime` (naive means UTC) or
                an ISO8601 string. Overrides `days_back`.
            until: Metrics window end, exclusive; defaults to now. Same formats as
                `since`.

        Returns:
            The template, with `user_prompt_pattern` and `system_prompt_pattern` — the
            full untruncated regexes `search_templates` omits — present as keys even
            when null, and `metrics` unless `include_metrics=False`.

        Raises:
            ValueError: If `template_id` is blank, not a string, or a relative path
                segment, or if `since`/`until` is neither a `datetime` nor a string.
            CoolhandAPIError: On a non-2xx response, with `status` set — `404` for an
                unknown id *or* one belonging to another client (existence is not
                disclosed, so this is never a `403`), `422` for a malformed or inverted
                `since`/`until` window or one over 365 days (validated unless
                `include_metrics=False`), and
                `504` on the same `log_count`
                timeout `search_templates` describes, which fetching the "Unmatched"
                bucket by id can trip on its own. Also raised, with `status` left
                `None`, on a transport failure or a non-JSON-object body.
        """
        encoded_id = _encode_path_id(template_id, "template_id", "get_template")
        url = _with_query(
            f"{self.config['base_url']}{TEMPLATES_ENDPOINT}/{encoded_id}",
            {
                "include_metrics": include_metrics,
                **_window_filters(days_back, since, until),
            },
        )

        body, _headers = self._get_json_object(url, "Template")

        template = cast(LlmRequestTemplateDetail, body)
        self._log(f"Fetched template {template.get('id', 'unknown')}")
        return template


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
