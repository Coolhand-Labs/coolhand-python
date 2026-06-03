# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

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
