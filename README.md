# Coolhand Python

[![PyPI version](https://badge.fury.io/py/coolhand.svg)](https://badge.fury.io/py/coolhand)

Monitor and log LLM API calls from OpenAI, Anthropic, Google Gemini, GitHub Copilot, and more to the Coolhand analytics platform.

> **Python 3.8 and 3.9 support deprecated**: As of v0.4.0, coolhand requires Python 3.10 or later. Python 3.8 reached end-of-life in October 2024 and 3.9 in October 2025. If you are on an older Python version, pin to `coolhand<0.4.0`.

## Installation

```bash
pip install coolhand
```

## Getting Started

1. **Get API Key**: Visit [coolhandlabs.com](https://coolhandlabs.com/) to create a free account
2. **Install**: `pip install coolhand`
3. **Configure**: Set `COOLHAND_API_KEY` and `import coolhand` in your app
4. **Deploy**: Your AI calls are now automatically monitored!

## Quick Start

### Automatic Global Monitoring

**Set it and forget it! Monitor ALL AI API calls across your entire application with minimal configuration.**

```python
import coolhand  # Auto-initializes and starts monitoring

# That's it! ALL AI API calls are now automatically monitored:
# ✅ OpenAI SDK calls
# ✅ Anthropic API calls
# ✅ Google Gemini API calls
# ✅ GitHub Copilot SDK calls
# ✅ ANY library making AI API calls via httpx

# Your existing code works unchanged:
from openai import OpenAI
client = OpenAI()
response = client.chat.completions.create(
    model="gpt-4",
    messages=[{"role": "user", "content": "Hello!"}]
)
# The request and response have been automatically logged to Coolhand!
```

**Why Automatic Monitoring:**
- **Zero refactoring** - No code changes to existing services
- **Complete coverage** - Monitors all AI libraries using httpx automatically
- **Security built-in** - Automatic credential sanitization
- **Performance optimized** - Negligible overhead via async logging
- **Future-proof** - Automatically captures new AI calls added by your team

## Configuration

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `COOLHAND_API_KEY` | Yes | - | Your Coolhand API key for authentication |
| `COOLHAND_BASE_URL` | No | `https://coolhandlabs.com` | Override the API host (self-hosted deployments) |
| `COOLHAND_SILENT` | No | `true` | Set to `false` for verbose logging output |

### Manual Configuration

```python
from coolhand import Coolhand

coolhand_client = Coolhand(
    api_key='your-api-key',
    silent=False,  # Enable verbose logging
)
```

For excluding noisy endpoints (health checks, batch jobs) or pointing the SDK at a self-hosted backend, see [Advanced Configuration](./docs/configuration.md).

## Usage Examples

### With OpenAI

```python
import coolhand
from openai import OpenAI

client = OpenAI()
response = client.chat.completions.create(
    model="gpt-4",
    messages=[{"role": "user", "content": "Hello!"}]
)
print(response.choices[0].message.content)
# Request automatically logged to Coolhand!
```

### With Anthropic

```python
import coolhand
from anthropic import Anthropic

client = Anthropic()
response = client.messages.create(
    model="claude-3-opus-20240229",
    max_tokens=1024,
    messages=[{"role": "user", "content": "Hello!"}]
)
print(response.content[0].text)
# Request automatically logged to Coolhand!
```

### With Google Gemini

```python
import coolhand
import google.generativeai as genai

genai.configure(api_key="your-gemini-api-key")
model = genai.GenerativeModel("gemini-pro")
response = model.generate_content("Hello!")
print(response.text)
# Request automatically logged to Coolhand!
```

### With GitHub Copilot SDK

```python
import coolhand
# GitHub Copilot SDK calls are automatically intercepted
# via JsonRpcClient patching — no additional setup needed.
```

### With Streaming

```python
import coolhand
from openai import OpenAI

client = OpenAI()
stream = client.chat.completions.create(
    model="gpt-4",
    messages=[{"role": "user", "content": "Tell me a story"}],
    stream=True
)

for chunk in stream:
    if chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="")
# Complete streamed response automatically logged to Coolhand!
```

## What Gets Logged

The monitor captures:

- **Request Data**: Method, URL, headers, request body
- **Response Data**: Status code, headers, response body
- **Timing**: Request timestamp, response timestamp, duration
- **LLM-Specific**: Model used, token counts, streaming status

Headers containing API keys are automatically sanitized for security.

## Supported Libraries

Coolhand works with any library that uses httpx, requests, or the GitHub Copilot SDK:

- OpenAI Python SDK
- Anthropic Python SDK
- Google Gemini (`google-generativeai` / `google-genai`)
- Azure AI Inference (`azure-ai-inference`)
- GitHub Models
- GitHub Copilot SDK
- Vertex AI
- Cloudflare AI Gateway
- pydantic-ai
- Any other library using httpx or requests

See [Supported Libraries](./docs/supported-libraries.md) for the full interception mechanism breakdown and streaming details.

## How It Works

Importing `coolhand` patches `httpx`, `requests`, and the GitHub Copilot `JsonRpcClient` at the class level. Requests to LLM APIs are intercepted, credentials are redacted, and data is submitted to Coolhand. Non-LLM requests pass through unchanged.

## Integrations

| Integration | Status | Guide |
|---|---|---|
| Dramatiq + pydantic-ai | ✅ Supported | [docs/dramatiq.md](./docs/dramatiq.md) |
| Celery | 🔜 Coming soon | — |

## Feedback Service

Collect user feedback on LLM responses to improve your AI outputs.

> **Frontend Feedback Widget**: For browser-based feedback collection, see [coolhand-js](https://github.com/Coolhand-Labs/coolhand-js).

```python
from coolhand import Coolhand

ch = Coolhand(api_key='your-api-key')

# Submit positive feedback
ch.create_feedback({
    'llm_request_log_id': 12345,
    'sentiment': 'like',
    'explanation': 'Very helpful response!'
})

# Submit negative feedback with a correction
ch.create_feedback({
    'original_output': 'The capital of France is London.',
    'sentiment': 'dislike',
    'revised_output': 'The capital of France is Paris.'
})
```

For the full field reference, matching strategies, and sentiment values, see [Feedback API](./docs/feedback.md).

## Troubleshooting

### Enable Debug Output

```python
from coolhand import Coolhand

Coolhand(
    api_key='your-api-key',
    silent=False  # Enable verbose logging
)
```

Or via environment variable:

```bash
export COOLHAND_SILENT=false
```

## API Key

**Sign up for free** at [coolhandlabs.com](https://coolhandlabs.com/) to get your API key and start monitoring your LLM usage.

**What you get:**
- Complete LLM request and response logging
- Usage analytics and insights
- No credit card required to start

## Security

- API keys in request headers are automatically redacted
- Sensitive URL query parameters (`key`, `api_key`, `token`, etc.) are automatically redacted
- No sensitive data is exposed in logs
- All data is sent via HTTPS to Coolhand servers

## Documentation

- [Advanced Configuration](./docs/configuration.md) — exclude patterns, self-hosted deployments, custom intercept addresses
- [Feedback API](./docs/feedback.md) — full field reference, matching strategies, sentiment values
- [Supported Libraries](./docs/supported-libraries.md) — interception mechanisms, streaming, thread/process safety
- [Dramatiq + pydantic-ai](./docs/dramatiq.md) — task queue integration guide, known gaps, workarounds

## Related Packages

- **Frontend (Feedback Collection Widget)**: [coolhand-js](https://github.com/Coolhand-Labs/coolhand-js) - Frontend feedback widget for collecting user feedback on AI outputs
- **Ruby**: [coolhand gem](https://github.com/Coolhand-Labs/coolhand-ruby) - Coolhand monitoring for Ruby applications
- **Node.js**: [coolhand-node package](https://github.com/Coolhand-Labs/coolhand-node) - Coolhand monitoring for Node.js applications

## Community

- **Questions?** [Create an issue](https://github.com/Coolhand-Labs/coolhand-python/issues)
- **Contribute?** [Submit a pull request](https://github.com/Coolhand-Labs/coolhand-python/pulls)
- **Support?** Visit [coolhandlabs.com](https://coolhandlabs.com)

## About Coolhand Labs

[Coolhand Labs](https://coolhandlabs.com) builds observability and feedback tooling for AI-powered applications. Our platform helps teams monitor LLM usage, collect structured human feedback, and improve output quality over time — across every provider and framework.

## License

Apache-2.0
