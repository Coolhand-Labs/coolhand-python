# Declared Workloads and Templates

By default Coolhand matches each captured LLM call to a template on the server. If you
already know which workload and template a call belongs to, **declare** it: the SDK adds
the identity to the captured log (`llm_request_log.declared`) and the server skips
matching for that log.

Two pieces:

- [`with_context(...)`](#with_context) declares the identity for calls captured inside a
  block or decorated function.
- [`WorkloadService.upsert`](#explicit-upserts) and
  [`TemplateService.upsert`](#explicit-upserts) create or rename workloads and templates
  explicitly (private API key).

## Quick Start

```python
from coolhand import Coolhand, with_context

Coolhand(api_key="your-api-key")

with with_context(
    reporter="my-app",
    workload={"key": "Parser#call", "name": "Parser#call"},
    template={"key": "Parser#call/squish:L2", "name": "Parser#call · squish L2"},
):
    client.chat.completions.create(...)  # captured with the declared identity
```

A `key` is find-or-created on the server. An `id` references an existing record (an
unknown or foreign id is rejected, and the log is **not saved**). If both are given on a
template, the `id` wins.

As a decorator (plain, `async def` or generator functions; a generator is declared only
while its body runs, not while it is suspended at a `yield`):

```python
@with_context(workload={"key": "Summarizer#run"})
async def run(ticket):
    ...
```

## `with_context`

```python
with_context(*, reporter=None, workload=None, template=None)
```

| Argument | Shape | Notes |
|---|---|---|
| `reporter` | `str` | Name of the system declaring the identity. |
| `workload` | `{"id" \| "key", "name"?, "description"?}` | `description` is at most 1,000 characters. |
| `template` | `{"id" \| "key", "name"?}` | A template `key` needs a workload (see below). |

Only those fields are forwarded; anything else in the mapping is dropped.

### Rules the server applies

- A workload on its own gets a derived `<workload key>/default` template.
- A template `key` with **no workload** is rejected and the log is not saved. A template
  `id` needs no workload.
- Ingest never renames or re-attributes an existing record: `name`, `description` and
  `reporter` apply only when a record is first created.
- Public-key callers are capped at 200 declared workloads and 1,000 templates per client.
  Over the cap the log is still saved, the declaration is ignored, and the server adds a
  message to the response `warnings`. The SDK logs those warnings; it never raises on
  them. Private keys are uncapped.
- Older servers ignore the unknown `declared` key, so sending it is safe.

### Nesting

Per top-level field (`reporter`, `workload`, `template`) the innermost value wins.

```python
with with_context(reporter="app", workload={"key": "A"}, template={"key": "A/t"}):
    with with_context(reporter="batch"):
        ...  # reporter "batch", workload A, template A/t

    with with_context(workload={"key": "B"}):
        ...  # workload B; the outer template is NOT inherited
```

- A `workload` or `template` mapping is **replaced wholesale**, never merged key by key
  (`{"id": x}` merged with `{"key": y}` would send both, and the server prefers `id`).
- A block that sets a `workload` without a `template` drops the outer template, so a call
  is never paired with another workload's template.
- A block that sets only a `template` keeps the outer workload.

### Validation

`with_context` validates before your code runs, never inside the host's LLM call, and
raises `ValueError` for:

- a `workload` or `template` that is not a mapping, or has neither `id` nor `key` (a
  blank or whitespace `id` counts as not given);
- a `key` over 255 characters, with leading or trailing whitespace, with control or
  invisible-format characters, with a leading, trailing or doubled `/`, or that is not
  valid text (such as a lone surrogate). `#`, `:` and `·` are fine;
- a `description` over 1,000 characters;
- a template `key` with no workload in this or an enclosing `with_context` (checked when
  the block is entered).

The server drops a whole log on a bad declaration, so these are caught client-side.

### When the context is read

The declaration is **snapshotted when the call is captured**, before the log is queued.
Background delivery never reads the context, so a log still in the queue after your block
exits keeps its identity, and a streamed response consumed later is still attributed to
the request that opened it.

### Threads and workers

`with_context` uses `contextvars`, so it follows `await` and tasks created by `asyncio`
(including `asyncio.to_thread`). It is **not** inherited by a plain `threading.Thread` or
a `ThreadPoolExecutor` worker. Either enter `with_context` inside the thread, or run the
work under a copy of the current context:

```python
import contextvars

with with_context(workload={"key": "Report#build"}):
    ctx = contextvars.copy_context()
    pool.submit(ctx.run, call_llm)
```

For task queues such as [Dramatiq](./dramatiq.md), enter `with_context` **inside the
actor body** (or decorate the actor function), not where the message is enqueued; the
worker thread does not see the enqueuing thread's context.

## Explicit Upserts

Both require your **private** API key and raise `CoolhandAPIError` (with `.status`) on a
non-2xx response, like the other services. Each has an async twin, `aupsert`.

```python
ch = Coolhand(api_key="your-private-api-key")

ch.workload_service.upsert(
    "Parser#call", name="Parser#call", description="Squishes prompts", reporter="my-app"
)

ch.template_service.upsert(
    "Parser#call/squish:L2",
    workload={"key": "Parser#call"},  # or {"id": "<hashid>"}
    name="Parser#call · squish L2",
)
```

- `WorkloadService.upsert(key, *, name=None, description=None, reporter=None)` →
  `PUT /api/v2/workloads/by_key/<key>`
- `TemplateService.upsert(key, *, workload=None, name=None, reporter=None)` →
  `PUT /api/v2/llm_request_templates/by_key/<key>`. `workload` is sent flat as
  `workload_id` or `workload_key` (`id` wins if both are given).

Only the fields you pass are sent, so an update changes only those. The key is immutable;
a new `name` renames. The record is returned; `201` means created and `200` updated.

The route is a glob over the key: `/` stays literal and every other reserved character is
percent-encoded, so `Parser#call/squish:L2` is requested as `Parser%23call/squish%3AL2`.
Blank keys and `.`, `..` or empty path segments are rejected with `ValueError` before any
request.

Errors: `422` for a taken name, a missing workload when creating, a different workload for
an existing template, or a key held by a deprecated template; `401` for a missing, invalid
or public key.

These calls use their own HTTP client, outside the capture patches, so they never appear
as captured LLM calls.

## Not Covered Yet

- Declaring `ClassName#method` automatically for wrapped functions, and opt-in framework
  hooks (the Ruby SDK ships both). Use `with_context` directly for now.
- Listing and searching workloads.
