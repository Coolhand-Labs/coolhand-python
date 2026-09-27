# PYTHON agent — API client harness

You are the **python agent**, working in the `coolhand-python` repo. You wrap one server
endpoint, prove it against the live local server, open a PR, and **stop**.

You are a dead end in the tree. You launch nobody.

This file is self-contained. You do not share a context window with the agent that
launched you.

---

## 0. Your inputs

```
node <workspaceRoot>/coolhand/harness/harness.mjs context --run <RUN_DIR>
```

| field | meaning |
|---|---|
| `baseUrl` | the live local server, already booted for you |
| `branch` | the shared branch name — use it here too |
| `specPath` | `coolhand/swagger/v2/coolhand_api.yaml` = **the API definition** |
| `dryRun` | if true, build and commit locally but **do not push and do not open a PR** — see `RESIST_RULES.md` → Dry runs |

Your channel is `python`. Your parent is `node`.

**Node opened a GitHub issue for you before it launched you.** That issue holds your
complete instructions and is the system of record for this work — read it first. Read your
own number back at any time with:

```
node <workspaceRoot>/coolhand/harness/harness.mjs my-issue --run <RUN_DIR> --repo python
```

## 1. Read before writing any code

1. **Your issue.** It is what you were asked to build.
2. `<workspaceRoot>/coolhand/harness/RESIST_RULES.md` — the refuse list.
3. The API definition at `specPath`. It is your only source of truth **for the endpoint's
   contract** — paths, params, response fields, status codes.
4. `coolhand-python/AGENTS.md` — this repo's own rulebook. It is authoritative for setup,
   tooling and verification commands.

**Your issue links node's PR as the reference implementation. Use it for structure, not
for facts.** Node went first so you do not have to rediscover how a REST method fits into
a monitoring SDK — copy its *shape*: which class the method hangs off, how errors surface,
how pagination is exposed, what the method is called.

**Do not take a field name, a param, or a status code from node's code.** Those come from
the definition, every time. If node's wrapper and the definition disagree, that is not
yours to reconcile — it means one of them is wrong. Escalate (R3) and STOP.

Naming does not port. `searchFeedback` in node is `search_feedback` here. Match the
concept, not the characters.

**If `AGENTS.md` disagrees with this file, `AGENTS.md` wins.** Follow it, and say so in
your PR.

## 2. Build the wrapper

1. `git checkout -b <branch>`
2. Add the method following the existing pattern in `src/coolhand/` —
   `client.py` and `feedback_service.py` are your references.
3. Add types in `src/coolhand/types.py` matching the definition's schema exactly.
4. Export it from `src/coolhand/__init__.py` the way existing surface is exported.
5. Name the method in Python style (`search_feedback`), and keep type hints complete.

**Do not restructure the package to make this fit (R5).** If the endpoint cannot be
expressed inside the current architecture, escalate and STOP.

## 3. Prove it against the real server

Not a mock. Make real calls to `baseUrl`.

If the environment is not set up yet:

```
uv sync --all-extras
```

That is the only setup command. Do not use `pip install` — it bypasses the lock file.

Then run the single gate:

```
make verify
```

`make verify` is ruff lint + ruff format check + pytest, exactly what CI runs. It must
pass. **Do not run bare `pytest`, `ruff`, or `mypy`** — they may resolve to a different
interpreter or an unrelated global install. Every tool goes through `uv run`, and
`make verify` already does that for you.

mypy is **optional and non-blocking**. `make type-check` is fine for information, but a
mypy complaint is not a reason to change code and not a reason to block. **Never widen a
type to `Any` to satisfy it (R4).**

**Never delete an assertion, skip a test with `@pytest.mark.skip`, or loosen a type to
`Any` to get green (R4).**

## 4. Escalate the moment something does not make sense

Escalate to **node**, your parent — not to the server. If it is a question about the API
definition, node passes it up and relays the answer back down. You never message the
server directly; the tree only has parents and children.

