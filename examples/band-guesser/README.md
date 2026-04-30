# Band Guesser

A sample FastAPI app that demonstrates [coolhand-python](https://github.com/Coolhand-Labs/coolhand-python) SDK monitoring across multiple LLM inference pathways.

Users write a sentence about themselves, the app guesses 8 bands they might like, and they check off which ones they actually enjoy. That selection is submitted back as structured feedback via the coolhand SDK.

## What it tests

### Three inference pathways

| Toggle | Transport | Coolhand capture method |
|---|---|---|
| **GitHub Copilot SDK** | JSON-RPC over stdio | `copilot_interceptor` patches the SDK's JSON-RPC client |
| **GitHub Models (httpx)** | `httpx.AsyncClient` directly | `httpx_interceptor` patches `httpx.AsyncClient.send` |
| **GitHub Models (SDK)** | `azure-ai-inference` `ChatCompletionsClient` | `httpx_interceptor` patches `requests.Session.send` |

The third mode is the key test case for [issue #12](https://github.com/Coolhand-Labs/coolhand-python/issues/12) — any SDK built on `azure-core` defaults to a `requests` transport, which was previously invisible to coolhand.

### Client compatibility

The app deliberately replicates the dependency environment of a real client (Teladoc QA Agent Orchestrator) to surface integration issues before they reach production:

- **OpenTelemetry** (`opentelemetry-instrumentation-httpx`, `opentelemetry-instrumentation-requests`) — both OTel instrumentors are applied *before* coolhand to test the worst-case patch ordering. Spans are discarded (no exporter configured); the point is that coolhand's patches wrap correctly on top of OTel's.
- **structlog** — configured before coolhand initializes. Coolhand is passed `silent=True` to prevent it calling `logging.basicConfig`, which would interfere with structlog's logging setup.
- **gunicorn** — `gunicorn.conf.py` runs 2 workers via `UvicornWorker`. Each worker is a separate process, so coolhand fires one heartbeat per worker on startup (expected behaviour with the `fix-heartbeat-once` fix).

### Feedback flow

After the user checks off bands they like, the app calls `_ch.create_feedback()` with:
- `original_output` — the raw LLM response, used by coolhand to fuzzy-match the logged interaction
- `like` — `True` if the user liked at least half the suggestions
- `explanation` — a human-readable summary ("Liked: X, Y. Disliked: Z.")

## Setup

```bash
cp .env.example .env
# Edit .env and add your Coolhand API key

pip install -r requirements.txt
```

## Running

```bash
# Development (single worker, auto-reload)
python3.11 -m uvicorn main:app --reload --port 8188

# Production-like (2 gunicorn workers — tests multi-process behaviour)
python3.11 -m gunicorn -c gunicorn.conf.py main:app
```

## GitHub token

The app needs a GitHub token to call the Copilot and GitHub Models APIs.

- Leave the token field blank to use `gh auth token` automatically.
- For the **Copilot SDK** mode, the token must be an OAuth token (`gho_`) — classic PATs (`ghp_`) are not supported by the Copilot API.
- For **GitHub Models** modes, both OAuth tokens and classic PATs work.
