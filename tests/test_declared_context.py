"""Tests for with_context: nesting, validation, and delivery in the payload."""

import asyncio
import contextvars
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch

import httpx
import pytest
import requests

from coolhand import CoolhandClient, httpx_interceptor, with_context
from coolhand.declared_context import snapshot

LLM_URL = "https://api.openai.com/v1/chat/completions"


class TestNesting:
    def test_no_context_snapshot_is_none(self):
        assert snapshot() is None

    def test_declares_inside_and_restores_after(self):
        with with_context(reporter="app", workload={"key": "W"}):
            assert snapshot() == {"reporter": "app", "workload": {"key": "W"}}
        assert snapshot() is None

    def test_restored_after_exception(self):
        with pytest.raises(RuntimeError):
            with with_context(workload={"key": "W"}):
                raise RuntimeError("boom")
        assert snapshot() is None

    def test_innermost_wins_per_field(self):
        with with_context(reporter="outer", workload={"key": "W"}):
            with with_context(reporter="inner"):
                assert snapshot() == {"reporter": "inner", "workload": {"key": "W"}}

    def test_workload_replaced_wholesale_never_merged(self):
        with with_context(workload={"id": "x", "name": "Old"}):
            with with_context(workload={"key": "y"}):
                assert snapshot() == {"workload": {"key": "y"}}

    def test_template_replaced_wholesale(self):
        with with_context(workload={"key": "W"}, template={"id": "t1", "name": "N"}):
            with with_context(template={"key": "t2"}):
                assert snapshot()["template"] == {"key": "t2"}

    def test_workload_without_template_clears_outer_template(self):
        with with_context(workload={"key": "A"}, template={"key": "A/t"}):
            with with_context(workload={"key": "B"}):
                assert snapshot() == {"workload": {"key": "B"}}
            assert snapshot()["template"] == {"key": "A/t"}

    def test_template_alone_inherits_outer_workload(self):
        with with_context(workload={"key": "W"}):
            with with_context(template={"key": "W/t"}):
                assert snapshot() == {
                    "workload": {"key": "W"},
                    "template": {"key": "W/t"},
                }

    def test_only_permitted_fields_forwarded(self):
        with with_context(
            workload={"key": "W", "name": "n", "description": "d", "extra": 1},
            template={"id": "t", "name": "tn", "description": "ignored"},
        ):
            declared = snapshot()
        assert declared["workload"] == {"key": "W", "name": "n", "description": "d"}
        assert declared["template"] == {"id": "t", "name": "tn"}

    def test_blank_id_counts_as_not_given(self):
        with with_context(workload={"id": "  ", "key": "W"}):
            assert snapshot()["workload"] == {"key": "W"}

    def test_snapshot_is_a_copy(self):
        with with_context(workload={"key": "W"}):
            snapshot()["workload"]["key"] = "mutated"
            assert snapshot()["workload"] == {"key": "W"}

    def test_empty_context_declares_nothing(self):
        with with_context():
            assert snapshot() is None

    def test_decorator_sync(self):
        @with_context(workload={"key": "W"})
        def run():
            return snapshot()

        assert run() == {"workload": {"key": "W"}}
        assert snapshot() is None

    @pytest.mark.asyncio
    async def test_decorator_async(self):
        @with_context(workload={"key": "W"})
        async def run():
            await asyncio.sleep(0)
            return snapshot()

        assert await run() == {"workload": {"key": "W"}}
        assert snapshot() is None

    @pytest.mark.asyncio
    async def test_propagates_into_asyncio_tasks(self):
        async def child():
            return snapshot()

        with with_context(workload={"key": "W"}):
            assert await asyncio.create_task(child()) == {"workload": {"key": "W"}}

    def test_does_not_propagate_into_plain_thread(self):

        seen = []
        with with_context(workload={"key": "W"}):
            thread = threading.Thread(target=lambda: seen.append(snapshot()))
            thread.start()
            thread.join()
        assert seen == [None]


