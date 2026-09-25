"""Link feedback to an optimization as supporting evidence.

Wraps `POST /api/v2/optimizations/{optimization_id}/feedback_links` (single and bulk
modes share that one route; the request body picks the mode) and
`DELETE /api/v2/optimizations/{optimization_id}/feedback_links/{id}`. All three
methods require the client's **private** API key; the public key gets a `401`.
"""

import json
import logging
import os
from typing import Any, cast
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request

from ._config import _DEFAULT_BASE_URL, _build_opener, _normalize_base_url
from .template_service import DEFAULT_TIMEOUT_SECONDS, CoolhandAPIError, _error_body
from .types import BulkLinkFeedbackResult, Config, OptimizationFeedbackLink
from .version import __version__

logger = logging.getLogger(__name__)

OPTIMIZATIONS_ENDPOINT = "/api/v2/optimizations"

# Server-side cap on `feedback_ids` per call (more is a 422).
BULK_LINK_BATCH_SIZE = 100

_MAX_ERROR_BODY_CHARS = 2000


def _encode_id(value: str, message: str) -> str:
    """URL-encode a hashid, rejecting values that would retarget the request.

    `quote` leaves `.` unescaped, so `.` and `..` would be resolved away as path
    segments and silently hit a different route.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError(message)
    if value.strip() in {".", ".."}:
        raise ValueError(f"{message} (and not a relative path segment)")
    return quote(value, safe="")


def _check_feedback_id(value: Any, message: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(message)


class FeedbackLinkService:
    """Attach feedback to an optimization as evidence.

    Example:
        >>> from coolhand import FeedbackLinkService
        >>> service = FeedbackLinkService(api_key="your-private-api-key")
        >>> link = service.link_feedback("optimizationHashid", "feedbackHashid")
        >>> service.unlink_feedback("optimizationHashid", link["id"])
    """

    def __init__(self, config: Config | None = None, **kwargs: Any) -> None:
        """Initialize the service.

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
        self._opener = _build_opener()

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
        """Get the HTTP timeout, in seconds."""
        return self.config.get("timeout", DEFAULT_TIMEOUT_SECONDS)

    def link_feedback(
        self,
        optimization_id: str,
        feedback_id: str,
        *,
        note: str | None = None,
    ) -> OptimizationFeedbackLink:
        """Link one feedback to an optimization.

        Args:
            optimization_id: Optimization hashid.
            feedback_id: Feedback hashid.
            note: Optional note explaining the link.

        Returns:
            The created link; its `id` is what `unlink_feedback` takes.

        Raises:
            ValueError: If an id is blank, not a string, or a relative path segment.
            CoolhandAPIError: On a non-2xx response, with `status` set — `422` if the
                feedback is already linked or `note` is too long, `404` for an unknown
                optimization or feedback (or one belonging to another client), `401`
                for a missing/public key. `status` is `None` on a transport failure or
                a non-JSON-object body.
        """
        _check_feedback_id(
            feedback_id, "link_feedback: feedback_id must be a non-empty string"
        )
        payload: dict[str, Any] = {"feedback_id": feedback_id}
        if note is not None:
            payload["note"] = note
        link = self._post_links(optimization_id, "link_feedback", payload)
        self._log(f"Linked feedback {feedback_id} to optimization {optimization_id}")
        return cast(OptimizationFeedbackLink, link)

    def bulk_link_feedback(
        self,
        optimization_id: str,
        feedback_ids: list[str],
        *,
        note: str | None = None,
    ) -> BulkLinkFeedbackResult:
        """Link many feedbacks to an optimization.

        The server accepts at most 100 ids per request, so longer lists are split into
        batches of 100 and the results merged: counts are summed and `not_found`
        concatenated. Ids are sent as given, so a duplicate across batches counts as
        `already_linked`.

        Already-linked ids are counted in `already_linked`, not treated as errors, so a
        failed run can simply be repeated. If a later batch raises, earlier batches
        have already been applied.

        Args:
            optimization_id: Optimization hashid.
            feedback_ids: One or more feedback hashids.
            note: Optional note, applied to every link created.

        Returns:
            `linked`, `already_linked` and `errored` counts, and `not_found` — the
            unknown, malformed and other-client ids.

        Raises:
            ValueError: If `feedback_ids` is empty or contains a blank id, or
                `optimization_id` is blank. Raised before any request is sent.
            CoolhandAPIError: On a non-2xx response, with `status` set — `404` for an
                unknown optimization, `422` for invalid input or a too-long `note`,
                `401` for a missing/public key.
        """
        if not isinstance(feedback_ids, (list, tuple)) or len(feedback_ids) == 0:
            raise ValueError(
                "bulk_link_feedback: feedback_ids must be a non-empty list"
            )
        for feedback_id in feedback_ids:
            _check_feedback_id(
                feedback_id,
                "bulk_link_feedback: every feedback id must be a non-empty string",
            )
        # Validated up front so a bad optimization_id cannot fail after batch one.
        _encode_id(
            optimization_id,
            "bulk_link_feedback: optimization_id must be a non-empty string",
        )

        total: BulkLinkFeedbackResult = {
            "linked": 0,
            "already_linked": 0,
            "errored": 0,
            "not_found": [],
        }
        for start in range(0, len(feedback_ids), BULK_LINK_BATCH_SIZE):
            payload: dict[str, Any] = {
                "feedback_ids": list(feedback_ids[start : start + BULK_LINK_BATCH_SIZE])
            }
            if note is not None:
                payload["note"] = note
            result = cast(
                BulkLinkFeedbackResult,
                self._post_links(optimization_id, "bulk_link_feedback", payload),
            )
            total["linked"] += result["linked"]
            total["already_linked"] += result["already_linked"]
            total["errored"] += result["errored"]
            total["not_found"].extend(result["not_found"])
        self._log(
            f"Bulk-linked {len(feedback_ids)} feedback(s) to optimization "
            f"{optimization_id}"
        )
        return total

    def unlink_feedback(self, optimization_id: str, link_id: str) -> None:
        """Remove a feedback link from an optimization.

        Args:
            optimization_id: Optimization hashid.
            link_id: The link's hashid — the `id` returned by `link_feedback`, not the
                feedback id.

        Raises:
            ValueError: If an id is blank, not a string, or a relative path segment.
            CoolhandAPIError: On a non-2xx response, with `status` set — `404` if the
                link does not exist, `401` for a missing/public key.
        """
        encoded_link = _encode_id(
            link_id, "unlink_feedback: link_id must be a non-empty string"
        )
        url = f"{self._links_url(optimization_id, 'unlink_feedback')}/{encoded_link}"
        self._send(Request(url=url, headers=self._headers(), method="DELETE"))
        self._log(f"Unlinked {link_id} from optimization {optimization_id}")

    def _links_url(self, optimization_id: str, method: str) -> str:
        encoded = _encode_id(
            optimization_id, f"{method}: optimization_id must be a non-empty string"
        )
        return (
            f"{self.config['base_url']}{OPTIMIZATIONS_ENDPOINT}/{encoded}"
            "/feedback_links"
        )

    def _headers(self, *, json_body: bool = False) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "X-API-Key": self.api_key,
            "User-Agent": f"coolhand-python/{__version__}",
        }
        if json_body:
            headers["Content-Type"] = "application/json"
        return headers

    def _post_links(
        self, optimization_id: str, method: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        request = Request(
            url=self._links_url(optimization_id, method),
            data=json.dumps(payload).encode("utf-8"),
            headers=self._headers(json_body=True),
            method="POST",
        )
        raw = self._send(request)
        try:
            body = json.loads(raw)
        except ValueError as error:
            raise CoolhandAPIError(
                "Feedback link response was not valid JSON: "
                f"{raw[:_MAX_ERROR_BODY_CHARS]}"
            ) from error
        if not isinstance(body, dict):
            raise CoolhandAPIError(
                "Feedback link response was not a JSON object: "
                f"{str(body)[:_MAX_ERROR_BODY_CHARS]}"
            )
        return body

    def _send(self, request: Request) -> str:
        """Send `request`; return the body, raising `CoolhandAPIError` on failure."""
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                return response.read().decode("utf-8")
        except HTTPError as error:
            raise CoolhandAPIError(
                f"Feedback link request failed ({error.code}): {_error_body(error)}",
                status=error.code,
            ) from error
        except URLError as error:
            raise CoolhandAPIError(
                f"Feedback link request failed: {error.reason}"
            ) from error

    def _log(self, message: str) -> None:
        if not self.silent:
            logger.info(message)
