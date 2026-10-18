# Verifying Interception in Your Tests

Coolhand patches `httpx.Client.send`, `httpx.AsyncClient.send` and `requests.Session.send` at the class level. A client that captured a reference to one of those functions **before** `import coolhand` (for example `send = httpx.Client().send` at import time of some other module) keeps calling the original, so the app is silently not monitored. No error or warning is raised.

`coolhand.testing` lets your own tests or CI assert that a request really was intercepted.

## Quick Start

```python
from coolhand.testing import (
    assert_interception_occurred,
    capture_interception_snapshot,
)

before = capture_interception_snapshot()
client.chat.completions.create(model="gpt-4o", messages=[...])  # real or mocked transport
assert_interception_occurred(before, "openai client bypassed Coolhand")
```

`assert_interception_occurred` raises `AssertionError` if no request was intercepted since the snapshot. The optional message is prepended to the failure text, which also lists likely causes and says whether monitoring is currently active. It has no pytest dependency.

The request must be one Coolhand would capture: an allow-listed host (see [Configuration](./configuration.md)), not localhost, and not matching an exclude pattern. A mocked transport on an allow-listed host such as `https://api.openai.com` works, so the test needs no network or API key.

## What it catches

| Cause | Assertion fails |
|---|---|
| `send` reference captured before `import coolhand` | Yes |
| Library uses a transport Coolhand does not patch (aiohttp, `urllib3`, `http.client`; see [Supported Libraries](./supported-libraries.md)) | Yes |
| Monitoring not active (`stop_monitoring()` called, or never started) | Yes |
| URL outside `intercept_addresses`, or matching `exclude_api_patterns` | Yes |

A client instance created before Coolhand was imported is **not** a bypass: it looks `send` up on the class at call time, so it is intercepted.

## Limits

- **Process-wide counter.** Concurrent unrelated intercepted traffic (other threads or tasks) can advance the counter and make the assertion pass falsely. Run it on an otherwise quiet process.
- **Counts interceptions, not delivery.** A request counts when it is captured, whether or not it is later delivered to Coolhand. Use `Coolhand.get_stats()` for delivery counters.
- **Needs a handler.** Requests made while patched but before any `Coolhand` instance has set a handler pass through uncounted, so the assertion fails; create the instance (or `import coolhand`, which auto-initializes) first.
- **httpx and `requests` only.** GitHub Copilot JSON-RPC calls are not counted.
- **Not per instance.** Patching is process-global in Python, so all `Coolhand` instances share one counter. A request intercepted for any instance advances the same counter. (coolhand-node's `test-utils` does not observe instance-based monitoring; this does.)