class TestScopeReuseAndConcurrency:
    def test_scope_object_can_be_reentered_sequentially(self):
        scope = with_context(workload={"key": "W"})
        for _ in range(2):
            with scope:
                assert snapshot() == {"workload": {"key": "W"}}
            assert snapshot() is None

    def test_scope_object_can_be_nested_in_itself(self):
        scope = with_context(workload={"key": "W"})
        with scope:
            with scope:
                assert snapshot() == {"workload": {"key": "W"}}
            assert snapshot() == {"workload": {"key": "W"}}
        assert snapshot() is None

    @pytest.mark.asyncio
    async def test_one_scope_shared_by_interleaved_tasks(self):
        scope = with_context(workload={"key": "W"})
        entered, release = asyncio.Event(), asyncio.Event()

        async def holder():
            with scope:
                entered.set()
                await release.wait()
                return snapshot()

        async def quick():
            await entered.wait()
            with scope:
                seen = snapshot()
            release.set()
            return seen

        held, quick_seen = await asyncio.gather(holder(), quick())
        assert held == quick_seen == {"workload": {"key": "W"}}
        assert snapshot() is None

    @pytest.mark.asyncio
    async def test_concurrent_calls_of_one_decorated_function(self):
        @with_context(reporter="app")
        async def run(name):
            await asyncio.sleep(0)
            with with_context(workload={"key": name}):
                await asyncio.sleep(0)
                return snapshot()["workload"]["key"]

        assert await asyncio.gather(*(run(f"w{i}") for i in range(5))) == [
            f"w{i}" for i in range(5)
        ]
        assert snapshot() is None

    def test_decorated_generator_holds_context_across_iteration(self):
        @with_context(workload={"key": "W"})
        def numbers():
            for _ in range(2):
                yield snapshot()

        assert list(numbers()) == [{"workload": {"key": "W"}}] * 2
        assert snapshot() is None

    def test_decorated_generator_is_not_declared_while_suspended(self):
        @with_context(workload={"key": "W"})
        def numbers():
            yield snapshot()
            yield snapshot()

        gen = numbers()
        assert next(gen) == {"workload": {"key": "W"}}
        assert snapshot() is None  # the consumer's own calls are not attributed
        assert next(gen) == {"workload": {"key": "W"}}
        gen.close()
        assert snapshot() is None

    def test_decorated_generator_passes_through_send_and_return(self):
        @with_context(workload={"key": "W"})
        def echo():
            received = yield 1
            return received

        gen = echo()
        assert next(gen) == 1
        with pytest.raises(StopIteration) as stop:
            gen.send("hello")
        assert stop.value.value == "hello"

    @pytest.mark.asyncio
    async def test_decorated_async_generator_is_not_declared_while_suspended(self):
        @with_context(workload={"key": "W"})
        async def numbers():
            yield snapshot()
            yield snapshot()

        gen = numbers()
        assert await gen.__anext__() == {"workload": {"key": "W"}}
        assert snapshot() is None
        await gen.aclose()
        assert snapshot() is None

    def test_exit_without_matching_enter_is_harmless(self):

        scope = with_context(workload={"key": "W"})
        contextvars.copy_context().run(scope.__exit__, None, None, None)
        assert snapshot() is None

    def test_many_blocks_do_not_grow_the_context(self):

        before = len(contextvars.copy_context())
        for _ in range(200):
            with with_context(workload={"key": "W"}):
                pass

            @with_context(workload={"key": "W"})
            def decorated():
                pass

            decorated()
        assert len(contextvars.copy_context()) == before

    @pytest.mark.asyncio
    async def test_decorated_async_generator_holds_context_across_iteration(self):
        @with_context(workload={"key": "W"})
        async def numbers():
            for _ in range(2):
                await asyncio.sleep(0)
                yield snapshot()

        assert [item async for item in numbers()] == [{"workload": {"key": "W"}}] * 2
        assert snapshot() is None

    def test_copy_context_carries_declaration_into_a_pool_thread(self):

        with ThreadPoolExecutor(max_workers=1) as pool:
            with with_context(workload={"key": "W"}):
                bare = pool.submit(snapshot).result()
                copied = pool.submit(contextvars.copy_context().run, snapshot).result()
        assert bare is None
        assert copied == {"workload": {"key": "W"}}

    @pytest.mark.asyncio
    async def test_to_thread_carries_declaration(self):
        with with_context(workload={"key": "W"}):
            assert await asyncio.to_thread(snapshot) == {"workload": {"key": "W"}}

    def test_failed_inner_entry_leaves_outer_context_intact(self):
        with with_context(reporter="outer"):
            with pytest.raises(ValueError):
                with with_context(template={"key": "t"}):
                    pass
            assert snapshot() == {"reporter": "outer"}
        assert snapshot() is None

    def test_template_with_id_and_key_needs_no_workload(self):
        with with_context(template={"id": "t1", "key": "t"}):
            assert snapshot() == {"template": {"id": "t1", "key": "t"}}


