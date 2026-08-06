---
description: Automated review → fix → repeat loop. Spawns a reviewer agent each round, fixes findings in this session, and repeats until the review comes back clean.
allowed-tools: Edit, Write, Read, Bash, Agent, Skill, Glob, Grep
argument-hint: [low|medium|high|max]
---

Run an automated code review + fix loop on the current branch. Keep iterating until the reviewer reports no issues.

## Setup

- Effort level: `$ARGUMENTS` (default: `high` if blank)
- Max iterations: 5
- Review scope: `git diff origin/main...HEAD`

## Loop Instructions

Repeat the following cycle up to 5 times:

### Step 1 — Review (Agent)

Spawn an Agent using the Agent tool with `thinking: "high"` enabled and this prompt (substitute ITERATION_NUM, EFFORT, and PREVIOUS_FIXES):

---
You are a code reviewer doing pass ITERATION_NUM of an automated review loop on `coolhand-python`, a Python SDK published to PyPI.

Run `git diff origin/main...HEAD` to get the current branch diff. Review it for:
- Correctness bugs and logic errors
- Security vulnerabilities introduced by this diff
- Missing/broken error handling
- Inefficiencies or unnecessary complexity
- Violations of project conventions in CLAUDE.md (e.g. `uv run` usage, README/docs split rules, `make verify` as the only gate)
- DRY violations and non-semantic naming — flag duplicated logic that should reuse an existing helper, and identifiers whose names don't convey intent
- Test coverage — new or changed behavior must have tests that actually assert on meaningful outcomes (not just "doesn't raise"); flag missing edge cases (error paths, empty/None inputs, type boundaries)
- PyPI interface stability — does this diff change a public interface (anything importable from `coolhand`, method signatures, field types/names on response models) without a compelling reason? If it does break something:
  - Confirm it's documented in `CHANGELOG.md` under `[Unreleased]` with a rationale, in the style of existing entries (what changed, why, who's affected)
  - Confirm `src/coolhand/version.py` and `pyproject.toml` were bumped consistent with semver (patch for fixes, minor for additive/backward-compatible, major for breaking)
  - Flag it if the bump doesn't match the actual severity of the change

Effort: EFFORT

Already fixed in prior iterations — do NOT re-flag these:
PREVIOUS_FIXES

Tag every finding with one of these severities:
- `[CRITICAL]` — security vulnerabilities, wrong/broken behavior, performance problems (a PyPI interface break shipped without a CHANGELOG entry and version bump is `[CRITICAL]`)
- `[NICE-TO-HAVE]` — DRY violations, missing test coverage, code-reuse opportunities
- `[NITPICK]` — documentation, comments, naming, formatting-adjacent issues

Return a numbered list of issues with file path and line numbers, each prefixed with its severity tag, e.g. `1. [CRITICAL] file:line — problem — fix`. Be specific about what to fix and why.
If there are NO issues, respond with exactly: LGTM: No issues found.
End your response with a line: `TOKENS_USED: <number>` — your best estimate of tokens used this pass (approximate is fine, this isn't metered).
---

### Step 2 — Check Result

- If the first line of the agent's response is exactly `LGTM: No issues found.` → exit the loop, go to CSV Run Log then Final Summary
- If iteration count has reached 5 → exit the loop, go to CSV Run Log then Final Summary (partial)
- Otherwise → proceed to Step 3

### Step 3 — Fix

For every finding the reviewer raised, either fix it or reject it with a one-line reason (false positive / out of scope / disagree with the call) — every finding must get one of these two dispositions. Use Edit, Write, and Bash tools to apply fixes directly. For any breaking interface change, update `CHANGELOG.md` and bump the version (`src/coolhand/version.py` and `pyproject.toml`) as part of the fix, not as an afterthought.

Track, per iteration, the fixed count and the rejected count broken down by severity.

Run `make verify` (ruff lint, ruff format check, pytest — never invoke these tools individually) before logging the iteration. If it fails, fix the failure before moving on.

### Step 4 — Log & Continue

Record this iteration in your running log (see format below), then go back to Step 1 with the next iteration number.

## Iteration Log Format

Maintain this log as you work:

```
=== Iteration 1 ===
Reviewer found N issues (C critical, N nice-to-have, K nitpick):
  1. [CRITICAL] [file:line] description
  2. ...
Fixed: X (by severity: ...)
  - Applied: [description of fix]
Rejected: Y (by severity: ...)
  - Rejected: [description] — [reason]
make verify: PASS | FAIL (details)

=== Iteration 2 ===
...

=== RESULT ===
[CLEAN after N iterations] or [STOPPED at max iterations — N issues remain]
```

## CSV Run Log

Run once at the very end of the loop, after it exits and before Final Summary.

Append one row per iteration to `~/loop-review-outputs/coolhand-python.csv`. If the directory or file doesn't exist, create them first with this header:

```
timestamp,branch,iteration,model,thinking_level,clock_seconds,tokens_used_approx,critical_found,nice_to_have_found,nitpick_found,total_found,issues_addressed,issues_ignored
```

For each iteration's row:
- `timestamp` — `date -u +%Y-%m-%dT%H:%M:%SZ` at write time
- `branch` — `git branch --show-current`
- `model` — `default`
- `thinking_level` — the EFFORT value used that iteration
- `clock_seconds` — wall-clock elapsed, bracketed with `date +%s` taken right before spawning that iteration's Step-1 agent and right after that iteration's Step-3 `make verify` completes
- `tokens_used_approx` — the `TOKENS_USED` value the reviewer reported that iteration
- `critical_found` / `nice_to_have_found` / `nitpick_found` / `total_found` — counts from Step 1
- `issues_addressed` — fixed count from Step 3
- `issues_ignored` — rejected count from Step 3

Use a plain `cat >> ~/loop-review-outputs/coolhand-python.csv <<EOF ... EOF` append per row — no CSV quoting needed since no field contains a comma.

## Final Summary

After the loop exits and the CSV Run Log has been written, output:

1. **Overall result**: CLEAN (N iterations) or STOPPED (issues remain)
2. **Per-iteration breakdown**: What was found (by severity) vs. what was fixed/rejected each round
3. **All files modified**: Complete list of files touched across all iterations
4. **Interface/versioning changes**: Any breaking changes made, and the CHANGELOG/version bump applied for each
5. **Remaining issues** (if stopped at max): Unresolved items with context on why they're hard to fix automatically
6. **CSV log**: Number of rows appended and the path (`~/loop-review-outputs/coolhand-python.csv`)
