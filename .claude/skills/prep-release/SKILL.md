---
name: prep-release
description: |
  Runs an entire release event for this package: triages every open PR
  into a quality/risk-rated merge recommendation, waits for the user's
  sign-off, squash-merges the chosen PRs, writes the release's changelog
  and version bump plus a whole-package security red-team on its own
  release branch, validates that branch with make verify and live-key
  example scripts, then opens a single release-prep PR for the user's
  final review. Never merges that PR, tags, or publishes. Use when the
  user types /prep-release, asks to "prep a release", "cut a release",
  "release checklist", or wants the open PRs triaged and merged into a
  release.
user_invocable: true
allowed-tools: Edit, Write, Read, Bash, Agent, Skill, Glob, Grep
version: 1.0.0
---

# Prep Release

Five phases, run in order. This is a whole-release audit, not a
single-branch review — Phase 3 onward operates on the whole `src/` tree
and everything merged since the last tag, not just one diff. For an
iterative diff-scoped review during normal development, use
`/loop-review` instead; this skill is for the release event itself.

Per `CLAUDE.md`, feature/fix branches never touch `CHANGELOG.md`,
`pyproject.toml`'s `version`, or `src/coolhand/version.py` — this skill is
the only place those get written. If a chosen PR's diff does touch any of
them, treat it as a normal part of that PR's diff (don't strip it), but
don't let it change how Phase 3 writes its own entry — Phase 3's
changelog write-up and version bump are authoritative regardless of what
an individual PR's diff already contains.

## Phase 1: Survey open PRs, recommend a release set

1. `gh pr list --state open --json number,title,author,isDraft,mergeable,mergeStateStatus,statusCheckRollup,additions,deletions,changedFiles,body,headRefName`.
2. For each PR, pull `gh pr diff <n>` and `gh pr checks <n>` and rate two
   independent axes:
   - **Quality** (High/Medium/Low): does the diff include test coverage
     proportional to the `src/` change, is the code consistent with this
     repo's style/conventions, does the PR description read as complete
     work rather than a stub or "WIP, not ready" note.
   - **Risk** (High/Medium/Low): does it touch a security- or
     interception-critical path (`src/coolhand/httpx_interceptor.py`,
     `src/coolhand/copilot_interceptor.py`, `src/coolhand/client.py`'s
     header/URL redaction — `_sanitize_headers`, `_sanitize_url`,
     `SENSITIVE_QUERY_PARAMS` — or `src/coolhand/default_exclude_api_patterns.json`)
     — weight those higher regardless of size; failing CI checks or a
     non-clean `mergeable` state also push risk up; an isolated additive
     feature or docs-only change is lower risk.
3. Present one table: PR number, title, quality, risk, CI status,
   mergeable state, and a one-line recommendation (include / exclude /
   needs work before it can be considered). Call out anything that looks
   unfinished — draft, a WIP-sounding title, failing checks, an empty or
   placeholder description, visible `TODO`/`FIXME` in the diff — as
   "exclude, not ready" rather than rating it neutrally.
4. Stop here and ask the user which PRs to include in this release. This
   is the one planned decision point in the whole skill — do not merge,
   write changelog entries, or touch the version files until the user
   answers. (Phase 2 step 3 below has its own unplanned-error stop for an
   unclean working tree; that's an abort on unexpected state, not a
   second decision point like this one.)

## Phase 2: Merge the chosen PRs

Process the user's chosen PRs one at a time, not as a batch:

1. Before each merge, re-check that PR's `mergeable`/`mergeStateStatus`
   (`gh pr view <n> --json mergeable,mergeStateStatus`) — an earlier merge
   in this same run can newly conflict a later one. If a chosen PR now
   conflicts, skip it, note it in the running list as "skipped — needs
   rebase," and continue with the rest. Don't resolve conflicts on someone
   else's branch unilaterally.
2. `gh pr merge <n> --squash --delete-branch` for each surviving PR.
3. After each merge, sync local `main` before evaluating the next PR.
   First check `git status --porcelain` — if it's not empty, stop and
   surface it to the user rather than discarding unknown local state;
   otherwise `git fetch origin main && git checkout main && git reset
   --hard origin/main` is safe, since it only overwrites a working tree
   already confirmed clean with the just-fetched remote `main`.

Keep a running list of what actually merged vs. what got skipped — Phase 5
reports both.

## Phase 3: Build the release branch — docs/changelog/version + red-team

Create `release/vX.Y.Z` off the freshly synced `main` (version number per
step 5 below) and do all of the following as commits on that branch —
never on `main` directly.

### Docs, changelog, version

