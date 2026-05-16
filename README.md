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

### Excluding API Patterns

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

Any request whose URL contains one of the listed substrings is passed through without logging. The default list (`DEFAULT_EXCLUDE_API_PATTERNS`) excludes `/batchPredictionJobs/`; setting `exclude_api_patterns` replaces the default entirely.

```python
from coolhand import DEFAULT_EXCLUDE_API_PATTERNS

# Extend the defaults rather than replace them
coolhand_client = Coolhand(
    api_key='your-api-key',
    exclude_api_patterns=DEFAULT_EXCLUDE_API_PATTERNS + ['/health'],
)
```

### Self-Hosted Deployments

If you run your own Coolhand-compatible backend (e.g. for compliance or data-residency requirements), point the SDK at your host with `base_url`:

```python
from coolhand import Coolhand

coolhand_client = Coolhand(
    api_key='your-api-key',
    base_url='https://feedback.example.com',  # must use https://
)
```

Or via environment variable (useful for 12-factor deployments):

```bash
export COOLHAND_API_KEY=your-api-key
export COOLHAND_BASE_URL=https://feedback.example.com
```

```python
import coolhand  # picks up COOLHAND_BASE_URL automatically
```

The SDK rejects non-`https://` URLs by default. `http://localhost` and `http://127.0.0.1` are allowed for local development only.

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

Coolhand intercepts AI API calls through two mechanisms:

**httpx patching** (covers any library built on httpx):

- OpenAI Python SDK
- Anthropic Python SDK
- Google Gemini (`google-generativeai` / `google-genai`)
- GitHub Models via Azure (`models.inference.ai.azure.com`)
- Any other library using httpx for HTTP requests

**requests patching** (covers any library built on `requests`):

- `azure-ai-inference`, `azure-openai`, and other `azure-core`-based SDKs
- Any other library using `requests` for HTTP requests (`requests` is optional — patch is skipped if not installed)

**JSON-RPC patching** (direct protocol interception):

- GitHub Copilot SDK (`JsonRpcClient`)

## How It Works

1. When you import `coolhand`, it automatically patches httpx, `requests.Session.send` (if installed), and the GitHub Copilot `JsonRpcClient`
2. Requests to AI APIs are intercepted (OpenAI, Anthropic, Gemini, GitHub Models, GitHub Copilot, and more)
3. Request and response data are captured (credentials and sensitive URL parameters sanitized)
4. Data is sent to Coolhand asynchronously
5. Your application continues without interruption

For non-LLM endpoints, requests pass through unchanged with zero overhead.

## Feedback Service

Collect user feedback on LLM responses to improve your AI outputs. The FeedbackService lets you capture sentiment ratings, explanations, and corrections.

> **Frontend Feedback Widget**: For browser-based feedback collection, see [coolhand-js](https://github.com/Coolhand-Labs/coolhand-js) - an accessible, lightweight JavaScript widget that leverages best UX practices to capture actionable user feedback on any AI output.

### Basic Usage

```python
from coolhand import Coolhand

# Initialize with your API key
ch = Coolhand(api_key='your-api-key')

# Submit positive feedback
ch.create_feedback({
    'llm_request_log_id': 12345,  # From Coolhand logs
    'sentiment': 'like',
    'explanation': 'Very helpful response!'
})

# Using original output for fuzzy matching
ch.create_feedback({
    'original_output': 'The capital of France is London.',
    'sentiment': 'dislike',
    'revised_output': 'The capital of France is Paris.'
})
```

### Feedback Fields

All fields are optional. At least one matching field (marked \*) is recommended to link feedback to the original LLM request.

| Field | Type | Description |
|-------|------|-------------|
| `sentiment` | str | **Preferred.** `"like"`, `"dislike"`, or `"neutral"` |
| `like` | bool | **Deprecated** — use `sentiment` instead. Auto-converted and stripped from the wire payload. Emits `DeprecationWarning`. |
| `llm_request_log_id` | int | \* Coolhand log ID (exact match) |
| `llm_provider_unique_id` | str | \* Provider's x-request-id (exact match) |
| `original_output` | str | \* Original response text (fuzzy match) |
| `client_unique_id` | str | \* Your internal identifier |
| `explanation` | str | Why the response was good/bad |
| `revised_output` | str | User's corrected version |
| `creator_unique_id` | str | ID of user providing feedback |
| `workload_hashid` | str | Associate feedback with a specific workload |
| `collector` | str | Override the SDK-generated collector string |

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

## Related Packages

- **Frontend (Feedback Collection Widget)**: [coolhand-js](https://github.com/Coolhand-Labs/coolhand-js) - Frontend feedback widget for collecting user feedback on AI outputs
- **Ruby**: [coolhand gem](https://github.com/Coolhand-Labs/coolhand-ruby) - Coolhand monitoring for Ruby applications
- **Node.js**: [coolhand-node package](https://github.com/Coolhand-Labs/coolhand-node) - Coolhand monitoring for Node.js applications

## Community

- **Questions?** [Create an issue](https://github.com/Coolhand-Labs/coolhand-python/issues)
- **Contribute?** [Submit a pull request](https://github.com/Coolhand-Labs/coolhand-python/pulls)
- **Support?** Visit [coolhandlabs.com](https://coolhandlabs.com)

## License

Apache-2.0
