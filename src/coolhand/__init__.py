"""
Coolhand Python SDK - Automatic monitoring for LLM API calls.

Usage:
    import coolhand  # Auto-initializes and starts monitoring

    # Or configure manually:
    coolhand.Coolhand(api_key="your-key", debug=True)
"""

import logging
from datetime import datetime

from . import copilot_interceptor, httpx_interceptor
from .client import CoolhandClient, get_instance, initialize, set_instance
from .feedback_service import (
    FeedbackService,
    acreate_feedback,
    create_feedback,
    get_feedback_service,
)
from .httpx_interceptor import DEFAULT_EXCLUDE_API_PATTERNS, DEFAULT_INTERCEPT_ADDRESSES
from .log_service import LogService
from .template_service import CoolhandAPIError, TemplateService, get_template_service
from .types import (
    Config,
    FeedbackData,
    FeedbackResponse,
    LlmMetrics,
    LlmRequestLogContent,
    LlmRequestLogCostBreakdown,
    LlmRequestLogOrder,
    LlmRequestLogSummary,
    LlmRequestTemplateDetail,
    LlmRequestTemplateStatus,
    LlmRequestTemplateSummary,
    LogPagination,
    Pagination,
    RequestData,
    ResponseData,
    SearchLogsResponse,
    SearchTemplatesResponse,
    SearchWorkloadsResponse,
    WorkloadSummary,
    WorkloadTemplate,
)
from .version import __version__
from .workload_service import WorkloadService

logger = logging.getLogger(__name__)