1. Find the last release tag: `git describe --tags --abbrev=0`.
2. Diff **everything since that tag** on the now-updated `main` —
   `git log <last-tag>..HEAD --oneline` and `git diff <last-tag>..HEAD --
   src/` — not just the PRs this run merged in Phase 2. `main` can carry
   unreleased changes Phase 2 never touched (a hotfix committed directly,
   a PR merged manually outside this skill, or a prior `/prep-release` run
   that merged PRs but was interrupted before finishing this phase); all
   of those still need a changelog entry, so treat this diff, not Phase
   2's merge list, as the source of truth for what's covered.
3. For each change, check it's reflected in:
   - `CHANGELOG.md` — one entry per change under `[Unreleased]` (or a new
     version heading), in Keep a Changelog format matching this repo's
     existing entries (bolded one-liner followed by a detailed paragraph;
     section headers drawn from `### Added` / `### Fixed` / `### Security`
     / `### Changed` / `### Breaking changes` / `### Notes` / `### Removed`
     / `### Internal` as appropriate) — plain-English migration notes for
     anything behavior-affecting. Attribute each entry to its PR number
     where one exists: check Phase 2's merge list first, then fall back to
     the squash-merge commit message (`git log --grep`, which carries the
     PR number in its title) for anything not merged in this run. If a
     change genuinely has no discoverable PR (a direct commit to `main`),
     write the entry without one rather than skipping it.
   - `README.md` / `docs/*.md` — any new config option, public method, or
     behavior change needs the relevant section updated. Follow this
     repo's docs philosophy from `CLAUDE.md`: the README stays a scannable
     landing page (basic config/feedback snippets only); anything needing
     more than one code block belongs in `docs/`.
4. **Clean, don't just append.** Look for docs that are now stale,
   contradictory, or redundant given the accumulated changes since the
   last tag — consolidate/rewrite rather than layering a new paragraph on
   top of an outdated one. Remove docs for anything removed from the
   package.
5. **Bump the version.** Since `CLAUDE.md` now forbids per-PR bumps, this
   should always be needed — but check `src/coolhand/version.py`'s
   `__version__` and `pyproject.toml`'s `version` against the last tag
   first as a defensive sanity check in case something bumped it out of
   band. Determine the SemVer bump this repo's convention implies (patch
   = fix, minor = backward-compatible addition or breaking change while
   pre-1.0), then bump **all three** in lockstep:
   - `pyproject.toml`'s `version`
   - `src/coolhand/version.py`'s `__version__`
   - run `uv lock` (or `uv sync`) so `uv.lock`'s `coolhand` self-entry
     (`source = { editable = "." }`) matches

   Turn the `[Unreleased]` CHANGELOG heading into
   `## [X.Y.Z] - <today's date>`, and add a fresh empty `[Unreleased]`
   heading above it for future work.

### Red-team

Adversarially review the entire `src/` tree (not just what merged in
Phase 2) for security issues. This package intercepts outgoing LLM API
traffic and logs it to Coolhand, so hunt specifically for:

- **Credential/secret leakage**: does any interceptor, logger, or error
  handler write an API key, bearer token, or provider auth header value
  into a log line, exception message, or the payload sent to Coolhand?
  Check that `_sanitize_headers`/`_sanitize_url` and
  `SENSITIVE_QUERY_PARAMS` in `src/coolhand/client.py` actually strip what
  they claim to (e.g. a differently-cased or differently-named header or
  query param slipping past the redaction list).
- **SSRF / address matching**: `DEFAULT_INTERCEPT_ADDRESSES` in
  `src/coolhand/httpx_interceptor.py` and the deny-list in
  `src/coolhand/default_exclude_api_patterns.json` are plain substring
  matches, not regex (so there's no ReDoS surface here) — but that means a
  crafted URL whose path or query string happens to contain one of these
  substrings could be mis-captured or mis-excluded. Check whether that's
  exploitable for the addresses/patterns currently in the lists.
- **Thread safety**: `flush()` hands interactions off to a bounded
  background dispatch queue drained by a worker thread (added to fix a
  prior event-loop-stall bug) — look for unsynchronized shared mutable
  state a concurrent request could race on.
- **Unsafe deserialization**: any `pickle.load`/`eval`/`exec` usage, and
  any parsing of response or batch payloads that trusts attacker-shaped
  JSON without validation.
- **Fail-open vs. fail-closed**: when Coolhand's API is unreachable, rate
  limited, or returns malformed data, does the package fail open in a way
  that silently drops security-relevant logging, or fail in a way that
  breaks the host application's actual LLM call? (`client.py` already has
  a "fail closed" precedent for redaction failures — check other paths
  are held to the same standard, and that none of them can break the
  underlying request.)

