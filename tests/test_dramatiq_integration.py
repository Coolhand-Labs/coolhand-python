"""
Integration tests: Coolhand captures LLM interactions inside Dramatiq workers.

Out-of-the-box behavior summary
--------------------------------
Thread workers (default): WORKS — Coolhand patches httpx.AsyncClient.send at
  the class level before workers start, so all threads share the patch.

Async in threads: WORKS — asyncio.run() inside a sync Dramatiq actor creates
  a per-thread event loop; httpx.AsyncClient works fine with it.

pydantic-ai (AnthropicProvider): WORKS — pydantic-ai uses httpx internally;
  the class-level patch intercepts its calls just like any other httpx usage.

Process workers (Redis/RabbitMQ with fork model): Fixed via
  CoolhandDramatiqMiddleware (coolhand.integrations.dramatiq), which calls
  coolhand.start_monitoring() in the after_worker_process_boot hook.

No task-to-LLM correlation: Coolhand has no mechanism to link a Dramatiq
  message ID to the LLM interactions it triggers. Session IDs are global,
  not per-message. This requires a custom solution (e.g. ContextVar +
  dramatiq middleware that propagates a per-message session ID).
"""

import asyncio
import json
import threading
from collections.abc import Generator
from typing import Any

import dramatiq
import httpx
import pytest
from dramatiq.brokers.stub import StubBroker
from pydantic_ai import Agent
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.providers.anthropic import AnthropicProvider

from coolhand import httpx_interceptor

# ---------------------------------------------------------------------------
# Fake HTTP transport — returns a canned Anthropic response, no network calls
# ---------------------------------------------------------------------------
FAKE_RESPONSE_BODY = json.dumps(
    {
        "id": "msg_test_001",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": "Paris is the capital of France."}],
        "model": "claude-3-5-haiku-20241022",
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 10, "output_tokens": 8},
    }
).encode()


class FakeAnthropicTransport(httpx.AsyncBaseTransport):
    """Returns a deterministic Anthropic response without hitting the network."""

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=FAKE_RESPONSE_BODY,
            headers={"content-type": "application/json"},
            request=request,
        )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def captured() -> list[tuple[Any, Any, Any]]:
    """Per-test list that receives every (request, response, error) triple."""
    return []


@pytest.fixture(autouse=True)
def interceptor(captured: list[Any]) -> Generator[None, None, None]:
    """Patch httpx with a capturing handler; clean up fully after each test.

    We always unpatch then repatch rather than trying to preserve the prior
    _patched flag.  Restoring _patched=True without re-running patch() leaves
    httpx.AsyncClient.send pointing at the real (unpatched) method while the
    flag says it is patched — causing every test after the first to miss all
    captures.
    """
    if httpx_interceptor.is_patched():
        httpx_interceptor.unpatch()

    httpx_interceptor.set_handler(
        lambda req, res, err: captured.append((req, res, err))
    )
    httpx_interceptor.patch()

    yield

    httpx_interceptor.unpatch()
    httpx_interceptor.set_handler(None)


@pytest.fixture
def stub_broker() -> Generator[StubBroker, None, None]:
    """Fresh StubBroker per test; flushes and closes after."""
    broker = StubBroker()
    yield broker
    broker.flush_all()
    broker.close()


def _run_actors(broker: StubBroker, *queue_names: str, worker_threads: int = 1) -> None:
    """Start a Worker, drain the given queues, then stop cleanly."""
    worker = dramatiq.Worker(broker, worker_threads=worker_threads)
    worker.start()
    for q in queue_names:
        broker.join(q, fail_fast=True)
    worker.stop()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _fake_anthropic_model() -> AnthropicModel:
    """AnthropicModel wired to a fake transport — no network calls needed."""
    provider = AnthropicProvider(
        api_key="test-key",
        http_client=httpx.AsyncClient(transport=FakeAnthropicTransport()),
    )
    return AnthropicModel("claude-3-5-haiku-20241022", provider=provider)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
