# Advanced Configuration

## Excluding API Patterns

Some endpoints — batch jobs, health checks, internal metrics — generate high-volume traffic that isn't useful to log. Use `exclude_api_patterns` to skip them:

```python
from coolhand import Coolhand

coolhand_client = Coolhand(
    api_key='your-api-key',
    exclude_api_patterns=[
        '/health',
        '/metrics',
        '/batchPredictionJobs/',
    ],
)
```

Any request whose URL contains one of the listed substrings is passed through without logging. The default list (`DEFAULT_EXCLUDE_API_PATTERNS`) excludes non-inference management endpoints for Vertex AI, Azure OpenAI and Cohere, such as `/batchPredictionJobs/`, `/openai/fine_tuning` and `api.cohere.com/v1/embed-jobs` (which would otherwise match the Cohere `/v1/embed` allow-list entry); setting `exclude_api_patterns` **replaces** the default entirely.

To extend the defaults rather than replace them:

```python
from coolhand import Coolhand, DEFAULT_EXCLUDE_API_PATTERNS

coolhand_client = Coolhand(
    api_key='your-api-key',
    exclude_api_patterns=DEFAULT_EXCLUDE_API_PATTERNS + ['/health', '/metrics'],
)
```

### Default excluded patterns (Vertex AI, Azure OpenAI and Cohere non-inference endpoints)

```python
from coolhand import DEFAULT_EXCLUDE_API_PATTERNS
print(DEFAULT_EXCLUDE_API_PATTERNS)
# ['/batchPredictionJobs/', '/datasets/', '/trainingPipelines/', ...]
```

These are excluded because they're management operations — Vertex AI training, dataset and pipeline endpoints, and Azure OpenAI file, batch, fine-tuning and model-listing endpoints — not LLM inference calls.

---

## Limiting Captured Body Size

Request and response bodies are captured in full by default, which can be large when prompts carry base64 images or audio. To keep memory bounded, each captured body is capped at `max_body_bytes` (default **1 MiB**, measured in UTF-8 bytes):

```python
from coolhand import Coolhand

coolhand_client = Coolhand(
    api_key='your-api-key',
    max_body_bytes=256 * 1024,  # 256 KiB
)
```

A body over the limit is cut to the first `max_body_bytes` bytes and ends with a marker, for example `[truncated: 5242880 bytes, kept first 1048576]`, where the first number is the original size. It is sent as a plain string rather than parsed JSON, so it will not be structured in the Coolhand dashboard. The cap applies to request bodies, response bodies and streaming responses (the stream accumulator stops buffering once the limit is reached). Credentials in Azure OpenAI `data_sources` are still redacted before truncation.

Set `max_body_bytes=None` (or `0`) to disable the cap and log bodies in full.

---

## Self-Hosted Deployments

If you run your own Coolhand-compatible backend (e.g. for compliance or data-residency requirements), point the SDK at your host with `base_url`:

```python
from coolhand import Coolhand

coolhand_client = Coolhand(
    api_key='your-api-key',
    base_url='https://feedback.example.com',
)
```

Or via environment variable — useful for 12-factor deployments where configuration comes from the environment:

```bash
export COOLHAND_API_KEY=your-api-key
export COOLHAND_BASE_URL=https://feedback.example.com
```

```python
import coolhand  # picks up COOLHAND_BASE_URL automatically
```

**URL validation rules:**
- `https://` is required for all non-local hosts
- `http://localhost` and `http://127.0.0.1` are allowed for local development only
- Non-HTTPS remote URLs are rejected at initialization time

---

## Custom Intercept Addresses

By default Coolhand captures requests to a built-in list of LLM API hosts (OpenAI, Anthropic, Gemini, etc.). To capture a custom endpoint — an internal proxy, a self-hosted model server, or a third-party gateway — pass `intercept_addresses`:

```python
from coolhand import Coolhand

coolhand_client = Coolhand(
    api_key='your-api-key',
    intercept_addresses=['my-llm-proxy.internal'],
)
```

Setting `intercept_addresses` **replaces** the default list entirely. To keep the built-in hosts and add to them:

```python
from coolhand import Coolhand, DEFAULT_INTERCEPT_ADDRESSES

coolhand_client = Coolhand(
    api_key='your-api-key',
    intercept_addresses=DEFAULT_INTERCEPT_ADDRESSES + ['my-llm-proxy.internal'],
)
```

Passing an empty list disables capture entirely — it does **not** fall back to the defaults:

```python
Coolhand(api_key='your-api-key', intercept_addresses=[])  # captures nothing
```

Omit `intercept_addresses` altogether to get the built-in list. Entries are matched as plain substrings against the full request URL, so a path fragment such as `'my-host.example.com/v1/chat'` works as well as a bare hostname.