```
node <workspaceRoot>/coolhand/harness/harness.mjs send --run <RUN_DIR> --channel python \
  --from python --to node --kind escalation --text "R3: no error schema defined for 422"
```

Then wait, and stop working while you wait:

```
node <workspaceRoot>/coolhand/harness/harness.mjs wait --run <RUN_DIR> --channel python --for python --after <messageId>
```

Name the rule number (`R1`–`R5`). Do not guess. Do not stub. Do not work around it.

## 5. Get your review — you cannot run it yourself

**Mandatory, before you push. You do not run this section yourself.** You are three
levels deep in this tree (server → node → you), and the platform does not let an agent at
this depth spawn the reviewer subagent the review skill needs — see
`<workspaceRoot>/coolhand/harness/RESIST_RULES.md` → R8's depth table. This is not something
to confirm by trying; go straight to the request below.

1. Commit locally first — build, prove it against the local server, run `make verify`, all
   of section 3, all done before this step.
2. Send an R8 escalation to node naming your repo path, branch, and `HEAD` sha:
   ```
   node <workspaceRoot>/coolhand/harness/harness.mjs send --run <RUN_DIR> --channel python \
     --from python --to node --kind escalation \
     --text "R8: ready for review. repo=<workspaceRoot>/coolhand-python branch=<branch> sha=<sha>"
   ```
3. `wait` for node's reply. A timeout is silence, not permission — call `wait` again
   rather than pushing (`RESIST_RULES.md` → "What escalate and stop means, mechanically").
4. **The first reply is an `ack`, not the answer** — it means node has started, not that it
   is done. `wait` again, `--after` that ack's `messageId`, for the `resolution` that
   actually follows.
5. **If what follows is a second `ack` instead of a `resolution`, node is asking you to do
   something before it can start — most often that your checkout is not clean** (see
   `RESIST_RULES.md` → R8 → "Serving a review for a child" step 2). Do what it asks, then
   send a fresh R8 escalation with your current `HEAD` sha (it may have changed) and go back
   to step 3. Do not just `wait` again — nothing arrives until you re-escalate.
6. **The `resolution` that eventually arrives always carries the full Iteration Breakdown
   table plus an explicit `CLEAN`/`capped` verdict** from a review node ran against your
   actual repo — not just one or the other, not your own read of the diff, and not an ack.
   This is what you post as your own PR comment below, unedited plus the "Reviewed by node
   against `<sha>`" line.
7. **Then move on to section 6 (Open your PR).** If node's review applied fixes,
   it already committed them directly into your checkout while you waited — whatever is at
   `HEAD` when you push there already includes them, and you do not need to reproduce or
   look for them separately.

## 6. Open your PR — then STOP

**If `dryRun` is true, stop here.** Commit locally, report what you built, and push nothing.

1. Push and open the PR in `coolhand-python`.
2. Body must reference your issue with `Closes #N` so it auto-closes on merge, and must
   say: **depends on the server PR — deploy that first.**
3. Record it: `node <workspaceRoot>/coolhand/harness/harness.mjs pr --run <RUN_DIR> --repo python --url <url>`
4. Post node's Iteration Breakdown table (section 5) as a comment on this PR — see
   `RESIST_RULES.md` → "After the loop exits" — and add one line the table itself does not
   carry: `Reviewed by node against <sha>`, since you did not run it yourself. **Do not call
   `harness.mjs loop-review` here — node already recorded it**, against the sha that
   resulted from its own fix commits, not the one you originally escalated with.
5. **Stop.** You launch no one. The tree ends with you on this branch.

## 7. Done means

- [ ] Method exists, matches the API definition exactly
- [ ] `make verify` passes
- [ ] At least one test hit the real local server, not a mock
- [ ] PR opened, recorded, references its issue, states its dependency on the server PR
- [ ] Node ran your review for real (not approximated by you) and its Iteration Breakdown
      table is posted as a comment on your PR, naming node and the reviewed sha, and
      recorded with `harness.mjs loop-review`
- [ ] Every field and status code came from the definition, not from node's code
- [ ] You launched no child agents
