# Examples

Run any example from the repo root with `uv run python examples/<name>.py`.

| Example | What it shows |
|---------|---------------|
| `basic_usage.py` | Manual initialization, `log_interaction`, feedback, shutdown (placeholder key) |
| `auto_monitor_example.py` | Zero-config monitoring (simulated provider calls) |
| `dramatiq_pydantic_ai.py` | Dramatiq + pydantic-ai, see [docs/dramatiq.md](../docs/dramatiq.md) |
| `band-guesser/` | FastAPI app, see its own README |
| `live_openai.py`, `live_anthropic.py` | Live smoke tests, below |

## Live smoke tests

`live_openai.py` and `live_anthropic.py` make **one real provider call** through the interceptor, then wait for
delivery (`coolhand.shutdown()`) and check that the log reached Coolhand with no drops or delivery failures. Unlike
the other examples they are pass/fail checks, so a release can be validated against real keys.

```bash
make live-examples
```

| Variable | Needed by |
|----------|-----------|
| `COOLHAND_API_KEY` | both scripts |
| `OPENAI_API_KEY` | `live_openai.py` |
| `ANTHROPIC_API_KEY` | `live_anthropic.py` |

Each script exits 77 with `Skipping ...` when a key is missing, 0 on success, and 1 when the provider call or the
delivery fails; the runner classifies from that exit status. The runner (`scripts/live-examples.sh`) starts them
concurrently, writes a log per script to `.context/live-examples/<name>.log` (override the directory with
`COOLHAND_EXAMPLES_LOG_DIR`), prints `PASS`, `SKIP` or `FAIL` for each, and exits non-zero only on `FAIL`. A run where
everything is `SKIP` exits 0 but warns that nothing was exercised — a skip is not a pass.

Set `COOLHAND_LIVE_OPENAI_MODEL` / `COOLHAND_LIVE_ANTHROPIC_MODEL` to override the default model if a provider
retires it.

The `openai` and `anthropic` SDKs aren't project dependencies; the runner adds them with `uv run --with`.

### Keys in `~/.zshrc`

Provider keys usually live in your zsh profile, which a non-interactive shell (an agent's tool shell, CI) never
sources, so key-gated scripts would silently skip. When `zsh` is installed the runner re-execs itself under `zsh -ic`
so keys exported from `~/.zshrc` are visible. Set `COOLHAND_EXAMPLES_NO_ZSH=1` to opt out and use the current
environment as-is.

### Relationship to `make test-live`

`make test-live` runs `tests/live` against a real Coolhand server with a private key (feedback links, templates) and
makes no provider calls. `make live-examples` covers the provider interception path. Neither is part of `make verify`.
