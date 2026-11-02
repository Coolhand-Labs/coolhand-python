# Reading Logs (Search, Get + Failure Summary)

`search_logs` and `get_log` read back the logs Coolhand has collected through automatic
monitoring, via `GET /api/v2/llm_request_logs` and
`GET /api/v2/llm_request_logs/{id}`. Every log carries a per-log `cost`.
`failure_summary` rolls the failed ones up by error pattern, via
`GET /api/v2/llm_request_logs/failure_summary`.

Both require your **private** API key. The public key used to submit logs is write-only
and is rejected here, so construct a separate `LogService` (or `Coolhand`) with the
private key if your process also logs with the public key.

Log ids and the `template_id` / `workload_id` filters are **hashids**: the `id` and
`template_id` strings in a search result. A workload hashid comes from
[`search_workloads`](./workloads.md).

**There is no `client_id`.** The client is always derived from the API key.

## Basic Usage

```python
from datetime import datetime, timezone

from coolhand import LogService

service = LogService(api_key="your-private-api-key")

# The most expensive requests in September.
result = service.search_logs(
    since=datetime(2026, 9, 1, tzinfo=timezone.utc),
    until=datetime(2026, 10, 1, tzinfo=timezone.utc),
    min_cost=0.5,
    order="cost_desc",
    per=25,
)
for log in result["logs"]:
    print(log["id"], log["model"], log["cost"])

content = service.get_log(result["logs"][0]["id"])
print(content["cost_breakdown"])
```

The same methods hang off the main client:

```python
from coolhand import Coolhand

ch = Coolhand(api_key="your-private-api-key")
result = ch.search_logs(model="gpt-4o", days_back=7)
```

## `search_logs(...)`

All arguments are keyword-only and optional. They are dedicated named filters, not raw
Ransack predicates; `sort` is the only Ransack passthrough.

| Argument | Type | Notes |
|---|---|---|
| `template_id` | `str` | Template hashid |
| `workload_id` | `str` | Workload hashid; matches every template in it |
| `system_prompt_contains` | `str` | Case-insensitive substring of the system prompt |
| `user_prompt_contains` | `str` | Case-insensitive substring of the user prompt |
| `error_contains` | `str` | Case-insensitive substring of the failed request's error text. Pass a `failure_summary` `error_pattern` to list the logs behind it |
| `model` | `str` | Model name |
| `source_api` | `str` | e.g. `"openai"`, `"anthropic"`, `"vertex"` |
| `source_api_result` | `str` | Result status, e.g. `"success"` or `"failed"` |
| `source_application` | `str` | Exact match on `source_application` |
| `project_path` | `str` | Exact match against `metadata.project_path` |
| `unmatched_only` | `bool` | Only logs with no assigned template |
| `days_back` | `int` | Logs created in the last N days. Unrestricted when omitted. Ignored when `since` is given; with only `until`, the window starts `days_back` days before it |
| `since` | `datetime \| str` | Lower bound on `created_at`, inclusive. Overrides `days_back` |
| `until` | `datetime \| str` | Upper bound on `created_at`, exclusive; must be after `since`. With only `until`, the window starts `days_back` days before it (or is unbounded below if `days_back` is unset) |
| `min_cost` | `float` | Only logs whose per-log `cost` (USD) is at least this. Unpriceable logs are excluded. `0` is sent |
| `order` | `"cost_desc"` | Sort by per-log `cost`, highest first, replacing `sort`. Priceable logs only. Any other value is a `422` |
| `include_prompts` | `bool` | Add `system_prompt` / `user_prompt`, truncated to 500 characters |
| `sort` | `str` | Ransack sort such as `"created_at desc"`, sent as `q[s]`. Defaults to newest first |
| `include_total` | `bool` | Ask for `X-Total-Count` / `X-Total-Pages`, at the cost of a `COUNT(*)`. Off by default |
| `page` | `int` | 1-based |
| `per` | `int` | Page size, default 25, max 100 (both enforced server-side) |

`since` and `until` take a `datetime` (naive means UTC) or an ISO8601 string; a string
without an offset is UTC, a date alone is midnight UTC, and a `+hh:mm` offset is
URL-encoded for you. Anything else raises `ValueError` before any request.

On a client with a lot of history, combine `min_cost` / `order="cost_desc"` with `since`
or `days_back`: the cost is computed per log, so an unbounded query can time out (`504`).

### Return value

A `SearchLogsResponse` — a dict with `logs` (the bare array the endpoint returns) and
`pagination`:

```python
{
    "logs": [
        {
            "id": "abc123",
            "collector": "coolhand-python-0.8.0-auto",
            "source_api": "openai",
            "source_application": None,
            "metadata": {},
            "source_api_result": "success",
            "model": "gpt-4o",
            "template_id": None,         # hashid, or None when unmatched
            "template_name": None,
            "input_tokens": 100,
            "output_tokens": 50,
            "latency_ms": 250,
            "created_at": "2026-09-01T00:00:00Z",
            "updated_at": "2026-09-01T00:00:00Z",
            "ingest_evidence": {},
            "cost": 0.0042,              # USD; None when it cannot be priced
            # "system_prompt" / "user_prompt" only with include_prompts
        }
    ],
    "pagination": {
        "current_page": 1,
        "per_page": 25,
        "total_count": None,             # None without include_total
        "total_pages": None,
        "has_next_page": False,
        "has_prev_page": False,
    },
}
```

