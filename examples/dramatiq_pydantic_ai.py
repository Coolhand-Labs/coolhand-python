#!/usr/bin/env python3
# Usage: cd <repo-root> && uv run python examples/dramatiq_pydantic_ai.py
"""
Dramatiq + pydantic-ai + Coolhand monitoring example.

Coolhand patches httpx.AsyncClient.send at the class level before any workers
start, so all Dramatiq thread workers automatically capture LLM calls — no
per-worker initialization needed.

Key rule: import coolhand before dramatiq and pydantic-ai so the patch is in
place before the broker and actors are set up.

For real usage, replace FakeAnthropicTransport with a real API key:
    export ANTHROPIC_API_KEY=sk-ant-...
    export COOLHAND_API_KEY=ch-...

Known limitation: process-based brokers (Redis/RabbitMQ with fork workers) do
NOT inherit the patch. Each worker process must import coolhand at startup —
add `import coolhand` to the worker entrypoint or use a dramatiq middleware.

Requirements:
    uv pip install dramatiq pydantic-ai
"""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# Import coolhand first — patches httpx before any workers start
import dramatiq  # noqa: E402
import httpx  # noqa: E402
from dramatiq.brokers.stub import StubBroker  # noqa: E402
from pydantic_ai import Agent  # noqa: E402
from pydantic_ai.models.anthropic import AnthropicModel  # noqa: E402
from pydantic_ai.providers.anthropic import AnthropicProvider  # noqa: E402

import coolhand  # noqa: E402

# ---------------------------------------------------------------------------
# Broker setup — StubBroker runs workers in-process (no Redis/RabbitMQ needed)
# ---------------------------------------------------------------------------
broker = StubBroker()
dramatiq.set_broker(broker)


# ---------------------------------------------------------------------------
# Fake transport — swap for None (auto) when ANTHROPIC_API_KEY is set
# ---------------------------------------------------------------------------
class FakeAnthropicTransport(httpx.AsyncBaseTransport):
    """Returns a canned Anthropic response without hitting the network."""

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        body = json.dumps(
            {
                "id": "msg_demo",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": "A short demo summary."}],
                "model": "claude-3-5-haiku-20241022",
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 12, "output_tokens": 8},
            }
        ).encode()
        return httpx.Response(
            200,
            content=body,
            headers={"content-type": "application/json"},
            request=request,
        )


def _build_model() -> AnthropicModel:
    """Return an AnthropicModel backed by a real or fake HTTP client."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if api_key:
        provider = AnthropicProvider(api_key=api_key)
    else:
        # Demo mode: fake transport so the example runs without a real key
        http_client = httpx.AsyncClient(transport=FakeAnthropicTransport())
        provider = AnthropicProvider(api_key="demo-key", http_client=http_client)
    return AnthropicModel("claude-3-5-haiku-20241022", provider=provider)


# ---------------------------------------------------------------------------
# Dramatiq actors
# ---------------------------------------------------------------------------
@dramatiq.actor
def summarize(text: str) -> None:
    """Summarize text via Claude. Runs inside a Dramatiq worker thread."""
    agent = Agent(_build_model(), model_settings={"max_tokens": 64})
    # pydantic-ai is async; asyncio.run() is the right call inside a sync actor
    result = asyncio.run(agent.run(f"Summarize in one sentence: {text}"))
    print(f"[worker] Summary: {result.output}")


@dramatiq.actor
def classify(text: str) -> None:
    """Classify text sentiment via Claude. Runs inside a Dramatiq worker thread."""
    agent = Agent(
        _build_model(),
        system_prompt="Reply with exactly one word: positive, negative, or neutral.",
        model_settings={"max_tokens": 8},
    )
    result = asyncio.run(agent.run(text))
    print(f"[worker] Sentiment: {result.output}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    instance = coolhand.get_global_instance()
    using_real_key = bool(os.getenv("ANTHROPIC_API_KEY"))

    print("Coolhand + Dramatiq + pydantic-ai example")
    print("=" * 45)
    print(f"Coolhand active : {instance is not None}")
    print(f"Real Anthropic  : {using_real_key}")
    print()

    print("Dispatching 3 tasks...")
    summarize.send("The quick brown fox jumps over the lazy dog.")
    summarize.send("Artificial intelligence is reshaping how software is built.")
    classify.send("I absolutely love how easy this SDK makes monitoring!")

    print("Processing tasks (2 worker threads)...")
    worker = dramatiq.Worker(broker, worker_threads=2)
    worker.start()
    broker.join(summarize.queue_name, fail_fast=True)
    broker.join(classify.queue_name, fail_fast=True)
    worker.stop()

    if instance:
        stats = instance.get_stats()
        count = stats["logging"].get("interaction_count", 0)
        print(f"\nCoolhand captured {count} LLM interaction(s) from worker threads")
        instance.flush()
    else:
        print("\nCoolhand not active — set COOLHAND_API_KEY to enable submission")


if __name__ == "__main__":
    if not os.getenv("COOLHAND_API_KEY"):
        os.environ["COOLHAND_API_KEY"] = "demo-key"

    try:
        main()
    except Exception as exc:
        print(f"\nError: {exc}")
        import traceback

        traceback.print_exc()
    finally:
        coolhand.shutdown()
