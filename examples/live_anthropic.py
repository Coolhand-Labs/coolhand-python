#!/usr/bin/env python3
# Usage: cd <repo-root> && uv run --with anthropic python examples/live_anthropic.py
"""
Live smoke test: one real Anthropic call through the Coolhand interceptor.

Needs COOLHAND_API_KEY and ANTHROPIC_API_KEY; exits with the skip code (77) and
"Skipping ..." if either is missing, and non-zero if the call fails or the log isn't delivered to Coolhand.
See examples/README.md ("Live smoke tests").
"""

import _live_check

NAME = "live_anthropic"
_live_check.skip_unless_env(NAME, "COOLHAND_API_KEY", "ANTHROPIC_API_KEY")

import coolhand  # noqa: E402, F401  (import patches httpx before the SDK sends)

# isort: split
from anthropic import Anthropic  # noqa: E402


def check_streamed_usage(records: list, final_message) -> None:
    """The captured SSE body must carry the same token counts and id the SDK reported."""
    res = _live_check.single_streamed_capture(NAME, records)
    events = _live_check.sse_events(res["body"])
    started = next(
        (e["message"] for e in events if e.get("type") == "message_start"), {}
    )
    ended = next((e for e in reversed(events) if e.get("type") == "message_delta"), {})
    input_tokens = (started.get("usage") or {}).get("input_tokens")
    output_tokens = (ended.get("usage") or {}).get("output_tokens")
    if started.get("id") != final_message.id:
        _live_check.fail(
            NAME, f"captured message id {started.get('id')!r} != {final_message.id!r}"
        )
    if not input_tokens or input_tokens != final_message.usage.input_tokens:
        _live_check.fail(
            NAME,
            f"captured input_tokens {input_tokens!r} != SDK {final_message.usage.input_tokens}",
        )
    if not output_tokens or output_tokens != final_message.usage.output_tokens:
        _live_check.fail(
            NAME,
            f"captured output_tokens {output_tokens!r} != SDK {final_message.usage.output_tokens}",
        )
    print(
        f"Streamed capture has id and tokens (in={input_tokens}, out={output_tokens})"
    )


def main() -> None:
    model = _live_check.model_from_env(
        "COOLHAND_LIVE_ANTHROPIC_MODEL", "claude-haiku-4-5-20251001"
    )
    messages = [{"role": "user", "content": "Reply with the single word: ok"}]
    try:
        client = Anthropic(
            timeout=_live_check.CALL_TIMEOUT_SECONDS,
            max_retries=_live_check.CALL_MAX_RETRIES,
        )
        response = client.messages.create(model=model, max_tokens=5, messages=messages)
    except Exception as e:
        _live_check.fail(NAME, f"Anthropic call failed: {type(e).__name__}: {e}")
    print(f"Anthropic call succeeded (model={response.model})")

    # The sync streaming path: its body must reach Coolhand with usage and the id.
    records = _live_check.capture_responses()
    try:
        with client.messages.stream(
            model=model, max_tokens=5, messages=messages
        ) as stream:
            final_message = stream.get_final_message()
    except Exception as e:
        _live_check.fail(NAME, f"Anthropic stream failed: {type(e).__name__}: {e}")
    check_streamed_usage(records, final_message)
    _live_check.confirm_delivery(NAME, expected_interactions=2)


if __name__ == "__main__":
    main()
