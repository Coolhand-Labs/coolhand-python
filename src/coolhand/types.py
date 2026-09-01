"""Minimal type definitions for Coolhand."""

from typing import Any, Literal

from typing_extensions import TypedDict


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
    # HTTP timeout in seconds for the read methods on TemplateService. The write paths
    # (client.flush, create_feedback) keep their own fixed 10s and ignore this.
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


class LlmRequestTemplateDetail(LlmRequestTemplateSummary, total=False):
    """A template from `GET /api/v2/llm_request_templates/{id}`.

    Every field of `LlmRequestTemplateSummary` plus the full untruncated regexes the
    list endpoint omits.
    """

    user_prompt_pattern: str | None
    system_prompt_pattern: str | None


class SearchTemplatesResponse(TypedDict):
    """Result of `TemplateService.search_templates`."""

    templates: list[LlmRequestTemplateSummary]
    pagination: Pagination
