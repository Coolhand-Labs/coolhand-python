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

Any request whose URL matches one of the listed patterns is passed through without logging. Patterns use the [entry syntax](#entry-syntax) below; a bare `/path` fragment is matched against the URL path only, never the query string. The default list (`DEFAULT_EXCLUDE_API_PATTERNS`) excludes non-inference management endpoints for Vertex AI, Azure OpenAI and Cohere, such as `/batchPredictionJobs/`, `/openai/fine_tuning` and `api.cohere.com/v1/embed-jobs` (which would otherwise match the Cohere `/v1/embed` allow-list entry); setting `exclude_api_patterns` **replaces** the default entirely.

`exclude_api_patterns` and `intercept_addresses` must be lists of strings; anything else logs a warning when `Coolhand(...)` is constructed. If matching still fails at request time (for example a non-string entry), the request is not captured — it is treated as excluded for `exclude_api_patterns` and as not matching for `intercept_addresses`. Fix the list to restore capture.

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

## Request Timeout

`request_timeout` is the timeout, in seconds, for every call the SDK makes to the Coolhand API: delivering captured interactions, submitting feedback, reading templates and linking feedback. It defaults to `30`. Raise it if a slow self-hosted backend (see above) trips the default:

```python
from coolhand import Coolhand

coolhand_client = Coolhand(
    api_key='your-api-key',
    base_url='https://feedback.example.com',
    request_timeout=60,
)
```

`Coolhand` passes the value to its feedback, template and feedback-link services; a standalone `FeedbackService`, `TemplateService` or `FeedbackLinkService` takes the same `request_timeout=` argument.

**Validation rules:**
- Must be a finite number greater than zero, at most `2147483647` (2^31-1) seconds. Floats such as `2.5` are fine.
- `0`, negative numbers, `NaN`, infinity, booleans and non-numbers raise `ValueError` at construction, rather than failing as an opaque network error on the first request.

**Notes:**
- This is the Python counterpart of coolhand-node's `requestTimeoutMs`. The unit is seconds here, and so fractional values are allowed.
- The limit is applied to the connection and to each read separately, as with any socket timeout. It is not a cap on the total time of one request, so a server that trickles bytes can run past it.
- `timeout` is the older spelling and still works; when both are set, `request_timeout` wins.
- Earlier versions used a fixed 10 seconds for delivering interactions and submitting feedback, and 30 seconds for template and feedback-link calls. All of them now share the 30-second default.
- coolhand-node keeps a separate, longer limit for file uploads. This SDK has no file upload path to the Coolhand API, so there is no second option.
- A slow or unreachable backend now ties up the background delivery worker for up to `request_timeout` per interaction, so a high value lets the delivery queue back up longer before it drops items.
- `shutdown()` still waits at most 5 seconds for in-flight deliveries, whatever `request_timeout` is, so a high value cannot hold up process exit.

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

Omit `intercept_addresses` altogether to get the built-in list.

### Entry syntax

`intercept_addresses` and `exclude_api_patterns` entries are matched against the *parsed* URL (host, port and path), not the raw string, so query text, userinfo (`https://api.openai.com@evil.com`) and lookalike hosts (`api.openai.com.evil.com`) cannot match. A leading `https://` or `//` on an entry is ignored. Hosts are case-insensitive, paths are case-sensitive and percent-decoded before matching, and an explicit default port (`:443` on `https`) matches the implied one. Entries cannot carry a query string (they would never match).

| Entry | Matches |
|---|---|
| `my-llm-proxy.internal` | that host and its subdomains |
| `my-host.example.com/v1/chat` | that host, with a path starting `/v1/chat` |
| `my-host.example.com:8443` | that host on that port |
| `:11434/api/chat` | any host on port 11434, path starting `/api/chat` |
| `/v1/inference` | any host, path containing `/v1/inference` |
| `googleapis.com/*:predict` | `*` is a glob; a globbed path must match the whole path |
| `bedrock-runtime.*.amazonaws.com` | `*` in a host matches one label (never a `.`); a globbed host is exact, with no implicit subdomains |
| `[2001:db8::1]:11434` | IPv6 hosts need brackets |

**Migration:** earlier versions matched entries as plain substrings of the full URL, and skipped any URL whose authority merely *contained* `localhost`. Loopback is now decided from the parsed host alone, so `localhost.example.com` or `user:localhost@host` are no longer skipped, while all of `127.0.0.0/8` and `*.localhost` now are. Entries that are a host, `host/path`, or a `/path` fragment keep working. An arbitrary fragment from the middle of a URL (for example `'openai'`, or a host-less `':predict'`) no longer matches; rewrite it with one of the forms above. The built-in Vertex AI / Gemini `:generateContent`, `:predict` etc. entries are now restricted to `googleapis.com` hosts (Vertex dedicated endpoints on `prediction.vertexai.goog` are matched by host; a custom domain or proxy in front of Vertex needs its own entry), and the Bedrock entries are listed by host (`bedrock-runtime.*.amazonaws.com`, `.amazonaws.com.cn`, `.api.aws`, plus FIPS variants).
