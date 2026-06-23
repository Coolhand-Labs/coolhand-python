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
| Process-based workers (spawned subprocesses) | ⚠️ Needs workaround — see below |
| Per-task session correlation in Coolhand | ⚠️ Needs workaround — see below |

## Known Gaps and Workarounds

### Gap 1 — Process-based Workers

**Problem:** Coolhand's httpx patch lives in the parent process's memory. Worker processes started with a fresh interpreter (e.g. `python -m dramatiq myapp`) begin without the patch applied.

**Workaround:** Put `import coolhand` at the top of your actors module — the same file that defines your `@dramatiq.actor` functions. Dramatiq imports this module inside each worker process at startup, so Coolhand initializes and patches httpx automatically in every process.

```python
# tasks.py  ← the module you pass to `python -m dramatiq tasks`
import coolhand           # <-- this one line is all that's needed
import asyncio
import dramatiq
from pydantic_ai import Agent
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.providers.anthropic import AnthropicProvider

@dramatiq.actor
def my_task(text: str) -> None:
    ...
```

Start your workers as usual:

```bash
python -m dramatiq tasks
```

> **Note:** Coolhand reads `COOLHAND_API_KEY` from the environment at import time. Make sure this variable is set in your worker process environment (e.g. via your process manager, Kubernetes secret, or `.env` file).

---

### Gap 2 — Per-task Interaction Correlation

**Problem:** All LLM calls across all worker threads share a single global `session_id`. There is no built-in way to know which Coolhand interaction was triggered by which Dramatiq message.

**Solution:** Use `log_interaction`'s `metadata` parameter to attach the message ID to each interaction. A thin middleware stores the current message ID in a `ContextVar`; a custom handler reads it and passes it as metadata.

```python
# coolhand_middleware.py
import contextvars
import threading

import dramatiq
from coolhand import httpx_interceptor

# Tracks the active Dramatiq message ID inside each worker thread.
_current_task_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "coolhand_task_id", default=None
)
_token_local = threading.local()


class CoolhandDramatiqMiddleware(dramatiq.Middleware):
    """
    Tags each Dramatiq message's LLM calls with the message ID via metadata.

    Add to your broker before starting workers:

        broker.add_middleware(CoolhandDramatiqMiddleware())
    """

    def before_process_message(self, broker, message):  # type: ignore[override]
        _token_local.token = _current_task_id.set(message.message_id)

    def after_process_message(self, broker, message, *, result=None, exception=None):  # type: ignore[override]
        if hasattr(_token_local, "token"):
            _current_task_id.reset(_token_local.token)
            del _token_local.token


def _task_metadata_handler(req, res, err):
    """httpx interceptor handler that injects the current task ID as metadata."""
    import coolhand
    task_id = _current_task_id.get()
    instance = coolhand.get_global_instance()
    if instance:
        metadata = {"task_id": task_id} if task_id else {}
        instance.log_interaction(req, res, err, metadata=metadata)


# Replace the global handler with the metadata-injecting handler.
# Call this once at startup, after `import coolhand`.
httpx_interceptor.set_handler(_task_metadata_handler)
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

Each interaction now carries a `task_id` field in its metadata, making it straightforward to trace which Dramatiq message triggered which LLM call in the Coolhand dashboard.

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

These gaps are tracked as separate issues and will be addressed with first-class SDK support:

| Gap | Planned fix |
|---|---|
| Process-based workers | A Dramatiq middleware shipped in the SDK that calls `coolhand.start_monitoring()` in the `after_worker_process_boot` lifecycle hook |
