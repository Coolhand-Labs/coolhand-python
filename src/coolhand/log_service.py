"""Read-only access to the LLM request log endpoints.

Wraps `GET /api/v2/llm_request_logs` (search) and `GET /api/v2/llm_request_logs/{id}`
(show). Both require the client's **private** API key — the public key used to submit
logs is write-only and is rejected here. Submitting logs is `CoolhandClient`'s job;
this service only reads them back.
"""

from datetime import datetime
from email.message import Message
from typing import cast

from ._read_service import (
    _MAX_ERROR_BODY_CHARS,
    CoolhandAPIError,
    QueryValue,
    ReadService,
    _encode_path_id,
    _page_and_per,
    _parse_header_int,
    _window_filters,
    _with_query,
)
from .types import (
    LlmRequestLogContent,
    LlmRequestLogOrder,
    LlmRequestLogSummary,
    LogPagination,
    SearchLogsResponse,
)

LOGS_ENDPOINT = "/api/v2/llm_request_logs"


def _log_pagination(
    headers: Message,
    logs: list[LlmRequestLogSummary],
    requested_page: int | None,
    requested_per: int | None,
) -> LogPagination:
    """Build `LogPagination`; totals stay `None` when the server did not send them."""
    current_page, per_page = _page_and_per(headers, requested_page, requested_per)

    total_count: int | None = None
    total_pages: int | None = None
    if headers.get("X-Total-Count") is not None:
        total_count = _parse_header_int(headers.get("X-Total-Count"), 0)
        fallback_pages = -(-total_count // per_page) if per_page > 0 else 0
        total_pages = _parse_header_int(headers.get("X-Total-Pages"), fallback_pages)

    if total_pages is not None:
        has_next_page = current_page < total_pages
    else:
        has_next_page = per_page > 0 and len(logs) >= per_page

    return {
        "current_page": current_page,
        "per_page": per_page,
        "total_count": total_count,
        "total_pages": total_pages,
        "has_next_page": has_next_page,
        "has_prev_page": current_page > 1,
    }


class LogService(ReadService):
    """Read back the LLM request logs Coolhand has collected.

    Example:
        >>> from coolhand import LogService
        >>> service = LogService(api_key="your-private-api-key")
        >>> result = service.search_logs(min_cost=0.5, order="cost_desc", days_back=7)
        >>> content = service.get_log(result["logs"][0]["id"])
    """

    _resource_label = "Log"

    def search_logs(
        self,
        *,
        template_id: str | None = None,
        workload_id: str | None = None,
        system_prompt_contains: str | None = None,
        user_prompt_contains: str | None = None,
        model: str | None = None,
        source_api: str | None = None,
        source_api_result: str | None = None,
        project_path: str | None = None,
        unmatched_only: bool | None = None,
        days_back: int | None = None,
        since: datetime | str | None = None,
        until: datetime | str | None = None,
        min_cost: float | None = None,
        order: LlmRequestLogOrder | None = None,
        include_prompts: bool | None = None,
        sort: str | None = None,
        include_total: bool | None = None,
        page: int | None = None,
        per: int | None = None,
    ) -> SearchLogsResponse:
        """Search logs by named filters.

        There is deliberately no `client_id`: the client is always derived from the
        authenticating API key. `sort` is the only raw Ransack passthrough (`q[s]`).

        Args:
            template_id: Template hashid.
            workload_id: Workload hashid; matches every template in it.
            system_prompt_contains: Case-insensitive substring of the system prompt.
            user_prompt_contains: Case-insensitive substring of the user prompt.
            model: Exact model name.
            source_api: Source API, e.g. "openai" or "anthropic".
            source_api_result: Result status, e.g. "success" or "failed".
            project_path: Exact match against `metadata.project_path`.
            unmatched_only: Only logs with no assigned template.
            days_back: Logs created in the last N days. Unrestricted when omitted.
                Ignored when `since` or `until` is given.
            since: Lower bound on `created_at`, inclusive. A `datetime` (naive means
                UTC) or an ISO8601 string; a string without an offset is UTC and a date
                alone is midnight UTC.
            until: Upper bound on `created_at`, exclusive. Same formats as `since`.
            min_cost: Only logs whose per-log `cost` (USD) is at least this; logs that
                cannot be priced are excluded. `0` is sent, not dropped.
            order: `"cost_desc"` sorts by per-log `cost`, highest first, replacing
                `sort`; priceable logs only. Any other value is a 422.
            include_prompts: Add `system_prompt` / `user_prompt`, truncated to 500
                characters, to each log.
            sort: Ransack sort expression such as `"created_at desc"`, sent as `q[s]`.
                Defaults to newest first server-side.
            include_total: Ask the server for `X-Total-Count` / `X-Total-Pages`, at the
                cost of a `COUNT(*)`. Leave unset for frequent polling.
            page: Page number, 1-based.
            per: Page size (default 25, max 100, both enforced server-side).

        Returns:
            A dict with `logs` (the bare array the endpoint returns) and `pagination`.
            Every log carries `cost` (USD), `None` when it has no tokens or its model
            has no pricing. Without `include_total`, `pagination["total_count"]` and
            `["total_pages"]` are `None`.

        Raises:
            ValueError: If `since` or `until` is neither a `datetime` nor a string,
                before any request is made.
            CoolhandAPIError: On a non-2xx response, with `status` set — `401` for a
                missing/invalid/public key, `422` for a bad `template_id` /
                `workload_id` / `days_back`, a malformed or inverted `since`/`until`, a
                negative or non-numeric `min_cost`, or an unknown `order`, and `504`
                when the search exceeds the server's statement timeout (combine
                `min_cost` / `order` with `since` or `days_back` to avoid it). Also
                raised, with `status` left `None`, on a transport failure or a body
                that is not the JSON array this endpoint returns.
        """
        filters: dict[str, QueryValue] = {
            "template_id": template_id,
            "workload_id": workload_id,
            "system_prompt_contains": system_prompt_contains,
            "user_prompt_contains": user_prompt_contains,
            "model": model,
            "source_api": source_api,
            "source_api_result": source_api_result,
            "project_path": project_path,
            "unmatched_only": unmatched_only,
            **_window_filters(days_back, since, until),
            "min_cost": min_cost,
            "order": order,
            "include_prompts": include_prompts,
            "q[s]": sort,
            "include_total": include_total,
            "page": page,
            "per": per,
        }
        url = _with_query(f"{self.config['base_url']}{LOGS_ENDPOINT}", filters)

        body, headers = self._get_json(url)
        if not isinstance(body, list):
            raise CoolhandAPIError(
                "Log list response was not a JSON array: "
                f"{str(body)[:_MAX_ERROR_BODY_CHARS]}"
            )

        logs: list[LlmRequestLogSummary] = body
        self._log(f"Fetched {len(logs)} log(s)")
        return {
            "logs": logs,
            "pagination": _log_pagination(headers, logs, page, per),
        }

    def get_log(
        self,
        log_id: str,
        *,
        section: str | None = None,
        max_chars: int | None = None,
        search_query: str | None = None,
        include_thinking: bool | None = None,
    ) -> LlmRequestLogContent:
        """Get one log's content and cost by hashid.

        Args:
            log_id: The log hashid, i.e. the `id` field from `search_logs`.
            section: "full", "beginning" or "end" (default "full"). Only takes effect
                together with `max_chars`.
            max_chars: Maximum characters per content field.
            search_query: Return up to 5 matching snippets per field instead of the
                content. Mutually exclusive in practice with `section` / `max_chars`.
            include_thinking: Add `thinking_response`, an array of thinking blocks.

        Returns:
            The log, with `cost` and `cost_breakdown` (both `None` when it cannot be
            priced) alongside the content fields, or `matches` / `search_query` when
            `search_query` was given.

        Raises:
            ValueError: If `log_id` is blank, not a string, or a relative path segment,
                or if `search_query` is given but blank (the server would silently
                return the content shape instead of a search result).
            CoolhandAPIError: On a non-2xx response, with `status` set — `401` for a
                missing/invalid/public key, `404` for an unknown id, another client's,
                or an internally generated record, and `422` for a non-positive
                `max_chars`. Also raised, with `status` left `None`, on a transport
                failure or a non-JSON-object body.
        """
        encoded_id = _encode_path_id(log_id, "log_id", "get_log")
        if search_query is not None and (
            not isinstance(search_query, str) or not search_query.strip()
        ):
            raise ValueError("get_log: search_query must be a non-empty string")

        filters: dict[str, QueryValue] = {
            "section": section,
            "max_chars": max_chars,
            "search_query": search_query,
            "include_thinking": include_thinking,
        }
        url = _with_query(
            f"{self.config['base_url']}{LOGS_ENDPOINT}/{encoded_id}", filters
        )

        body, _headers = self._get_json(url)
        if not isinstance(body, dict):
            raise CoolhandAPIError(
                "Log response was not a JSON object: "
                f"{str(body)[:_MAX_ERROR_BODY_CHARS]}"
            )

        log = cast(LlmRequestLogContent, body)
        self._log(f"Fetched log {log.get('id', 'unknown')}")
        return log
