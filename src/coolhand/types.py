"""Minimal type definitions for Coolhand."""

from typing import Any, Dict, List, Optional, Union
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
