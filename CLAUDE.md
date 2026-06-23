# coolhand-python

## Setup

```bash
uv sync --all-extras
```

This is the only setup command needed. Do not use `pip install`, `pip install -e ".[dev]"`, or any variant — they bypass the lock file and may resolve a different interpreter.

## Verify before committing

```bash
make verify
```

This runs ruff lint, ruff format check, and pytest — exactly what CI runs. A green `make verify` means a green CI run. Do not run these steps individually as a substitute; use `make verify` as the single gate.

## Running individual tools

Always prefix with `uv run` to ensure the project's venv is used:

```bash
uv run pytest tests/test_client.py   # run a single test file
uv run ruff check src                # lint only
uv run mypy src                      # type check (non-blocking in CI)
```

Never invoke `pytest`, `ruff`, or `mypy` directly — they may resolve to a different interpreter or an unrelated global install.

## Other make targets

| Target | What it does |
|--------|-------------|
| `make test` | Run the full test suite |
| `make lint` | Ruff lint across src, tests, examples |
| `make format` | Auto-fix then format (ruff check --fix, then ruff format) |
| `make type-check` | mypy on src/ |
| `make build` | Build the package (calls `uv run python -m build`) |

## Python version

Pinned to 3.12 via `.python-version`. uv picks this up automatically.

## README and docs philosophy

The README is a landing page — install, quick start, what it supports, where to go next. Keep it scannable. When in doubt, link rather than expand.

**Three rules:**
- **Config**: env vars table and the basic `Coolhand(api_key=...)` snippet belong in the README. Anything requiring more than one code block (exclude patterns, self-hosted, custom intercept addresses) goes in `docs/configuration.md`.
- **Feedback**: the two basic `create_feedback()` examples belong in the README. The full field table, matching strategies, and sentiment conversion details go in `docs/feedback.md`.
- **Supported libraries**: a flat bulleted list belongs in the README. The interception mechanism breakdown (httpx vs requests vs JSON-RPC), streaming behavior, and thread/process safety notes go in `docs/supported-libraries.md`.

**Integrations** each get their own `docs/<name>.md` file. The README links to them from both an Integrations table and the Documentation section at the bottom. Follow the structure of `docs/dramatiq.md`: quick start, what works / what doesn't table, known gaps with workarounds, roadmap.

**Align with coolhand-node.** When adding a section that exists in the Node README, match its structure and tone. The two READMEs should feel like siblings.

**Discoverability (SEO / AEO).** The README is indexed by search engines and consumed by AI agents doing package research. Write headings, the package description, and the supported-libraries list with this in mind: use the full names of supported providers and frameworks (e.g. "OpenAI", "Anthropic", "pydantic-ai", "Dramatiq") rather than abbreviations, and make the one-line description in the README header accurate and keyword-rich. The goal is that both humans and agents searching for "Python LLM monitoring", "OpenAI request logging", or "pydantic-ai observability" land here.
