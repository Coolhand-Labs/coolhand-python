---
name: finish-release
description: |
  Finishes a release from its open release-prep PR (from /prep-release):
  squash-merges that PR, creates and pushes the annotated vX.Y.Z tag on the
  merge commit, then publishes the GitHub release with the CHANGELOG section
  as its notes. Pushing the tag triggers the Publish workflow (PyPI Trusted
  Publishing), so this is the step that ships the package. Use when the user
  types /finish-release, asks to "merge and tag the release", "publish the
  release notes", "ship v0.x.y", or has reviewed the release-prep PR.
user_invocable: true
---

# Finish Release

The counterpart to `/prep-release`. That skill stops at an open release PR and
never merges, tags or publishes; this one starts after the user has reviewed
that PR. **Invoking it is the user's authorization to merge that PR, tag, and
publish.** Four steps, in this order: **merge, tag, release notes, report**.
The release is created with `--verify-tag`, so the tag has to exist first.

**What pushing the tag does:** `.github/workflows/publish.yml` runs on any
`v*.*.*` tag push. The `build` job runs `make verify`, `uv build`, `twine check`
and fails unless the tag matches `pyproject.toml`'s `version`. The `publish`
job then uploads to PyPI by Trusted Publishing behind the `pypi-publish`
environment: a 15-minute wait timer, and only `main` and `v*.*.*` refs may
deploy. No human approval. Say so in the final report.

## Phase 1: Preconditions (abort on any failure, change nothing)

1. `git fetch origin main --tags`.
2. Find the release PR (branch `release/vX.Y.Z`, title `chore: release vX.Y.Z`):
   `gh pr list --search "release v in:title" --state all --json
   number,title,state,isDraft,headRefName,mergeStateStatus,mergeCommit --limit 5`.
   Take `X.Y.Z` from the branch name and call the PR `<n>`.
   - **Merged** (the user merged it by hand): skip Phase 2, use its
     `mergeCommit` as `<sha>`, and go to Phase 3.
   - **Open:** `git fetch origin release/vX.Y.Z` and carry on below.
   - None, or several open: stop and ask.
3. Read the version from `origin/release/vX.Y.Z` (or `<sha>` if merged), never
   the working tree. All three must equal `X.Y.Z`: `pyproject.toml`
   (`version = `), `src/coolhand/version.py` (`__version__`), and the
   `name = "coolhand"` entry in `uv.lock`. The workflow only checks
   `pyproject.toml`, so a mismatch in the other two ships silently.
4. Check all of these, and stop and tell the user which one failed:
   - `X.Y.Z` is greater than the latest tag (`git describe --tags --abbrev=0
     origin/main`), and `CHANGELOG.md` at the same ref has a
     `## [X.Y.Z] - ` heading.
   - If open: not a draft, `mergeStateStatus` is `CLEAN`, and `gh pr checks
     <n>` shows every check passed (main has no branch protection, so there
     is no "required" list; treat all of them as required). Pending counts as
     a failure.
   - The tag does not exist, locally (`git tag -l vX.Y.Z`) or on the remote
     (`git ls-remote --tags origin 'vX.Y.Z*'`), and no release exists
     (`gh release view vX.Y.Z` fails). If the tag exists but the release does
     not, confirm the tag points at the merge commit, skip Phases 2 and 3, and
     resume at Phase 4.

## Phase 2: Merge the release PR

1. Save the PR's current description outside the repo:
   `gh pr view <n> --json body --jq .body > /tmp/release-pr-<n>-body.md`.
2. `gh pr merge <n> --squash --delete-branch --subject "release vX.Y.Z (#<n>)"
   --body-file /tmp/release-pr-<n>-body.md`. This matches history (`release
   v0.10.1 (#209)`, body = PR description). `--subject` is used verbatim, so
   keep the `(#<n>)`. Add no attribution lines. Never `--admin` or bypass
   checks; if the merge is refused, stop and report.