class TestValidation:
    @pytest.mark.parametrize("bad", ["W", ["key"], 5])
    def test_non_mapping_workload(self, bad):
        with pytest.raises(ValueError, match="workload"):
            with_context(workload=bad)

    def test_non_mapping_template(self):
        with pytest.raises(ValueError, match="template"):
            with_context(workload={"key": "W"}, template="t")

    @pytest.mark.parametrize("bad", [{}, {"id": ""}, {"id": "  "}, {"name": "n"}])
    def test_needs_id_or_key(self, bad):
        with pytest.raises(ValueError, match="id.*key"):
            with_context(workload=bad)

    def test_non_string_id(self):
        with pytest.raises(ValueError, match="id"):
            with_context(workload={"id": 5})

    @pytest.mark.parametrize(
        "key",
        [
            "",
            " lead",
            "trail ",
            "x" * 256,
            "a\x00b",
            "a\nb",
            "zero​width",
            "bad\ud800surrogate",
            "/leading",
            "trailing/",
            "dou//ble",
            5,
        ],
    )
    def test_invalid_keys(self, key):
        with pytest.raises(ValueError):
            with_context(workload={"key": key})

    @pytest.mark.parametrize(
        "key", ["Parser#call", "Parser#call/squish:L2", "a·b", "a/b/c", "x" * 255]
    )
    def test_valid_keys(self, key):
        with with_context(workload={"key": key}):
            assert snapshot()["workload"]["key"] == key

    def test_description_too_long(self):
        with pytest.raises(ValueError, match="description"):
            with_context(workload={"key": "W", "description": "d" * 1001})

    def test_non_string_reporter(self):
        with pytest.raises(ValueError, match="reporter"):
            with_context(reporter=5)

    def test_blank_reporter(self):
        with pytest.raises(ValueError, match="reporter"):
            with_context(reporter=" ")

    def test_template_key_without_workload_fails_on_entry(self):
        scope = with_context(template={"key": "t"})
        ran = []
        with pytest.raises(ValueError, match="workload"):
            with scope:
                ran.append(True)
        assert ran == []

    def test_template_id_needs_no_workload(self):
        with with_context(template={"id": "t1"}):
            assert snapshot() == {"template": {"id": "t1"}}

    def test_template_key_accepted_with_enclosing_workload(self):
        with with_context(workload={"key": "W"}):
            with with_context(template={"key": "t"}):
                assert snapshot()["template"] == {"key": "t"}

    def test_decorated_function_not_run_on_invalid_context(self):
        ran = []

        @with_context(template={"key": "t"})
        def run():
            ran.append(True)

        with pytest.raises(ValueError):
            run()
        assert ran == []


def _delivered_payloads(client, opener):
    """Flush, drain the worker thread, and return the JSON bodies POSTed."""
    client.flush()
    client.shutdown()
    return [json.loads(call.args[0].data) for call in opener.call_args_list]


@pytest.fixture
def client_and_opener(reset_global_instance):
    client = CoolhandClient(
        api_key="test-api-key-12345678",
        base_url="https://test.coolhandlabs.com",
        silent=True,
        auto_submit=False,
    )
    with patch("coolhand.client._opener.open") as opener:
        response = MagicMock()
        response.status = 201
        response.read.return_value = b"{}"
        response.__enter__ = MagicMock(return_value=response)
        response.__exit__ = MagicMock(return_value=False)
        opener.return_value = response
        yield client, opener, response


@pytest.fixture
def patched_httpx(client_and_opener):
    import requests

    client, _opener, _response = client_and_opener
    # The package auto-initializes on import, so the class methods are already patched;
    # reset_global_instance only clears the module flags. Put the real methods back
    # first, or patch() would record a patched method as the "original" and recurse.
    for owner, attr, original in (
        (httpx.Client, "send", httpx_interceptor._original_send),
        (httpx.AsyncClient, "send", httpx_interceptor._original_async_send),
        (requests.Session, "send", httpx_interceptor._original_requests_send),
    ):
        if original is not None:
            setattr(owner, attr, original)
    httpx_interceptor.set_handler(client.log_interaction)
    httpx_interceptor.patch()
    yield
    httpx_interceptor.unpatch()
    httpx_interceptor.set_handler(None)


def _ok(_request):
    return httpx.Response(
        200, json={"choices": []}, headers={"content-type": "application/json"}
    )


