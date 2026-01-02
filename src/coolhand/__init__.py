"""
Coolhand Python SDK - Automatic monitoring for LLM API calls.

Usage:
    import coolhand  # Auto-initializes and starts monitoring

    # Or configure manually:
    coolhand.Coolhand(api_key="your-key", debug=True)
"""

from .version import __version__
from .types import Config, RequestData, ResponseData
from .client import CoolhandClient, get_instance, set_instance, initialize
from . import interceptor

import atexit
import logging

logger = logging.getLogger(__name__)


class Coolhand(CoolhandClient):
    """Main Coolhand class - monitors LLM API calls automatically."""

    def __init__(self, config=None, **kwargs):
        super().__init__(config, **kwargs)

        # Set as global instance
        set_instance(self)

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
    if instance and hasattr(instance, 'start_monitoring'):
        instance.start_monitoring()


def stop_monitoring():
    """Stop monitoring on global instance."""
    instance = get_instance()
    if instance and hasattr(instance, 'stop_monitoring'):
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
    '__version__',
    'Coolhand',
    'Config',
    'RequestData',
    'ResponseData',
    'initialize',
    'get_instance',
    'get_global_instance',
    'status',
    'start_monitoring',
    'stop_monitoring',
    'shutdown',
]
