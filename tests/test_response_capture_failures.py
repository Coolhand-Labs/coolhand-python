"""Response instrumentation must not change the result of a successful request."""

from unittest.mock import AsyncMock, MagicMock

import pytest
import requests

from coolhand import httpx_interceptor as interceptor


@pytest.fixture(autouse=True)
def reset_interceptor():
    interceptor.unpatch()
    interceptor.set_handler(None)
    yield
    interceptor.unpatch()
    interceptor.set_handler(None)


@pytest.mark.parametrize("client_module", ["httpx", "httpx2"])
@pytest.mark.parametrize("async_send", [False, True])
@pytest.mark.asyncio
async def test_httpx_capture_failure_preserves_response(
    monkeypatch, client_module, async_send
):
    module = __import__(client_module)
    response = module.Response(200, json={"ok": True})
    request = module.Request("POST", "https://api.openai.com/v1/chat/completions")
    captured = []
    interceptor.set_handler(lambda *args: captured.append(args))
    interceptor.patch()
    original_name = "_original_" + ("httpx2_" if client_module == "httpx2" else "")
    original_name += "async_send" if async_send else "send"
    monkeypatch.setattr(
        interceptor,
        original_name,
        AsyncMock(return_value=response)
        if async_send
        else MagicMock(return_value=response),
    )
    monkeypatch.setattr(
        interceptor,
        "_read_response_body",
        MagicMock(side_effect=ValueError("capture failed")),
    )

    if async_send:
        async with module.AsyncClient() as client:
            result = await client.send(request)
    else:
        with module.Client() as client:
            result = client.send(request)

    assert result is response
    assert captured == []
    assert not interceptor._intercepting.get()


def test_requests_capture_failure_preserves_response(monkeypatch):
    response = requests.Response()
    response.status_code = 200
    response.headers = {"content-type": "application/json"}
    response._content = b"{}"
    request = requests.Request(
        "POST", "https://api.openai.com/v1/chat/completions"
    ).prepare()
    captured = []
    interceptor.set_handler(lambda *args: captured.append(args))
    interceptor.patch()
    monkeypatch.setattr(
        interceptor, "_original_requests_send", MagicMock(return_value=response)
    )
    response.headers = MagicMock()
    response.headers.get.return_value = "application/json"
    response.headers.keys.side_effect = ValueError("capture failed")

    with requests.Session() as session:
        result = session.send(request)

    assert result is response
    assert captured == []
    assert not interceptor._intercepting.get()