class Coolhand(CoolhandClient):
    """Main Coolhand class - monitors LLM API calls automatically."""

    def __init__(self, config=None, **kwargs):
        super().__init__(config, **kwargs)

        # Set as global instance
        set_instance(self)

        # Initialize feedback and read services with same config
        self._feedback_service = FeedbackService(self.config)
        self._template_service = TemplateService(self.config)
        self._workload_service = WorkloadService(self.config)
        self._log_service = LogService(self.config)

        # Start monitoring
        self.start_monitoring()

        # Note: shutdown() is already registered with atexit by
        # CoolhandClient.__init__ (via super().__init__() above).

        logger.info(f"Coolhand initialized (session: {self.session_id})")

    def start_monitoring(self):
        """Start HTTP monitoring."""
        # Set unconditionally. These are module-level globals shared by every
        # instance in the process, so an omitted intercept_addresses key must
        # actively restore the defaults (None) rather than inherit a previous
        # instance's override. exclude_api_patterns is always present in the
        # default config, so it is passed through for symmetry.
        httpx_interceptor.set_intercept_addresses(
            self.config.get("intercept_addresses")
        )
        httpx_interceptor.set_exclude_api_patterns(
            self.config.get("exclude_api_patterns")
        )
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

    def create_feedback(self, feedback: FeedbackData) -> FeedbackResponse | None:
        """Submit feedback for an LLM response.

        Args:
            feedback: Feedback data. All fields are optional. Provide at least
                one matching field (llm_request_log_id, llm_provider_unique_id,
                original_output, or client_unique_id) to link the feedback to a
                log. For sentiment, prefer `sentiment` ("like"/"dislike"/"neutral")
                over the deprecated boolean `like`.

        Returns:
            FeedbackResponse with created feedback details, or None on error
            (including the rare case of a 2xx response with no body).

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

    async def acreate_feedback(self, feedback: FeedbackData) -> FeedbackResponse | None:
        """Async equivalent of `create_feedback`.

        Runs the blocking HTTP call on a worker thread via `asyncio.to_thread`
        so it never blocks the caller's event loop. See `create_feedback` for
        argument and return value details.
        """
        return await self._feedback_service.acreate_feedback(feedback)

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
        include_metrics: bool | None = None,
        days_back: int | None = None,
        since: datetime | str | None = None,
        until: datetime | str | None = None,
        page: int | None = None,
        per: int | None = None,
    ) -> SearchTemplatesResponse:
        """List the LLM request templates your logs are matched against.

        Requires the **private** API key. Search is a parameter on the list endpoint
        rather than a route of its own, so this is one method and not a list/search
        pair. The "Unmatched" / "Ignored API Calls" system buckets are hidden unless
        `include_system=True`. `include_metrics=True` adds a `metrics` object per
        template over a rolling `days_back` window or an explicit `since` / `until`
        one (`datetime` or ISO8601 string).

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
            include_metrics=include_metrics,
            days_back=days_back,
            since=since,
            until=until,
            page=page,
            per=per,
        )

    def get_template(
        self,
        template_id: str,
        *,
        include_metrics: bool | None = None,
        days_back: int | None = None,
        since: datetime | str | None = None,
        until: datetime | str | None = None,
    ) -> LlmRequestTemplateDetail:
        """Get a single template by hashid, including both prompt patterns.

        Requires the **private** API key. Deprecated and system templates are reachable
        here by id with no opt-in flag, unlike the list. `metrics` is returned by
        default, over `days_back` or an explicit `since` / `until` window.

        Args:
            template_id: The template hashid, i.e. the `id` field from
                `search_templates`.
            include_metrics: Pass `False` to omit `metrics` (server default is true),
                which also skips validating the window.
            days_back: Rolling metrics window in days. Ignored when `since` is given.
            since: Metrics window start, inclusive (`datetime` or ISO8601 string).
            until: Metrics window end, exclusive; defaults to now.

        Raises:
            ValueError: If `template_id` is blank, not a string, or a relative path
                segment, or if `since`/`until` is neither a `datetime` nor a string.
            CoolhandAPIError: On a non-2xx response, with the HTTP status on `status`
                (`404` for an unknown id or one belonging to another client).
        """
        return self._template_service.get_template(
            template_id,
            include_metrics=include_metrics,
            days_back=days_back,
            since=since,
            until=until,
        )

    @property
    def workload_service(self) -> WorkloadService:
        """Get the workload service instance."""
        return self._workload_service

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
        """List your workloads, optionally with cost and performance metrics.

        Requires the **private** API key. A workload's `id` is its hashid, the value the
        `workload_id` filter on `search_templates` and `search_logs` expects.

        See `WorkloadService.search_workloads` for the full filter and error reference.

        Raises:
            ValueError: If `since`/`until` is neither a `datetime` nor a string.
            CoolhandAPIError: On a non-2xx response, with the HTTP status on `status`.
                A `504` is expected and retryable rather than a bug.
        """
        return self._workload_service.search_workloads(
            search=search,
            include_archived=include_archived,
            include_system=include_system,
            include_templates=include_templates,
            include_metrics=include_metrics,
            days_back=days_back,
            since=since,
            until=until,
            page=page,
            per=per,
        )

    @property
    def log_service(self) -> LogService:
        """Get the log read service instance."""
        return self._log_service

    def search_logs(
        self,
        *,
        template_id: str | None = None,
        workload_id: str | None = None,
        system_prompt_contains: str | None = None,
        user_prompt_contains: str | None = None,
        model: str | None = None,
        source_api: str | None = None,
        source_api_result: str | None = None,
        source_application: str | None = None,
        project_path: str | None = None,
        unmatched_only: bool | None = None,
        days_back: int | None = None,
        since: datetime | str | None = None,
        until: datetime | str | None = None,
        min_cost: float | None = None,
        order: LlmRequestLogOrder | None = None,
        include_prompts: bool | None = None,
        sort: str | None = None,
        include_total: bool | None = None,
        page: int | None = None,
        per: int | None = None,
    ) -> SearchLogsResponse:
        """Search the logs Coolhand has collected, with per-log `cost`.

        Requires the **private** API key. `since` / `until` bound `created_at` and
        replace `days_back`; `min_cost` and `order="cost_desc"` limit the result to logs
        that can be priced.

        See `LogService.search_logs` for the full filter and error reference.

        Raises:
            ValueError: If `since`/`until` is neither a `datetime` nor a string.
            CoolhandAPIError: On a non-2xx response, with the HTTP status on `status`.
        """
        return self._log_service.search_logs(
            template_id=template_id,
            workload_id=workload_id,
            system_prompt_contains=system_prompt_contains,
            user_prompt_contains=user_prompt_contains,
            model=model,
            source_api=source_api,
            source_api_result=source_api_result,
            source_application=source_application,
            project_path=project_path,
            unmatched_only=unmatched_only,
            days_back=days_back,
            since=since,
            until=until,
            min_cost=min_cost,
            order=order,
            include_prompts=include_prompts,
            sort=sort,
            include_total=include_total,
            page=page,
            per=per,
        )

    def get_log(
        self,
        log_id: str,
        *,
        section: str | None = None,
        max_chars: int | None = None,
        search_query: str | None = None,
        include_thinking: bool | None = None,
    ) -> LlmRequestLogContent:
        """Get one log's content, `cost` and `cost_breakdown` by hashid.

        Requires the **private** API key.

        Raises:
            ValueError: If `log_id` is blank, not a string, or a relative path segment,
                or if `search_query` is given but blank.
            CoolhandAPIError: On a non-2xx response, with the HTTP status on `status`
                (`404` for an unknown id or one belonging to another client).
        """
        return self._log_service.get_log(
            log_id,
            section=section,
            max_chars=max_chars,
            search_query=search_query,
            include_thinking=include_thinking,
        )


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
    "DEFAULT_INTERCEPT_ADDRESSES",
    "RequestData",
    "ResponseData",
    "FeedbackData",
    "FeedbackResponse",
    "FeedbackService",
    "get_feedback_service",
    "create_feedback",
    "acreate_feedback",
    "CoolhandAPIError",
    "LlmMetrics",
    "LlmRequestLogContent",
    "LlmRequestLogCostBreakdown",
    "LlmRequestLogOrder",
    "LlmRequestLogSummary",
    "LlmRequestTemplateDetail",
    "LlmRequestTemplateStatus",
    "LlmRequestTemplateSummary",
    "LogPagination",
    "LogService",
    "Pagination",
    "SearchLogsResponse",
    "SearchTemplatesResponse",
    "SearchWorkloadsResponse",
    "TemplateService",
    "WorkloadService",
    "WorkloadSummary",
    "WorkloadTemplate",
    "get_template_service",
    "initialize",
    "get_instance",
    "get_global_instance",
    "status",
    "start_monitoring",
    "stop_monitoring",
    "shutdown",
]
