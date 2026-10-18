"""Test helpers for asserting that Coolhand really intercepted a request.

Interception can fail silently: a client that captured a reference to
``httpx.Client.send`` or ``requests.Session.send`` before ``import coolhand``
never goes through the patched wrapper, so the app simply stops being
monitored with no error. Use these helpers in your own tests or CI to catch it:

    from coolhand.testing import (
        assert_interception_occurred,
        capture_interception_snapshot,
    )

    before = capture_interception_snapshot()
    client.chat.completions.create(...)
    assert_interception_occurred(before, "openai client bypassed Coolhand")

The counter is process-wide, so concurrent unrelated intercepted traffic can
cause a false pass. It counts interceptions, not delivery, and does not cover
the GitHub Copilot JSON-RPC interceptor. See ``docs/testing.md``.
"""

from dataclasses import dataclass

from . import httpx_interceptor

__all__ = [
    "InterceptionSnapshot",
    "assert_interception_occurred",
    "capture_interception_snapshot",
]


@dataclass(frozen=True)
class InterceptionSnapshot:
    """The intercepted-request counter at one point in time."""

    count: int


def capture_interception_snapshot() -> InterceptionSnapshot:
    """Record the current intercepted-request count for a later assertion."""
    return InterceptionSnapshot(count=httpx_interceptor.get_intercept_count())


def assert_interception_occurred(
    before: InterceptionSnapshot, message: str | None = None
) -> None:
    """Raise ``AssertionError`` if no request was intercepted since ``before``.

    Args:
        before: Snapshot from ``capture_interception_snapshot()``, taken before
            the request under test.
        message: Optional context prepended to the failure message.
    """
    delta = httpx_interceptor.get_intercept_count() - before.count
    if delta > 0:
        return

    reasons = ["Coolhand did not intercept any request since the snapshot."]
    if not httpx_interceptor.is_patched():
        reasons.append(
            "Monitoring is not active (stop_monitoring() was called or "
            "monitoring was never started)."
        )
    elif not httpx_interceptor.has_handler():
        reasons.append(
            "No handler is set, so patched requests pass through uncounted "
            "(was a Coolhand instance ever created?)."
        )
    reasons.append(
        "Likely causes: a client captured a reference to httpx.Client.send / "
        "requests.Session.send before coolhand was imported; the request used "
        "a transport Coolhand does not patch (e.g. aiohttp, urllib3, "
        "http.client); or the URL is outside intercept_addresses, localhost, "
        "or matched exclude_api_patterns."
    )
    detail = " ".join(reasons)
    raise AssertionError(f"{message}: {detail}" if message else detail)
