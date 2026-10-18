"""Create or update workloads by their stable key.

Wraps `PUT /api/v2/workloads/by_key/{key}`. Requires the client's **private** API key.
Listing and searching workloads is not covered here.
"""

import asyncio
import logging
import os
from typing import Any

from ._config import (
    _DEFAULT_BASE_URL,
    _build_opener,
    _normalize_base_url,
    _resolve_request_timeout,
)
from .declared_context import (
    MAX_DESCRIPTION_LENGTH,
    optional_reporter,
    optional_text,
)
from .template_service import (
    _by_key_url,
    _put_json,
)
from .types import Config

logger = logging.getLogger(__name__)

WORKLOADS_ENDPOINT = "/api/v2/workloads"


class WorkloadService:
    """Create or rename the workloads your LLM calls are attributed to.

    Example:
        >>> from coolhand import WorkloadService
        >>> service = WorkloadService(api_key="your-private-api-key")
        >>> service.upsert("Parser#call", name="Parser#call", description="Squishes")
    """

    def __init__(self, config: Config | None = None, **kwargs: Any) -> None:
        """Initialize the workload service.

        Args:
            config: Configuration dictionary. `api_key` must be the **private** key.
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
        """The configured API key, stripped of surrounding whitespace."""
        return (self.config.get("api_key") or "").strip()

    @property
    def silent(self) -> bool:
        """Whether silent mode is enabled."""
        return self.config.get("silent", True)

    @property
    def timeout(self) -> float:
        """The HTTP timeout, in seconds."""
        return self.config["request_timeout"]

    def _log(self, message: str) -> None:
        """Log a message if not in silent mode."""
        if not self.silent:
            logger.info(message)

    def upsert(
        self,
        key: str,
        *,
        name: str | None = None,
        description: str | None = None,
        reporter: str | None = None,
    ) -> dict[str, Any]:
        """Create or update a workload by its stable key.

        Only the fields you pass are sent, so an update changes only those. The key is
        immutable; a new `name` renames the workload.

        Args:
            key: Stable workload key, e.g. `"Parser#call"` (<= 255 chars, no control
                characters, no leading/trailing whitespace or `/`, no `//`).
            name: Display name.
            description: Free text, at most 1000 characters.
            reporter: Name of the system making the declaration.

        Returns:
            The workload record as returned by the server (`201` created, `200`
            updated).

        Raises:
            ValueError: If `key` is invalid or contains a `.`/`..`/empty path segment,
                or a field is not a string or `description` is too long. Raised before
                any request is made.
            CoolhandAPIError: On a non-2xx response, with `status` set — `401` for a
                missing/invalid/public key, `422` for a taken name.
        """
        label = "upsert_workload"
        url = _by_key_url(self.config["base_url"], WORKLOADS_ENDPOINT, key, label)

        payload: dict[str, Any] = {}
        if name is not None:
            payload["name"] = optional_text(name, f"{label}: name")
        if description is not None:
            payload["description"] = optional_text(
                description, f"{label}: description", MAX_DESCRIPTION_LENGTH
            )
        if reporter is not None:
            payload["reporter"] = optional_reporter(reporter, f"{label}: reporter")

        status, body = _put_json(
            self._opener,
            url,
            payload,
            api_key=self.api_key,
            timeout=self.timeout,
            label="Workload",
        )
        self._log(f"{'Created' if status == 201 else 'Updated'} workload {key}")
        return body

    async def aupsert(
        self,
        key: str,
        *,
        name: str | None = None,
        description: str | None = None,
        reporter: str | None = None,
    ) -> dict[str, Any]:
        """Async `upsert`, run on a worker thread via `asyncio.to_thread`."""
        return await asyncio.to_thread(
            self.upsert,
            key,
            name=name,
            description=description,
            reporter=reporter,
        )
