#!/usr/bin/env python3
# Usage: cd <repo-root> && uv run python examples/copilot_send_and_wait.py
"""
Offline end-to-end check that GitHub Copilot ``send_and_wait`` runs are captured.

No keys, no outside network and no ``copilot`` CLI. A fake ``copilot._jsonrpc`` module
stands in for github-copilot-sdk. Its ``JsonRpcClient`` behaves like the real one for the
two calls that matter: ``session.sendAndWait`` stays open for the whole agent turn and the
runtime emits one ``assistant.message`` event per model call (a tool loop) while it is
open, whereas ``session.send`` returns at once and the events follow. The real
interceptor and the real Coolhand client then deliver to a throwaway local HTTP server
standing in for the Coolhand API, and the check is on what that server receives:

- a ``send_and_wait`` turn of three model calls reaches Coolhand as three logs, the last
  being the one that wrote the answer, each with the request prompt;
- each log carries the Copilot message id as ``id`` (usable as ``llm_provider_unique_id``);
- a plain ``session.send`` still logs only its first ``assistant.message``.

Exits 1 on any mismatch.
"""

import asyncio
import sys
import types

import _fake_api
import _live_check

NAME = "copilot_send_and_wait"
PROMPT = "write a test plan"

TURN = ["I'll read the skill first.", "Now checking the diff.", "THE FINAL TEST PLAN"]


class FakeJsonRpcClient:
    """The slice of github-copilot-sdk's JsonRpcClient the interceptor patches."""

    async def request(self, method, params=None, timeout=None, **kwargs):
        if method == "session.create":
            return {"sessionId": params["sessionId"], "model": "gpt-example"}
        session_id = (params or {}).get("sessionId")
        if method == "session.sendAndWait":
            # The RPC is open for the whole turn: events arrive before it returns.
            for i, content in enumerate(TURN):
                await asyncio.sleep(0)
                self._emit(session_id, "assistant.usage", {"inputTokens": 10 + i})
                self._emit(
                    session_id,
                    "assistant.message",
                    {"messageId": f"msg-{i}", "content": content},
                )
            return {"messageId": "ack"}
        # session.send: acknowledged immediately, events follow afterwards.
        return {"messageId": "ack"}

    def _emit(self, session_id, event_type, data):
        self._handle_message(
            {
                "method": "session.event",
                "params": {
                    "sessionId": session_id,
                    "event": {"type": event_type, "data": data},
                },
            }
        )

    def _handle_message(self, message):
        pass


def install_fake_sdk() -> None:
    copilot = types.ModuleType("copilot")
    jsonrpc = types.ModuleType("copilot._jsonrpc")
    jsonrpc.JsonRpcClient = FakeJsonRpcClient  # type: ignore[attr-defined]
    sys.modules["copilot"] = copilot
    sys.modules["copilot._jsonrpc"] = jsonrpc


def raw_requests(sent: list[dict]) -> list[dict]:
    """The captured request/response records Coolhand received, in delivery order."""
    return [p["llm_request_log"]["raw_request"] for p in sent if "llm_request_log" in p]


async def run_turns(client: FakeJsonRpcClient) -> None:
    await client.request("session.create", {"sessionId": "s-wait"})
    await client.request(
        "session.sendAndWait", {"sessionId": "s-wait", "prompt": PROMPT}
    )
    await client.request("session.create", {"sessionId": "s-send"})
    await client.request("session.send", {"sessionId": "s-send", "prompt": "hi"})
    # A plain send's turn can emit several messages too; only the first is logged.
    client._emit("s-send", "assistant.message", {"messageId": "first", "content": "a"})
    client._emit("s-send", "assistant.message", {"messageId": "second", "content": "b"})


def main() -> None:
    import coolhand
    from coolhand import copilot_interceptor, httpx_interceptor

    install_fake_sdk()
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
            if not copilot_interceptor.is_patched():
                _live_check.fail(NAME, "the Copilot interceptor did not patch")
            asyncio.run(run_turns(FakeJsonRpcClient()))
            coolhand.shutdown()
            sent = list(_fake_api.FakeCoolhandApi.received)
    finally:
        httpx_interceptor.unpatch()
        httpx_interceptor.set_handler(None)
        copilot_interceptor.unpatch()
        copilot_interceptor.set_handler(None)  # type: ignore[arg-type]

    records = raw_requests(sent)

    wait_records = [
        r for r in records if r["request_body"].get("sessionId") == "s-wait"
    ]
    wait_contents = [r["response_body"]["content"] for r in wait_records]
    if wait_contents != TURN:
        _live_check.fail(
            NAME,
            f"send_and_wait should log every model call {TURN!r}, got {wait_contents!r}",
        )
    print(f"PASS {NAME}: send_and_wait logged all {len(TURN)} model calls in order")

    if any(r["request_body"].get("prompt") != PROMPT for r in wait_records):
        _live_check.fail(NAME, "a send_and_wait log lost the request prompt")
    if [r["response_body"].get("inputTokens") for r in wait_records] != [10, 11, 12]:
        _live_check.fail(NAME, "send_and_wait logs lost their per-call token usage")
    print(f"PASS {NAME}: every log carries the prompt and its own token usage")

    final = wait_records[-1]["response_body"]
    if final.get("id") != "msg-2" or final.get("messageId") != "msg-2":
        _live_check.fail(NAME, f"final call has no message id as 'id': {final!r}")
    print(f"PASS {NAME}: the call that wrote the plan carries its message id as 'id'")

    send_contents = [
        r["response_body"]["content"]
        for r in records
        if r["request_body"].get("sessionId") == "s-send"
    ]
    if send_contents != ["a"]:
        _live_check.fail(
            NAME,
            f"plain session.send should log only its first message: {send_contents!r}",
        )
    print(f"PASS {NAME}: plain session.send still logs only its first message")


if __name__ == "__main__":
    main()
