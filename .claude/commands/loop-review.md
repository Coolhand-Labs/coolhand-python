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

Return a numbered list of issues with file path and line numbers. Be specific about what to fix and why.
If there are NO issues, respond with exactly: LGTM: No issues found.
---

### Step 2 — Check Result

- If the agent says `LGTM: No issues found.` → exit the loop, go to Final Summary
- If iteration count has reached 5 → exit the loop, go to Final Summary (partial)
- Otherwise → proceed to Step 3

### Step 3 — Fix

Fix EVERY issue the reviewer raised. Use Edit, Write, and Bash tools to apply fixes directly. Do not skip any finding. For any breaking interface change, update `CHANGELOG.md` and bump the version (`src/coolhand/version.py` and `pyproject.toml`) as part of the fix, not as an afterthought.

Run `make verify` (ruff lint, ruff format check, pytest — never invoke these tools individually) before logging the iteration. If it fails, fix the failure before moving on.

### Step 4 — Log & Continue

Record this iteration in your running log (see format below), then go back to Step 1 with the next iteration number.

## Iteration Log Format

Maintain this log as you work:

```
=== Iteration 1 ===
Reviewer found N issues:
  1. [file:line] description
  2. ...
Fixed:
  - Applied: [description of fix]
  - Applied: [description of fix]
make verify: PASS | FAIL (details)

=== Iteration 2 ===
...

=== RESULT ===
[CLEAN after N iterations] or [STOPPED at max iterations — N issues remain]
```

## Final Summary

After the loop exits, output:

1. **Overall result**: CLEAN (N iterations) or STOPPED (issues remain)
2. **Per-iteration breakdown**: What was found vs. what was fixed each round
3. **All files modified**: Complete list of files touched across all iterations
4. **Interface/versioning changes**: Any breaking changes made, and the CHANGELOG/version bump applied for each
5. **Remaining issues** (if stopped at max): Unresolved items with context on why they're hard to fix automatically
