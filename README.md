# Coolhand Python

Monitor and log LLM API calls from OpenAI and Anthropic to the Coolhand analytics platform.

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
| `COOLHAND_SILENT` | No | `true` | Set to `false` for verbose logging output |

### Manual Configuration

```python
from coolhand import Coolhand

coolhand_client = Coolhand(
    api_key='your-api-key',
    silent=False,  # Enable verbose logging
)
```

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

Coolhand monitors HTTP requests made via **httpx**, which is used by:

- OpenAI Python SDK
- Anthropic Python SDK
- Any other library using httpx for HTTP requests

## How It Works

1. When you import `coolhand`, it automatically patches httpx
2. Requests to OpenAI and Anthropic APIs are intercepted
3. Request and response data are captured (credentials sanitized)
4. Data is sent to Coolhand asynchronously
5. Your application continues without interruption

For non-LLM endpoints, requests pass through unchanged with zero overhead.

## Feedback Service

Collect user feedback on LLM responses to improve your AI outputs. The FeedbackService lets you capture thumbs up/down ratings, explanations, and corrections.

> **Frontend Feedback Widget**: For browser-based feedback collection, see [coolhand-js](https://github.com/Coolhand-Labs/coolhand-js) - an accessible, lightweight JavaScript widget that leverages best UX practices to capture actionable user feedback on any AI output.

### Basic Usage

```python
from coolhand import Coolhand

# Initialize with your API key
ch = Coolhand(api_key='your-api-key')

# Submit positive feedback
ch.create_feedback({
    'llm_request_log_id': 12345,  # From Coolhand logs
    'like': True,
    'explanation': 'Very helpful response!'
})

# Using original output for fuzzy matching
ch.create_feedback({
    'original_output': 'The capital of France is London.',
    'like': False,
    'revised_output': 'The capital of France is Paris.'
})
```

### Feedback Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `like` | bool | Yes | Thumbs up (`True`) or down (`False`) |
| `llm_request_log_id` | int | No* | Coolhand log ID (exact match) |
| `llm_provider_unique_id` | str | No* | Provider's x-request-id (exact match) |
| `original_output` | str | No* | Original response text (fuzzy match) |
| `client_unique_id` | str | No* | Your internal identifier |
| `explanation` | str | No | Why the response was good/bad |
| `revised_output` | str | No | User's corrected version |
| `creator_unique_id` | str | No | ID of user providing feedback |

*At least one matching field is recommended to link feedback to the original request.

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
- No sensitive data is exposed in logs
- All data is sent via HTTPS to Coolhand servers

## Related Packages

- **Frontend (Feedback Collection Widget)**: [coolhand-js](https://github.com/Coolhand-Labs/coolhand-js) - Frontend feedback widget for collecting user sentiment on AI outputs
- **Ruby**: [coolhand gem](https://github.com/Coolhand-Labs/coolhand-ruby) - Coolhand monitoring for Ruby applications
- **Node.js**: [coolhand-node package](https://github.com/Coolhand-Labs/coolhand-node) - Coolhand monitoring for Node.js applications

## Community

- **Questions?** [Create an issue](https://github.com/Coolhand-Labs/coolhand-python/issues)
- **Contribute?** [Submit a pull request](https://github.com/Coolhand-Labs/coolhand-python/pulls)
- **Support?** Visit [coolhandlabs.com](https://coolhandlabs.com)

## License

Apache-2.0