class TestAsyncHttpxFromDramatiqThread:
    """Coolhand captures httpx.AsyncClient calls made from worker threads."""

    def test_async_call_is_captured(
        self, captured: list[Any], stub_broker: StubBroker
    ) -> None:
        @dramatiq.actor(broker=stub_broker)
        def call_llm_async() -> None:
            async def _inner() -> None:
                async with httpx.AsyncClient(
                    transport=FakeAnthropicTransport()
                ) as client:
                    await client.post(
                        "https://api.anthropic.com/v1/messages",
                        json={"model": "claude-3-5-haiku-20241022", "messages": []},
                        headers={"x-api-key": "test-key"},
                    )

            asyncio.run(_inner())

        call_llm_async.send()
        _run_actors(stub_broker, call_llm_async.queue_name)

        assert len(captured) == 1, "Expected 1 captured interaction"
        req, res, err = captured[0]
        assert "api.anthropic.com" in req["url"]
        assert err is None
        assert res is not None
        assert res["status_code"] == 200

    def test_multiple_actors_all_captured(
        self, captured: list[Any], stub_broker: StubBroker
    ) -> None:
        @dramatiq.actor(broker=stub_broker)
        def call_llm_multi() -> None:
            async def _inner() -> None:
                async with httpx.AsyncClient(
                    transport=FakeAnthropicTransport()
                ) as client:
                    await client.post(
                        "https://api.anthropic.com/v1/messages",
                        json={},
                        headers={"x-api-key": "test-key"},
                    )

            asyncio.run(_inner())

        call_llm_multi.send()
        call_llm_multi.send()
        call_llm_multi.send()
        _run_actors(stub_broker, call_llm_multi.queue_name, worker_threads=2)

        assert len(captured) == 3, (
            f"Expected 3 captured interactions, got {len(captured)}"
        )

    def test_worker_thread_identity(
        self, captured: list[Any], stub_broker: StubBroker
    ) -> None:
        main_thread_id = threading.current_thread().ident
        actor_thread_ids: list[int] = []

        @dramatiq.actor(broker=stub_broker)
        def record_thread() -> None:
            actor_thread_ids.append(threading.current_thread().ident or 0)

            async def _inner() -> None:
                async with httpx.AsyncClient(
                    transport=FakeAnthropicTransport()
                ) as client:
                    await client.post(
                        "https://api.anthropic.com/v1/messages",
                        json={},
                        headers={"x-api-key": "test-key"},
                    )

            asyncio.run(_inner())

        record_thread.send()
        _run_actors(stub_broker, record_thread.queue_name)

        # Actor ran in a different thread from the test
        assert actor_thread_ids[0] != main_thread_id
        # Coolhand still captured it
        assert len(captured) == 1


class TestSyncHttpxFromDramatiqThread:
    """Coolhand captures httpx.Client (sync) calls from worker threads."""

    def test_sync_call_is_captured(
        self, captured: list[Any], stub_broker: StubBroker
    ) -> None:
        @dramatiq.actor(broker=stub_broker)
        def call_llm_sync() -> None:
            class FakeSyncTransport(httpx.BaseTransport):
                def handle_request(self, request: httpx.Request) -> httpx.Response:
                    return httpx.Response(
                        200,
                        content=FAKE_RESPONSE_BODY,
                        headers={"content-type": "application/json"},
                        request=request,
                    )

            with httpx.Client(transport=FakeSyncTransport()) as client:
                client.post(
                    "https://api.anthropic.com/v1/messages",
                    json={"model": "claude-3-5-haiku-20241022", "messages": []},
                    headers={"x-api-key": "test-key"},
                )

        call_llm_sync.send()
        _run_actors(stub_broker, call_llm_sync.queue_name)

        assert len(captured) == 1
        req, res, err = captured[0]
        assert "api.anthropic.com" in req["url"]
        assert err is None


class TestPydanticAiFromDramatiqThread:
    """Coolhand captures pydantic-ai calls made from Dramatiq worker threads.

    pydantic-ai uses httpx internally (via the Anthropic SDK). Coolhand's
    class-level patch intercepts those calls without any pydantic-ai-specific
    configuration.
    """

    def test_pydantic_ai_call_is_captured(
        self, captured: list[Any], stub_broker: StubBroker
    ) -> None:
        @dramatiq.actor(broker=stub_broker)
        def ask_claude(question: str) -> None:
            agent = Agent(_fake_anthropic_model())
            # asyncio.run() is the correct pattern inside a sync Dramatiq actor
            asyncio.run(agent.run(question))

        ask_claude.send("What is the capital of France?")
        _run_actors(stub_broker, ask_claude.queue_name)

        assert len(captured) >= 1, (
            "Coolhand should have captured at least one interaction "
            "from the pydantic-ai / Anthropic SDK call"
        )
        req, res, err = captured[0]
        assert "api.anthropic.com" in req["url"]
        assert err is None

    def test_pydantic_ai_multiple_tasks(
        self, captured: list[Any], stub_broker: StubBroker
    ) -> None:
        @dramatiq.actor(broker=stub_broker)
        def ask_claude_multi(question: str) -> None:
            agent = Agent(_fake_anthropic_model())
            asyncio.run(agent.run(question))

        ask_claude_multi.send("Question 1?")
        ask_claude_multi.send("Question 2?")
        _run_actors(stub_broker, ask_claude_multi.queue_name, worker_threads=2)

        assert len(captured) >= 2, (
            f"Expected ≥2 captured interactions, got {len(captured)}"
        )


