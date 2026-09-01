# Reading Templates (Search + Get)

`search_templates` and `get_template` read back the LLM request templates your logs are
matched against, via `GET /api/v2/llm_request_templates` and
`GET /api/v2/llm_request_templates/{id}`.

Both require your **private** API key. The public key is write-only on this API and is
rejected exactly like an invalid key.

This surface is read-only. Template creation, update and deprecation stay on the MCP
surface, and there is no version-history sub-resource.

> **This is not the `search_templates` MCP tool.** The numbers deliberately differ — see
> [Differences from the MCP tool](#differences-from-the-mcp-tool).

## Basic Usage

```python
from coolhand import TemplateService

service = TemplateService(api_key="your-private-api-key")

# List templates, newest first. The system buckets are hidden by default.
result = service.search_templates(search="summar", status="published")
for template in result["templates"]:
    print(template["name"], template["log_count"])

print(result["pagination"]["total_count"])

# Fetch one template, prompt patterns included.
detail = service.get_template(result["templates"][0]["id"])
print(detail["user_prompt_pattern"])
```

The same two methods hang off the main client, if you already have one:

```python
from coolhand import Coolhand

ch = Coolhand(api_key="your-private-api-key")
result = ch.search_templates(include_system=True)
```

## `search_templates(...)`

Search is a *parameter* on the list endpoint, not a route of its own, so there is one
method rather than a separate list/search pair.

All arguments are keyword-only and optional.

| Argument | Type | Notes |
|---|---|---|
| `search` | `str` | Case-insensitive **literal** substring match on the template name. `%` and `_` are escaped server-side, so they match themselves — do not escape them again |
| `workload_id` | `str` | Workload hashid. One that does not decode, or that belongs to another client, returns `422` rather than an empty list |
| `status` | `"draft"` / `"published"` / `"failure"` | Any other non-empty value returns `422` |
| `include_deprecated` | `bool` | Include templates with a non-null `deprecated_at`. Defaults to false server-side |
| `include_system` | `bool` | Include the `Unmatched` / `Ignored API Calls` buckets. Defaults to false server-side |
| `page` | `int` | 1-based |
| `per` | `int` | Page size, default 25, max 100 (both enforced server-side) |

**There is no `client_id`.** The client is always derived from the authenticating API
key and cannot be supplied by the caller.

### Return value

A `SearchTemplatesResponse` — a dict with `templates` and `pagination`:

```python
{
    "templates": [
        {
            "id": "tmpl123abc456",          # hashid, never the integer primary key
            "name": "Summarize ticket",     # never null, but may be blank on a draft
            "status": "published",          # str | None
            "version": "3",                 # str | None
            "group": "user_prompt",         # str | None
            "workload_id": "wkld789xyz123", # hashid, never null
            "workload_name": "Support",     # never null
            "system_template": False,
            "deprecated_at": None,          # ISO-8601 UTC; non-null means superseded
            "log_count": 42,
            "created_at": "2026-08-01T00:00:00Z",
            "updated_at": "2026-08-02T00:00:00Z",
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

Results are ordered newest first (`created_at DESC`, with a primary-key tiebreaker so
paging is stable).

**Prompt patterns are not in list rows.** They come from `get_template` only.

`pagination` is built from the `X-Page`, `X-Per-Page`, `X-Total-Count` and
`X-Total-Pages` response headers, which this endpoint always sends — unlike the log
search endpoint, it has no `include_total` opt-out. Totals are never recomputed from the
number of rows returned, so `total_count` describes the whole collection rather than the
page in hand.

### System templates

Every client is created with two system buckets, `Unmatched` and `Ignored API Calls`.
They are hidden unless you pass `include_system=True`, so **a client with no templates
of its own returns an empty list, not those two rows.** Each row carries a
`system_template` boolean so you never have to match on names.

`Unmatched` is the bucket to inspect when logs are misrouting.

## `get_template(template_id)`

`template_id` is the template hashid — the `id` field from a `search_templates` row.

Unlike the list, this applies no filtering beyond client ownership: a deprecated or
system template is reachable by id **with no opt-in flag**, since inspecting one of
those is the usual reason to fetch a template directly.

### Return value

An `LlmRequestTemplateDetail` — every list field above, plus the full untruncated
regexes the list omits:

| Field | Type |
|---|---|
| `user_prompt_pattern` | `str \| None` |
| `system_prompt_pattern` | `str \| None` |

Both are present as keys even when null.

`get_template` raises `ValueError` before making a request if `template_id` is blank,
not a string, or a relative path segment (`.` / `..`) — any of which would otherwise
resolve away to the list route and return an array where you expect one template.

## Errors

**The read methods raise; they do not return `None`.** This is the opposite of the write
methods (`create_feedback`, and the auto-monitor's own submission path), which log and
return `None` on failure. A caller reading data has to be able to tell a `404` from a
`504`, so failures surface as an exception instead.

Both methods raise `CoolhandAPIError`, which carries the HTTP status on `.status`:

```python
from coolhand import CoolhandAPIError, TemplateService

service = TemplateService(api_key="your-private-api-key")

try:
    result = service.search_templates()
except CoolhandAPIError as error:
    if error.status == 504:
        # Retryable: narrow the query rather than giving up.
        result = service.search_templates(workload_id="wkld789xyz123", per=10)
    else:
        raise
```

| Status | When | Body shape |
|---|---|---|
| `401` | Missing, empty, invalid, or public API key | `{"error": "..."}` |
| `404` | Unknown template id, **or** one belonging to another client | `{"errors": {"<model>": ["..."]}}` |
| `422` | Unrecognized `status`, or an undecodable/foreign `workload_id` | `{"errors": {"<param>": ["..."]}}` |
| `504` | The `log_count` aggregate exceeded the server's statement timeout | `{"errors": {"system": ["..."]}}` |

A template belonging to another client returns `404`, not `403` — its existence is not
disclosed.

`.status` is `None` when there was no HTTP response at all (a transport failure) or the
body was not JSON.

### `504` is expected, not a bug

`log_count` aggregates over `llm_request_logs`, and the `Unmatched` bucket can hold
every log that never matched a template. Every query behind these endpoints is bounded
by a 10-second statement timeout and returns `504` rather than hanging. Narrow with
`workload_id`, `search`, or a smaller `per` and retry.

Because the server's own budget is 10 seconds, the client-side HTTP timeout defaults to
30 seconds — a shorter one would abort the connection just before the `504` arrived.
Override it with `timeout` if your network is slower:

```python
service = TemplateService(api_key="your-private-api-key", timeout=120)
```

## Differences from the MCP tool

`search_templates` here is **not** a port of the `search_templates` MCP tool and must
not be treated as equivalent to it:

- **`log_count` counts only directly-collected client logs** — the same records
  `GET /api/v2/llm_request_logs?template_id=...` returns. Evals, bakeoff comparisons and
  synthetic logs are excluded, so this number is often lower than the MCP tool's.
- **Archived workloads are not filtered out.** The MCP tool hides templates whose
  workload has been archived; this endpoint returns them, so the list agrees with
  `get_template`, which can always fetch such a template by id. Narrow with
  `workload_id` instead.

## Verifying against a live server

The opt-in live suite exercises both methods against a real Coolhand server with no
mocking. It is deliberately excluded from `make verify` — CI has neither a server nor a
private key — and its tests are not collected at all unless you opt in, so a default run
never reports a skip that quietly proved nothing.

```bash
COOLHAND_LIVE_BASE_URL=http://127.0.0.1:3111 \
COOLHAND_LIVE_API_KEY=<your private key> \
make test-live
```

Every request it makes is read-only, so it is safe to point at a shared development
database. Both variables are required; a missing one is a hard failure rather than a
silent skip.
