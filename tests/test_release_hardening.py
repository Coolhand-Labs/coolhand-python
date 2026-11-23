"""Regressions for the v0.10.0 pre-release security review."""

import logging
import os
import time

import httpx
import pytest
import requests

from coolhand import Coolhand, FeedbackLinkService
from coolhand import httpx_interceptor as interceptor
from coolhand._config import _normalize_base_url
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
            "private_token",
            "accessKey",
            "access_key",
            "client-secret",
            "session_token",
            "apitoken",
            "accesstoken",
            "secretkey",
            "subscription_key",
            "hf_token",
            "Ocp-Apim-Subscription-Key",
            "X-Goog-Credential",
        ],
    )
    def test_credential_query_param_spellings_are_redacted(self, param):
        result = _sanitize_url(f"https://h.example/p?{param}=SECRETVALUE&ok=1")
        assert "SECRETVALUE" not in result
        assert "ok=1" in result

    @pytest.mark.parametrize(
        "header",
        [
            "Authentication",
            "X-Bearer",
            "X-Auth",
            "X-Session-Token",
            "X-CSRF-Token",
            "X-CSRFToken",
            "X-XSRF-TOKEN",
            "PRIVATE-TOKEN",
            "X-Client-Secret",
            "X-Api-Secret",
        ],
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

    @pytest.mark.parametrize("call_site", ["get_json", "put_json", "feedback_link"])
    def test_malformed_key_value_error_never_reaches_the_traceback(self, call_site):
        """A control character in the key makes http.client raise ValueError quoting
        the whole header value. The wrapped error must neither carry it in its
        message nor chain to it, or any logged traceback would print the key."""
        import traceback
        from urllib.request import Request

        from coolhand._read_service import _put_json
        from coolhand.feedback_link_service import FeedbackLinkService

        leaked = ValueError("Invalid header value b'sk-abc\\ndef-SECRET'")
        opener = _FailingOpener(leaked)
        url = "https://e.test/x"

        def call():
            if call_site == "get_json":
                service = ReadService(api_key="k", base_url="https://e.test")
                service._opener = opener
                service._get_json(url)
            elif call_site == "put_json":
                _put_json(opener, url, {}, api_key="k", timeout=1, label="Workload")
            else:
                service = FeedbackLinkService(api_key="k", base_url="https://e.test")
                service._opener = opener
                service._send(Request(url))

        with pytest.raises(CoolhandAPIError) as excinfo:
            call()

        rendered = "".join(traceback.format_exception(excinfo.value))
        assert "SECRET" not in rendered
        assert excinfo.value.__cause__ is None
        assert "ValueError" in str(excinfo.value)


class TestHttpx2OptionalAndDiagnostics:
    def test_patching_works_when_httpx2_is_not_installed(self, monkeypatch):
        import sys

        interceptor.unpatch()
        real_send = httpx.Client.send
        monkeypatch.setitem(sys.modules, "httpx2", None)  # makes `import httpx2` fail
        interceptor.set_handler(lambda *_: None)
        try:
            assert interceptor.patch() is True
            assert httpx.Client.send is not real_send
            assert interceptor.patched_targets() == [
                "httpx.Client.send",
                "httpx.AsyncClient.send",
            ]
        finally:
            interceptor.unpatch()
            interceptor.set_handler(None)
        assert httpx.Client.send is real_send

    def test_patched_targets_lists_httpx2_when_installed(self):
        httpx2 = pytest.importorskip("httpx2")
        interceptor.unpatch()
        assert interceptor.patched_targets() == []
        interceptor.set_handler(lambda *_: None)
        try:
            interceptor.patch()
            targets = interceptor.patched_targets()
            assert "httpx2.Client.send" in targets
            assert "httpx2.AsyncClient.send" in targets
            assert httpx2.Client.send._coolhand_original
        finally:
            interceptor.unpatch()
            interceptor.set_handler(None)
        assert interceptor.patched_targets() == []


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


class TestV0110RedactionSpellings:
    """Credential spellings added in 0.11.0 that earlier lists missed."""

    @pytest.mark.parametrize(
        "header",
        [
            "Helicone-Auth",
            "HELICONE-AUTH",
            "X-Github-Token",
            "Github-Token",
            "X-Vault-Token",
            "Vault-Token",
            "HF-Token",
            "Api-Token",
            "X-Client-Key",
            "X-Credential",
            "Auth-Token",
            "Access-Key",
            "X-Access-Key",
            "X-Private-Key",
            "X-Signature",
            "Signature",
        ],
    )
    def test_header_is_masked(self, header):
        secret = "supersecretvalue123456"
        masked = _sanitize_headers({header: secret})[header]
        assert secret not in masked
        assert masked

    def test_benign_token_count_headers_are_not_masked(self):
        headers = {
            "x-ratelimit-remaining-tokens": "1000",
            "access-control-allow-credentials": "true",
            "content-type": "application/json",
        }
        assert _sanitize_headers(headers) == headers

    @pytest.mark.parametrize(
        "param",
        [
            "bearer_token",
            "bearer-token",
            "secret_key",
            "secret-key",
            "clientSecret",
            "pwd",
            "passwd",
            "code_verifier",
        ],
    )
    def test_query_param_is_redacted(self, param):
        url = _sanitize_url(f"https://api.example.com/v1?{param}=hunter2&model=x")
        assert "hunter2" not in url
        assert "model=x" in url

    @pytest.mark.parametrize(
        ("url", "message"),
        [
            ("https://user:hunter2@coolhandlabs.com", "must not contain credentials"),
            ("https://hunter2@coolhandlabs.com", "must not contain credentials"),
            ("http://user:hunter2@localhost:3000", "must not contain credentials"),
            ("http://user:hunter2@example.com", "must use https://"),
            ("user:hunter2@example.com", "must use https://"),
        ],
    )
    def test_base_url_with_userinfo_is_rejected_without_echoing_it(self, url, message):
        with pytest.raises(ValueError, match=message) as excinfo:
            _normalize_base_url(url)
        assert "hunter2" not in str(excinfo.value)

    @pytest.mark.parametrize(
        "build",
        [
            lambda url: Coolhand(api_key="k", base_url=url, auto_submit=False),
            lambda url: ReadService(api_key="k", base_url=url),
            lambda url: FeedbackLinkService(api_key="k", base_url=url),
        ],
        ids=["Coolhand", "ReadService", "FeedbackLinkService"],
    )
    def test_every_entry_point_rejects_credentials_in_base_url(self, build):
        with pytest.raises(ValueError, match="must not contain credentials"):
            build("https://user:hunter2@coolhandlabs.com")


class TestV0121RedTeamRedaction:
    """Credential spellings the v0.12.1 red-team found slipping past redaction."""

    @pytest.mark.parametrize(
        "param",
        [
            "authToken",
            "bearerToken",
            "sessionToken",
            "privateKey",
            "private_key",
            "apiSecret",
            "idToken",
            "id-token",
            "jwt",
            "sas_token",
        ],
    )
    def test_more_query_param_spellings_are_redacted(self, param):
        result = _sanitize_url(f"https://h.example/p?{param}=SECRETVALUE&ok=1")
        assert "SECRETVALUE" not in result
        assert "ok=1" in result

    @pytest.mark.parametrize("param", ["max_tokens", "page", "model", "tokens"])
    def test_benign_query_params_are_kept(self, param):
        result = _sanitize_url(f"https://h.example/p?{param}=5")
        assert f"{param}=5" in result

    def test_url_fragment_credentials_are_dropped(self):
        result = _sanitize_url("https://h.example/p?ok=1#access_token=SECRETVALUE")
        assert "SECRETVALUE" not in result
        assert "ok=1" in result

    def test_fail_closed_url_still_drops_userinfo(self):
        # An unterminated IPv6 literal makes urlparse raise.
        result = _sanitize_url("https://user:SECRETPW@[abc/x?key=SECRETKEY")
        assert "SECRETPW" not in result
        assert "SECRETKEY" not in result

    @pytest.mark.parametrize(
        "header",
        [
            "Token",
            "X-Token",
            "X-Id-Token",
            "X-Refresh-Token",
            "Cf-Access-Jwt-Assertion",
            "X-Key",
            "Auth",
            "X-Anthropic-Key",
            "X-Password",
        ],
    )
    def test_more_credential_header_names_are_masked(self, header):
        result = _sanitize_headers({header: "SECRETVALUE-0123456789"})
        assert "SECRETVALUE" not in result[header]

    @pytest.mark.parametrize(
        "header",
        [
            "x-ratelimit-remaining-tokens",
            "anthropic-ratelimit-tokens-remaining",
            "Content-Type",
            "X-Request-Id",
        ],
    )
    def test_rate_limit_and_ordinary_headers_stay_readable(self, header):
        assert _sanitize_headers({header: "123"})[header] == "123"


class TestV0121FollowUps:
    """Follow-ups to the v0.12.1 red-team: memory budget, fork safety, error text."""

    @staticmethod
    def _client(**config):
        from unittest.mock import patch

        from coolhand.client import CoolhandClient

        client = CoolhandClient(
            auto_submit=False, api_key="real-api-key-12345", **config
        )
        # Keep the worker from draining, so the backlog is observable.
        patcher = patch.object(client, "_ensure_worker_locked", return_value=True)
        patcher.start()
        return client, patcher

    def test_queue_is_bounded_by_bytes_not_just_count(self, reset_global_instance):
        client, patcher = self._client(max_queued_bytes=100_000, max_body_bytes=60_000)
        try:
            for _ in range(10):
                client.log_interaction(
                    {"method": "POST", "url": LLM_URL, "body": {"p": "x" * 50_000}},
                    {"status_code": 200, "body": {"r": "y" * 50_000}},
                )
            assert client.flush() is False
            stats = client.get_stats()["logging"]
            # Only the first (empty queue) item fits; the rest are purged.
            assert stats["pending_delivery"] == 1
            assert stats["dropped_count"] == 9
            assert 0 < stats["queued_bytes"] <= 120_000
            assert len(client._queue) == 0
        finally:
            patcher.stop()

    def test_byte_budget_is_released_after_delivery(self, reset_global_instance):
        client, patcher = self._client(max_queued_bytes=100_000)
        try:
            client.log_interaction(
                {"method": "POST", "url": LLM_URL, "body": {"p": "x" * 1000}}
            )
            client.flush()
            assert client._queued_bytes > 0
            item = client._dispatch_queue.get_nowait()
            client._send_one = lambda i: True  # type: ignore[method-assign]
            client._deliver(item)
            assert client._queued_bytes == 0
        finally:
            patcher.stop()

    def test_budget_can_be_disabled(self, reset_global_instance):
        client, patcher = self._client(max_queued_bytes=0, max_body_bytes=60_000)
        try:
            for _ in range(5):
                client.log_interaction(
                    {"method": "POST", "url": LLM_URL, "body": {"p": "x" * 50_000}}
                )
            assert client.flush() is True
            assert client.get_stats()["logging"]["pending_delivery"] == 5
        finally:
            patcher.stop()

    def test_queued_weight_is_not_sent_to_coolhand(self, reset_global_instance):
        from unittest.mock import patch

        client, patcher = self._client()
        patcher.stop()
        client.log_interaction({"method": "POST", "url": LLM_URL, "body": {"a": 1}})
        item = client._queue[0]
        assert item["_weight"] > 0
        with patch("coolhand.client._opener") as opener:
            opener.open.return_value.__enter__.return_value.status = 200
            assert client._send_one(item) is True
            sent = opener.open.call_args[0][0].data.decode()
        assert "_weight" not in sent

    def test_drop_warning_is_rate_limited(self, reset_global_instance, caplog):
        client, patcher = self._client(max_queued_bytes=1)
        try:
            with caplog.at_level(logging.WARNING, logger="coolhand.client"):
                for _ in range(20):
                    client.log_interaction(
                        {"method": "POST", "url": LLM_URL, "body": {"a": 1}}
                    )
                    client.flush()
            warnings = [r for r in caplog.records if "Dropped" in r.getMessage()]
            assert len(warnings) == 1
            assert client._dropped_count == 19
        finally:
            patcher.stop()

    @pytest.mark.skipif(not hasattr(os, "fork"), reason="needs os.fork")
    @pytest.mark.filterwarnings("ignore:.*fork.*:DeprecationWarning")
    def test_plain_fork_resets_inherited_client_state(self, reset_global_instance):
        client, patcher = self._client()
        patcher.stop()
        client.log_interaction({"method": "POST", "url": LLM_URL, "body": {"a": 1}})
        client._state_lock.acquire()  # a parent thread mid-update at fork time
        read_fd, write_fd = os.pipe()
        pid = os.fork()
        if pid == 0:  # child: must not hang and must not own the parent's items
            try:
                import signal

                signal.alarm(5)
                client.log_interaction(
                    {"method": "POST", "url": LLM_URL, "body": {"child": 1}}
                )
                os.write(write_fd, str(len(client._queue)).encode())
            finally:
                os._exit(0)
        os.close(write_fd)
        client._state_lock.release()
        data = os.read(read_fd, 10)
        os.waitpid(pid, 0)
        assert data == b"1"  # only the child's own interaction, not the parent's

    def test_error_text_is_forwarded_and_scrubbed(self, reset_global_instance):
        client, patcher = self._client()
        patcher.stop()
        client.log_interaction(
            {"method": "POST", "url": LLM_URL, "body": {"a": 1}},
            None,
            "HTTPSConnectionPool: Max retries exceeded with url: "
            "https://api.openai.com/v1/x?key=SECRET123&ok=1 "
            "Authorization: Bearer sk-abcdefghijklmnop api_key=SECRET456",
        )
        error = client._queue[0]["error"]
        assert "SECRET123" not in error
        assert "SECRET456" not in error
        assert "abcdefghijklmnop" not in error
        assert "Max retries exceeded" in error
        assert "ok=1" in error

    def test_no_error_field_without_an_error(self, reset_global_instance):
        client, patcher = self._client()
        patcher.stop()
        client.log_interaction({"method": "POST", "url": LLM_URL}, {"status_code": 200})
        assert "error" not in client._queue[0]

    def test_error_text_is_truncated(self, reset_global_instance):
        from coolhand.client import _sanitize_error

        assert len(_sanitize_error("e" * 5000)) == 1000

    @pytest.mark.parametrize(
        "path",
        [
            "/v1/organization/admin_api_keys",
            "/v1/files",
            "/v1/uploads/upload_1/parts",
            "/v1/batches",
            "/v1/fine_tuning/jobs",
            "/v1/models",
            "/v1/realtime/client_secrets",
        ],
    )
    def test_openai_non_inference_endpoints_are_not_captured(self, path):
        interceptor.set_exclude_api_patterns(None)
        assert interceptor._should_capture_url(f"https://api.openai.com{path}") is False

    @pytest.mark.parametrize(
        "path",
        [
            "/v1/chat/completions",
            "/v1/responses",
            "/v1/embeddings",
            "/v1/audio/transcriptions",
            "/v1/images/generations",
        ],
    )
    def test_openai_inference_endpoints_are_still_captured(self, path):
        interceptor.set_exclude_api_patterns(None)
        assert interceptor._should_capture_url(f"https://api.openai.com{path}") is True


class TestV0121ReviewFollowUps:
    @pytest.mark.parametrize(
        "text",
        [
            "failed password='hunter2' retry",
            'api_key="abc123456" retry',
            "token = 'abcdef' retry",
            "payload {'api_key': 'abcd1234xyz'} retry",
            'payload {"token": "abcdef"} retry',
            "Bearer abcdefghijklmnop retry",
        ],
    )
    def test_error_scrub_covers_quoted_and_json_style_pairs(self, text):
        from coolhand.client import _sanitize_error

        result = _sanitize_error(text)
        for secret in (
            "hunter2",
            "abc123456",
            "abcdef",
            "abcd1234xyz",
            "abcdefghijklmnop",
        ):
            assert secret not in result
        assert "retry" in result

    def test_error_scrub_keeps_counters(self):
        from coolhand.client import _sanitize_error

        assert "max_tokens=100" in _sanitize_error("bad max_tokens=100")

    @pytest.mark.parametrize("text", ["a-" * 50_000, "x" * 100_000, "key" * 40_000])
    def test_error_scrub_is_fast_on_hostile_input(self, text):
        from coolhand.client import _sanitize_error

        started = time.perf_counter()
        result = _sanitize_error(text)
        assert time.perf_counter() - started < 1.0
        assert len(result) <= 1000

    @pytest.mark.parametrize("bad", ["16", -1, 1.5, True])
    def test_invalid_max_queued_bytes_fails_at_construction(self, bad):
        from coolhand.client import CoolhandClient

        with pytest.raises(ValueError, match="max_queued_bytes"):
            CoolhandClient(auto_submit=False, max_queued_bytes=bad)

    @pytest.mark.parametrize("off", [None, 0])
    def test_max_queued_bytes_can_be_disabled(self, off):
        from coolhand.client import CoolhandClient

        client = CoolhandClient(auto_submit=False, max_queued_bytes=off)
        assert client.config["max_queued_bytes"] is None

    def test_budget_is_released_when_the_dispatch_queue_is_full(
        self, reset_global_instance
    ):
        from unittest.mock import patch

        from coolhand import client as client_module
        from coolhand.client import CoolhandClient

        with patch.object(client_module, "_MAX_DISPATCH_QUEUE_SIZE", 1):
            client = CoolhandClient(auto_submit=False, api_key="real-api-key-12345")
        with patch.object(client, "_ensure_worker_locked", return_value=True):
            for _ in range(3):
                client.log_interaction(
                    {"method": "POST", "url": LLM_URL, "body": {"a": 1}}
                )
            client.flush()
        # Only the one item that fit still holds budget.
        weight = client._dispatch_queue.get_nowait()["_weight"]
        assert client._queued_bytes == weight
        assert client._dropped_count == 2

    def test_budget_is_released_when_delivery_raises(self, reset_global_instance):
        from coolhand.client import CoolhandClient

        client = CoolhandClient(auto_submit=False, api_key="real-api-key-12345")
        client._queued_bytes = 500

        def boom(_item):
            raise RuntimeError("boom")

        client._send_one = boom  # type: ignore[method-assign]
        client._deliver({"_weight": 500})
        assert client._queued_bytes == 0
        assert client._delivery_failure_count == 1

    def test_cap_body_sized_reports_size_of_what_is_kept(self):
        from coolhand._body_limit import cap_body_sized

        assert cap_body_sized(None, 10) == (None, 0)
        body, size = cap_body_sized({"a": "b"}, 1000)
        assert body == {"a": "b"} and size == len('{"a": "b"}')
        truncated, size = cap_body_sized("x" * 5000, 100)
        assert truncated.startswith("x" * 100) and "truncated" in truncated
        assert size == len(truncated.encode())
        # No cap: still measured, so the memory budget can account for it.
        assert cap_body_sized("x" * 5000, None) == ("x" * 5000, 5000)

    def test_fork_hooks_replace_module_locks(self):
        from coolhand import copilot_interceptor

        before = copilot_interceptor._lock
        copilot_interceptor._pre_pending["s"] = [{"x": 1}]
        try:
            copilot_interceptor._reset_after_fork()
            assert copilot_interceptor._lock is not before
            assert copilot_interceptor._pre_pending == {}
        finally:
            copilot_interceptor._pre_pending.clear()

        before = interceptor._intercept_count_lock
        interceptor._reset_reporter_after_fork()
        assert interceptor._intercept_count_lock is not before
        assert interceptor.get_intercept_count() >= 0  # still usable

    def test_error_scrub_is_idempotent(self):
        from coolhand.client import _sanitize_error

        once = _sanitize_error("token=SECRETVALUE1 x")
        assert once == "token=[REDACTED] x"
        assert _sanitize_error(once) == once
