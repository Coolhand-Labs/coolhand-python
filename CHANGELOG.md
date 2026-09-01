# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Added
- **OpenCode interception** — `DEFAULT_INTERCEPT_ADDRESSES` now includes `opencode.ai` (OpenCode's Zen model gateway), so OpenCode API calls are captured automatically without any configuration change. Also lists `api.opencode.ai` explicitly for documentation purposes — it's a commonly-misconfigured host observed in real client traffic, not a valid OpenCode endpoint, and matching against it is already implied by the `opencode.ai` substring match, but naming it here makes the known-misconfiguration case discoverable when reading the address list.
- **`search_templates(...)` / `get_template(id)` + a new `TemplateService`** — read back the LLM request templates your logs are matched against, via the new `GET /api/v2/llm_request_templates` and `GET /api/v2/llm_request_templates/{id}` endpoints. Requires the **private** API key (the public key is write-only on this API and is rejected exactly like an invalid key). `search_templates` filters on `search` / `workload_id` / `status` / `include_deprecated` / `include_system` plus `page` / `per`, all keyword-only, and returns `{"templates": [...], "pagination": {...}}` newest-first. `pagination` is sourced from the endpoint's `X-Page` / `X-Per-Page` / `X-Total-Count` / `X-Total-Pages` response headers, never recomputed from the number of rows returned. `get_template` adds `user_prompt_pattern` / `system_prompt_pattern`, which the list omits, and reaches deprecated and system templates by id with no opt-in flag. Search is a *parameter* on the list endpoint rather than a route of its own, so this is one method, not a list/search pair. Both are also exposed on `Coolhand`. New exported names: `TemplateService`, `get_template_service`, `CoolhandAPIError`, `LlmRequestTemplateSummary`, `LlmRequestTemplateDetail`, `LlmRequestTemplateStatus`, `Pagination`, `SearchTemplatesResponse`. See [docs/templates.md](./docs/templates.md). (#98)
- **`Config.timeout`** — HTTP timeout in seconds for the new read methods, defaulting to 30. It is deliberately longer than the server's own 10-second statement timeout: a shorter client timeout would abort the connection just before an expected `504` arrived, turning a reportable server answer into an opaque network error. The existing write paths keep their own fixed 10s and ignore this field.
- **Opt-in live test suite (`make test-live`, `tests/live/`)** — exercises the template methods against a real Coolhand server with no mocking, driven by `COOLHAND_LIVE_BASE_URL` / `COOLHAND_LIVE_API_KEY`. Excluded from `make verify` rather than conditionally skipped inside it, so CI (which has neither a server nor a private key) stays green without any test being marked skipped — the tests are not collected at all unless you opt in. Every request it makes is read-only.

### Notes
- **The template endpoints depend on a backend change that may not yet be live in production.** They ship in [Coolhand-Labs/coolhand#1376](https://github.com/Coolhand-Labs/coolhand/pull/1376); against a backend that has not deployed it, `search_templates` / `get_template` will 404. Confirm your target Coolhand backend before relying on them.
- **The read methods raise where the write methods return `None`.** `create_feedback` and the auto-monitor's submission path log and return `None` on failure; `search_templates` / `get_template` raise `CoolhandAPIError` instead, because a caller reading data has to be able to tell a `404` from a `504`. The HTTP status is on `.status`, so no string-matching on the message is needed.
- **`search_templates` is not a port of the `search_templates` MCP tool and does not match its numbers.** `log_count` here counts only directly-collected client logs — the same records `GET /api/v2/llm_request_logs?template_id=...` returns — so it excludes evals, bakeoff comparisons and synthetic logs, and is often lower than the MCP tool's count. Templates on archived workloads are also returned rather than hidden, so the list agrees with `get_template`.
- **A `504` from either method is expected and retryable, not a server fault.** `log_count` aggregates over `llm_request_logs` and is bounded by a 10-second statement timeout, so the `Unmatched` bucket in particular can exceed it. Narrow with `workload_id` / `search` / a smaller `per` and retry.

## [0.5.0] - 2026-07-30

### Added
- **`FeedbackResponse.workload_id` added (`str | None`)** — the server now includes this as a hashid on responses.
- **Dramatiq + pydantic-ai support verified** — Coolhand's httpx patch works out of the box with Dramatiq thread-based workers running pydantic-ai agents (via `AnthropicModel` / `OpenAIModel`). See [docs/dramatiq.md](./docs/dramatiq.md) for a quick start, a what-works/what-doesn't table, and workarounds for the two known gaps (process-based workers, per-task session correlation). Includes a runnable example (`examples/dramatiq_pydantic_ai.py`) and 8 integration tests. (#57)

### Fixed
- **Double-logging in the auto-monitor interceptor** — a `contextvars.ContextVar` reentrancy guard now prevents the same intercepted call from being logged twice when it re-enters `send()` internally (e.g. a `requests`→httpx adapter chain triggering both `patched_requests_send` and `patched_send` for one logical request). This eliminated the 2–6x duplicate submissions observed server-side via `llm_provider_unique_id` collision detection. No API changes; concurrent requests on separate threads or asyncio Tasks are unaffected. (Closes #48, #58)

### Security
- **Query-string redaction now fails closed** — if `_sanitize_url`'s redaction logic hits an unexpected error, it now strips the entire query string instead of falling back to the raw, unredacted URL (which could have contained an `api_key`/`token`/`secret` param).
- **Expanded header redaction** — `SENSITIVE_HEADERS` now also masks `cookie`, `set-cookie`, `proxy-authorization`, `x-amz-security-token`, and `x-amz-signature`, so session cookies and AWS SigV4 credentials aren't forwarded unmasked when a custom `intercept_addresses` target (e.g. AWS Bedrock) sends them.

### Breaking changes
- **`llm_request_log_id` in `FeedbackResponse` is now `str | None`, not `int`** — the Coolhand API now returns this as a hashid, matching every other external-facing identifier on the record (it previously leaked the raw integer foreign key). `FeedbackData.llm_request_log_id` (the `create_feedback` input field) is now typed `int | str | None` — existing callers passing a raw integer are unaffected; the server still accepts either format on write. Nothing in this SDK's own logic depended on the previous numeric type (it was only ever logged or checked for presence), so this is a type-level breaking change only — no runtime behavior changes beyond the `TypedDict` definitions.
- **`FeedbackResponse.id` is now `str`, not `int`** — the Coolhand server has actually returned a hashid for this field for some time; the type was simply wrong. This is a type-only correction (no server behavior change), but is still breaking for code type-checked against the old `int` type.
- **Removed `FeedbackResponse.workload_hashid`** — this field was speculative and never actually returned by the server (only accepted as a write-side parameter, which remains on `FeedbackData`); `workload_id` above is the real hashid-bearing field on responses. Since the server never populated it, no caller could have received a real value through it.

## [0.4.3] - 2026-06-22

### Added

- **OpenRouter interception** — `DEFAULT_INTERCEPT_ADDRESSES` now includes `openrouter.ai`, so calls routed through OpenRouter's API are captured automatically without any configuration change.

## [0.4.2] - 2026-06-03

### Added

- **Vertex AI and Cloudflare AI Gateway interception** — `DEFAULT_INTERCEPT_ADDRESSES` now includes `aiplatform.googleapis.com` (covers the OpenAI-compatible `/chat/completions` endpoint in addition to the existing `:generateContent`/`:streamGenerateContent` path patterns) and `gateway.ai.cloudflare.com` (Cloudflare AI Gateway proxy). (#46)
- **Expanded default Vertex AI exclusions** — `default_exclude_api_patterns.json` now excludes common non-LLM Vertex AI resource paths (datasets, training pipelines, feature stores, indexes, tensorboards, etc.) to limit inadvertent capture of non-LLM traffic via the `aiplatform.googleapis.com` domain match. (#46)

### Fixed

- **Copilot interceptor now captures full token usage** — `inputTokens`, `cacheReadTokens`, `cacheWriteTokens`, `reasoningTokens`, and `cost` were previously dropped because they arrive in a separate `assistant.usage` event that fires just before `assistant.message`. The interceptor now caches the `assistant.usage` payload by session and merges it into the response body when `assistant.message` fires. (#50)

## [0.4.1] - 2026-06-03

### Fixed

- **Copilot interceptor compatibility with github-copilot-sdk 1.0** — `patched_request` now accepts and forwards `**kwargs` to the original `JsonRpcClient.request`. SDK 1.0 added the keyword-only `on_response_inline` parameter (used by `create_session`); the old wrapper raised `TypeError` for any call that passed it. The fix is forward-compatible: any future kwargs the SDK adds will also pass through without a code change.

## [0.4.0] - 2026-05-13

### Added

- **`sentiment` field** on `FeedbackData` and `FeedbackResponse` — string `"like"`, `"dislike"`, or `"neutral"`. This is now the preferred way to express feedback polarity; `like` (bool) is deprecated. (#37)
- **`workload_hashid` field** on `FeedbackData` and `FeedbackResponse` — associates feedback with a specific workload. (#37)
- **`collector` field** on `FeedbackData` — callers can now override the SDK-generated collector string; the SDK default is used only when this field is absent. (#37)
- **`base_url` configuration** — `Coolhand` and `FeedbackService` now accept a `base_url` kwarg and read a `COOLHAND_BASE_URL` environment variable. When unset, behavior is unchanged (defaults to `https://coolhandlabs.com`). Intended for self-hosted deployments and staging environments. (#21)
- Shared `src/coolhand/_config.py` module — houses `_normalize_base_url`, `_DEFAULT_BASE_URL`, and `_ssl_context` to avoid cross-module private imports between `client.py` and `feedback_service.py`.
- **`certifi` is now a required dependency** — a shared SSL context layering certifi's Mozilla CA bundle on top of the system trust store is built once in `_config.py` and used by all outbound `urlopen` calls. Fixes `CERTIFICATE_VERIFY_FAILED` on macOS python.org installs; `SSL_CERT_FILE` and enterprise CA setups continue to work. (#39)
- **Band Guesser example app** (`examples/band-guesser`) — FastAPI demo that exercises all three coolhand capture methods in one app: GitHub Copilot SDK (JSON-RPC), GitHub Models via httpx, and GitHub Models via `azure-ai-inference` (`requests` transport). Also validates correct patch ordering alongside OpenTelemetry instrumentors and structlog.
- **`requests` library interception** — `requests.Session.send` is now patched alongside httpx when `patch()` is called, enabling monitoring of any SDK that uses `requests` as its HTTP transport (e.g. `azure-ai-inference`, `azure-openai`, and other `azure-core`-based SDKs). `requests` remains an optional dependency; the patch is silently skipped if it is not installed. (#18, closes #12)

### Changed

- **`like` is deprecated** — use `sentiment` instead. Passing `like` without `sentiment` emits a `DeprecationWarning`. The SDK auto-converts `like=True` → `sentiment="like"` and `like=False` → `sentiment="dislike"` before sending, then strips `like` from the wire payload entirely. (#37)
- `base_url` validation rejects non-`https://` values at construction time. `http://localhost` and `http://127.0.0.1` (and `http://[::1]`) are allowed for local development. Hostname check uses `urlparse` to block subdomain and userinfo spoofing (e.g. `http://localhost.attacker.com`, `http://localhost@attacker.com`).
- `Config` TypedDict gains a `base_url: str` field.
- **Minimum Python version raised to 3.10** — Python 3.8 (EOL October 2024) and 3.9 (EOL October 2025) are no longer supported. If you need to stay on Python 3.7–3.9, pin to `coolhand<0.4.0`.

### Breaking changes

- **`like` is no longer required** — `create_feedback` no longer raises `ValueError` when `like` is absent. Callers using that exception as input validation will now silently send sentiment-less feedback. (#37)
- **`like` field is stripped from the wire payload** — even when the caller explicitly provides it, `like` is removed before the HTTP request is sent. Callers inspecting the raw request body should use `sentiment` instead. (#37)

### Security

- **pytest** upgraded 7.4.0 → 9.0.3 (CVE-2025-71176 — insecure tmpdir permissions)
- **black** upgraded 24.3.0 → 26.3.1 (CVE-2026-32274 — path traversal via cache filename)

### Internal

- `pytest-asyncio` upgraded 0.21.1 → 1.3.0 for pytest 9.x compatibility
- CI matrix updated to Python 3.10, 3.11, 3.12

## [0.3.0] - 2026-04-28

### Added

- **GitHub Copilot SDK interception** via `JsonRpcClient` patch — captures Copilot completions without any code changes to existing integrations (#13)
- **Google Gemini API support** — intercepts `generateContent` and `streamGenerateContent` calls; `x-goog-api-key` header is automatically redacted (#7)
- **GitHub Models endpoint** — `models.inference.ai.azure.com` added to the default set of monitored addresses (#10)
- **URL query parameter sanitization** — sensitive parameters (`key`, `api_key`, `token`, etc.) are automatically redacted from logged URLs (#4)

### Changed

- URL matching logic refactored from hostname-only to full-URL substring matching, enabling path-based differentiation required for Gemini's API structure

### Removed

- SDK initialization heartbeat (added in #15, removed in #16 during review)

### Internal

- Black formatter upgraded 23.3.0 → 24.3.0 (#2)
- Flake8 linting fixes and `.flake8` config added (#5)

## [0.2.0]

Initial public release with OpenAI and Anthropic monitoring via httpx patching.
