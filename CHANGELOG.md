# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

## [0.7.2] - 2026-09-20

### Fixed
- **`CoolhandDramatiqMiddleware` now applies constructor-kwarg Coolhand config in every worker process.** Previously a freshly spawned worker's `after_process_boot` built a bare `Coolhand()`, so config passed to the parent's `Coolhand(api_key=..., intercept_addresses=..., ...)` silently never reached workers — only `COOLHAND_*` environment variables did. The middleware now accepts the same `config`/keyword arguments as `Coolhand(...)` and, in each worker, keeps the existing instance if it already matches that config or otherwise replaces it. A replaced instance is only unregistered from `atexit`, not shut down: in a forked worker it is a copy of the parent's instance, and flushing it would re-deliver the parent's in-flight interactions once per worker. A failure constructing the replacement is logged and swallowed rather than crashing the worker. `CoolhandDramatiqMiddleware()` with no arguments behaves as before. See [docs/dramatiq.md](./docs/dramatiq.md). (#130)
- **Copilot requests that never receive an `assistant.message` are no longer silently dropped.** If the model errors out or rejects a request without emitting the notification (for example, context window exceeded), the interaction was evicted from the interceptor's pending table without ever being logged. It is now reported to Coolhand as an error (`"no assistant.message event received"`) 60 seconds after the `session.send` acknowledgement (`COPILOT_INTERCEPTOR_FALLBACK_TIMEOUT`), and the stale-entry sweep reports any entry the fallback didn't already report. (#131)

### Security
- **anyio 4.13.0 → 4.14.2**, fixing CVE-2026-63374 and CVE-2026-64847 in the development/test dependency lock (`uv.lock`); `pip-audit` in CI had been failing on both. anyio is a transitive dependency of the optional integrations, not a runtime dependency of `coolhand` itself. (#138)

### Notes
- **A Copilot response slower than 60 seconds is now logged twice.** The fallback error is reported at 60 seconds without evicting the pending entry, so a legitimately slow `assistant.message` that arrives afterwards is still delivered and logged as a success. Consumers will see an error record followed by a success record for the same request in that case. (#131)

### Internal
- Dramatiq-based tests and `examples/dramatiq_pydantic_ai.py` now bound `broker.join()` with an explicit timeout and always stop the worker in a `finally`, so a permanently failing actor fails in seconds rather than hanging through Dramatiq's default retry backoff; CI jobs also gained `timeout-minutes`. (#136)
- Added test coverage for the async httpx SSE wrappers (`aiter_bytes`, `aiter_text`, `aiter_raw`), empty async streams, and the `requests` interceptor's binary-content-type handling. (#135)
- `examples/band-guesser`'s `azure` and `azure-sdk` modes now call a Microsoft Foundry / Azure OpenAI deployment configured via `AZURE_INFERENCE_ENDPOINT`, `AZURE_INFERENCE_KEY` and optional `AZURE_INFERENCE_MODEL`, since GitHub Models (`models.github.ai`) was retired on 2026-07-30. The `prep-release` skill's band-guesser step was updated to match. (#139)

## [0.7.1] - 2026-09-17

### Added
- **Complete Azure inference URI coverage.** `DEFAULT_INTERCEPT_ADDRESSES` previously recognized only two Azure hosts, so most Azure inference traffic was silently never captured. Now includes Azure OpenAI's GA `/openai/v1/` paths, Azure AI Foundry (`services.ai.azure.com`), Azure AI Services (`cognitiveservices.azure.com`), serverless/MaaS deployments (`inference.ai.azure.com`, `models.ai.azure.com`), Azure Machine Learning managed online endpoints (`inference.ml.azure.com`), and the US Gov / China sovereign-cloud variants of each. The multi-service AI Services and Foundry hosts are path-anchored to `/openai/` and `/models/` so Speech, Vision, Language and Content Safety traffic on the same hostname is not captured; the Foundry portal domain `ai.azure.com` is deliberately excluded. `DEFAULT_INTERCEPT_ADDRESSES` is now re-exported from the package root, matching `DEFAULT_EXCLUDE_API_PATTERNS`. The deny-list gains `/openai/{files,batches,fine_tuning,models}` and their `/openai/v1/` twins, so fine-tuning uploads and `models.list()` calls stay out of the logs. (#129)

### Security
- **Azure OpenAI "On Your Data" datastore credentials in request bodies are now redacted.** Header and query-param sanitization never saw these — On Your Data carries datastore credentials in the request body itself (`data_sources[*].parameters.authentication.key`, legacy `dataSources[*].parameters.connectionString`/`embeddingKey`, Elasticsearch `encoded_api_key`, and similar auth shapes). New `_sanitize_body` redacts them, scoped to the `data_sources`/`dataSources` config subtree so message content and tool schemas are still logged verbatim. (#129)
- **`Ocp-Apim-Subscription-Key` (Azure AI Services' canonical key header) is now redacted.** It was previously forwarded in cleartext — `"api-key"` is not a substring of it, so the existing header redaction missed it entirely. Added to both `SENSITIVE_HEADERS` and `SENSITIVE_QUERY_PARAMS`. (#129)

### Breaking changes
- **`intercept_addresses=[]` now means "capture nothing"** instead of silently falling back to the default address list. If you were passing an empty list expecting the defaults, omit the argument instead. `start_monitoring` also now resets the interceptor's module globals on every instance, so a prior instance's `intercept_addresses`/`exclude_api_patterns` override no longer leaks into a later default instance. (#129)

### Internal
- Dependency bumps: ruff 0.16.3 → 0.16.7 (#127), dramatiq 2.2.0 → 2.2.1 (#126), pytest-asyncio 1.3.0 → 1.4.0 (#125), build 0.10.0 → 1.6.1 (#124), astral-sh/setup-uv GitHub Action 10.0.1 → 10.1.0 (#122).

## [0.7.0] - 2026-09-13

### Added
- **ElevenLabs interception** — `DEFAULT_INTERCEPT_ADDRESSES` now includes `api.elevenlabs.io`, so ElevenLabs API calls (e.g. text-to-speech) are captured automatically without any configuration change. Brings the Python SDK to parity with coolhand-ruby, which has included this address for some time. (#38)
- **`coolhand.integrations.dramatiq.CoolhandDramatiqMiddleware`** — a Dramatiq middleware that activates Coolhand monitoring in every worker process via the `after_process_boot` lifecycle hook, closing the "process-based workers" gap documented in [docs/dramatiq.md](./docs/dramatiq.md): workers started with a fresh interpreter (`python -m dramatiq myapp`) previously began without the httpx patch applied. `dramatiq` remains a soft dependency — importing the middleware without it installed raises `ImportError` with an install hint. (#60)

### Fixed
- **Binary response bodies (audio/video/image) are no longer captured as raw bytes.** Responses with an `audio/*`, `video/*`, `image/*`, or `application/octet-stream` content type — now reachable via the new ElevenLabs interception above, among others — are logged with the body replaced by the placeholder string `"[binary]"` instead of the raw content, avoiding large non-text payloads being sent to Coolhand. (#38)

## [0.6.0] - 2026-09-08

### Added
- **OpenCode interception** — `DEFAULT_INTERCEPT_ADDRESSES` now includes `opencode.ai` (OpenCode's Zen model gateway), so OpenCode API calls are captured automatically without any configuration change. Also lists `api.opencode.ai` explicitly for documentation purposes — it's a commonly-misconfigured host observed in real client traffic, not a valid OpenCode endpoint, and matching against it is already implied by the `opencode.ai` substring match, but naming it here makes the known-misconfiguration case discoverable when reading the address list.
- **`search_templates(...)` / `get_template(id)` + a new `TemplateService`** — read back the LLM request templates your logs are matched against, via the new `GET /api/v2/llm_request_templates` and `GET /api/v2/llm_request_templates/{id}` endpoints. Requires the **private** API key (the public key is write-only on this API and is rejected exactly like an invalid key). `search_templates` filters on `search` / `workload_id` / `status` / `include_deprecated` / `include_system` plus `page` / `per`, all keyword-only, and returns `{"templates": [...], "pagination": {...}}` newest-first. `pagination` is sourced from the endpoint's `X-Page` / `X-Per-Page` / `X-Total-Count` / `X-Total-Pages` response headers, never recomputed from the number of rows returned. `get_template` adds `user_prompt_pattern` / `system_prompt_pattern`, which the list omits, and reaches deprecated and system templates by id with no opt-in flag. Search is a *parameter* on the list endpoint rather than a route of its own, so this is one method, not a list/search pair. Both are also exposed on `Coolhand`. New exported names: `TemplateService`, `get_template_service`, `CoolhandAPIError`, `LlmRequestTemplateSummary`, `LlmRequestTemplateDetail`, `LlmRequestTemplateStatus`, `Pagination`, `SearchTemplatesResponse`. See [docs/templates.md](./docs/templates.md). (#98)
- **`Config.timeout`** — HTTP timeout in seconds for the new read methods, defaulting to 30. It is deliberately longer than the server's own 10-second statement timeout: a shorter client timeout would abort the connection just before an expected `504` arrived, turning a reportable server answer into an opaque network error. The existing write paths keep their own fixed 10s and ignore this field.
- **Opt-in live test suite (`make test-live`, `tests/live/`)** — exercises the template methods against a real Coolhand server with no mocking, driven by `COOLHAND_LIVE_BASE_URL` / `COOLHAND_LIVE_API_KEY`. Excluded from `make verify` rather than conditionally skipped inside it, so CI (which has neither a server nor a private key) stays green without any test being marked skipped — the tests are not collected at all unless you opt in. Every request it makes is read-only.
- **`acreate_feedback`** (module-level function, plus `Coolhand.acreate_feedback` / `FeedbackService.acreate_feedback`) — an async twin of `create_feedback` for callers running inside an event loop. It runs the same blocking HTTP POST on a worker thread via `asyncio.to_thread`, so it never blocks the caller's event loop. `create_feedback` itself is unchanged and remains fully synchronous.

### Fixed
- **Auto-monitoring no longer stalls the asyncio event loop for up to 10 seconds per captured request.** `CoolhandClient.flush()` used to POST each captured interaction to Coolhand synchronously — including from inside the coroutine that patches `httpx.AsyncClient.send` — so a slow or unreachable Coolhand backend blocked the *entire* event loop (not just the current request) for up to the 10-second `urlopen` timeout, on every concurrent async LLM call. `flush()` now hands interactions off to a bounded background dispatch queue drained by a single worker thread instead of POSTing inline; delivery still uses the same 10-second per-request timeout, it just no longer runs on the caller's thread. Also applies to the sync httpx/`requests`/Copilot JSON-RPC interceptors, which funnel through the same `flush()`.
- **`flush()`'s return value was inverted.** The old implementation cleared `self._queue` *before* comparing `success_count == len(self._queue)`, so the comparison was always against `0` — it returned `True` almost exclusively when **nothing** was submitted successfully, and `False` whenever any submission succeeded (this is why `examples/basic_usage.py` printed "⚠ Some data may not have been flushed" on the happy path). Any code branching on `flush()`'s return value was already getting the wrong answer; the rewrite below fixes this as a side effect of changing what the return value means in the first place.
- **A `2xx` status other than exactly `200`/`201` (e.g. `202 Accepted`) is now treated as a successful submission**, in both the auto-monitor's `flush()` path and `create_feedback`'s. Previously the check was an exact `== 200 or == 201` comparison, so anything else in the `2xx` range logged `"Unexpected status code"` and counted as a failure even though the server had accepted it.

### Security
- **The auto-monitor and feedback write paths (`CoolhandClient._send_one`, `FeedbackService._submit`) now refuse to follow HTTP redirects**, closing a credential/PII exfiltration path: `urlopen`'s default redirect handling would replay the `X-API-Key` header — and, for the auto-monitor, the full captured request/response body, which may contain end-user PII — to whatever host a 3xx response named. `TemplateService`'s read methods already refused redirects for this reason; the same `_RefuseRedirects` opener (now shared via `coolhand._config._build_opener`) is used by all three. A redirect now surfaces as an `HTTPError`/failed submission instead of being followed silently. Exploitable only if `base_url`/`COOLHAND_BASE_URL` points at a compromised, misconfigured, or malicious endpoint (e.g. a compromised edge/CDN in front of a self-hosted collector) — the default `coolhandlabs.com` endpoint does not redirect. No API changes; nothing to migrate.

### Changed
- **Constructing a `CoolhandClient` (or `Coolhand`) now registers an `atexit` hook on the instance itself**, not just on the `Coolhand` facade as before — a bare `CoolhandClient()` gets the same "attempt delivery on process exit" behavior. This keeps the instance alive for the life of the process (it's referenced from the `atexit` registry until `shutdown()` explicitly unregisters it), same as the previous `Coolhand`-only behavior; code that already relied on one long-lived `Coolhand`/`CoolhandClient` instance (the documented usage pattern) is unaffected. Constructing many short-lived clients directly, instead of the documented single long-lived instance, will now accumulate more of this per-instance overhead than before.

### Breaking changes
- **`CoolhandClient.flush()` no longer blocks on delivery.** It now returns as soon as queued interactions are handed off to the bounded background dispatch queue (capacity 1000; once full, new items are dropped and counted rather than blocking the caller), not after the HTTP POSTs complete. Its return value changed meaning again: `True` now means "nothing was dropped" — items are dropped when the dispatch queue is full, or when no delivery worker could be started (e.g. the interpreter is already shutting down); see the `### Fixed` entry above for what the return value meant, inverted, before this release. It's also `True` when no API key is configured, since items are discarded rather than dropped in that case (nowhere to submit them, not a delivery failure); `get_stats()["config"]["has_api_key"]` tells you why nothing is ever delivered. Code that called `flush()` as a synchronous delivery barrier (e.g. right before process exit) should call `shutdown()` instead — it waits up to 5 seconds for the background worker to drain before returning. `shutdown()` is safe to call more than once and is not a one-way switch: if you call it mid-program rather than at process exit, monitoring keeps working afterwards (a later `flush()` simply starts a fresh worker). `get_stats()["logging"]` gained `dropped_count`, `delivery_failure_count`, and `pending_delivery` to make queue backpressure and delivery failures (e.g. a bad API key) observable — previously a failing delivery was only visible as a per-item log line, with `get_stats()` giving no signal that anything was wrong.
- **`Coolhand.create_feedback`'s return type annotation corrected to `FeedbackResponse | None`** (was `FeedbackResponse`). Type-only — the method could always return `None` on error (its own docstring already said so), matching `FeedbackService.create_feedback` and the new `acreate_feedback`. Breaking only for callers type-checking against the previous, inaccurate annotation.

### Notes
- **The template endpoints depend on a backend change that has since shipped.** They require [Coolhand-Labs/coolhand#1376](https://github.com/Coolhand-Labs/coolhand/pull/1376), merged to the backend's `main` on 2026-09-01; the managed `coolhandlabs.com` backend should have it by the time this SDK version is released. Self-hosted backends that don't deploy from a recent `main` will 404 on `search_templates` / `get_template` until they update — confirm your target Coolhand backend's version before relying on them.
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