3. `git fetch origin main --tags`; `<sha>` is `gh pr view <n> --json
   mergeCommit --jq .mergeCommit.oid`. Re-read the three version files at
   `<sha>` and confirm they still say `X.Y.Z`.

## Phase 3: Tag

**The tag target is `<sha>`, the release PR's merge commit, not `origin/main`
HEAD.** Main keeps moving: v0.10.1 points at `release v0.10.1 (#209)` while main
was already past it. Tagging HEAD would publish unchangelogged code.

1. `git tag -a vX.Y.Z <sha> -m "Release vX.Y.Z"`. Annotated, like the existing
   tags (`git cat-file -p v<previous>`: `type commit`, message `Release vX.Y.Z`).
2. `git push origin refs/tags/vX.Y.Z`. Push only that ref, never `--tags`.
3. Verify with `git ls-remote --tags origin 'vX.Y.Z*'`. The `git push` output
   can be swallowed by the shell's output filter, so don't trust its silence;
   the `^{}` line must show `<sha>`.

Never `make publish`, `twine upload` or `uv publish`; CI publishes.
## Phase 4: Release notes

This repo's release body is the CHANGELOG section itself, verbatim: the
`## [X.Y.Z] - YYYY-MM-DD` heading and everything under it up to the next
`## [` heading, with no emoji sections, no "Known Follow-ups" and no
"Resources". v0.8.0 through v0.10.1 all match their CHANGELOG sections exactly.
Don't invent a different layout; improve the CHANGELOG in a PR instead.

1. Extract the section from the tagged commit, not the working tree:
   `git show vX.Y.Z:CHANGELOG.md`. Write it to `.context/release-notes-vX.Y.Z.md`
   (gitignored). Compare with `gh release view v<previous> --json body --jq
   .body` to confirm the convention still holds; if it deviates, say so and ask.
2. **Verify before publishing.** The changelog was approved in the PR, so don't
   rewrite it, but check what it links: every `(#N)` is a merged PR
   (`gh pr view N --json state`) and every `./docs/...` link resolves at
   `<sha>`. Flag, don't silently edit, a `Known gap` the release touched.
3. `gh release create vX.Y.Z --verify-tag --title "vX.Y.Z" --notes-file
   .context/release-notes-vX.Y.Z.md --latest`. Not a draft and not a
   prerelease, same as the previous release. Confirm with
   `gh release view vX.Y.Z --json name,tagName,isDraft,isPrerelease,url`.

## Phase 5: Report

State, in the final message:
- The merged PR number and its merge commit (or that it was already merged).
- The tag and the commit it points at, and the release URL.
- The Publish run (`gh run list --workflow publish.yml --limit 1`): about 15-20
  minutes, mostly the `pypi-publish` wait timer. Do not approve, re-run or
  publish by any other route. If asked whether it is live: `curl -s -o
  /dev/null -w '%{http_code}' https://pypi.org/pypi/coolhand/X.Y.Z/json`
  returns `200` once published (not `pip index`, which can hide versions).

## Rationalizations to resist

- *"`gh pr merge` will write a fine default message."* The squash subject and
  body are the release's record (`release vX.Y.Z (#N)` plus the PR description).
  Pass both explicitly.
- *"Checks are probably green."* Run `gh pr checks`. A pending check is not a
  pass, and `--admin` is never the answer to a red one.
- *"`origin/main` HEAD is the release, I'll tag that."* Other PRs can merge
  after the release PR. Tag its merge commit.
- *"`pyproject.toml` says X.Y.Z, so the version is right."* The workflow only
  checks that file. `__version__` feeds the User-Agent and `uv.lock` feeds
  installs; check all three.
- *"I'll publish the notes first and tag after."* `--verify-tag` exists so a
  release can't point at nothing; GitHub would otherwise create the tag itself
  from the default branch tip, unannotated and unreviewed.
- *"`git push` printed nothing, so it probably worked."* Verify with
  `git ls-remote`.
