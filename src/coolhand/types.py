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
    llm_request_log_id: int | None
    llm_provider_unique_id: str | None
    original_output: str | None
    client_unique_id: str | None

    # Feedback fields
    sentiment: Literal["like", "dislike", "neutral"] | None  # Preferred over `like`
    like: bool  # Deprecated — use `sentiment` instead
    explanation: str | None  # Why the response was good/bad
    revised_output: str | None  # User's corrected version
    creator_unique_id: str | None  # User who created the feedback
    collector: str | None  # Collection method / SDK version identifier
    workload_hashid: str | None  # Associate feedback with a specific workload


class FeedbackResponse(TypedDict, total=False):
    """Response from the feedback API."""

    id: int
    llm_request_log_id: int
    sentiment: Literal["like", "dislike", "neutral"] | None
    like: bool
    explanation: str | None
    revised_output: str | None
    llm_provider_unique_id: str | None
    original_output: str | None
    client_unique_id: str | None
    workload_hashid: str | None
    created_at: str
    updated_at: str
