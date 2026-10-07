"""Read-only access to the workloads endpoint.

Wraps `GET /api/v2/workloads`. It requires the client's **private** API key — the public
key is write-only on this API and is rejected exactly like an invalid one. There is no
per-workload endpoint.
"""

from datetime import datetime

from ._read_service import (
    QueryValue,
    ReadService,
    _pagination_from_headers,
    _window_filters,
    _with_query,
)
from .types import SearchWorkloadsResponse, WorkloadSummary

WORKLOADS_ENDPOINT = "/api/v2/workloads"


class WorkloadService(ReadService):
    """Read the workloads (groups of prompt templates) your logs belong to.

    Example:
        >>> from coolhand import WorkloadService
        >>> service = WorkloadService(api_key="your-private-api-key")
        >>> result = service.search_workloads(include_metrics=True, days_back=30)
        >>> result["workloads"][0]["metrics"]["total_cost"]
    """

    _resource_label = "Workload"

    def search_workloads(
        self,
        *,
        search: str | None = None,
        include_archived: bool | None = None,
        include_system: bool | None = None,
        include_templates: bool | None = None,
        include_metrics: bool | None = None,
        days_back: int | None = None,
        since: datetime | str | None = None,
        until: datetime | str | None = None,
        page: int | None = None,
        per: int | None = None,
    ) -> SearchWorkloadsResponse:
        """List workloads, optionally with cost and performance metrics.

        There is deliberately no `client_id`: the client is always derived from the
        authenticating API key and cannot be supplied by the caller.

        Args:
            search: Case-insensitive substring match against the workload name.
            include_archived: Include archived workloads. Defaults to false server-side.
            include_system: Include system workloads such as `Unmatched` and
                `Embedding Requests`. Defaults to false server-side.
            include_templates: Add each workload's active templates and their routing
                patterns as `templates`.
            include_metrics: Add a `metrics` rollup per workload across all of its
                templates; the same object `search_templates` returns per template.
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
            A dict with `workloads` (ordered by name) and `pagination`, the latter read
            from the response headers the endpoint always sends. A workload's `id` is
            its hashid, a string.

        Raises:
            ValueError: If `since` or `until` is neither a `datetime` nor a string,
                before any request is made.
            CoolhandAPIError: On a non-2xx response, with `status` set — `401` for a
                missing/invalid/public key, `422` for a bad `days_back` or a malformed,
                inverted or over-365-day `since`/`until` window (only checked with
                `include_metrics`), and `504` when an aggregate exceeds the server's
                statement timeout; a `504` is retryable, so narrow with `search` or a
                smaller `per`. Also raised, with `status` left `None`, on a transport
                failure or a body that is not the JSON array this endpoint returns.
        """
        filters: dict[str, QueryValue] = {
            "search": search,
            "include_archived": include_archived,
            "include_system": include_system,
            "include_templates": include_templates,
            "include_metrics": include_metrics,
            **_window_filters(days_back, since, until),
            "page": page,
            "per": per,
        }
        url = _with_query(f"{self.config['base_url']}{WORKLOADS_ENDPOINT}", filters)

        body, headers = self._get_json_array(url, "Workload list")

        workloads: list[WorkloadSummary] = body
        self._log(f"Fetched {len(workloads)} workload(s)")
        return {
            "workloads": workloads,
            "pagination": _pagination_from_headers(headers, page, per),
        }
