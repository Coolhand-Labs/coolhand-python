# Dramatiq + pydantic-ai Support

Coolhand works with [Dramatiq](https://dramatiq.io/) task queues and [pydantic-ai](https://ai.pydantic.dev/) out of the box for the common thread-worker setup. This page covers what works, what doesn't, and workarounds for the two known gaps.

## Quick Start

```bash
pip install coolhand dramatiq pydantic-ai
```

**Import `coolhand` before starting any Dramatiq workers.** Since Coolhand patches `httpx.AsyncClient.send` at the class level, any thread that runs after the patch is applied will have its LLM calls captured automatically.

```python
import coolhand  # must come before dramatiq workers start

import asyncio
import dramatiq
from pydantic_ai import Agent
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.providers.anthropic import AnthropicProvider

@dramatiq.actor
def summarize(text: str) -> None:
    model = AnthropicModel(
        "claude-3-5-haiku-20241022",
        provider=AnthropicProvider(api_key="sk-ant-..."),
    )
    agent = Agent(model, model_settings={"max_tokens": 128})
    # pydantic-ai is async — asyncio.run() is the correct pattern inside
    # a sync Dramatiq actor
    result = asyncio.run(agent.run(f"Summarize: {text}"))
    print(result.output)
```

All three actor invocations below are captured automatically:

```python
summarize.send("The quick brown fox jumps over the lazy dog.")
summarize.send("Artificial intelligence is reshaping software engineering.")
summarize.send("Coolhand monitors every LLM call in your stack.")
```

See [`examples/dramatiq_pydantic_ai.py`](../examples/dramatiq_pydantic_ai.py) for a fully runnable version.

## What Works Out of the Box

| Scenario | Status |
|---|---|
| Thread-based workers (default: Redis, RabbitMQ, StubBroker) | ✅ Works |
| `asyncio.run()` inside a sync Dramatiq actor | ✅ Works |
| pydantic-ai via `AnthropicModel` / `OpenAIModel` | ✅ Works |
| Any httpx-based library (openai, anthropic SDKs) | ✅ Works |
| Streaming responses | ✅ Works |
| Process-based workers (spawned subprocesses) | ✅ Works with `CoolhandDramatiqMiddleware` |
| Per-task session correlation in Coolhand | ⚠️ Needs workaround — see below |

## Known Gaps and Workarounds

### Gap 1 — Process-based Workers

**Problem:** Coolhand's httpx patch lives in the parent process's memory. Worker processes started with a fresh interpreter (e.g. `python -m dramatiq myapp`) begin without the patch applied.

**Fix:** Add `CoolhandDramatiqMiddleware` to your broker. It calls `coolhand.start_monitoring()` in Dramatiq's `after_process_boot` lifecycle hook, ensuring monitoring is active in every worker process.

```python
import coolhand
import dramatiq
from coolhand.integrations.dramatiq import CoolhandDramatiqMiddleware
from dramatiq.brokers.redis import RedisBroker

broker = RedisBroker(url="redis://localhost:6379")
broker.add_middleware(CoolhandDramatiqMiddleware())
dramatiq.set_broker(broker)
```

Start your workers as usual:

```bash
python -m dramatiq tasks
```

> **Note:** Coolhand reads `COOLHAND_API_KEY` from the environment at import time. Make sure this variable is set in your worker process environment (e.g. via your process manager, Kubernetes secret, or `.env` file).

---

### Gap 2 — Per-task Session Correlation

**Problem:** All LLM calls across all worker threads share a single global `session_id`. There is no built-in way to know which Coolhand interaction was triggered by which Dramatiq message.

**Workaround:** Add the `CoolhandDramatiqMiddleware` below to your broker. It uses a `ContextVar` to tag each worker thread with the current message ID and routes LLM interactions to a per-task Coolhand instance carrying that ID as the `session_id`.

```python
# coolhand_middleware.py
import contextvars
import os
import threading

import coolhand
import dramatiq
from coolhand import httpx_interceptor

# Tracks the active Dramatiq message ID inside each worker thread.
_current_task_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "coolhand_task_id", default=None
)
_token_local = threading.local()


class CoolhandDramatiqMiddleware(dramatiq.Middleware):
    """
    Routes each Dramatiq message's LLM calls to a dedicated Coolhand
    session_id derived from the message ID.

    Add to your broker before starting workers:

        broker.add_middleware(CoolhandDramatiqMiddleware())
    """

    def before_process_message(self, broker, message):  # type: ignore[override]
        _token_local.token = _current_task_id.set(message.message_id)

    def after_process_message(self, broker, message, *, result=None, exception=None):  # type: ignore[override]
        if hasattr(_token_local, "token"):
            _current_task_id.reset(_token_local.token)
            del _token_local.token


def _task_routing_handler(req, res, err):
    """httpx interceptor handler that dispatches to a per-task Coolhand instance."""
    task_id = _current_task_id.get()
    global_instance = coolhand.get_global_instance()
    session_id = task_id or (global_instance.session_id if global_instance else None)
    instance = coolhand.Coolhand(
        api_key=os.environ.get("COOLHAND_API_KEY", ""),
        session_id=session_id,
        silent=True,
        auto_submit=True,
    )
    instance.log_interaction(req, res, err)


# Replace the global handler with the routing handler.
# Call this once at startup, after `import coolhand`.
httpx_interceptor.set_handler(_task_routing_handler)
```

Wire it up in your broker setup:

```python
import coolhand                          # patches httpx first
from coolhand_middleware import CoolhandDramatiqMiddleware

import dramatiq
from dramatiq.brokers.redis import RedisBroker

broker = RedisBroker(url="redis://localhost:6379")
broker.add_middleware(CoolhandDramatiqMiddleware())
dramatiq.set_broker(broker)
```

Each Dramatiq message ID now appears as a distinct `session_id` in the Coolhand dashboard, making it straightforward to trace which task triggered which LLM call.

> **Limitation:** Each LLM call creates a lightweight `Coolhand` instance. For very high-throughput workloads, consider caching instances by `task_id` (or by a stable hash of it) to reduce object allocation.

---

## pydantic-ai Notes

### Use `AnthropicModel`, not `AnthropicProvider`, as the `Agent` model

pydantic-ai's `AnthropicProvider` is a credential/transport container, not a model. Pass it through `AnthropicModel`:

```python
# ✅ Correct
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.providers.anthropic import AnthropicProvider

model = AnthropicModel(
    "claude-3-5-haiku-20241022",
    provider=AnthropicProvider(api_key="sk-ant-..."),
)
agent = Agent(model)

# ❌ Wrong — provider passed directly as model, real API call made with no transport override
agent = Agent("anthropic:claude-3-5-haiku-20241022")
result = await agent.run("question", model=some_provider)
```

### Async pydantic-ai inside a sync Dramatiq actor

Dramatiq actors are synchronous by default. Use `asyncio.run()` to drive pydantic-ai's async API from within one:

```python
@dramatiq.actor
def my_task(text: str) -> None:
    agent = Agent(AnthropicModel("claude-3-5-haiku-20241022", provider=...))
    result = asyncio.run(agent.run(text))  # creates a fresh event loop per call
    print(result.output)
```

`asyncio.run()` creates and destroys an event loop per invocation. This is safe in a Dramatiq thread worker and is the recommended pattern until Dramatiq gains native async actor support.

---

## Roadmap

| Gap | Status |
|---|---|
| Process-based workers | ✅ Fixed — use `CoolhandDramatiqMiddleware` from `coolhand.integrations.dramatiq` |
| Per-task session correlation | Planned — a `metadata` / `tags` field on `log_interaction` so any task queue framework can attach arbitrary identifiers to individual LLM calls without a custom middleware |
