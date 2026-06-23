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

Any request whose URL contains one of the listed substrings is passed through without logging. The default list (`DEFAULT_EXCLUDE_API_PATTERNS`) excludes non-inference Vertex AI endpoints such as `/batchPredictionJobs/`; setting `exclude_api_patterns` **replaces** the default entirely.

To extend the defaults rather than replace them:

```python
from coolhand import Coolhand, DEFAULT_EXCLUDE_API_PATTERNS

coolhand_client = Coolhand(
    api_key='your-api-key',
    exclude_api_patterns=DEFAULT_EXCLUDE_API_PATTERNS + ['/health', '/metrics'],
)
```

### Default excluded patterns (Vertex AI non-inference endpoints)

```python
from coolhand import DEFAULT_EXCLUDE_API_PATTERNS
print(DEFAULT_EXCLUDE_API_PATTERNS)
# ['/batchPredictionJobs/', '/datasets/', '/trainingPipelines/', ...]
```

These are excluded because they're Vertex AI management operations, not LLM inference calls.

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
    intercept_addresses=[
        'my-llm-proxy.internal',
        'api.openai.com',           # include the defaults you still want
        'api.anthropic.com',
    ],
)
```

Setting `intercept_addresses` **replaces** the default list entirely, so include any default hosts you still need.
