# Reading Step Templates (List + Get)

A step template is a skill or other recurring step Coolhand detects in captured agent
sessions. A `prompt_step` matches a user prompt such as a slash command; a `tool_step`
matches tool usage. `list_step_templates` and `get_step_template` read them back, via
`GET /api/v2/step_templates` and `GET /api/v2/step_templates/{id}`. They are read-only;
there are no write endpoints.

Both require your **private** API key; the public key is rejected with a `401`. The
client is derived from the key, so there is no `client_id` argument.

## Basic Usage

```python
from coolhand import StepTemplateService

service = StepTemplateService(api_key="your-private-api-key")

result = service.list_step_templates(step_type="prompt_step", include_metrics=True)
for step_template in result["step_templates"]:
    print(step_template["name"], step_template["sessions_count"])

print(result["pagination"]["total_count"])

detail = service.get_step_template(result["step_templates"][0]["id"])
print(detail["metrics"]["notes"])
```

The same two methods hang off the main client:

```python
from coolhand import Coolhand

ch = Coolhand(api_key="your-private-api-key")
result = ch.list_step_templates(search="review")
```

## `list_step_templates(...)`

All arguments are keyword-only and optional.

| Argument | Type | Notes |
|---|---|---|
| `search` | `str` | Case-insensitive substring match on the step template name |
| `step_type` | `"prompt_step"` / `"tool_step"` | Only step templates of this type. Anything else is a `422` |
| `include_metrics` | `bool` | Add a [`metrics`](#metrics) object per row. Off by default, and the server caps `per` at 25 when it is set (`pagination["per_page"]` reports the size applied) |
| `page` | `int` | 1-based |
| `per` | `int` | Page size, default 25, max 100 (both enforced server-side) |

Returns a dict with `step_templates` (newest first) and `pagination`. The endpoint
renders a bare array; `pagination` is read from the `X-Page`, `X-Per-Page`,
`X-Total-Count` and `X-Total-Pages` response headers, in the same shape `search_templates`
returns.

## `get_step_template(step_template_id, ...)`

```python
step_template = ch.get_step_template(14)
lean = ch.get_step_template(14, include_metrics=False)
```

`step_template_id` is the integer `id` from the list (not a hashid). Unlike the list,
`metrics` is included by default; `include_metrics=False` skips the metrics queries. It
raises `ValueError` before any request if the id is not a positive integer.

## Fields

Every row has `id`, `name`, `step_type`, `status`, `user_turn_pattern` (nullable),
`created_at`, `updated_at`, `sessions_count` (distinct sessions it appeared in),
`occurrences_count` and `last_seen_at` (nullable). `metrics` is present only when
requested (or by default on `get_step_template`).

### Metrics

| `metrics` field | Meaning |
|---|---|
| `total_runs` | Runs counted; excludes scrubbed prompt-step sessions |
| `tool_stats` | `{name, pct_of_runs, avg_per_run}` list, or `None` when there is nothing to show |
| `turn_stats` | `{avg, median, high, single_turn_pct}` (`single_turn_pct` may be `None`), or `None` |
| `efficiency` | `{interactive, automated, all, unavailable_runs}`, or `None` for tool steps and step templates with no runs. Each cohort is `None` when it has no runs |
| `notes` | Plain-sentence explanations of each data gap that applies |

A cohort is `{runs, avg_turns_after, avg_tool_calls, avg_requests, token_runs,
tokens_available, median_tokens, p90_tokens, tokens_per_tool_call}`. The last three are
`None`, and `tokens_available` is `False`, when no run in the cohort has token data
(expected today).

## Errors

Both methods raise `CoolhandAPIError` on any non-2xx response, with the HTTP status on
`.status`:

| Status | When |
|---|---|
| `401` | No API key, an invalid key, or the public key |
| `404` | `get_step_template` only: unknown id, a draft or failed step template, or another client's |
| `422` | `list_step_templates` only: a `step_type` other than `prompt_step` or `tool_step` |
| `504` | A query exceeded the server's statement timeout. Retryable: narrow with `search` or a smaller `per` |

`.status` is `None` on a transport failure or a non-JSON body.
