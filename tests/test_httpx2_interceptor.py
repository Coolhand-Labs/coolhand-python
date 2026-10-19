"""httpx2 interception: the OpenAI / Anthropic / pydantic-ai SDKs build on it.

httpx2 is a separate package from httpx, so patching ``httpx.Client.send`` alone
silently misses every call these SDKs make.
"""

import pytest

httpx2 = pytest.importorskip("httpx2")

from coolhand import httpx_interceptor as interceptor  # noqa: E402

LLM_URL = "https://api.openai.com/v1/chat/completions"


@pytest.fixture
def captured():
    interceptor.unpatch()
    interceptor.set_exclude_api_patterns(None)
    calls: list[tuple] = []
    interceptor.set_handler(lambda req, res, err: calls.append((req, res, err)))
    assert interceptor.patch() is True
    yield calls
    interceptor.unpatch()
    interceptor.set_handler(None)


def _json_transport(body=b'{"ok": true}', status=200):
    return httpx2.MockTransport(
        lambda request: httpx2.Response(
            status, content=body, headers={"content-type": "application/json"}
        )
    )


class TestSync:
    def test_captures_request_and_response(self, captured):
        with httpx2.Client(transport=_json_transport()) as client:
            response = client.post(LLM_URL, json={"model": "gpt-x"})

        assert response.json() == {"ok": True}
        assert len(captured) == 1
        req, res, err = captured[0]
        assert err is None
        assert req["method"] == "POST"
        assert req["url"] == LLM_URL
        assert '"model":"gpt-x"' in req["body"].replace(" ", "")
        assert res["status_code"] == 200
        assert res["body"] == b'{"ok": true}'

    def test_non_llm_url_is_not_captured(self, captured):
        with httpx2.Client(transport=_json_transport()) as client:
            client.get("https://example.com/health")
        assert captured == []

    def test_deny_listed_url_is_not_captured(self, captured):
        interceptor.set_exclude_api_patterns(["api.openai.com/v1/chat"])
        with httpx2.Client(transport=_json_transport()) as client:
            client.post(LLM_URL, json={})
        assert captured == []

    def test_transport_error_is_reported_and_reraised(self, captured):
        def boom(request):
            raise httpx2.ConnectError("refused", request=request)

        with httpx2.Client(transport=httpx2.MockTransport(boom)) as client:
            with pytest.raises(httpx2.ConnectError):
                client.post(LLM_URL, json={})

        assert len(captured) == 1
        _, res, err = captured[0]
        assert res is None
        assert "refused" in err

    def test_increments_intercept_count(self, captured):
        before = interceptor.get_intercept_count()
        with httpx2.Client(transport=_json_transport()) as client:
            client.post(LLM_URL, json={})
        assert interceptor.get_intercept_count() == before + 1


@pytest.mark.asyncio
class TestAsync:
    async def test_captures_request_and_response(self, captured):
        async with httpx2.AsyncClient(transport=_json_transport()) as client:
            response = await client.post(LLM_URL, json={"model": "gpt-x"})

        assert response.status_code == 200
        assert len(captured) == 1
        req, res, err = captured[0]
        assert err is None
        assert req["url"] == LLM_URL
        assert res["is_streaming"] is False

    async def test_streaming_body_is_captured_once_after_iteration(self, captured):
        sse = b"data: one\n\ndata: two\n\n"
        transport = httpx2.MockTransport(
            lambda request: httpx2.Response(
                200, content=sse, headers={"content-type": "text/event-stream"}
            )
        )
        async with httpx2.AsyncClient(transport=transport) as client:
            async with client.stream("POST", LLM_URL, json={}) as response:
                lines = [line async for line in response.aiter_lines() if line]

        assert lines == ["data: one", "data: two"]
        assert len(captured) == 1
        _, res, err = captured[0]
        assert err is None
        assert res["is_streaming"] is True
        assert "data: one" in res["body"] and "data: two" in res["body"]

    async def test_transport_error_is_reported_and_reraised(self, captured):
        def boom(request):
            raise httpx2.ConnectError("refused", request=request)

        async with httpx2.AsyncClient(transport=httpx2.MockTransport(boom)) as client:
            with pytest.raises(httpx2.ConnectError):
                await client.post(LLM_URL, json={})

        assert len(captured) == 1
        assert captured[0][1] is None


class TestPatchLifecycle:
    def test_unpatch_restores_httpx2_originals(self):
        interceptor.unpatch()
        original_sync = httpx2.Client.send
        original_async = httpx2.AsyncClient.send

        interceptor.set_handler(lambda *_: None)
        interceptor.patch()
        assert httpx2.Client.send is not original_sync
        assert httpx2.AsyncClient.send is not original_async

        interceptor.unpatch()
        interceptor.set_handler(None)
        assert httpx2.Client.send is original_sync
        assert httpx2.AsyncClient.send is original_async

    def test_httpx_and_httpx2_are_both_patched(self, captured):
        import httpx

        with httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json={}))
        ) as client:
            client.post(LLM_URL, json={})
        with httpx2.Client(transport=_json_transport()) as client:
            client.post(LLM_URL, json={})

        assert len(captured) == 2


class TestHandlerFailuresNeverBreakTheHostCall:
    """A broken capture handler must not change what the host application sees."""

    @staticmethod
    def _install_raising_handler():
        def handler(req, res, err):
            raise RuntimeError("handler blew up")

        interceptor.set_handler(handler)

    def test_sync_success_path(self, captured):
        self._install_raising_handler()
        with httpx2.Client(transport=_json_transport()) as client:
            assert client.post(LLM_URL, json={}).json() == {"ok": True}

    def test_sync_error_path_reraises_the_original_error(self, captured):
        self._install_raising_handler()

        def boom(request):
            raise httpx2.ConnectError("refused", request=request)

        with httpx2.Client(transport=httpx2.MockTransport(boom)) as client:
            with pytest.raises(httpx2.ConnectError):
                client.post(LLM_URL, json={})

    @pytest.mark.asyncio
    async def test_async_success_path(self, captured):
        self._install_raising_handler()
        async with httpx2.AsyncClient(transport=_json_transport()) as client:
            response = await client.post(LLM_URL, json={})
        assert response.json() == {"ok": True}

    @pytest.mark.asyncio
    async def test_async_error_path_reraises_the_original_error(self, captured):
        self._install_raising_handler()

        def boom(request):
            raise httpx2.ConnectError("refused", request=request)

        async with httpx2.AsyncClient(transport=httpx2.MockTransport(boom)) as client:
            with pytest.raises(httpx2.ConnectError):
                await client.post(LLM_URL, json={})

    @pytest.mark.asyncio
    async def test_async_streaming_path(self, captured):
        self._install_raising_handler()
        transport = httpx2.MockTransport(
            lambda request: httpx2.Response(
                200,
                content=b"data: x\n\n",
                headers={"content-type": "text/event-stream"},
            )
        )
        async with httpx2.AsyncClient(transport=transport) as client:
            async with client.stream("POST", LLM_URL, json={}) as response:
                lines = [line async for line in response.aiter_lines() if line]
        assert lines == ["data: x"]
