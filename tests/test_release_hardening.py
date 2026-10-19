"""Regressions for the v0.10.0 pre-release security review."""

import logging
import time

import httpx
import pytest
import requests

from coolhand import httpx_interceptor as interceptor
from coolhand._read_service import CoolhandAPIError, ReadService
from coolhand.client import _sanitize_headers, _sanitize_url

LLM_URL = "https://api.openai.com/v1/chat/completions"


class TestUrlAndHeaderRedaction:
    def test_every_casing_of_a_repeated_sensitive_param_is_redacted(self):
        result = _sanitize_url("https://h.example/p?Key=SECRET1&key=SECRET2&ok=1")
        assert "SECRET1" not in result
        assert "SECRET2" not in result
        assert "ok=1" in result

    @pytest.mark.parametrize(
        "param",
        [
            "x-api-key",
            "auth",
            "auth_token",
            "access-token",
            "api-token",
            "authorization",
            "bearer",
            "X-Auth-Token",
        ],
    )
    def test_credential_query_param_spellings_are_redacted(self, param):
        result = _sanitize_url(f"https://h.example/p?{param}=SECRETVALUE&ok=1")
        assert "SECRETVALUE" not in result
        assert "ok=1" in result

    @pytest.mark.parametrize(
        "header",
        ["Authentication", "X-Bearer", "X-Auth", "X-Session-Token", "X-CSRF-Token"],
    )
    def test_credential_header_spellings_are_masked(self, header):
        value = "supersecretvalue-0123456789"
        assert _sanitize_headers({header: value})[header] != value

    def test_ratelimit_headers_stay_readable(self):
        headers = {"x-ratelimit-remaining-tokens": "12345"}
        assert _sanitize_headers(headers) == headers


class TestRequestsWrapperAfterUnpatch:
    def test_a_retained_wrapper_still_sends_after_unpatch(self):
        """Mock/record tools wrap Session.send and can outlive unpatch()."""
        interceptor.unpatch()
        real = requests.Session.send
        try:
            interceptor.set_handler(lambda *_: None)
            interceptor.patch()
            retained = requests.Session.send
            interceptor.unpatch()
            interceptor.set_handler(None)
            assert interceptor._original_requests_send is None

            session = requests.Session()
            session.mount(
                "https://",
                _StaticAdapter(),
            )
            response = retained(
                session, requests.Request("GET", "https://e.x/").prepare()
            )
            assert response.status_code == 200
        finally:
            requests.Session.send = real
            interceptor._patched = False


class _StaticAdapter(requests.adapters.BaseAdapter):
    def send(self, request, **kwargs):
        response = requests.Response()
        response.status_code = 200
        response._content = b"{}"
        response.request = request
        return response

    def close(self):
        pass


class TestPatchIsIdempotent:
    def test_repatching_after_module_state_was_reset_does_not_recurse(self):
        """A reset _patched flag must not make a wrapper save itself as original."""
        interceptor.unpatch()
        seen = []
        interceptor.set_handler(lambda req, res, err: seen.append(res))
        try:
            assert interceptor.patch() is True
            interceptor._patched = False  # what a sloppy test fixture does
            assert interceptor.patch() is True

            with httpx.Client(
                transport=httpx.MockTransport(lambda r: httpx.Response(200, json={}))
            ) as client:
                assert client.post(LLM_URL, json={}).status_code == 200
            assert len(seen) == 1  # captured once, not once per stacked wrapper
        finally:
            interceptor.unpatch()
            interceptor.set_handler(None)

    def test_unpatch_restores_the_true_original_after_a_double_patch(self):
        interceptor.unpatch()
        interceptor.set_handler(lambda *_: None)
        interceptor.patch()
        original = interceptor._original_send
        interceptor._patched = False
        interceptor.patch()
        interceptor.unpatch()
        interceptor.set_handler(None)
        assert httpx.Client.send is original

    def test_concurrent_patch_calls_install_one_wrapper_over_the_real_original(self):
        import threading

        interceptor.unpatch()
        real_send = httpx.Client.send
        seen = []
        interceptor.set_handler(lambda req, res, err: seen.append(res))
        threads = 8
        barrier = threading.Barrier(threads)
        results = []

        def race():
            barrier.wait()
            results.append(interceptor.patch())

        try:
            workers = [threading.Thread(target=race) for _ in range(threads)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join()

            assert results == [True] * threads
            assert httpx.Client.send._coolhand_original is real_send
            with httpx.Client(
                transport=httpx.MockTransport(lambda r: httpx.Response(200, json={}))
            ) as client:
                assert client.post(LLM_URL, json={}).status_code == 200
            assert len(seen) == 1
        finally:
            interceptor.unpatch()
            interceptor.set_handler(None)
        assert httpx.Client.send is real_send


class _FailingOpener:
    def __init__(self, error):
        self.error = error

    def open(self, request, timeout=None):
        raise self.error


class TestReadServiceWrapsReadTimeFailures:
    @pytest.mark.parametrize(
        "error", [TimeoutError("timed out"), ConnectionResetError("reset")]
    )
    def test_get_json_raises_coolhand_api_error(self, error):
        service = ReadService(api_key="k", base_url="https://example.test")
        service._opener = _FailingOpener(error)
        with pytest.raises(CoolhandAPIError):
            service._get_json("https://example.test/x")


class TestGlobCostIsBounded:
    def test_many_consecutive_stars_in_a_host_entry_stay_fast(self):
        # Uncollapsed, this many stars takes minutes; collapsed it is sub-millisecond,
        # so a 1s bound separates the two on any machine.
        entry = "a" + "*" * 4000 + "b.example.com"
        host = "a" + "x" * 200 + "b.example.com"
        started = time.perf_counter()
        assert interceptor._compile_entry(entry)(host, 443, "/") is True
        assert interceptor._compile_entry(entry)("ab.example.net", 443, "/") is False
        assert time.perf_counter() - started < 1.0


class TestKeyBytesAreNotLogged:
    def test_malformed_key_is_not_in_the_delivery_warning(
        self, reset_global_instance, caplog
    ):
        """An embedded control character survives strip() and http.client's
        ValueError quotes the whole header value."""
        from coolhand.client import CoolhandClient

        caplog.set_level(logging.WARNING)
        client = CoolhandClient(
            auto_submit=False, silent=True, api_key="sk-live-abc\ndef-SECRETSECRET"
        )
        assert client._send_one({"id": "x", "method": "post", "url": "u"}) is False
        assert "SECRETSECRET" not in caplog.text
        assert "Unexpected error submitting interaction" in caplog.text
