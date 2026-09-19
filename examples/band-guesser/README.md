# Band Guesser

A sample FastAPI app that demonstrates [coolhand-python](https://github.com/Coolhand-Labs/coolhand-python) SDK monitoring across multiple LLM inference pathways.

Users write a sentence about themselves, the app guesses 8 bands they might like, and they check off which ones they actually enjoy. That selection is submitted back as structured feedback via the coolhand SDK.

## What it tests

### Three inference pathways

| Toggle | Transport | Coolhand capture method |
|---|---|---|
| **GitHub Copilot SDK** | JSON-RPC over stdio | `copilot_interceptor` patches the SDK's JSON-RPC client |
| **Azure Foundry (httpx)** | `httpx.AsyncClient` directly | `httpx_interceptor` patches `httpx.AsyncClient.send` |
| **Azure Foundry (SDK)** | `azure-ai-inference` `ChatCompletionsClient` | `httpx_interceptor` patches `requests.Session.send` |

The third mode is the key test case for [issue #12](https://github.com/Coolhand-Labs/coolhand-python/issues/12) — any SDK built on `azure-core` defaults to a `requests` transport, which was previously invisible to coolhand.

### Client compatibility

The app deliberately replicates the dependency environment of a real client (Teladoc QA Agent Orchestrator) to surface integration issues before they reach production:

- **OpenTelemetry** (`opentelemetry-instrumentation-httpx`, `opentelemetry-instrumentation-requests`) — both OTel instrumentors are applied *before* coolhand to test the worst-case patch ordering. Spans are discarded (no exporter configured); the point is that coolhand's patches wrap correctly on top of OTel's.
- **structlog** — configured before coolhand initializes. Coolhand is passed `silent=True` to prevent it calling `logging.basicConfig`, which would interfere with structlog's logging setup.
- **gunicorn** — `gunicorn.conf.py` runs 2 workers via `UvicornWorker`. Each worker is a separate process, so coolhand fires one heartbeat per worker on startup (expected behaviour with the `fix-heartbeat-once` fix).

### Feedback flow

After the user checks off bands they like, the app calls `_ch.create_feedback()` with:
- `original_output` — the raw LLM response, used by coolhand to fuzzy-match the logged interaction
- `sentiment` — `"like"` if the user liked at least half the suggestions, `"dislike"` otherwise
- `explanation` — a human-readable summary ("Liked: X, Y. Disliked: Z.")

## Setup

```bash
cp .env.example .env
# Edit .env and add your Coolhand API key, plus the Azure Foundry / Azure OpenAI
# endpoint and key if you want to exercise the Azure modes (see below)

pip install -r requirements.txt
```

## Testing locally (against an unreleased SDK build)

To run the app against your local checkout of `coolhand-python` instead of the version installed from git:

```bash
cd examples/band-guesser

uv venv --python 3.12 .venv
source .venv/bin/activate

# Install remaining deps first (skip the coolhand git line)
grep -v '^coolhand' requirements.txt | uv pip install -r -

# Install local SDK in editable mode (takes precedence)
uv pip install -e ../../
```

Then start the server:

```bash
source .venv/bin/activate
uvicorn main:app --reload --port 8188
```

## Running

```bash
# Development (single worker, auto-reload)
python -m uvicorn main:app --reload --port 8188

# Production-like (2 gunicorn workers — tests multi-process behaviour)
python -m gunicorn -c gunicorn.conf.py main:app
```

## Credentials

### Azure modes (`azure`, `azure-sdk`)

GitHub Models (`models.github.ai`) was retired on 2026-07-30, so these modes call a
[Microsoft Foundry](https://ai.azure.com) / Azure OpenAI deployment you provide. Set in `.env`:

| Variable | Description |
|---|---|
| `AZURE_INFERENCE_ENDPOINT` | Base URL, e.g. `https://<resource>.services.ai.azure.com/models` |
| `AZURE_INFERENCE_KEY` | API key for that resource |
| `AZURE_INFERENCE_MODEL` | Model / deployment name (default `gpt-4.1-mini`) |

Without the first two, these modes return a `501` explaining what is missing. The request's GitHub token is ignored.

Coolhand captures the Azure calls through its default intercept list, which covers `openai.azure.com` and `services.ai.azure.com/models/`. If your endpoint sits on a host outside that list (a custom domain or API gateway), the calls still succeed but are not captured. Add the host via `intercept_addresses` in `main.py`.

### GitHub token (`copilot` mode only)

- Leave the token field blank to use `gh auth token` automatically.
- The token must be an OAuth token (`gho_`) — classic PATs (`ghp_`) are not supported by the Copilot API.
