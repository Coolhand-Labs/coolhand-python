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
