"""
Coolhand Python SDK - Automatic monitoring for LLM API calls.

Usage:
    import coolhand  # Auto-initializes and starts monitoring

    # Or configure manually:
    coolhand.Coolhand(api_key="your-key", debug=True)
"""

import atexit
import logging

from . import interceptor
from .client import CoolhandClient, get_instance, initialize, set_instance
from .feedback_service import FeedbackService, create_feedback, get_feedback_service
from .types import Config, FeedbackData, FeedbackResponse, RequestData, ResponseData
from .version import __version__

logger = logging.getLogger(__name__)


class Coolhand(CoolhandClient):
    """Main Coolhand class - monitors LLM API calls automatically."""

    def __init__(self, config=None, **kwargs):
        super().__init__(config, **kwargs)

        # Set as global instance
        set_instance(self)

        # Initialize feedback service with same config
        self._feedback_service = FeedbackService(self.config)

        # Start monitoring
        self.start_monitoring()

        # Cleanup on exit
        atexit.register(self.shutdown)

        logger.info(f"Coolhand initialized (session: {self.session_id})")

    def start_monitoring(self):
        """Start HTTP monitoring."""
        interceptor.set_handler(self.log_interaction)
        interceptor.patch()
        logger.info("HTTP monitoring started")

    def stop_monitoring(self):
        """Stop HTTP monitoring."""
        interceptor.unpatch()
        logger.info("HTTP monitoring stopped")

    @property
    def feedback_service(self) -> FeedbackService:
        """Get the feedback service instance."""
        return self._feedback_service

    def create_feedback(self, feedback: FeedbackData) -> FeedbackResponse:
        """Submit feedback for an LLM response.

        Args:
            feedback: Feedback data containing:
                - like (bool): Required. Thumbs up (True) or down (False).
                - At least one matching field (llm_request_log_id,
                  llm_provider_unique_id, original_output, or client_unique_id).
                - Optional: explanation, revised_output, creator_unique_id.

        Returns:
            FeedbackResponse with created feedback details, or None on error.

        Example:
            >>> coolhand_instance.create_feedback({
            ...     "llm_request_log_id": 12345,
            ...     "like": True,
            ...     "explanation": "Accurate and helpful response"
            ... })
        """
        return self._feedback_service.create_feedback(feedback)


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
    "RequestData",
    "ResponseData",
    "FeedbackData",
    "FeedbackResponse",
    "FeedbackService",
    "get_feedback_service",
    "create_feedback",
    "initialize",
    "get_instance",
    "get_global_instance",
    "status",
    "start_monitoring",
    "stop_monitoring",
    "shutdown",
]
