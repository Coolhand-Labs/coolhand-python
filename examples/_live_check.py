"""Shared pass/skip/fail helpers for the live provider examples (examples/live_*.py).

Contract, relied on by scripts/live-examples.sh:
  - exit SKIP_EXIT_CODE and print "Skipping ..." when a required key is missing
  - exit 0 when the provider call succeeded and Coolhand confirmed delivery
  - exit 1 (message on stderr) when the call or the delivery failed
"""

import json
import os
import sys
from typing import Any, NoReturn

# Distinct from 0 and 1 (and the same code automake uses for "skipped") so the runner
# classifies SKIP from the exit status, never by grepping log text.
SKIP_EXIT_CODE = 77

# The provider SDKs default to a 10 minute timeout; a stalled provider must not hang a
# release check for that long.
CALL_TIMEOUT_SECONDS = 60

# SDK retries on 429/5xx would log each HTTP attempt, so one call could be 2 interactions
# and trip the "exactly 1" check. No retries: a transient provider error fails with the
# provider's own message instead.
CALL_MAX_RETRIES = 0

# Add the src directory to the Python path for running examples directly
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def skip_unless_env(name: str, *required: str) -> None:
    """Exit SKIP_EXIT_CODE with a "Skipping ..." message if any required env var is unset."""
    missing = [var for var in required if not os.getenv(var)]
    if missing:
        print(f"Skipping {name}: {', '.join(missing)} not set")
        sys.exit(SKIP_EXIT_CODE)


def model_from_env(var: str, default: str) -> str:
    """Model id, overridable via env so a retired default doesn't fail a release check."""
    return os.getenv(var) or default


def fail(name: str, message: str) -> NoReturn:
    print(f"FAIL {name}: {message}", file=sys.stderr)
    sys.exit(1)


def capture_responses() -> list[tuple[Any, Any, Any]]:
    """Record every (request, response, error) the interceptor reports, from now on.

    The records are the raw capture, before redaction and delivery, so a check on them
    proves what the interceptor saw. The previously installed handler (Coolhand's
    delivery) still runs, so delivery is unaffected.
    """
    from coolhand import httpx_interceptor

    records: list[tuple[Any, Any, Any]] = []
    previous = httpx_interceptor._handler

    def tap(request: Any, response: Any, error: Any) -> None:
        records.append((request, response, error))
        if previous:
            previous(request, response, error)

    httpx_interceptor.set_handler(tap)
    return records


def sse_events(body: Any) -> list[dict]:
    """The JSON payloads of an SSE body's ``data:`` lines (``[DONE]`` and non-JSON skipped)."""
    if not isinstance(body, str):
        return []
    events = []
    for line in body.splitlines():
        if not line.startswith("data:"):
            continue
        try:
            event = json.loads(line[5:].strip())
        except ValueError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def single_streamed_capture(name: str, records: list) -> dict:
    """Fail unless exactly one complete streamed response was captured; return its response.

    This is the sync-streaming regression check: a sync stream used to be logged with
    ``is_streaming: false`` and the placeholder body ``"[streaming]"``, so the server
    could extract no tokens, output or message id from it.
    """
    streamed = [res for _, res, err in records if err is None and res]
    if len(streamed) != 1:
        fail(name, f"expected 1 captured streamed response, got {len(streamed)}")
    res = streamed[0]
    if res.get("is_streaming") is not True:
        fail(
            name,
            f"captured response not marked is_streaming: {res.get('is_streaming')!r}",
        )
    if res.get("body_truncated"):
        fail(name, "captured streamed body was truncated")
    body = res.get("body")
    if not isinstance(body, str) or body.strip() == "[streaming]" or not body.strip():
        fail(name, f"streamed body was not captured: {str(body)[:80]!r}")
    return res


def confirm_delivery(name: str, expected_interactions: int = 1) -> None:
    """Wait for the delivery barrier, then fail unless the log reached Coolhand.

    Known limit: shutdown() waits a bounded time (5s), so against a very slow backend a
    POST still in flight is invisible to the counters. A slow backend then looks like a
    PASS rather than a FAIL; a rejected or unreachable one is still caught.
    """
    import coolhand

    # shutdown() (not flush()) waits for the background worker to actually POST
    # everything queued, so the counters below are final.
    coolhand.shutdown()
    status = coolhand.status()
    if "logging" not in status:
        fail(name, f"Coolhand is not initialized: {status}")
    logging_stats = status["logging"]

    # Exactly the calls made: fewer means one wasn't captured, more means it was
    # double-logged.
    if logging_stats["interaction_count"] != expected_interactions:
        fail(
            name,
            f"expected {expected_interactions} captured interaction(s), "
            f"got {logging_stats['interaction_count']}",
        )
    if logging_stats["delivery_failure_count"]:
        fail(name, f"Coolhand delivery failed: {logging_stats}")
    if logging_stats["dropped_count"] or logging_stats["pending_delivery"]:
        fail(name, f"log was dropped or not delivered in time: {logging_stats}")

    print(
        f"PASS {name}: call captured and delivered "
        f"({logging_stats['interaction_count']} interaction(s))"
    )
