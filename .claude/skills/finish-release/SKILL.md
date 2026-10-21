---
name: finish-release
description: |
  Finishes a release once its release-prep PR (from /prep-release) has been
  merged: creates and pushes the annotated vX.Y.Z tag on the release commit,
  then publishes the GitHub release with the CHANGELOG section as its notes.
  Pushing the tag triggers the Publish workflow (PyPI Trusted Publishing),
  so this is the step that ships the package. Use when the user types
  /finish-release, asks to "tag the release", "publish the release notes",
  "ship v0.x.y", or has just merged the release-prep PR.
user_invocable: true
---

# Finish Release

The counterpart to `/prep-release`. That skill stops at an open release PR and
never tags or publishes; this one starts after the user has reviewed and merged
that PR. Running it is the user's go-ahead to tag and publish. Two steps, in
this order: **tag, then release notes**. The release is created with
`--verify-tag`, so the tag has to exist first.

**What pushing the tag does:** `.github/workflows/publish.yml` runs on any
`v*.*.*` tag push. The `build` job runs `make verify`, `uv build`,
`twine check`, and fails unless the tag matches `pyproject.toml`'s `version`.
The `publish` job then uploads to PyPI by Trusted Publishing, behind the
`pypi-publish` environment: a 15-minute wait timer, and only `main` and
`v*.*.*` refs may deploy. It does not need a human approval. Say so in the
final report.

## Phase 1: Preconditions (abort on any failure, change nothing)

1. `git fetch origin main --tags`.
2. Find the release-prep PR: `gh pr list --state merged --search "release v in:title"
   --json number,title,mergeCommit,mergedAt --limit 5`, newest first. Its title
   is `chore: release vX.Y.Z`; take `X.Y.Z` from it and note the merge commit
   `<sha>`. If there is none, or the newest one is already tagged, stop.
3. Read the version from `<sha>` (never the working tree). All three must equal
   `X.Y.Z`: `git show <sha>:pyproject.toml` (`version = `),
   `git show <sha>:src/coolhand/version.py` (`__version__`), and the
   `name = "coolhand"` entry in `git show <sha>:uv.lock`. The workflow only
   checks `pyproject.toml`, so a mismatch in the other two ships silently.
4. Check all of these, and stop and tell the user which one failed:
   - `<sha>` is on main: `git merge-base --is-ancestor <sha> origin/main`.
   - `X.Y.Z` is greater than the latest tag (`git describe --tags --abbrev=0
     <sha>`), and `git show <sha>:CHANGELOG.md` has a `## [X.Y.Z] - ` heading.
   - The tag does not already exist, locally (`git tag -l vX.Y.Z`) or on the
     remote (`git ls-remote --tags origin 'vX.Y.Z*'`), and no release exists
     (`gh release view vX.Y.Z` fails). If the tag exists but the release does
     not, skip Phase 2 and resume at Phase 3, after confirming the tag points
     at `<sha>`.

**The tag target is `<sha>`, not `origin/main` HEAD.** Main keeps moving: v0.10.1
points at `release v0.10.1 (#209)` while main was already past it. Tagging HEAD
would publish unreleased, unchangelogged code. Do not tag a local branch.

## Phase 2: Tag

1. `git tag -a vX.Y.Z <sha> -m "Release vX.Y.Z"`. Annotated, like the existing
   tags (`git cat-file -p v<previous>`: `type commit`, message `Release vX.Y.Z`).
2. `git push origin refs/tags/vX.Y.Z`. Push only that ref, never `--tags`.
3. Verify with `git ls-remote --tags origin 'vX.Y.Z*'`. The `git push` output
   can be swallowed by the shell's output filter, so don't trust its silence;
   the `^{}` line must show `<sha>`.

Never `make publish`, `twine upload` or `uv publish`; CI publishes.

## Phase 3: Release notes

This repo's release body is the CHANGELOG section itself, verbatim: the
`## [X.Y.Z] - YYYY-MM-DD` heading and everything under it up to the next
`## [` heading, with no emoji sections, no "Known Follow-ups" and no
"Resources". v0.8.0 through v0.10.1 all match their CHANGELOG sections exactly.
Don't invent a different layout.

1. Extract the section from the tagged commit, not the working tree:
   `git show vX.Y.Z:CHANGELOG.md`, from `## [X.Y.Z]` to the line before the next
   `## [`. Write it to `.context/release-notes-vX.Y.Z.md` (gitignored). Compare
   with `gh release view v<previous> --json body --jq .body` to confirm the
   convention still holds; if the previous release deviates, say so and ask.
2. **Verify before publishing.** The changelog was written by `/prep-release`
   and approved in the PR, so don't rewrite it, but check what the notes link:
   every `(#N)` is a merged PR (`gh pr view N --json state`) and every relative
   link (`./docs/...`) resolves to a file at `<sha>`. Any `Known gap` in
   `### Notes` should still be true; if the release touched it, flag it to the
   user instead of editing silently. If a fix is needed, it is a follow-up PR
   to `CHANGELOG.md`, not an edit to the notes alone, or they drift apart.
3. `gh release create vX.Y.Z --verify-tag --title "vX.Y.Z" --notes-file
   .context/release-notes-vX.Y.Z.md --latest`. Not a draft and not a
   prerelease, same as the previous release. Confirm with
   `gh release view vX.Y.Z --json name,tagName,isDraft,isPrerelease,url` and
   `gh release list --limit 1` showing `Latest`.

## Phase 4: Report

State, in the final message:
- The tag and the commit it points at.
- The release URL.
- The Publish run (`gh run list --workflow publish.yml --limit 1`): the whole
  run takes about 15-20 minutes, mostly the 15-minute `pypi-publish` wait
  timer after the build job. Do not approve, re-run or publish by any other route. If the user asks
  whether it is live, check again: `curl -s -o /dev/null -w '%{http_code}'
  https://pypi.org/pypi/coolhand/X.Y.Z/json` returns `200` once published
  (`pip index versions coolhand` can lag or hide versions your Python can't
  use, so don't rely on it).

## Rationalizations to resist

- *"`origin/main` HEAD is the release, I'll tag that."* Other PRs can merge
  between the release PR and this skill. Tag the release PR's merge commit.
- *"`pyproject.toml` says X.Y.Z, so the version is right."* The workflow only
  checks that file. `__version__` feeds the User-Agent and collector string and
  `uv.lock` feeds installs; check all three at `<sha>`.
- *"I'll publish the notes first and tag after."* `--verify-tag` exists so a
  release can't point at nothing; GitHub would otherwise create the tag itself
  from the default branch tip, unannotated and unreviewed.
- *"The notes read thin, I'll dress them up."* The CHANGELOG is the source of
  truth and the release body mirrors it. Improve the changelog in a PR.
- *"`git push` printed nothing, so it probably worked."* Verify with
  `git ls-remote`.
