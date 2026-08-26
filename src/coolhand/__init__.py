"""
Coolhand Python SDK - Automatic monitoring for LLM API calls.

Usage:
    import coolhand  # Auto-initializes and starts monitoring

    # Or configure manually:
    coolhand.Coolhand(api_key="your-key", debug=True)
"""

import atexit
import logging

from . import copilot_interceptor, httpx_interceptor
from .client import CoolhandClient, get_instance, initialize, set_instance
from .feedback_service import FeedbackService, create_feedback, get_feedback_service
from .httpx_interceptor import DEFAULT_EXCLUDE_API_PATTERNS
from .template_service import CoolhandAPIError, TemplateService, get_template_service
from .types import (
    Config,
    FeedbackData,
    FeedbackResponse,
    LlmRequestTemplateDetail,
    LlmRequestTemplateStatus,
    LlmRequestTemplateSummary,
    Pagination,
    RequestData,
    ResponseData,
    SearchTemplatesResponse,
)
from .version import __version__

logger = logging.getLogger(__name__)


class Coolhand(CoolhandClient):
    """Main Coolhand class - monitors LLM API calls automatically."""

    def __init__(self, config=None, **kwargs):
        super().__init__(config, **kwargs)

        # Set as global instance
        set_instance(self)

        # Initialize feedback and template services with same config
        self._feedback_service = FeedbackService(self.config)
        self._template_service = TemplateService(self.config)

        # Start monitoring
        self.start_monitoring()

        # Cleanup on exit
        atexit.register(self.shutdown)

        logger.info(f"Coolhand initialized (session: {self.session_id})")

    def start_monitoring(self):
        """Start HTTP monitoring."""
        addresses = self.config.get("intercept_addresses")
        if addresses:
            httpx_interceptor.set_intercept_addresses(addresses)
        exclude_patterns = self.config.get("exclude_api_patterns")
        if exclude_patterns is not None:
            httpx_interceptor.set_exclude_api_patterns(exclude_patterns)
        httpx_interceptor.set_handler(self.log_interaction)
        httpx_interceptor.patch()
        copilot_interceptor.set_handler(self.log_interaction)
        copilot_interceptor.patch()
        logger.info("HTTP monitoring started")

    def stop_monitoring(self):
        """Stop HTTP monitoring."""
        httpx_interceptor.unpatch()
        copilot_interceptor.unpatch()
        logger.info("HTTP monitoring stopped")

    @property
    def feedback_service(self) -> FeedbackService:
        """Get the feedback service instance."""
        return self._feedback_service

    def create_feedback(self, feedback: FeedbackData) -> FeedbackResponse:
        """Submit feedback for an LLM response.

        Args:
            feedback: Feedback data. All fields are optional. Provide at least
                one matching field (llm_request_log_id, llm_provider_unique_id,
                original_output, or client_unique_id) to link the feedback to a
                log. For sentiment, prefer `sentiment` ("like"/"dislike"/"neutral")
                over the deprecated boolean `like`.

        Returns:
            FeedbackResponse with created feedback details, or None on error.

        Example:
            >>> # llm_request_log_id: hashid from a prior response (a raw
            >>> # integer FK also still works)
            >>> coolhand_instance.create_feedback({
            ...     "llm_request_log_id": "abc123def456",
            ...     "sentiment": "like",
            ...     "explanation": "Accurate and helpful response"
            ... })
        """
        return self._feedback_service.create_feedback(feedback)

    @property
    def template_service(self) -> TemplateService:
        """Get the template service instance."""
        return self._template_service

    def search_templates(
        self,
        *,
        search: str | None = None,
        workload_id: str | None = None,
        status: LlmRequestTemplateStatus | None = None,
        include_deprecated: bool | None = None,
        include_system: bool | None = None,
        page: int | None = None,
        per: int | None = None,
    ) -> SearchTemplatesResponse:
        """List the LLM request templates your logs are matched against.

        Requires the **private** API key. Search is a parameter on the list endpoint
        rather than a route of its own, so this is one method and not a list/search
        pair. The "Unmatched" / "Ignored API Calls" system buckets are hidden unless
        `include_system=True`.

        See `TemplateService.search_templates` for the full filter and error reference.

        Returns:
            A dict with `templates` (newest first) and `pagination`, the latter read
            from the response headers.

        Raises:
            CoolhandAPIError: On a non-2xx response, with the HTTP status on `status`.
                A `504` is expected and retryable rather than a bug.
        """
        return self._template_service.search_templates(
            search=search,
            workload_id=workload_id,
            status=status,
            include_deprecated=include_deprecated,
            include_system=include_system,
            page=page,
            per=per,
        )

    def get_template(self, template_id: str) -> LlmRequestTemplateDetail:
        """Get a single template by hashid, including both prompt patterns.

        Requires the **private** API key. Deprecated and system templates are reachable
        here by id with no opt-in flag, unlike the list.

        Args:
            template_id: The template hashid, i.e. the `id` field from
                `search_templates`.

        Raises:
            ValueError: If `template_id` is blank, not a string, or a relative path
                segment.
            CoolhandAPIError: On a non-2xx response, with the HTTP status on `status`
                (`404` for an unknown id or one belonging to another client).
        """
        return self._template_service.get_template(template_id)


# Module-level convenience functions
def status() -> dict:
    """Get status of global instance."""
    instance = get_instance()
    if instance:
        return instance.get_stats()
    return {"error": "Not initialized"}


def start_monitoring():
    """Start monitoring on global instance."""
    instance = get_instance()
    if instance and hasattr(instance, "start_monitoring"):
        instance.start_monitoring()


def stop_monitoring():
    """Stop monitoring on global instance."""
    instance = get_instance()
    if instance and hasattr(instance, "stop_monitoring"):
        instance.stop_monitoring()


def shutdown():
    """Shutdown global instance."""
    instance = get_instance()
    if instance:
        instance.shutdown()


def get_global_instance():
    """Get global instance (for compatibility)."""
    return get_instance()


# Auto-initialize on import
try:
    if get_instance() is None:
        _instance = Coolhand()
        logger.info("Coolhand auto-initialized with global monitoring enabled")
except Exception as e:
    logger.debug(f"Auto-initialization skipped: {e}")


__all__ = [
    "__version__",
    "Coolhand",
    "Config",
    "DEFAULT_EXCLUDE_API_PATTERNS",
    "RequestData",
    "ResponseData",
    "FeedbackData",
    "FeedbackResponse",
    "FeedbackService",
    "get_feedback_service",
    "create_feedback",
    "CoolhandAPIError",
    "LlmRequestTemplateDetail",
    "LlmRequestTemplateStatus",
    "LlmRequestTemplateSummary",
    "Pagination",
    "SearchTemplatesResponse",
    "TemplateService",
    "get_template_service",
    "initialize",
    "get_instance",
    "get_global_instance",
    "status",
    "start_monitoring",
    "stop_monitoring",
    "shutdown",
]
