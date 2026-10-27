#!/usr/bin/env python3
# Usage: cd <repo-root> && uv run --with openai python examples/live_openai.py
"""
Live smoke test: one real OpenAI call through the Coolhand interceptor.

Needs COOLHAND_API_KEY and OPENAI_API_KEY; exits with the skip code (77) and
"Skipping ..." if either is missing, and non-zero if the call fails or the log isn't delivered to Coolhand.
See examples/README.md ("Live smoke tests").
"""

import _live_check

NAME = "live_openai"
_live_check.skip_unless_env(NAME, "COOLHAND_API_KEY", "OPENAI_API_KEY")

import coolhand  # noqa: E402, F401  (import patches httpx before the SDK sends)

# isort: split
from openai import OpenAI  # noqa: E402


def check_streamed_usage(records: list, usage, chunk_id: str) -> None:
    """The captured SSE body must carry the same token counts and id the SDK reported."""
    res = _live_check.single_streamed_capture(NAME, records)
    events = _live_check.sse_events(res["body"])
    captured = next((e["usage"] for e in reversed(events) if e.get("usage")), {})
    ids = {e.get("id") for e in events}
    if chunk_id not in ids:
        _live_check.fail(NAME, f"captured body has no chunk with id {chunk_id!r}")
    if (
        not captured.get("total_tokens")
        or captured["total_tokens"] != usage.total_tokens
    ):
        _live_check.fail(
            NAME,
            f"captured total_tokens {captured.get('total_tokens')!r} != SDK {usage.total_tokens}",
        )
    print(f"Streamed capture has id and tokens (total={captured['total_tokens']})")


def main() -> None:
    model = _live_check.model_from_env("COOLHAND_LIVE_OPENAI_MODEL", "gpt-4o-mini")
    messages = [{"role": "user", "content": "Reply with the single word: ok"}]
    try:
        client = OpenAI(
            timeout=_live_check.CALL_TIMEOUT_SECONDS,
            max_retries=_live_check.CALL_MAX_RETRIES,
        )
        response = client.chat.completions.create(
            model=model, messages=messages, max_completion_tokens=16
        )
    except Exception as e:
        _live_check.fail(NAME, f"OpenAI call failed: {type(e).__name__}: {e}")
    print(f"OpenAI call succeeded (model={response.model})")

    # The sync streaming path: its body must reach Coolhand with usage and the id.
    records = _live_check.capture_responses()
    usage = chunk_id = None
    try:
        for chunk in client.chat.completions.create(
            model=model,
            messages=messages,
            max_completion_tokens=16,
            stream=True,
            stream_options={"include_usage": True},
        ):
            chunk_id = chunk.id
            usage = chunk.usage or usage
    except Exception as e:
        _live_check.fail(NAME, f"OpenAI stream failed: {type(e).__name__}: {e}")
    if usage is None:
        _live_check.fail(NAME, "OpenAI stream returned no usage chunk")
    check_streamed_usage(records, usage, chunk_id)
    _live_check.confirm_delivery(NAME, expected_interactions=2)


if __name__ == "__main__":
    main()
