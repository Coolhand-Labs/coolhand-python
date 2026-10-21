# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

## [0.10.1] - 2026-10-21

### Added
- **Copilot `session.send` bodies carry a top-level `system` string.** The nested `systemMessage.content` is now also surfaced as `system`, mirroring Anthropic's `body.system`, so the backend's existing system-prompt extractor recognises Copilot prompts it previously missed. `systemMessage` is left in place unchanged. A non-string or empty `content` is not promoted. (#207)
- **A warning for `?` or `#` in `exclude_api_patterns` and `intercept_addresses` entries.** `Coolhand(...)`, `set_exclude_api_patterns` and `set_intercept_addresses` now log a WARNING when an entry contains `?` or `#`, because patterns are matched against the host, port and path and so can never match a query string or fragment. The warning prints only the text before the first `?` or `#`, so a secret such as `?api_key=...` never reaches the logs. `*` is not flagged. Matching behavior is unchanged. (#206)

### Fixed
- **Cloud/remote Copilot sessions no longer silently lose their `session.create` params.** With `CopilotClient(enable_remote_sessions=True)` the `sessionId` is assigned by the server and absent from the request, so the system prompt and model were dropped for those sessions. The params are now buffered under a temporary key and re-keyed once the response supplies the real `sessionId`; this applies to both system-prompt capture and model caching. (#207)
- **Forked Dramatiq workers no longer reuse the parent's client state.** A lock held by another parent thread at fork time could hang the child's first capture, and the parent's queued interactions could be delivered again by every worker. `CoolhandDramatiqMiddleware.after_process_boot` now replaces a fork-inherited client's locks, dispatch queue and worker state and drops the parent's pending interactions, since the parent still delivers them. Config and session are kept, thread-based workers are unaffected, and a failed reset is logged without stopping monitoring. (#205)

### Changed
- **Docs for pattern matching list two over-matches.** `docs/configuration.md` now states that `*` is the only wildcard and `?`/`#` are literal, that a non-globbed host always includes its subdomains, and that a `/fragment` entry matches any path containing it (end a `host/path` entry with `/` to avoid sibling paths). (#206)

### Security
- **More credential spellings are redacted.** Headers: any name containing `secret` (for example `X-Client-Secret`, `X-Api-Secret`), plus `X-CSRFToken`, `X-XSRF-TOKEN` and `PRIVATE-TOKEN`. Query parameters: `private_token`, `private-token`, `access_key`, `access-key`, `accesskey`, `api_secret`, `api-secret`, `client-secret`, `session_token` and `session-token`. These previously reached Coolhand unmasked. Query-parameter names are still matched exactly (not as substrings, unlike headers), so a custom name such as `my_api_key` is not redacted.

- **`certifi` 2026.7.22 → 2026.10.19 in `uv.lock`**, fixing PYSEC-2026-4320, which the `Audit dependencies` step in CI had started failing on. `certifi` is a runtime dependency of `coolhand` (`certifi>=2017.4.17`) and the SDK uses it for TLS verification, but the lock only governs this repository's own environments: an application installing `coolhand` gets whatever `certifi` its own resolver picks, so upgrade `certifi` there if you are on 2026.7.22. (#208)

### Notes
- **Known gap:** the module-level interceptor locks (`httpx_interceptor._patch_lock`, `_intercept_count_lock`) are not reset in a forked child. See [docs/dramatiq.md](./docs/dramatiq.md) and [docs/supported-libraries.md](./docs/supported-libraries.md).

### Internal
- New regression tests that the API key never appears in exception text or logs from `FeedbackService` and `TemplateService` (`search_templates`, `get_template`, `upsert`). No runtime change. (#204)
- Development dependency updates: `mcp` 1.28.1 → 1.30.0 (#200), `pytest-mock` 3.15.1 → 3.16.0 (#203) and `ruff` 0.17.0 → 0.17.1 (#202).

## [0.10.0] - 2026-10-18

### Added
- **Interception of `httpx2` clients.** Recent releases of the OpenAI (3.x), Anthropic (1.x) and pydantic-ai (2.5x) SDKs are built on `httpx2`, a separate package whose clients never go through `httpx.Client.send`, so calls made through them were silently not captured. `patch()` now also wraps `httpx2.Client.send` and `httpx2.AsyncClient.send` (when `httpx2` is installed) with the same capture, redaction, allow/deny-list, streaming and error handling as `httpx`, and `unpatch()` restores them. Nothing to configure. If you upgraded those SDKs and noticed interactions stop appearing, this is the fix. `coolhand.testing` counts `httpx2` interceptions too, and `get_stats()["monitoring"]["patched_libraries"]` now lists the `httpx2` methods when they are patched. See [docs/supported-libraries.md](./docs/supported-libraries.md).
- **Declare the workload and template of captured calls.** `with_context(reporter=, workload=, template=)` is a context manager and decorator (plain, `async def` and generator functions) that declares the workload/template for LLM calls captured inside it, sent as `llm_request_log.declared` so the server skips template matching (older servers ignore the key). `WorkloadService.upsert` / `aupsert` and `TemplateService.upsert` / `aupsert` create or rename workloads and templates by stable key (private API key; failures raise `CoolhandAPIError`). `Coolhand` gains `workload_service`; `with_context` and `WorkloadService` are exported from the package root. The declaration is snapshotted at capture time and follows asyncio tasks and `to_thread`, but not plain threads unless run under `contextvars.copy_context()`. Nesting is per field: a `workload` without a `template` does not inherit the outer template. Invalid declarations raise `ValueError` before your code runs. See [docs/declared-workloads.md](./docs/declared-workloads.md). (#199)
- **Read workloads, cost metrics and logs.** New `search_workloads` (`GET /api/v2/workloads`), `search_logs` and `get_log` (`LogService`), on `Coolhand` and as `WorkloadService` / `LogService`. `search_templates` and `get_template` gain `include_metrics`, `days_back`, `since` and `until`; `search_logs` supports `since`/`until`, `min_cost`, `order="cost_desc"`, `source_application`, `project_path`, `include_total` and `sort`, and rows carry a nullable per-log `cost`. `since`/`until` take a `datetime` (naive means UTC) or an ISO8601 string. These need the **private** API key, raise `CoolhandAPIError` on any non-2xx, and expect a Coolhand server that has the matching endpoints deployed. Without `include_total`, `search_logs` pagination reports `total_count` / `total_pages` as `None` rather than guessing. The shared HTTP and pagination plumbing now lives in `_read_service.py`; `TemplateService` behavior and its public imports are unchanged. See [docs/workloads.md](./docs/workloads.md), [docs/logs.md](./docs/logs.md) and [docs/templates.md](./docs/templates.md). (#170)
- **`max_body_bytes` caps captured body size** (default 1 MiB; `None` or `0` disables). Over-limit request and response bodies, including the async streaming accumulator, are cut to the first `max_body_bytes` UTF-8 bytes and end with a `[truncated: N bytes, kept first M]` marker; the cap is applied after parsing and redaction, so it covers the httpx, httpx2, requests and Copilot interceptors. `ResponseData` gains `body_truncated`. See [docs/configuration.md](./docs/configuration.md). (#191)
- **`request_timeout` option** (seconds, default `30`) on `Coolhand`, `FeedbackService`, `TemplateService`, `WorkloadService`, `LogService` and `FeedbackLinkService`, bounding every call the SDK makes to the Coolhand API. It must be a number greater than zero and at most `2147483647`; `0`, negatives, `NaN`, infinity, booleans, non-numbers and oversized values raise `ValueError` at construction instead of failing opaquely on the first request. Applies to the connection and to each read, not to the whole request. (#197)
- **`coolhand.testing`** gives your own tests and CI a way to assert that interception really happened: `capture_interception_snapshot()` and `assert_interception_occurred(before, message=None)`. It catches the silent bypass where a client captured `httpx.Client.send` or `requests.Session.send` before `import coolhand`, and also unpatched transports, `stop_monitoring()`, and URLs outside the allow-list or inside the deny-list. Failures raise a plain `AssertionError` that explains likely causes. The counter is process-wide and counts interceptions, not delivery; Copilot JSON-RPC calls are not counted. See [docs/testing.md](./docs/testing.md). (#198)
- **Live provider smoke tests.** `make live-examples` runs `examples/live_openai.py` and `examples/live_anthropic.py` against real keys and confirms the log reached Coolhand; a missing key is reported as `SKIP`, never a pass. See [examples/README.md](./examples/README.md). (#196)

### Changed
- **`intercept_addresses` and `exclude_api_patterns` are now matched against the parsed URL, not as raw substrings.** Entries are matched against the host, port and percent-decoded path, with a documented syntax: bare host (and its subdomains), `host/path` prefix, `host:port`, `:port/path`, `/path` fragment, bracketed IPv6, and `*` globs. Query text, userinfo (`api.openai.com@evil.com`) and lookalike hosts (`api.openai.com.evil.com`) no longer match, and deny patterns match the path only. The host-less `:generateContent`, `:streamGenerateContent`, `:predict` and `:streamRawPredict` defaults are now limited to `googleapis.com` hosts (so TensorFlow Serving's `/v1/models/foo:predict` is no longer captured), with `*-aiplatform.googleapis.com` and `prediction.vertexai.goog` added for Vertex AI; Bedrock is matched by host; and localhost detection uses the parsed hostname (`localhost.example.com` is no longer skipped; `*.localhost` and all of `127.0.0.0/8` now are). **Migration:** a custom entry that relied on an arbitrary mid-URL substring (for example `"openai"` or a host-less `":predict"`) stops matching; rewrite it as a host, `host/path` or `/path` entry. Vertex AI behind a custom domain or proxy needs its own entry. See [docs/configuration.md](./docs/configuration.md). (#195)
- **The deny-list now fails closed.** If matching against `exclude_api_patterns` raises (for example a non-`str` entry), the request is treated as excluded and not captured; previously it was captured, which could record requests you meant to exclude (file uploads, fine-tuning). The allow-list is unchanged: a matching error means not captured. `Coolhand(...)` now logs a WARNING when either list is not a list/tuple (for example a bare string) or contains non-`str` entries. If you have a malformed list, capture stops for the affected requests, with a warning, until you fix it. (#190)
- **Captured interactions and feedback are now sent with a 30-second timeout instead of a fixed 10 seconds.** The fixed write timeout is gone; those paths use `request_timeout`. `shutdown()` still waits at most 5 seconds. A slow or unreachable backend can now hold the background delivery worker up to three times longer per interaction, so the dispatch queue may fill sooner before it starts dropping items. The older `timeout` key still works as an alias (`request_timeout` wins if both are set) but is now validated, so a previously accepted invalid value raises `ValueError` at construction. `TemplateService` and `FeedbackLinkService` no longer populate `config["timeout"]`; read `config["request_timeout"]` or the `.timeout` property. (#197)
- **`requests` calls made with `stream=True` are no longer captured with a body.** They are recorded as `body="[streaming]"` with `is_streaming=True` (also when `Session.stream` is set, or the content type is SSE/NDJSON), and the response body is never read. A `stream=True` response with a binary content type is now `[streaming]` rather than `[binary]`. If you relied on the captured body of a `stream=True` plain-JSON response, it is no longer recorded. (#189)
- **Bodies over `max_body_bytes` are logged as a truncated plain string instead of parsed JSON.** Set `max_body_bytes=None` to restore full-size capture. (#191)
- **Constructing a non-silent client no longer reconfigures the root logger.** `CoolhandClient(silent=False)` (and `Coolhand`) no longer calls `logging.basicConfig`; only the `coolhand` logger is configured (INFO when unset and not already more verbose, with a `StreamHandler` only if neither `coolhand` nor root has a handler). A host with root at WARNING and its own handlers now also sees coolhand INFO lines when `silent=False`, and an app that calls `basicConfig` after creating the client may see them twice. (#188)

### Fixed
- **Streaming responses through the async httpx/httpx2 wrappers no longer stall or get captured several times.** The wrappers forced `chunk_size=1024` on `aiter_bytes` and `aiter_raw`, so httpx held small server-sent events until a full kilobyte had arrived and a token stream reached the host application in batches. They also recorded each chunk once per layer (`aiter_lines` runs on `aiter_text` on `aiter_bytes` on `aiter_raw`), inflating the captured body 2–4× and burning the `max_body_bytes` budget. Arguments now pass through untouched and only the outermost running layer captures. This affected 0.9.0 and earlier.
- **`patch()` and `unpatch()` are now serialised and idempotent.** Two threads calling `patch()` at once (two `Coolhand()` constructions, or Dramatiq worker boot) could save the first thread's wrapper as the "original", after which every httpx call, LLM or not, recursed until `RecursionError`. A second `patch()` over an already-patched class now unwraps first.
- **`requests` calls no longer fail with `TypeError` after `stop_monitoring()`** if a library (for example a mock or record tool such as `responses` or `vcr`) still holds the wrapper it captured while monitoring was on.
- **`TemplateService`, `WorkloadService` and `LogService` reads now raise `CoolhandAPIError` for a read timeout, connection reset or undecodable body**, as documented, instead of letting the raw `OSError` / `HTTPException` escape. The same applies to a malformed API key (for example one with an embedded newline), which used to surface as a raw `ValueError` quoting the key; it is now a `CoolhandAPIError` naming only the exception type, with no chained cause, in these services, `FeedbackLinkService` and the upserts. Code that caught `ValueError` for this case should catch `CoolhandAPIError`.
- **`examples/dramatiq_pydantic_ai.py` now works with the `httpx2`-based pydantic-ai and exits non-zero on failure** (it previously swallowed its own error and exited 0).

### Security
- **Intercept/deny-list matching can no longer be spoofed by URL text.** See the matching change above (#195): a crafted URL whose userinfo, query string or lookalike host contained an intercept address or a deny pattern was previously mis-captured or mis-excluded.
- **Query-string redaction no longer misses a repeated parameter in a different case.** `?Key=SECRET1&key=SECRET2` redacted only `key`, so `SECRET1` reached Coolhand. Every spelling is now redacted. The query-parameter list also gained `x-api-key`, `auth`, `auth_token`, `auth-token`, `access-token`, `api-token`, `api_token`, `authorization`, `bearer`, `x-auth-token` and `x-access-token`, and the header list gained `authentication`, `bearer`, `x-auth`, `session-token` and `csrf-token` (rate-limit headers such as `x-ratelimit-remaining-tokens` stay readable).
- **A malformed API key (for example one with an embedded newline) is no longer written to the log.** The "Unexpected error submitting interaction/feedback" warnings now log the exception type only; `http.client`'s `ValueError` message quotes the whole header value.
- **A host pattern with many consecutive `*` characters can no longer take minutes to match.** Runs of stars are collapsed (they match the same hosts), keeping the glob matcher linear.
- **Dependency lock updates clear all `pip-audit` findings.** `pydantic-ai` 2.31.0 → 2.54.0 (and 2.57.0 on newer Pythons), fixing CVE-2026-107286, -107287, -107289, -107290 and -107291, and `mcp` 1.28.1 → 1.30.0, fixing CVE-2026-59951. These are development/test dependencies in `uv.lock`, not runtime dependencies of `coolhand`, but the `Audit dependencies` step in CI had been failing on them. `anthropic` 0.111.0 → 1.15.0 and `openai` 2.54.0 → 3.35.1 came along, which is why `httpx2` support was needed.

### Notes
- **`httpx2` support is automatic and optional.** `httpx2` is patched only if it is installed; `coolhand` does not depend on it. Older SDK releases built on `httpx` are unaffected.
- **Read endpoints need a recent Coolhand server.** `search_workloads`, `search_logs`, `get_log` and the metrics parameters use API v2 endpoints from Coolhand-Labs/coolhand#1753.

### Internal
- The httpx send wrappers are now built by `_make_patched_send` / `_make_patched_async_send` factories so `httpx` and `httpx2` share one implementation.
- `/prep-release` now runs `make live-examples` and treats `SKIP` as unexercised. (#196)
- Development dependency and CI updates: ruff 0.16.9 → 0.17.0 (#184), mypy 2.3.1 → 2.4.0 (#180), coverage 7.16.1 → 7.16.2 (#183), types-requests 2.33.0.20260906 → 2.33.0.20261006 (#182), `astral-sh/setup-uv` 10.2.0 → 10.3.0 (#179), `actions/download-artifact` 8.0.1 → 8.0.2 (#178) and `actions/upload-artifact` 7.0.1 → 7.0.2 (#177). The Dependabot PRs for `pydantic-ai` (#181) and `pydantic-ai-slim` (#193) are superseded by the lock update above.

## [0.9.0] - 2026-10-08

### Added
- **Link feedback to an optimization as evidence.** New `link_feedback(optimization_id, feedback_id, note=None)`, `bulk_link_feedback(optimization_id, feedback_ids, note=None)` and `unlink_feedback(optimization_id, link_id)` methods on `Coolhand`, backed by a new `FeedbackLinkService` (also exported from the package root, with the `OptimizationFeedbackLink` and `BulkLinkFeedbackResult` types). They wrap `POST` / `DELETE /api/v2/optimizations/{optimization_id}/feedback_links` and require the **private** API key; a non-2xx response raises `CoolhandAPIError` with the HTTP status on `.status`. `bulk_link_feedback` splits longer lists into batches of 100 (the server cap) and merges the counts; if a later batch fails, earlier batches stay applied, and re-running is safe because already-linked ids are counted rather than rejected. See [docs/feedback-links.md](./docs/feedback-links.md). (#160)

### Security
- **A `COOLHAND_API_KEY` with trailing whitespace or a newline is now stripped before use in `TemplateService` and `FeedbackLinkService`.** The 0.8.0 fix covered only the write paths (`CoolhandClient`, `FeedbackService`); the private-key read services still sent the raw value, so `http.client` rejected the header and raised a `ValueError` whose message contained the full private key into the host application's traceback or logs.
- **Copilot `session.create` credentials are no longer captured in cleartext.** The interceptor merges the `session.create` params into every captured `session.send` body, but only `requestHeaders` was masked, so a `github_token` passed to `create_session` (sent as `gitHubToken`), a BYOK `provider` `api_key` / `bearer_token` / `headers`, and MCP server `env` / `headers` all reached Coolhand unmasked. These are now redacted: `gitHubToken` entirely; credential-named keys, every `headers` / `env` value, and URL query secrets inside `provider`, `mcpServers` and `customAgents`. If redaction itself fails the whole subtree is dropped. Secrets embedded in MCP server `args` (for example `--token=...`) are not detectable and are still captured as given. Because matching is by key name, a non-secret field such as `maxPromptTokens` inside `provider` is also masked.

### Internal
- Renamed `CLAUDE.md` to `AGENTS.md` and updated references. (#158)
- Rewrote the harness's review step so a leaf agent requests its review from the parent node instead of running it itself (`AGENTS.harness.md`). (#161)
- `make test-live` now covers the feedback link live tests (`COOLHAND_LIVE_OPTIMIZATION_ID`, `COOLHAND_LIVE_FEEDBACK_IDS`). (#160)
- Development dependency and CI updates: ruff 0.16.7 → 0.16.9 (#163), coverage 7.16.0 → 7.16.1 (#154), types-requests 2.33.0.20260712 → 2.33.0.20260906 (#155), `astral-sh/setup-uv` 10.1.0 → 10.2.0 (#162), and `uv.lock` bumps of urllib3 2.7.0 → 2.8.0 (#167), PyJWT 2.13.0 → 2.15.0 (#165) and virtualenv 21.3.1 → 21.7.13 (#166).

## [0.8.0] - 2026-09-20

### Added
- **Interception for DeepSeek, Mistral, Perplexity, xAI, Cohere, TypeSafe Jev, Amazon Bedrock and Ollama.** `DEFAULT_INTERCEPT_ADDRESSES` now captures these automatically with no configuration change: `api.deepseek.com`, `api.mistral.ai`, `api.perplexity.ai`, `api.x.ai`; Cohere on `api.cohere.com` and `api.cohere.ai`, path-scoped to `/v2/chat`, `/v1/embed` and `/v2/embed` only (its other endpoints have different envelopes the server would record as empty successes); TypeSafe Jev at `api.typesafe.ai/v1/systemone`; and Amazon Bedrock runtime in any region via the `//bedrock-runtime.` / `//bedrock-runtime-fips.` host prefixes, for clients built on httpx or requests (for example the Anthropic Bedrock SDK or LiteLLM — plain boto3 uses urllib3 and is not captured). Bedrock's binary `application/vnd.amazon.eventstream` responses are recorded as `"[binary]"` instead of being decoded as text. `/v1/embed-jobs` is added to the default deny-list because it contains `/v1/embed`. See [docs/supported-libraries.md](./docs/supported-libraries.md). (#141)

### Fixed
- **`CoolhandDramatiqMiddleware` now applies constructor-kwarg Coolhand config in every worker process.** Previously a freshly spawned worker's `after_process_boot` built a bare `Coolhand()`, so config passed to the parent's `Coolhand(api_key=..., intercept_addresses=..., ...)` silently never reached workers — only `COOLHAND_*` environment variables did. The middleware now accepts the same `config`/keyword arguments as `Coolhand(...)` and, in each worker, keeps the existing instance if it already matches that config or otherwise replaces it. A replaced instance is only unregistered from `atexit`, not shut down: in a forked worker it is a copy of the parent's instance, and flushing it would re-deliver the parent's in-flight interactions once per worker. A failure constructing the replacement is logged and swallowed rather than crashing the worker. `CoolhandDramatiqMiddleware()` with no arguments behaves as before. See [docs/dramatiq.md](./docs/dramatiq.md). (#130)
- **Copilot requests that never receive an `assistant.message` are no longer silently dropped.** If the model errors out or rejects a request without emitting the notification (for example, context window exceeded), the interaction was evicted from the interceptor's pending table without ever being logged. It is now reported to Coolhand as an error (`"no assistant.message event received"`) 60 seconds after the `session.send` acknowledgement (`COPILOT_INTERCEPTOR_FALLBACK_TIMEOUT`), and the stale-entry sweep reports any entry the fallback didn't already report. (#131)

- **Requests with streamed bodies (multipart file uploads, generator/async-generator bodies) no longer fail while being captured.** With auto-monitoring on, `httpx.Client.send` / `AsyncClient.send` built the captured request inside the same `try` as the real call, and reading `request.content` raises `httpx.RequestNotRead` for such bodies — so an intercepted call to, for example, `api.openai.com/v1/audio/transcriptions` raised `RequestNotRead` in the host application instead of being sent. The body is now recorded as `"[unreadable body]"` and the request proceeds normally.

### Security
- **Wider credential redaction.** Headers `apikey`, `api_key`, `x-auth-token`, `x-api-token`, `x-access-token`, `x-functions-key` and `x-amz-credential` are now masked, and query parameters `sig`, `signature`, `x-amz-signature`, `x-amz-credential`, `x-amz-security-token`, `x-goog-api-key`, `x-goog-signature`, `api-key`, `client_secret`, `refresh_token`, `id_token` and `password` are now redacted — covering pre-signed URL credentials (AWS SigV4, Azure SAS, GCS) that previously went to Coolhand in cleartext. `user:password@` userinfo is also stripped from captured URLs. Bare `token`/`secret` are deliberately not header markers, so rate-limit headers such as `x-ratelimit-remaining-tokens` stay readable.
- **Copilot `requestHeaders` are no longer duplicated, unmasked, inside the captured request body.** They were correctly masked in `headers` but also spread into `request_body`, so `Authorization` / `X-API-Key` values reached Coolhand in cleartext there.
- **A `COOLHAND_API_KEY` with trailing whitespace or a newline is now stripped before use.** Previously `http.client` rejected the header and the resulting `ValueError` — whose message contains the full key — was logged at WARNING on every interaction, regardless of `silent`.
- **Copilot interceptor handler errors can no longer replace the SDK's own error or break its reader thread.** The `session.send` error path and the stale-entry sweep now guard the handler call.
- **anyio 4.13.0 → 4.14.2**, fixing CVE-2026-63374 and CVE-2026-64847 in the development/test dependency lock (`uv.lock`); `pip-audit` in CI had been failing on both. anyio is a transitive dependency of the optional integrations, not a runtime dependency of `coolhand` itself. (#138)

### Notes
- **Ollama is only partly covered.** Ollama has no fixed host, so `:11434/api/chat`, `/api/generate`, `/api/embed` and `/api/embeddings` are anchored to its default port; this captures remote, LAN and Docker Compose hosts (for example `http://ollama:11434`). Bare `localhost:11434` — the most common setup — is still skipped by the existing localhost guard, which is unchanged. (#141)
- **If you replace the default deny-list, you also drop the Cohere `/v1/embed-jobs` guard.** `exclude_api_patterns=[...]` replaces `DEFAULT_EXCLUDE_API_PATTERNS` rather than extending it; extend it instead (see [docs/configuration.md](./docs/configuration.md)) to keep the defaults. (#141)
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
