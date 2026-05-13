"""Feedback service for submitting user feedback on LLM responses."""

import json
import logging
import os
import warnings
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ._config import _DEFAULT_BASE_URL, _normalize_base_url, _ssl_context
from .types import Config, FeedbackData, FeedbackResponse
from .version import __version__

logger = logging.getLogger(__name__)

FEEDBACK_ENDPOINT = "/api/v2/llm_request_log_feedbacks"


class FeedbackService:
    """Service for submitting user feedback on LLM responses.

    Feedback helps improve LLM outputs by capturing user sentiment,
    corrections, and explanations about response quality.

    Example:
        >>> from coolhand import FeedbackService
        >>> service = FeedbackService(api_key="your-key")
        >>> response = service.create_feedback({
        ...     "llm_request_log_id": 12345,
        ...     "sentiment": "like",
        ...     "explanation": "Response was accurate and helpful"
        ... })
    """

    def __init__(self, config: Config | None = None, **kwargs):
        """Initialize the feedback service.

        Args:
            config: Configuration dictionary with api_key and optional settings.
            **kwargs: Override config values (api_key, silent).
        """
        self.config: Config = {
            "api_key": os.getenv("COOLHAND_API_KEY", ""),
            "base_url": os.getenv("COOLHAND_BASE_URL") or _DEFAULT_BASE_URL,
            "silent": os.getenv("COOLHAND_SILENT", "true").lower() == "true",
        }
        if config:
            self.config.update(config)
        self.config.update(kwargs)
        self.config["base_url"] = _normalize_base_url(
            self.config.get("base_url", _DEFAULT_BASE_URL)
        )

    @property
    def api_key(self) -> str:
        """Get the configured API key."""
        return self.config.get("api_key", "")

    @property
    def silent(self) -> bool:
        """Check if silent mode is enabled."""
        return self.config.get("silent", True)

    def _get_collector_string(self) -> str:
        """Get the collector identifier string."""
        return f"coolhand-python-{__version__}-manual"

    def _log(self, message: str) -> None:
        """Log a message if not in silent mode."""
        if not self.silent:
            logger.info(message)

    def create_feedback(self, feedback: FeedbackData) -> FeedbackResponse | None:
        """Submit feedback for an LLM response.

        Args:
            feedback: Feedback data. All fields are optional, but at least one
                matching field should be provided to link the feedback to a log:
                - llm_request_log_id: Coolhand log ID (exact match)
                - llm_provider_unique_id: Provider's x-request-id (exact match)
                - original_output: Original response text (fuzzy match)
                - client_unique_id: Your internal identifier

                Sentiment fields (both optional, neither required):
                - sentiment: Preferred. One of "like", "dislike", "neutral".
                - like (bool): Deprecated — use sentiment instead.

                Other optional fields:
                - explanation: Why the response was good/bad
                - revised_output: User's corrected version of the response
                - creator_unique_id: ID of the user providing feedback
                - workload_hashid: Associate feedback with a specific workload
                - collector: Override the SDK-generated collector string

        Returns:
            FeedbackResponse with created feedback details, or None on error.

        Example:
            >>> # Feedback with sentiment (preferred)
            >>> service.create_feedback({
            ...     "llm_request_log_id": 12345,
            ...     "sentiment": "dislike",
            ...     "explanation": "Response contained incorrect information",
            ...     "revised_output": "The correct answer is..."
            ... })

            >>> # Feedback with fuzzy text match
            >>> service.create_feedback({
            ...     "original_output": "The capital of France is London.",
            ...     "sentiment": "dislike",
            ...     "revised_output": "The capital of France is Paris."
            ... })
        """
        if "like" in feedback and "sentiment" not in feedback:
            warnings.warn(
                "'like' is deprecated; use 'sentiment' instead",
                DeprecationWarning,
                stacklevel=2,
            )

        # Check for at least one matching field
        matching_fields = [
            "llm_request_log_id",
            "llm_provider_unique_id",
            "original_output",
            "client_unique_id",
        ]
        has_matching_field = any(
            feedback.get(field) is not None for field in matching_fields
        )
        if not has_matching_field:
            logger.warning(
                "No matching field provided. Feedback may not be "
                "linked to an LLM request. Consider providing one "
                "of: llm_request_log_id, llm_provider_unique_id, "
                "original_output, or client_unique_id"
            )

        # Check API key
        if not self.api_key:
            logger.warning("No API key configured, feedback will not be submitted")
            return None

        # Convert deprecated like (bool) → sentiment (string) when sentiment is absent.
        # true → "like", false → "dislike". sentiment takes precedence if both are set.
        feedback_with_collector = dict(feedback)
        if (
            "like" in feedback_with_collector
            and "sentiment" not in feedback_with_collector
        ):
            feedback_with_collector["sentiment"] = (
                "like" if feedback_with_collector["like"] else "dislike"
            )
        feedback_with_collector.pop("like", None)  # always strip deprecated field
        feedback_with_collector.setdefault("collector", self._get_collector_string())

        # Build payload
        payload = {"llm_request_log_feedback": feedback_with_collector}

        # Log the converted payload so the log matches what was sent
        self._log_feedback_info(feedback_with_collector)

        # Send request
        try:
            request = Request(
                url=f"{self.config['base_url']}{FEEDBACK_ENDPOINT}",
                data=json.dumps(payload, default=str).encode("utf-8"),
                headers={
                    "X-API-Key": self.api_key,
                    "Content-Type": "application/json",
                    "User-Agent": f"coolhand-python/{__version__}",
                },
                method="POST",
            )

            with urlopen(request, context=_ssl_context, timeout=10) as resp:
                if resp.status in (200, 201):
                    response_data = json.loads(resp.read().decode("utf-8"))
                    self._log("Successfully created feedback")
                    return response_data
                else:
                    logger.warning(f"Unexpected status code: {resp.status}")
                    return None

        except HTTPError as e:
            logger.warning(f"Failed to submit feedback: HTTP {e.code} - {e.reason}")
            return None
        except URLError as e:
            logger.warning(f"Failed to submit feedback: {e.reason}")
            return None
        except Exception as e:
            logger.warning(f"Unexpected error submitting feedback: {e}")
            return None

    def _log_feedback_info(self, feedback: FeedbackData) -> None:
        """Log feedback details if not in silent mode."""
        if self.silent:
            return

        log_id = feedback.get("llm_request_log_id", "N/A")
        sentiment = feedback.get("sentiment")
        like = feedback.get("like")
        if sentiment is not None:
            sentiment_str = sentiment
        elif like is True:
            sentiment_str = "thumbs up"
        elif like is False:
            sentiment_str = "thumbs down"
        else:
            sentiment_str = "unknown"

        self._log(f"Creating feedback for LLM Request Log ID: {log_id}")
        self._log(f"Sentiment: {sentiment_str}")

        explanation = feedback.get("explanation")
        if explanation:
            truncated = (
                explanation[:100] + "..." if len(explanation) > 100 else explanation
            )
            self._log(f"Explanation: {truncated}")

        if feedback.get("revised_output"):
            self._log("Includes revised output")

        self._log(f"Sending to: {self.config['base_url']}{FEEDBACK_ENDPOINT}")


# Module-level convenience function
_default_service: FeedbackService | None = None


def get_feedback_service(config: Config | None = None, **kwargs) -> FeedbackService:
    """Get a feedback service instance.

    If no config is provided and a default service exists, returns the default.
    Otherwise creates a new service with the provided config.

    Args:
        config: Optional configuration dictionary.
        **kwargs: Override config values.

    Returns:
        FeedbackService instance.
    """
    global _default_service

    if config is None and not kwargs and _default_service is not None:
        return _default_service

    service = FeedbackService(config, **kwargs)

    if _default_service is None:
        _default_service = service

    return service


def create_feedback(feedback: FeedbackData, **kwargs) -> FeedbackResponse | None:
    """Convenience function to create feedback using default service.

    Args:
        feedback: Feedback data (see FeedbackService.create_feedback).
        **kwargs: Config overrides for the service.

    Returns:
        FeedbackResponse or None on error.
    """
    service = get_feedback_service(**kwargs)
    return service.create_feedback(feedback)
