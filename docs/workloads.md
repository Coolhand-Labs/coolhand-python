# Reading Workloads (Search)

`search_workloads` lists your workloads, the groups of prompt templates that make up one
task or agent, optionally with a cost and performance `metrics` rollup per workload, via
`GET /api/v2/workloads`. It is read-only, and there is no per-workload endpoint.

It requires your **private** API key. The public key is write-only on this API and is
rejected exactly like an invalid key.

A workload's `id` is its **hashid**, a string, never an integer. It is what the
`workload_id` filter on `search_templates` and `search_logs` expects.

**There is no `client_id`.** The client is always derived from the authenticating API
key.

## Basic Usage

```python
from datetime import datetime, timezone

from coolhand import WorkloadService

service = WorkloadService(api_key="your-private-api-key")

result = service.search_workloads(
    include_metrics=True,
    since=datetime(2026, 9, 1, tzinfo=timezone.utc),
    until=datetime(2026, 10, 1, tzinfo=timezone.utc),
    per=50,
)
for workload in result["workloads"]:
    print(workload["name"], workload["metrics"]["total_cost"])
```

The same method hangs off the main client:

```python
from coolhand import Coolhand

ch = Coolhand(api_key="your-private-api-key")
result = ch.search_workloads(include_metrics=True, days_back=30)
```

## `search_workloads(...)`

All arguments are keyword-only and optional.

| Argument | Type | Notes |
|---|---|---|
| `search` | `str` | Case-insensitive substring match against the workload name |
| `include_archived` | `bool` | Include archived workloads. Defaults to false server-side |
| `include_system` | `bool` | Include system workloads such as `Unmatched` and `Embedding Requests`. Defaults to false server-side |
| `include_templates` | `bool` | Add each workload's active templates and their routing patterns as `templates` |
| `include_metrics` | `bool` | Add a `metrics` rollup per workload across all of its templates |
| `days_back` | `int` | Rolling metrics window in days ending now (server default 28, max 365). Ignored when `since` is given |
| `since` | `datetime \| str` | Metrics window start, inclusive. Overrides `days_back` |
| `until` | `datetime \| str` | Metrics window end, exclusive; defaults to now |
| `page` | `int` | 1-based |
| `per` | `int` | Page size, default 25, max 100 (both enforced server-side). `per_page` is accepted on the wire as an alias; this SDK only sends `per` |

`metrics` is the same object `search_templates` returns per template, with the same
window rules, counters and SQL as the dashboard. See
[Metrics in templates.md](./templates.md#metrics).

### Return value

A `SearchWorkloadsResponse` — a dict with `workloads` (ordered by name) and `pagination`:

```python
{
    "workloads": [
        {
            "id": "j35494sql6yd",          # hashid
            "name": "Agent Engineering",
            "description": None,
            "archived": False,
            "system": False,
            "merged": False,
            "template_count": 2,            # active templates
            "draft_template_count": 1,
            "log_count": 160,               # every log on any of its templates
            "last_activity": "2026-08-25T20:03:47Z",
            # "metrics": {...}              only with include_metrics
            # "templates": [...]            only with include_templates
        }
    ],
    "pagination": {
        "current_page": 1,
        "per_page": 25,
        "total_count": 1,
        "total_pages": 1,
        "has_next_page": False,
        "has_prev_page": False,
    },
}
```

`pagination` is read off the `X-Page`, `X-Per-Page`, `X-Total-Count` and `X-Total-Pages`
response headers, which the endpoint always sends, never computed from the number of
rows returned.

`log_count` counts every log attached to any of the workload's templates, so it can
exceed the sum of the templates' own `log_count`, which counts only directly-collected
client logs.

## Errors

Raises `CoolhandAPIError` on any non-2xx response, with the HTTP status on `.status` and
the server's body in the message (`{"errors": {"<field>": ["msg"]}}` on a `422`):

| Status | When |
|---|---|
| `401` | No API key, an invalid key, or the public key (which cannot read) |
| `422` | A malformed `since`/`until`, a `since` not before `until`, a window over 365 days, or a bad `days_back`. The window is only checked when `include_metrics` is set |
| `504` | An aggregate exceeded the server's statement timeout. Retryable: narrow with `search` or a smaller `per` |

A `since` or `until` that is neither a `datetime` nor a string raises `ValueError`
before any request is made. `.status` is `None` on a transport failure or a non-JSON
body.
