# Changelog

All notable changes to this project will be documented in this file.

## [0.4.0] - 2026-04-30

### Added

- **Band Guesser example app** (`examples/band-guesser`) — FastAPI demo that exercises all three coolhand capture methods in one app: GitHub Copilot SDK (JSON-RPC), GitHub Models via httpx, and GitHub Models via `azure-ai-inference` (`requests` transport). Also validates correct patch ordering alongside OpenTelemetry instrumentors and structlog.
- **`requests` library interception** — `requests.Session.send` is now patched alongside httpx when `patch()` is called, enabling monitoring of any SDK that uses `requests` as its HTTP transport (e.g. `azure-ai-inference`, `azure-openai`, and other `azure-core`-based SDKs). `requests` remains an optional dependency; the patch is silently skipped if it is not installed. (#18, closes #12)

### Changed

- **Minimum Python version raised to 3.10** — Python 3.8 (EOL October 2024) and 3.9 (EOL October 2025) are no longer supported. If you need to stay on Python 3.7–3.9, pin to `coolhand<0.4.0`.

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
