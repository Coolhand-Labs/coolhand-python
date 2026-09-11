"""
Coolhand middleware for Dramatiq.

Ensures monitoring is active in every worker process, including process-based
workers that start with a fresh interpreter (spawn model).

Usage::

    from coolhand.integrations.dramatiq import CoolhandDramatiqMiddleware

    broker = RedisBroker(url="redis://...")
    broker.add_middleware(CoolhandDramatiqMiddleware())
    dramatiq.set_broker(broker)

``dramatiq`` must be installed separately — it is not a core Coolhand dependency.
"""

from __future__ import annotations

try:
    import dramatiq
except ImportError as e:
    raise ImportError(
        "The 'dramatiq' package is required to use CoolhandDramatiqMiddleware. "
        "Install it with: pip install dramatiq"
    ) from e

import coolhand as _coolhand


class CoolhandDramatiqMiddleware(dramatiq.Middleware):
    """Dramatiq middleware that activates Coolhand monitoring in every worker process.

    Handles both spawn-based workers (fresh interpreter, no inherited patch) and
    fork-based workers (inherited module state where the httpx patch may need to
    be re-applied after fork).

    Add to your broker before starting workers::

        broker.add_middleware(CoolhandDramatiqMiddleware())
    """

    def after_process_boot(self, broker: dramatiq.Broker) -> None:
        """Called once in each worker process after it boots.

        Creates a Coolhand instance if none exists (spawn-based workers), or
        re-applies the httpx patch if one already exists (fork-based workers).
        """
        instance = _coolhand.get_instance()
        if instance is None:
            _coolhand.Coolhand()
        else:
            _coolhand.start_monitoring()