class TestKnownLimitations:
    """Document behaviors that do NOT work out of the box."""

    def test_no_per_task_session_correlation(
        self, captured: list[Any], stub_broker: StubBroker
    ) -> None:
        """Coolhand captures interactions but cannot link them to specific Dramatiq
        message IDs. All interactions share the same global session ID.

        Fix: use a dramatiq middleware that sets a per-message ContextVar and
        a custom Coolhand session_id derived from the message ID.
        """
        task_ids_seen: list[str] = []

        @dramatiq.actor(broker=stub_broker)
        def task_with_llm(task_id: str) -> None:
            task_ids_seen.append(task_id)

            async def _inner() -> None:
                async with httpx.AsyncClient(
                    transport=FakeAnthropicTransport()
                ) as client:
                    await client.post(
                        "https://api.anthropic.com/v1/messages",
                        json={},
                        headers={"x-api-key": "test-key"},
                    )

            asyncio.run(_inner())

        task_with_llm.send("task-A")
        task_with_llm.send("task-B")
        _run_actors(stub_broker, task_with_llm.queue_name)

        assert len(captured) == 2
        # There is no field in the captured request/response that identifies
        # which Dramatiq task triggered the call.
        for req, _res, _err in captured:
            assert "task-A" not in str(req)
            assert "task-B" not in str(req)

    def test_process_worker_note(self) -> None:
        """Process-based workers (fork model, e.g. gunicorn-style) do NOT
        inherit the httpx patch from the parent process.

        This test simply documents the limitation — testing actual process
        forking is out of scope here.

        Fix: use CoolhandDramatiqMiddleware (coolhand.integrations.dramatiq),
        which calls coolhand.start_monitoring() in after_worker_process_boot.
        """
        # The patch lives in the parent process's memory. After fork(), the
        # child has a copy of that memory, but httpx.AsyncClient.send in the
        # child process still points to the patched version — which is correct!
        # The real problem is workers that use spawn() (fresh interpreter) or
        # that import everything before coolhand patches httpx.
        pass


class TestCoolhandDramatiqMiddleware:
    """CoolhandDramatiqMiddleware activates monitoring in worker processes."""

    def test_after_process_boot_with_no_instance(self) -> None:
        """When no Coolhand instance exists (fresh spawn worker), booting
        creates one and starts monitoring."""
        import unittest.mock

        import coolhand
        from coolhand.integrations.dramatiq import CoolhandDramatiqMiddleware

        middleware = CoolhandDramatiqMiddleware()
        broker = unittest.mock.MagicMock()

        with unittest.mock.patch("coolhand.get_instance", return_value=None):
            with unittest.mock.patch("coolhand.Coolhand") as mock_coolhand_cls:
                middleware.after_process_boot(broker)
                mock_coolhand_cls.assert_called_once()

        # suppress unused import warning
        _ = coolhand

    def test_after_process_boot_with_existing_instance(self) -> None:
        """When an instance already exists (fork worker), booting re-applies
        the monitoring patch via start_monitoring()."""
        import unittest.mock

        import coolhand
        from coolhand.integrations.dramatiq import CoolhandDramatiqMiddleware

        middleware = CoolhandDramatiqMiddleware()
        broker = unittest.mock.MagicMock()
        fake_instance = unittest.mock.MagicMock()

        with unittest.mock.patch("coolhand.get_instance", return_value=fake_instance):
            with unittest.mock.patch("coolhand.start_monitoring") as mock_start:
                middleware.after_process_boot(broker)
                mock_start.assert_called_once()

        _ = coolhand

    def test_import_error_without_dramatiq(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Importing the middleware without dramatiq installed raises ImportError
        with a helpful message."""
        import importlib
        import sys
        import unittest.mock

        # Remove cached module so re-import triggers the guard
        monkeypatch.delitem(
            sys.modules, "coolhand.integrations.dramatiq", raising=False
        )

        with unittest.mock.patch.dict(sys.modules, {"dramatiq": None}):
            with pytest.raises(ImportError, match="pip install dramatiq"):
                importlib.import_module("coolhand.integrations.dramatiq")
