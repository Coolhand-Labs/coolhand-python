"""Minimal type definitions for Coolhand."""

from typing import Any, Literal

from typing_extensions import NotRequired, TypedDict


class RequestData(TypedDict, total=False):
    """HTTP request data."""

    method: str
    url: str
    headers: dict[str, str]
    body: str | bytes | dict[str, Any] | None
    timestamp: float


class ResponseData(TypedDict, total=False):
    """HTTP response data."""

    status_code: int
    headers: dict[str, str]
    body: str | bytes | dict[str, Any] | None
    timestamp: float
    duration: float
    is_streaming: bool


class Config(TypedDict, total=False):
    """Coolhand configuration."""

    api_key: str | None
    base_url: str
    silent: bool
    auto_submit: bool
    session_id: str | None
    intercept_addresses: list[str] | None
    exclude_api_patterns: list[str] | None
    # HTTP timeout in seconds for the read methods (TemplateService, WorkloadService,
    # LogService). The write paths (CoolhandClient._send_one, FeedbackService._submit)
    # keep their own fixed _config._WRITE_TIMEOUT_SECONDS and ignore this.
    timeout: float


class FeedbackData(TypedDict, total=False):
    """Feedback data for LLM responses.

    At least one of the following must be provided to match the feedback
    to an LLM request log:
    - llm_request_log_id: Exact match via Coolhand log ID
    - llm_provider_unique_id: Exact match via provider's x-request-id
    - original_output: Fuzzy match via the original LLM response text
    - client_unique_id: Match via your internal identifier
    """

    # Matching fields (at least one required)
    # Either the raw integer FK or a hashid string (e.g. from a prior response's
    # llm_request_log_id) — the server accepts both on write.
    llm_request_log_id: int | str | None
    llm_provider_unique_id: str | None
    original_output: str | None
    client_unique_id: str | None

    # Feedback fields
    sentiment: Literal["like", "dislike", "neutral"] | None  # Preferred over `like`
    like: bool  # Deprecated — use `sentiment` instead
    explanation: str | None  # Why the response was good/bad
    revised_output: str | None  # User's corrected version
    creator_unique_id: str | None  # User who created the feedback
    creator_type: (
        Literal["human", "agent", "unknown"] | None
    )  # What kind of creator submitted the feedback
    collector: str | None  # Collection method / SDK version identifier
    workload_hashid: str | None  # Associate feedback with a specific workload


class FeedbackResponse(TypedDict, total=False):
    """Response from the feedback API."""

    id: str
    llm_request_log_id: str | None
    sentiment: Literal["like", "dislike", "neutral"] | None
    like: bool
    explanation: str | None
    revised_output: str | None
    llm_provider_unique_id: str | None
    original_output: str | None
    client_unique_id: str | None
    creator_type: Literal["human", "agent", "unknown"] | None
    workload_id: str | None
    created_at: str
    updated_at: str


# The `status` values GET /api/v2/llm_request_templates accepts as a *filter*. The API
# definition enumerates them on the query parameter and returns 422 for anything else
# non-empty. Deliberately not reused for the `status` field on the response types below:
# the definition types that field as a plain nullable string, and narrowing it here
# would break callers the day the server adds a fourth status.
LlmRequestTemplateStatus = Literal["draft", "published", "failure"]


class Pagination(TypedDict):
    """Pagination metadata for a paginated list response.

    Built from the `X-Page` / `X-Per-Page` / `X-Total-Count` / `X-Total-Pages` response
    headers, never from the length of the returned page.
    """

    current_page: int
    per_page: int
    total_count: int
    total_pages: int
    has_next_page: bool
    has_prev_page: bool


class LlmMetrics(TypedDict):
    """The `metrics` object on a workload or template.

    Computed by the same SQL as the dashboard, so tiered pricing, cached-token discounts
    and reasoning tokens are applied. Everything except `first_request_at` /
    `last_request_at` (lifetime) and `sentiment_score` / `revision_score` (all-time)
    covers the resolved `since`..`until` window over non-failed, directly-collected
    client logs.
    """

    days_back: int | None  # None when an explicit `since` defined the window
    since: str  # Resolved window start, ISO-8601 UTC
    until: str  # Resolved window end (exclusive), ISO-8601 UTC
    request_count: int
    failure_count: int  # Failed logs in the window
    error_rate: float | None  # None when `failure_count` is 0
    error_rate_change: float | None
    avg_cost_per_request: float | None
    total_cost: float | None  # USD; None when no log in the window could be priced
    # Non-failed logs with tokens whose model has pricing: the logs `total_cost` covers.
    priced_request_count: int
    # Priced logs that crossed their model's input-token pricing tier.
    long_context_request_count: int
    # Raw token columns summed over non-failed logs, not cache-adjusted.
    total_input_tokens: int
    total_output_tokens: int
    avg_input_tokens: int | None
    avg_output_tokens: int | None
    avg_latency_ms: float | None
    correctness_score: float | None
    sentiment_score: float | None
    revision_score: float | None  # 0-100
    first_request_at: str | None
    last_request_at: str | None


