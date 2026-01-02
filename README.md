# Coolhand Python

Automatic monitoring for LLM API calls in Python applications.

## Installation

```bash
pip install coolhand
```

## Quick Start

```python
import coolhand  # Auto-initializes and starts monitoring

# All LLM API calls are now automatically captured
from openai import OpenAI
client = OpenAI()
response = client.chat.completions.create(
    model="gpt-4",
    messages=[{"role": "user", "content": "Hello!"}]
)
```

## Manual Configuration

```python
from coolhand import Coolhand

coolhand_client = Coolhand(
    api_key='your-api-key',
    debug=True,   # Print captured requests
    silent=False, # Enable logging
)
```

## Environment Variables

- `COOLHAND_API_KEY`: Your Coolhand API key
- `COOLHAND_SILENT`: Set to `false` for verbose logging

## Supported LLM APIs

Automatically captures requests to:
- OpenAI (api.openai.com)
- Anthropic (api.anthropic.com)

## Requirements

- Python 3.7+
- httpx (for HTTP client patching)

## License

Apache-2.0