class TestPayload:
    def test_declared_is_sibling_of_raw_request(self, client_and_opener, patched_httpx):
        client, opener, _ = client_and_opener
        with with_context(
            reporter="app", workload={"key": "W"}, template={"key": "W/t"}
        ):
            httpx.Client(transport=httpx.MockTransport(_ok)).post(LLM_URL, json={})

        (payload,) = _delivered_payloads(client, opener)
        log = payload["llm_request_log"]
        assert log["declared"] == {
            "reporter": "app",
            "workload": {"key": "W"},
            "template": {"key": "W/t"},
        }
        assert "declared" not in log["raw_request"]
        assert "_declared" not in log["raw_request"]
        assert set(log) == {"raw_request", "collector", "declared"}

    def test_omitted_without_context(self, client_and_opener, patched_httpx):
        client, opener, _ = client_and_opener
        httpx.Client(transport=httpx.MockTransport(_ok)).post(LLM_URL, json={})

        (payload,) = _delivered_payloads(client, opener)
        assert set(payload["llm_request_log"]) == {"raw_request", "collector"}

    def test_snapshot_survives_batched_background_send(
        self, client_and_opener, patched_httpx
    ):
        """The context is gone by the time the worker thread delivers the log."""
        client, opener, _ = client_and_opener
        http = httpx.Client(transport=httpx.MockTransport(_ok))

        with with_context(workload={"key": "first"}):
            http.post(LLM_URL, json={})
        with with_context(workload={"key": "second"}):
            http.post(LLM_URL, json={})
        http.post(LLM_URL, json={})
        assert snapshot() is None

        payloads = _delivered_payloads(client, opener)
        assert [p["llm_request_log"].get("declared") for p in payloads] == [
            {"workload": {"key": "first"}},
            {"workload": {"key": "second"}},
            None,
        ]

    @pytest.mark.asyncio
    async def test_async_client_snapshot_survives_deferred_send(
        self, client_and_opener, patched_httpx
    ):
        client, opener, _ = client_and_opener
        async with httpx.AsyncClient(transport=httpx.MockTransport(_ok)) as http:
            with with_context(workload={"key": "W"}):
                await http.post(LLM_URL, json={})

        (payload,) = _delivered_payloads(client, opener)
        assert payload["llm_request_log"]["declared"] == {"workload": {"key": "W"}}

    def test_streamed_response_consumed_after_context_exit(
        self, client_and_opener, patched_httpx
    ):
        """The declaration belongs to the request, not to wherever it is consumed."""
        client, opener, _ = client_and_opener

        def stream(_request):
            return httpx.Response(
                200,
                content=b"data: {}\n\n",
                headers={"content-type": "text/event-stream"},
            )

        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(stream)) as http:
                with with_context(workload={"key": "W"}):
                    request = http.build_request("POST", LLM_URL, json={})
                    response = await http.send(request, stream=True)
                async for _ in response.aiter_bytes():
                    pass

        asyncio.run(run())
        (payload,) = _delivered_payloads(client, opener)
        assert payload["llm_request_log"]["declared"] == {"workload": {"key": "W"}}

    def test_requests_session_snapshot_survives_deferred_send(
        self, client_and_opener, patched_httpx
    ):

        client, opener, _ = client_and_opener
        session = requests.Session()
        canned = requests.Response()
        canned.status_code = 200
        canned.headers["content-type"] = "application/json"
        canned._content = b"{}"
        # The mounted adapter keeps the real network out of the patched send.
        adapter = MagicMock()
        adapter.send.return_value = canned
        session.mount("https://", adapter)

        with with_context(workload={"key": "W"}):
            session.post(LLM_URL, json={})

        (payload,) = _delivered_payloads(client, opener)
        assert payload["llm_request_log"]["declared"] == {"workload": {"key": "W"}}

    def test_warnings_not_read_for_logs_without_a_declaration(
        self, client_and_opener, patched_httpx
    ):
        client, opener, response = client_and_opener
        httpx.Client(transport=httpx.MockTransport(_ok)).post(LLM_URL, json={})
        _delivered_payloads(client, opener)
        response.read.assert_not_called()

    def test_thread_worker_declares_inside_its_own_context(
        self, client_and_opener, patched_httpx
    ):
        """Dramatiq-style: a worker thread enters with_context inside the task body."""

        client, opener, _ = client_and_opener
        http = httpx.Client(transport=httpx.MockTransport(_ok))

        def task():
            with with_context(workload={"key": "actor"}):
                http.post(LLM_URL, json={})

        thread = threading.Thread(target=task)
        thread.start()
        thread.join()

        (payload,) = _delivered_payloads(client, opener)
        assert payload["llm_request_log"]["declared"] == {"workload": {"key": "actor"}}

    def test_response_warnings_are_logged_not_raised(
        self, client_and_opener, patched_httpx, caplog
    ):
        client, opener, response = client_and_opener
        response.read.return_value = json.dumps(
            {"warnings": ["declared workload cap reached"]}
        ).encode()
        with with_context(workload={"key": "W"}):
            httpx.Client(transport=httpx.MockTransport(_ok)).post(LLM_URL, json={})

        with caplog.at_level(logging.WARNING, logger="coolhand.client"):
            _delivered_payloads(client, opener)
        assert "declared workload cap reached" in caplog.text

    @pytest.mark.parametrize("body", [b"", b"not json", b"[]", b'{"warnings": 5}'])
    def test_unreadable_response_body_is_ignored(
        self, client_and_opener, patched_httpx, body
    ):
        client, opener, response = client_and_opener
        response.read.return_value = body
        with with_context(workload={"key": "W"}):
            httpx.Client(transport=httpx.MockTransport(_ok)).post(LLM_URL, json={})
        assert len(_delivered_payloads(client, opener)) == 1
        response.read.assert_called_once()
