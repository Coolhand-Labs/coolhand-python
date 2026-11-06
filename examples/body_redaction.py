#!/usr/bin/env python3
# Usage: cd <repo-root> && uv run python examples/body_redaction.py
"""
Offline end-to-end check that credentials in captured bodies never leave the process.

No keys and no outside network. Sends Anthropic- and OpenAI-shaped calls through a mock
transport, the real interceptor and the real Coolhand client, which delivers to a
throwaway local HTTP server standing in for the Coolhand API. The check is on what that
server receives: the whole POST text must not contain any secret value, the secret
fields must read ``[REDACTED]``, and ordinary fields (``max_tokens``, a tool-schema
property merely named ``authorization``) must be left alone. Exits 1 on any mismatch.
"""

import json

import _fake_api
import _live_check

NAME = "body_redaction"

import httpx  # noqa: E402

import coolhand  # noqa: E402
from coolhand import httpx_interceptor  # noqa: E402

ANTHROPIC_TOKEN = "mcp-oauth-SECRET-anthropic-0001"
OPENAI_TOKEN = "Bearer sk-SECRET-openai-mcp-0002"
MCP_HEADER = "hdr-SECRET-mcp-0003"
EPHEMERAL_KEY = "ek_SECRET_realtime_0004"

# (name, url, request body, response body, [(secret value, field that holds it)])
CASES = [
    (
        "anthropic mcp authorization_token",
        "https://api.anthropic.com/v1/messages",
        {
            "model": "claude-example",
            "max_tokens": 100,
            "mcp_servers": [
                {
                    "type": "url",
                    "url": "https://mcp.example.com/sse",
                    "name": "example",
                    "authorization_token": ANTHROPIC_TOKEN,
                }
            ],
        },
        {"id": "msg_01Example", "content": [{"type": "text", "text": "ok"}]},
        [(ANTHROPIC_TOKEN, "authorization_token")],
    ),
    (
        "openai responses mcp authorization and headers",
        "https://api.openai.com/v1/responses",
        {
            "model": "gpt-example",
            "max_output_tokens": 50,
            "tools": [
                {
                    "type": "mcp",
                    "server_url": "https://mcp.example.com",
                    "authorization": OPENAI_TOKEN,
                    "headers": {"X-Custom-Auth": MCP_HEADER},
                },
                {
                    "type": "function",
                    "name": "lookup",
                    "parameters": {
                        "type": "object",
                        "properties": {"authorization": {"type": "string"}},
                    },
                },
            ],
        },
        {"id": "resp_Example", "output": []},
        [(OPENAI_TOKEN, "authorization"), (MCP_HEADER, "X-Custom-Auth")],
    ),
    (
        "openai realtime client_secret in the response",
        "https://api.openai.com/v1/realtime/sessions",
        {"model": "gpt-realtime-example"},
        {"id": "sess_Example", "client_secret": {"value": EPHEMERAL_KEY}},
        [(EPHEMERAL_KEY, "client_secret")],
    ),
]


def run_case(
    name: str, url: str, request_body: dict, response_body: dict, secrets: list
) -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json=response_body)
    )
    _fake_api.FakeCoolhandApi.received.clear()
    with httpx.Client(transport=transport) as client:
        assert client.post(url, json=request_body).status_code == 200
    coolhand.shutdown()
    posts = _fake_api.FakeCoolhandApi.received
    if len(posts) != 1:
        _live_check.fail(name, f"expected 1 delivered log, got {len(posts)}")
    wire = json.dumps(posts[0])
    for secret, field in secrets:
        if secret in wire:
            _live_check.fail(name, f"secret value of {field!r} reached Coolhand")
        if field not in wire or "[REDACTED]" not in wire:
            _live_check.fail(name, f"{field!r} was dropped instead of redacted")
    for kept in ("max_tokens", "max_output_tokens", "lookup", '"type": "string"'):
        sent = json.dumps(request_body)
        if kept in sent and kept not in wire:
            _live_check.fail(name, f"ordinary field {kept!r} was altered or dropped")
    print(f"PASS {name}: secret redacted, ordinary fields kept")


def main() -> None:
    httpx_interceptor.set_exclude_api_patterns(None)
    try:
        with _fake_api.serve() as base_url:
            coolhand.Coolhand(
                {
                    "api_key": "example-key-0123456789",
                    "base_url": base_url,
                    "silent": True,
                }
            )
            for case in CASES:
                run_case(*case)
    finally:
        httpx_interceptor.unpatch()
        httpx_interceptor.set_handler(None)
    print(f"PASS {NAME}: {len(CASES)} credential-bearing bodies delivered redacted")


if __name__ == "__main__":
    main()
