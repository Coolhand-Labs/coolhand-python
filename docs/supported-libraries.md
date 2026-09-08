# Supported Libraries

Coolhand intercepts LLM API calls through three mechanisms. Together they cover virtually any Python library that communicates with an LLM provider.

## Interception Mechanisms

### httpx patching

Patches `httpx.Client.send` and `httpx.AsyncClient.send` at the class level. Covers any library that uses httpx internally — which includes the official SDKs for all major providers:

- **OpenAI Python SDK** (`openai`)
- **Anthropic Python SDK** (`anthropic`)
- **Google Gemini** (`google-generativeai` / `google-genai`)
- **GitHub Models** via `models.github.ai` or the legacy `models.inference.ai.azure.com`
- **Vertex AI** inference endpoints (`aiplatform.googleapis.com`)
- **Cloudflare AI Gateway** (`gateway.ai.cloudflare.com`)
- **OpenRouter** (`openrouter.ai`)
- **OpenCode** (`opencode.ai`)
- **pydantic-ai** (via its underlying provider SDK)
- Any other library that makes HTTP requests using httpx

### requests patching

Patches `requests.Session.send`. Only applied if the `requests` package is installed — skipped silently if not. Covers libraries that use `requests` rather than httpx:

- **Azure AI Inference** (`azure-ai-inference`)
- **Azure OpenAI** via `azure-core`
- Any other library using the `requests` library

### JSON-RPC patching

Directly patches `JsonRpcClient.request` in the GitHub Copilot SDK. Used because GitHub Copilot communicates over JSON-RPC rather than plain HTTP, so httpx patching alone doesn't capture it.

- **GitHub Copilot SDK** (`github-copilot-sdk`)

---

## How It Works

1. Importing `coolhand` patches `httpx.Client.send`, `httpx.AsyncClient.send`, `requests.Session.send` (if installed), and the GitHub Copilot `JsonRpcClient` — all at the class level, so every instance created after the patch is automatically covered.
2. Each patched method checks the request URL against the intercept allow-list and the exclude deny-list. Non-LLM requests pass through with zero overhead.
3. For matching requests, the request and response data are captured (with credentials redacted from headers and URL parameters) and queued for submission.
4. With `auto_submit=True` (the default), each interaction is submitted to Coolhand immediately after capture via a synchronous `urllib` POST.
5. Your application continues uninterrupted — errors during capture or submission are swallowed silently unless `silent=False`.

---

## URL Matching

The intercept allow-list is a list of substrings matched against the full request URL. A request is captured if the URL contains any entry in the list **and** does not contain any entry in the exclude deny-list.

**Default allow-list** (domains and path fragments):

```python
from coolhand.httpx_interceptor import DEFAULT_INTERCEPT_ADDRESSES
# ['api.openai.com', 'api.anthropic.com', 'generativelanguage.googleapis.com',
#  'aiplatform.googleapis.com', 'gateway.ai.cloudflare.com', 'models.github.ai',
#  'models.inference.ai.azure.com', 'openrouter.ai', 'opencode.ai',
#  'api.opencode.ai', ':generateContent', ':streamGenerateContent',
#  ':predict', ':streamRawPredict']
```

To override: pass `intercept_addresses=[...]` to `Coolhand()`. See [Advanced Configuration](./configuration.md#custom-intercept-addresses).

---

## Streaming Support

Streaming responses (SSE / `text/event-stream` and NDJSON) are captured differently from non-streaming:

- The patched async send wraps the response's async iterator methods (`aiter_bytes`, `aiter_lines`, `aiter_text`, `aiter_raw`) to accumulate chunks as they stream past.
- Capture and submission happen when the iterator is exhausted — after your code has consumed the full stream.
- The captured body is the complete concatenated stream content, with `is_streaming: true` in the logged metadata.

---

## Thread and Process Safety

- **Thread workers** (Dramatiq, Celery with threads): patches are class-level and visible across all threads. Works out of the box.
- **Process workers** (forked subprocesses): patches survive `fork()` but not `spawn()`. See the [Dramatiq guide](./dramatiq.md#gap-1--process-based-workers) for the workaround. A forked child also inherits a *copy* of the parent's in-memory delivery queue and worker state, not a fresh one — anything already queued in the parent before the fork can be delivered by both the parent and the child, appearing twice in your Coolhand account. Restart monitoring in the child (or avoid forking after interactions have been captured) if this matters for your workload.
- **asyncio**: async patching is fully compatible with any event loop. `asyncio.run()` inside a thread worker also works correctly.

### Delivery is asynchronous and best-effort

Captured interactions aren't sent to Coolhand synchronously. `flush()` hands them off to a bounded in-memory queue drained by a single background thread, so it returns immediately — including from inside an async event loop — without waiting for the HTTP request to complete. Two consequences:

- **Under sustained backpressure, interactions are dropped, not queued indefinitely.** If Coolhand is slow or unreachable for long enough that the delivery queue fills up (1000 interactions), newer interactions are dropped rather than blocking your application; `get_stats()["logging"]["dropped_count"]` tracks how many. This is distinct from `delivery_failure_count`, which tracks interactions that *did* reach the delivery queue but whose POST itself failed (bad API key, non-2xx status, network error) — a client with a revoked API key has `dropped_count: 0` (the queue isn't full) but a growing `delivery_failure_count`.
- **`flush()` is not a delivery barrier — `shutdown()` is.** `flush()` only guarantees the hand-off happened, not that delivery completed. If you need to know queued interactions were actually sent (e.g. right before a script exits), call `shutdown()` instead — it waits (up to a bounded timeout) for the background worker to finish.
