# Changelog

All notable changes to this project will be documented in this file.

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
