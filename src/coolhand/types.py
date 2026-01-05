"""Minimal type definitions for Coolhand."""

from typing import Any, Dict, Optional, Union

from typing_extensions import TypedDict


class RequestData(TypedDict, total=False):
    """HTTP request data."""

    method: str
    url: str
    headers: Dict[str, str]
    body: Optional[Union[str, bytes, Dict[str, Any]]]
    timestamp: float


class ResponseData(TypedDict, total=False):
    """HTTP response data."""

    status_code: int
    headers: Dict[str, str]
    body: Optional[Union[str, bytes, Dict[str, Any]]]
    timestamp: float
    duration: float
    is_streaming: bool


class Config(TypedDict, total=False):
    """Coolhand configuration."""

    api_key: Optional[str]
    silent: bool
    auto_submit: bool
    session_id: Optional[str]


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
    llm_request_log_id: Optional[int]
    llm_provider_unique_id: Optional[str]
    original_output: Optional[str]
    client_unique_id: Optional[str]

    # Feedback fields
    like: bool  # Required: thumbs up (True) or down (False)
    explanation: Optional[str]  # Why the response was good/bad
    revised_output: Optional[str]  # User's corrected version
    creator_unique_id: Optional[str]  # User who created the feedback


class FeedbackResponse(TypedDict, total=False):
    """Response from the feedback API."""

    id: int
    llm_request_log_id: int
    like: bool
    explanation: Optional[str]
    revised_output: Optional[str]
    llm_provider_unique_id: Optional[str]
    original_output: Optional[str]
    client_unique_id: Optional[str]
    created_at: str
    updated_at: str
