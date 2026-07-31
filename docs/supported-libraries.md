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
#  'models.inference.ai.azure.com', 'openrouter.ai', ':generateContent',
#  ':streamGenerateContent', ':predict', ':streamRawPredict']
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
- **Process workers** (forked subprocesses): patches survive `fork()` but not `spawn()`. See the [Dramatiq guide](./dramatiq.md#gap-1--process-based-workers) for the workaround.
- **asyncio**: async patching is fully compatible with any event loop. `asyncio.run()` inside a thread worker also works correctly.