`cost` is priced by the same SQL as the dashboard, so tiered pricing, cached-token
discounts and reasoning tokens are applied. It is `None` when the log has no tokens or its
model has no pricing. Summing per-log `cost` can exceed a metrics `total_cost`, which
excludes failed logs and logs with a zero token count.

### Pagination

`X-Page` and `X-Per-Page` always come back; `X-Total-Count` and `X-Total-Pages` only with
`include_total=True`. Without them **`total_count` and `total_pages` are `None`**, not an
estimate, and `has_next_page` is true when the page came back full (there may be one
more, possibly empty, page). `has_prev_page` is `current_page > 1`. Leave `include_total`
unset for frequent polling.

## `get_log(log_id, ...)`

`log_id` is the log hashid. Optional keyword arguments:

| Argument | Type | Notes |
|---|---|---|
| `section` | `str` | `"full"`, `"beginning"` or `"end"` (default `"full"`). Only takes effect with `max_chars` |
| `max_chars` | `int` | Maximum characters per content field. Non-positive is a `422` |
| `search_query` | `str` | Return up to 5 matching snippets per field instead of the content. Must be non-blank |
| `include_thinking` | `bool` | Add `thinking_response`, an array of thinking blocks |

The result carries the fields above plus `url`, `system_prompt`, `user_prompt`,
`output`, `cost` and `cost_breakdown`. The breakdown is `None` when the log cannot be
priced; otherwise it holds `total_cost`, `input_cost`, `output_cost`,
`cached_input_cost`, `cache_creation_input_cost` and `reasoning_output_cost`. With
`search_query`, `matches` and `search_query` come back instead of the content.

`get_log` raises `ValueError` before any request if `log_id` is blank, not a string, or
a relative path segment (`.` / `..`), or if `search_query` is given but blank (the server
would silently return the content shape instead of a search result).

## `failure_summary(days_back=None)`

Rolls this client's recent failed requests up by error pattern and by template, and
sorts each pattern into whose problem it is. Failures inside a confirmed provider outage
are left out and counted in `provider_outage_excluded_count`. `days_back` is optional
(the server defaults to 7 and allows up to 36500); it is sent only when given and is not
validated client-side, so `0` or a negative value reaches the server's `422`.

```python
summary = ch.failure_summary(days_back=30)
print(summary["total_failed_count"], summary["actionable_summary"])

if summary["error_breakdown"]:
    top = summary["error_breakdown"][0]
    print(top["category"], top["count"], top["error_pattern"])

    # The logs behind that pattern.
    logs = ch.search_logs(error_contains=top["error_pattern"], days_back=30)["logs"]
```

The result is one `FailureSummaryResponse` dict (not paginated), with every key always
present:

| Key | Notes |
|---|---|
| `client_id`, `client_name` | The client the API key belongs to |
| `days_analyzed` | The window the server applied |
| `total_failed_count` | Every failed request in the window, across all patterns |
| `provider_outage_excluded_count` | Failures left out because they fell inside a confirmed provider outage |
| `error_breakdown` | The 50 most frequent patterns, each with `error_pattern`, `source_api` (or `None`), `count`, `first_seen_at`, `last_seen_at`, `category`, `example_error_details`, `example_log_ids` (up to 3) and `example_log_urls` |
| `by_template` | Per template: `template_id` (or `None`), `template_name`, `failed_count`, `error_patterns` (dicts; the definition does not type their keys), `example_log_ids`, `example_log_urls` |
| `actionable_summary` | `client_fixable_count` and `server_side_count`, counting every pattern |

`category` is one of `client_error` (a 4xx the caller can fix), `rate_limit` (429),
`server_error` (5xx), `provider_error` (no HTTP status) or `relayed_upstream` (an
upstream vendor's account or key error surfaced through a relay).

## Errors

All three methods raise `CoolhandAPIError` on any non-2xx response, with the HTTP status on
`.status`:

| Status | When |
|---|---|
| `401` | No API key, an invalid key, or the public key |
| `404` | `get_log` only: unknown id, another client's, or an internally generated record |
| `422` | A bad `template_id` / `workload_id` / `days_back`, a malformed or inverted `since` / `until`, a negative or non-numeric `min_cost`, an unknown `order`, or a non-positive `max_chars`; `failure_summary` also 422s on a non-positive `days_back` |
| `504` | The search exceeded the server's statement timeout. Narrow it with `since` or `days_back`; for `failure_summary`, retry with a smaller `days_back` |

`.status` is `None` on a transport failure or a non-JSON body.