class LlmRequestTemplateSummary(TypedDict, total=False):
    """A template as rendered by `GET /api/v2/llm_request_templates`.

    Prompt patterns are not here — they come from `get_template` only.
    """

    id: str  # Hashid, never the integer primary key
    name: str  # Never null (NOT NULL column), but may be blank on a draft
    status: str | None  # "draft", "published" or "failure"
    version: str | None
    # Known values: "chat", "user_prompt", "user_prompt_with_system_prompt",
    # "embedding", "other". Left a plain string because the API definition does not
    # enumerate it on the response.
    group: str | None
    workload_id: str  # Workload hashid; never null
    workload_name: str  # Never null
    system_template: bool  # True for the "Unmatched" / "Ignored API Calls" buckets
    deprecated_at: str | None  # ISO-8601 UTC; non-null means superseded
    # Directly-collected client logs only — the same records
    # GET /api/v2/llm_request_logs?template_id=... returns. Excludes evals, bakeoff
    # comparisons and synthetic logs, which is why it can be lower than the count the
    # `search_templates` MCP tool reports.
    log_count: int
    created_at: str  # ISO-8601 UTC
    updated_at: str  # ISO-8601 UTC
    metrics: LlmMetrics  # Only with `include_metrics=True`


class LlmRequestTemplateDetail(LlmRequestTemplateSummary, total=False):
    """A template from `GET /api/v2/llm_request_templates/{id}`.

    Every field of `LlmRequestTemplateSummary` plus the full untruncated regexes the
    list endpoint omits. `metrics` is present unless `include_metrics=False` was
    passed, unlike on the list, where it is opt-in.
    """

    user_prompt_pattern: str | None
    system_prompt_pattern: str | None


class SearchTemplatesResponse(TypedDict):
    """Result of `TemplateService.search_templates`."""

    templates: list[LlmRequestTemplateSummary]
    pagination: Pagination


class WorkloadTemplate(TypedDict, total=False):
    """One of a workload's active templates, as embedded by `include_templates`."""

    id: str  # Hashid
    name: str
    status: str | None
    user_prompt_pattern: str | None
    system_prompt_pattern: str | None


class WorkloadSummary(TypedDict, total=False):
    """A workload as rendered by `GET /api/v2/workloads`."""

    id: str  # The workload hashid (a string, never the integer primary key)
    name: str
    description: str | None
    archived: bool
    system: bool
    merged: bool
    template_count: int  # Active templates
    draft_template_count: int
    # Every log attached to any of the workload's templates, all generators, so it can
    # exceed the sum of the templates' own `log_count`, which counts only
    # directly-collected client logs.
    log_count: int
    last_activity: str | None  # ISO-8601 UTC
    templates: list[WorkloadTemplate]  # Only with `include_templates=True`
    metrics: LlmMetrics  # Only with `include_metrics=True`


class SearchWorkloadsResponse(TypedDict):
    """Result of `WorkloadService.search_workloads`."""

    workloads: list[WorkloadSummary]
    pagination: Pagination


# The only `order` value GET /api/v2/llm_request_logs accepts; anything else is a 422.
LlmRequestLogOrder = Literal["cost_desc"]


class LlmRequestLogCostBreakdown(TypedDict):
    """Per-component USD cost of one log, from `GET /api/v2/llm_request_logs/{id}`."""

    total_cost: float
    input_cost: float
    output_cost: float
    cached_input_cost: float
    cache_creation_input_cost: float
    reasoning_output_cost: float


class LlmRequestLogSummary(TypedDict):
    """A log as rendered by `GET /api/v2/llm_request_logs`."""

    id: str  # Hashid
    collector: str | None
    source_api: str | None
    source_application: str | None
    metadata: dict[str, Any]
    source_api_result: str | None
    model: str | None
    template_id: str | None  # Hashid; None when the log is unmatched
    template_name: str | None
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int | None
    created_at: str  # ISO-8601 UTC
    updated_at: str  # ISO-8601 UTC
    ingest_evidence: dict[str, Any]
    # USD, priced by the same SQL as the dashboard. None when the log has no tokens or
    # its model has no pricing.
    cost: float | None
    # Only with `include_prompts=True`, truncated to 500 characters.
    system_prompt: NotRequired[str | None]
    user_prompt: NotRequired[str | None]


class LogPagination(TypedDict):
    """Pagination metadata for `LogService.search_logs`.

    `X-Total-Count` / `X-Total-Pages` are only sent when `include_total=True`, because
    they cost a `COUNT(*)` server-side. Without them `total_count` and `total_pages` are
    `None` rather than a guess, and `has_next_page` is true when the page came back full
    (there may be one more, possibly empty, page).
    """

    current_page: int
    per_page: int
    total_count: int | None
    total_pages: int | None
    has_next_page: bool
    has_prev_page: bool


class SearchLogsResponse(TypedDict):
    """Result of `LogService.search_logs`."""

    logs: list[LlmRequestLogSummary]
    pagination: LogPagination


class LlmRequestLogContent(TypedDict, total=False):
    """A log from `GET /api/v2/llm_request_logs/{id}`.

    Without `search_query` the content fields (`system_prompt`, `user_prompt`, `output`,
    plus `truncated` / `total_chars` on a partial fetch) are present; with it, `matches`
    and `search_query` come back instead.
    """

    id: str  # Hashid
    url: str
    collector: str | None
    metadata: dict[str, Any]
    model: str | None
    source_api: str | None
    source_application: str | None
    source_api_result: str | None
    template_id: str | None
    template_name: str | None
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int | None
    cost: float | None
    cost_breakdown: LlmRequestLogCostBreakdown | None
    created_at: str
    updated_at: str
    ingest_evidence: dict[str, Any]
    system_prompt: str | None
    user_prompt: str | None
    output: str | None
    truncated: bool
    total_chars: dict[str, int]  # Keys: system_prompt, user_prompt, output
    search_query: str
    matches: dict[str, list[str]]  # Keys: system_prompt, user_prompt, output
    # Only with `include_thinking=True`; the server returns an array of blocks.
    thinking_response: list[str] | None