For each finding, report file, line, a concrete failure scenario, and
severity. Apply safe, mechanical, low-risk fixes directly, as commits on
`release/vX.Y.Z` (e.g. a missing header-redaction pattern, a missing
timeout), then re-run `make verify`. Flag but do not silently apply
anything that's a behavior/architecture decision (e.g. changing a
fail-open security default, adding replay protection, moving synchronous
work to a background thread) — surface these to the user for a decision,
the same "hand it to a human" rule `/loop-review` uses for stuck findings.

## Phase 4: Validate the release branch

1. Run `make verify` (ruff lint, ruff format check, pytest — this repo's
   single gate per `CLAUDE.md`; never invoke the tools individually) on
   `release/vX.Y.Z`. Everything must pass before continuing — a release
   doesn't ship on a red build. If it fails, stop here and report the
   failures; fixing genuine bugs takes priority over the rest of this
   phase and Phase 5.

   Then judge coverage on quality, not just percentage: find the gaps and
   weight by risk (an uncovered error-handling or security-check branch
   matters more than an uncovered trivial getter); audit existing tests
   for meaningfulness, not just count (flag tests that only assert a mock
   returns what it was configured to return, missing negative/error-path
   cases, missing domain edge cases); recommend specific tests for the
   highest-risk gaps, named by `file::test_name` — don't add tests purely
   to move the percentage.

2. If step 1 is green, run the example scripts:
   `uv run python examples/basic_usage.py`,
   `uv run python examples/auto_monitor_example.py`, and
   `uv run python examples/dramatiq_pydantic_ai.py`. Unlike a
   clean-skip-on-missing-key pattern, these scripts always run to
   completion — they fall back to a fake `COOLHAND_API_KEY`/provider key
   (or a fake transport, for `dramatiq_pydantic_ai.py`) when real ones
   aren't set, rather than exiting early. So treat a **non-zero exit** as
   the failure signal regardless of which keys are present in the
   environment; if a real provider key (e.g. `ANTHROPIC_API_KEY`) is set,
   the run additionally exercises a real intercepted call instead of a
   simulated one, which is a stronger signal but not required for a pass.
   If `COOLHAND_LIVE_BASE_URL` and `COOLHAND_LIVE_API_KEY` are set in the
   environment, also run `make test-live` against the real server.
   Record pass/fail per script (and whether it ran live or simulated).

3. **`examples/band-guesser/` is explicitly in scope — run it, don't just
   note it.** It's a plain FastAPI JSON API (`POST /api/guess-bands`,
   `POST /api/submit-feedback`); the `templates/index.html` form is a
   convenience for humans, not a requirement for exercising it.

   - Set it up per its own README's "Testing locally" section: a
     dedicated `.venv`, deps installed with the `coolhand` line stripped
     from `requirements.txt`, then `uv pip install -e ../../` so it
     exercises the release branch, not a pinned PyPI version.
   - Source the GitHub token via `gh auth token` — this is already
     `main.py`'s own non-interactive fallback (`_resolve_github_token`)
     when no token is posted in the request body, so no new env var or
     hardcoded reference is needed. Confirm it works first (`gh auth
     token` exits 0) before starting the server.
   - The Copilot mode additionally needs the `copilot` CLI on `PATH`
     (`github-copilot-sdk` shells out to it over JSON-RPC/stdio). If
     missing, install it with `npm install -g @github/copilot` — this
     installs a global tool on the machine, not just a repo dependency,
     so it's a one-time setup cost worth calling out in the Phase 5
     report the first time it happens.
   - Start the server in the background on a scratch port (e.g.
     `uvicorn main:app --port 8188`), poll `GET /` until it responds,
     then `POST /api/guess-bands` once per `mode` (`copilot`, `azure`,
     `azure-sdk`) with a fixed test sentence and empty `github_token`
     (so it falls through to the `gh auth token` path), and
     `POST /api/submit-feedback` with the returned `raw_response` to
     exercise the feedback path too. A 429 (rate limit) on any mode is a
     transient condition, not a failure — retry once before giving up on
     that mode.
   - **Always terminate the server process afterward**, success or
     failure — per the standing instruction to clean up anything you
     start.
   - Record pass/fail per mode, and fold real failures (non-2xx other
     than a retried 429, a hung request, a malformed response) into
     Phase 4 step 1's "must be green before continuing" bar — don't let
     a red band-guesser run slide through to Phase 5 unremarked.

