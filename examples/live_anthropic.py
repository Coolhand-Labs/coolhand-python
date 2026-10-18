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


def main() -> None:
    try:
        client = Anthropic(
            timeout=_live_check.CALL_TIMEOUT_SECONDS,
            max_retries=_live_check.CALL_MAX_RETRIES,
        )
        response = client.messages.create(
            model=_live_check.model_from_env(
                "COOLHAND_LIVE_ANTHROPIC_MODEL", "claude-haiku-4-5-20251001"
            ),
            max_tokens=5,
            messages=[{"role": "user", "content": "Reply with the single word: ok"}],
        )
    except Exception as e:
        _live_check.fail(NAME, f"Anthropic call failed: {type(e).__name__}: {e}")
    print(f"Anthropic call succeeded (model={response.model})")
    _live_check.confirm_delivery(NAME)


if __name__ == "__main__":
    main()
