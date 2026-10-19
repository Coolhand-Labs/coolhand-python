"""
Coolhand middleware for Dramatiq.

Ensures monitoring is active — with the right config — in every worker
process, including process-based workers that start with a fresh interpreter
(spawn model). See :class:`CoolhandDramatiqMiddleware` for details.

Usage::

    from coolhand.integrations.dramatiq import CoolhandDramatiqMiddleware

    broker = RedisBroker(url="redis://...")
    broker.add_middleware(CoolhandDramatiqMiddleware(api_key="..."))
    dramatiq.set_broker(broker)

``dramatiq`` must be installed separately — it is not a core Coolhand dependency.
"""

from __future__ import annotations

import atexit
import logging
from typing import Any, cast

try:
    import dramatiq
except ImportError as e:
    raise ImportError(
        "The 'dramatiq' package is required to use CoolhandDramatiqMiddleware. "
        "Install it with: pip install dramatiq"
    ) from e

import coolhand as _coolhand
from coolhand._config import _normalize_base_url
from coolhand.types import Config

logger = logging.getLogger(__name__)


class CoolhandDramatiqMiddleware(dramatiq.Middleware):
    """Dramatiq middleware that activates Coolhand monitoring in every worker process.

    Accepts the same ``config``/keyword arguments as :class:`coolhand.Coolhand`
    (``api_key``, ``intercept_addresses``, ``exclude_api_patterns``, ``base_url``,
    etc.). See :meth:`after_process_boot` for how and when they're applied.

    Add to your broker before starting workers::

        broker.add_middleware(CoolhandDramatiqMiddleware(api_key="..."))
    """

    def __init__(self, config: Config | None = None, **kwargs: Any) -> None:
        # Snapshotted (not a reference to the caller's dict) so a config
        # mutated after construction can't disagree with `self._requested`
        # below, which is computed once here.
        self._config: Config | None = (
            cast(Config, dict(config)) if config is not None else None
        )
        self._kwargs = kwargs
        # Merged once up front (not per after_process_boot call) so a bad
        # base_url raises here, at middleware-construction time, rather than
        # only surfacing later inside the boot hook.
        #
        # base_url is normalized to match what CoolhandClient.__init__ stores
        # on `instance.config`, so _matches_requested_config compares
        # like-for-like — if CoolhandClient.__init__ starts normalizing
        # another field, mirror that here too.
        self._requested: dict[str, Any] = dict(self._config or {})
        self._requested.update(kwargs)
        if "base_url" in self._requested:
            self._requested["base_url"] = _normalize_base_url(
                self._requested["base_url"]
            )

    def after_process_boot(self, broker: dramatiq.Broker) -> None:
        """Called once in each worker process after it boots.

        If no instance exists yet, or the existing one doesn't already match
        this middleware's config — typically the instance auto-initialized
        from environment variables only when ``coolhand`` was imported,
        before this middleware's own config was applied — constructs one
        that does, then unregisters the old instance's atexit shutdown hook.
        Otherwise (the existing instance already matches, e.g. a fork-based
        worker that inherited a compatible one) just re-applies the httpx
        patch, preserving that instance's session.

        The replacement is constructed before the old instance's hook is
        unregistered, so a failure leaves the worker's existing instance
        (if any) intact instead of leaving it with none at all. Unregistering
        is all that happens to the old instance — it is not shut down, since
        in a forked worker it (and its dispatch queue) is a fork-inherited
        copy of the *parent* process's instance, and flushing it here would
        re-deliver the parent's in-flight interactions once per worker and
        risks deadlocking on a lock that was held mid-flush at fork time.

        Failures constructing the replacement are logged and swallowed
        rather than propagated, consistent with `coolhand`'s own auto-init
        (`coolhand/__init__.py`): a monitoring integration should never be
        able to crash the worker it's observing.
        """
        existing_instance = _coolhand.get_instance()
        if existing_instance is not None and self._matches_requested_config(
            existing_instance
        ):
            _coolhand.start_monitoring()
            return

        try:
            _coolhand.Coolhand(self._config, **self._kwargs)
        except Exception:
            logger.exception(
                "Coolhand: failed to apply configured monitoring in this worker "
                "process; leaving the existing instance (if any) in place"
            )
            return

        if existing_instance is not None:
            atexit.unregister(existing_instance.shutdown)

    def _matches_requested_config(self, instance: _coolhand.CoolhandClient) -> bool:
        """Whether `instance` already reflects the config passed to this middleware."""
        return all(
            instance.config.get(key) == value for key, value in self._requested.items()
        )
