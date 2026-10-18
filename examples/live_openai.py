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


def main() -> None:
    try:
        client = OpenAI(
            timeout=_live_check.CALL_TIMEOUT_SECONDS,
            max_retries=_live_check.CALL_MAX_RETRIES,
        )
        response = client.chat.completions.create(
            model=_live_check.model_from_env(
                "COOLHAND_LIVE_OPENAI_MODEL", "gpt-4o-mini"
            ),
            messages=[{"role": "user", "content": "Reply with the single word: ok"}],
            max_completion_tokens=16,
        )
    except Exception as e:
        _live_check.fail(NAME, f"OpenAI call failed: {type(e).__name__}: {e}")
    print(f"OpenAI call succeeded (model={response.model})")
    _live_check.confirm_delivery(NAME)


if __name__ == "__main__":
    main()
