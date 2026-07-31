---
description: Get the package ready to cut a release — run the full test suite, bring docs up to date with everything since the last tag, and red-team the whole package for security issues.
allowed-tools: Edit, Write, Read, Bash, Agent, Skill, Glob, Grep
argument-hint: [patch|minor|major]
---

Prepare `coolhand-python` for release: run tests, update docs, red-team the package, and bump the version. This does NOT tag or push/publish — those remain manual steps for a human to confirm.

## Setup

- Suggested bump: `$ARGUMENTS` (optional hint; if blank, infer it from the changes found in Step 2)
- Last tag: run `git describe --tags --abbrev=0` to find it
- Changes since last tag: `git log <last-tag>..HEAD --oneline` and `git diff <last-tag>...HEAD`

## Step 1 — Run all tests

Run `make verify` (ruff lint, ruff format check, pytest — this is the only gate per CLAUDE.md; never invoke the tools individually).

- If it fails, STOP here. Report the failures and tell the user to fix them first (e.g. via `/loop-review`) — do not attempt to prep a release on top of a red build.
- If it passes, continue to Step 2.

## Step 2 — Update & clean the docs

Spawn an Agent with this prompt:

---
You are preparing release documentation for `coolhand-python`, a Python SDK published to PyPI.

Find the last tag with `git describe --tags --abbrev=0`, then review every change since it: `git log <last-tag>..HEAD` and `git diff <last-tag>...HEAD`.

1. Summarize every user-facing change since the last tag: new features, bug fixes, and breaking changes.
2. Cross-check against `CHANGELOG.md`'s `[Unreleased]` section. For any change that isn't already documented there, draft an entry in the existing style (see prior entries under released versions) — describe what changed, why, and who's affected. For breaking changes, be explicit about what breaks and what the migration path is.
3. Review `README.md` and `docs/*.md` for staleness relative to these changes: new config options, new supported libraries/integrations, changed behavior, outdated examples, dead links. Follow this repo's documentation split rules from CLAUDE.md (README stays a scannable landing page; details belong in `docs/configuration.md`, `docs/feedback.md`, `docs/supported-libraries.md`, or a per-integration `docs/<name>.md`).
4. Remove or correct stale doc content that no longer reflects the codebase — don't just add, actively clean up.

Apply all doc edits directly with Edit/Write. Return a summary of every file you changed and why.
---

Apply the agent's edits (it should have already written them directly — verify the changes look correct and complete the summary).

## Step 3 — Red-team the whole package

Spawn an Agent with this prompt:

---
You are doing a pre-release security review of `coolhand-python`, a Python SDK published to PyPI that intercepts and logs LLM API requests/responses (which may contain end-user PII).

Review the ENTIRE package under `src/` — not just changes since the last release. Look for:
- Credential/secret handling and logging (API keys, tokens ending up in logs or error messages)
- Injection points: shell, SQL, deserialization (`pickle`, `eval`, `exec`)
- SSRF risk in outbound HTTP calls (unvalidated URLs, redirects)
- Overly broad exception handling that could silently swallow security-relevant failures
- Leakage of request/response payloads (possible end-user PII) to logs, telemetry, or third parties beyond the intended collector
- Insecure defaults (e.g. disabled TLS verification, permissive CORS, world-readable temp files)
- Dependency-level risk worth flagging (e.g. a dep known for CVEs) — mention but don't attempt to fix

Return a numbered list of findings with file path, line number, and severity (critical/high/medium/low). If nothing is found, respond with exactly: LGTM: No security issues found.
---

For each finding:
- If it's a straightforward, low-risk fix, apply it directly with Edit and re-run `make verify`.
- If it requires a design decision or could have wider blast radius, do NOT guess — list it under "Manual follow-up required" in the Final Summary instead.

If any fixes were applied in this step, re-run `make verify` before moving on and stop to report if it fails.

## Step 4 — Bump the version (no tag, no push)

Check whether the version has already been bumped ahead of the last tag: compare `src/coolhand/version.py`'s `__version__` (and `pyproject.toml`'s `version`) against the last tag found in Setup.

- If both files already show a version newer than the last tag, skip this step — someone already bumped it — and note that in the Final Summary.
- Otherwise, bump it:
  1. Decide the bump type: use `$ARGUMENTS` if given, otherwise use the inference from Step 2 (patch for fixes only, minor for additive/backward-compatible changes, major for any breaking change documented in `CHANGELOG.md`).
  2. Update `__version__` in `src/coolhand/version.py` and `version` in `pyproject.toml` to match, keeping them in sync.
  3. In `CHANGELOG.md`, rename the `[Unreleased]` heading to `[X.Y.Z] - <today's date>` (get today's date via `date +%F`), matching the format of existing released entries. Add a fresh empty `[Unreleased]` heading above it for future work.
  4. Run `make verify` again to confirm nothing broke.
  5. Do NOT create a git tag, and do NOT push or publish. Leave the version bump as an uncommitted (or committed, if the user's workflow expects it — do not commit automatically) change for the human to review.

## Final Summary

Output:

1. **Tests**: `make verify` result (should be PASS to have reached this point)
2. **Docs updated**: files changed in Step 2, and a list of new/edited `CHANGELOG.md` entries
3. **Security findings**: fixed vs. flagged for manual follow-up, with severity
4. **Version bump**: old → new version, bump type used and why, or "already bumped" / "skipped" with reason
5. **Remaining manual steps**: review the changes, commit if not already committed, then tag and publish (`make build` / `make publish`) — these are NOT performed by this command.