4. **Explicitly test for added latency — don't assume a small diff is
   latency-neutral.** Two complementary checks, both against the *diff
   since the last tag* (Phase 3 step 2's `git diff <last-tag>..HEAD --
   src/`), not just this run's merged PRs:

   - **Synthetic interceptor microbenchmark (primary signal).** Live LLM
     call latency is dominated by network and model inference time —
     hundreds of milliseconds to seconds — which drowns out anything a
     few extra string comparisons or an added content-type check could
     plausibly cost. Isolate Coolhand's *own* overhead instead: write a
     throwaway benchmark script (not committed) that calls the patched
     `httpx.Client.send`/`AsyncClient.send`/`requests.Session.send`
     wrappers against a mocked response many times (e.g. 1000 iterations
     each), for both a JSON body and a body shaped like whatever this
     release's diff added/changed handling for (e.g. a binary
     `audio/`/`video`/`image` content type), and compare median
     per-call overhead against the same wrapper checked out at the last
     tag. Flag anything that grows by more than roughly 20% or crosses a
     low-single-digit-millisecond absolute budget — either signals the
     diff added real per-request cost, not measurement noise.
   - **End-to-end wall-clock on the live band-guesser calls (sanity
     check, not precision measurement).** Record total latency for each
     of the three `guess-bands` modes run in step 3. These numbers are
     too network-noisy to prove Coolhand added zero overhead, but they
     do catch the failure mode a microbenchmark can miss: a hang, a
     timeout, or unbounded growth (e.g. an accidental O(n²) path over a
     large captured body) that only shows up against a real response.

   Report both sets of numbers in Phase 5 rather than a bare pass/fail —
   the actual figures are what let the user judge "substantive" for
   themselves.

## Phase 5: Open the release-prep PR, report everything

1. Push `release/vX.Y.Z` and `gh pr create` (e.g. "chore: release
   vX.Y.Z") targeting `main`. This PR is the user's final checkpoint
   before the changelog/version/red-team commit lands — never merge it,
   tag it, or run `make build`/`make publish` yourself.
2. Report one consolidated summary covering the whole run:
   - Phase 1's PR table and which PRs the user chose.
   - Phase 2's outcome: which PRs merged, which were skipped for new
     conflicts (and need a rebase before the next release).
   - The release-prep PR link, the version bump and why.
   - Coverage-quality gaps plus recommended tests.
   - Docs updated.
   - Red-team findings split into fixed vs. flagged-for-decision.
   - Phase 4's `make verify` result, the example-script results
     (pass/fail, simulated/live per script), the `make test-live` result
     if it ran, the `band-guesser` per-mode pass/fail, and both latency
     checks' numbers (microbenchmark deltas and end-to-end wall-clock
     per mode) with a call on whether either looks substantive.

## Safety

- Bumping `pyproject.toml`'s `version` and `src/coolhand/version.py`'s
  `__version__`, finalizing the CHANGELOG heading, and running `uv lock`
  for `uv.lock` are all in scope and don't need a stop-and-ask — they're
  mechanical, reversible, and gated on Phase 4 already being green before
  the PR opens.
- Squash-merging PRs the user explicitly chose in Phase 1, and pushing the
  `release/vX.Y.Z` branch to open its own PR, are both in scope.
- Never push a commit directly to `main`. All release-branch work lands on
  `main` only via the Phase 5 PR, which the user reviews and merges
  themselves.
- Never create or push a git tag, and never run `make build`, `make
  publish`, or `twine upload`. `.github/workflows/publish.yml` auto-
  publishes to PyPI via trusted OIDC publishing on any `v*.*.*` tag push —
  pushing a tag from this repo is a real, automatic release, not a
  placeholder step. Tagging and publishing are the user's action once
  they've reviewed and merged this skill's PR, not something this skill
  does. Never merge the Phase 5 PR yourself either.

## Rationalizations to resist

- *"This PR's CI is green and the diff is small, I don't need to look at
  the actual diff."* CI passing doesn't rule out unfinished work — a
  small, green diff can still be a stub that leaves a feature half-built.
  Read the diff.
- *"The diff since the last tag is small, I'll skip the red-team."* Small
  diffs can still sit on top of latent issues in code nobody's touched
  recently — that's exactly what "whole package, not just the diff" means.
- *"Tests pass, so coverage is fine."* Passing tests and meaningful
  coverage are different questions. A red build blocks release; a green
  build with hollow tests doesn't guarantee anything.
- *"Docs are close enough, I'll skip the cleanup pass."* Accumulated
  changes since the last tag are exactly when docs drift from behavior —
  this phase exists because per-PR doc updates miss the cross-cutting
  view.
- *"The example scripts are just smoke tests, I'll skip them since tests
  passed."* `tests/` mocks the transport layer; the example scripts are
  the only step in this skill that exercise the real
  `httpx`/`requests`/Copilot interception path end-to-end — that's a
  different failure mode than a unit test can catch.
