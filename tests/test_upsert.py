"""Tests for TemplateService.upsert and WorkloadService.upsert."""

import http.client
import json
from email.message import Message
from urllib.error import HTTPError, URLError

import pytest

from coolhand import Coolhand, CoolhandAPIError, TemplateService, WorkloadService

BASE_URL = "https://test.coolhandlabs.com"


class _FakeResponse:
    def __init__(self, body, status=200):
        raw = body if isinstance(body, str) else json.dumps(body)
        self._body = raw.encode("utf-8")
        self.status = status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _FakeOpener:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.requests = []

    def open(self, request, timeout=None):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return self.response

    @property
    def request(self):
        return self.requests[-1]

    @property
    def sent(self):
        return json.loads(self.request.data)


def http_error(status, body="{}"):
    error = HTTPError(url=BASE_URL, code=status, msg="error", hdrs=Message(), fp=None)
    error.read = lambda: body.encode("utf-8")
    return error


def build(cls, body=None, status=200, error=None):
    service = cls(api_key="test-private-key", base_url=BASE_URL, silent=True)
    service._opener = _FakeOpener(
        response=None if error else _FakeResponse(body or {"id": "abc"}, status),
        error=error,
    )
    return service


@pytest.fixture(params=[TemplateService, WorkloadService], ids=["template", "workload"])
def service_cls(request):
    return request.param


ENDPOINTS = {
    TemplateService: "/api/v2/llm_request_templates/by_key/",
    WorkloadService: "/api/v2/workloads/by_key/",
}


class TestRequestShape:
    def test_put_to_by_key_with_json_and_key_header(self, service_cls):
        service = build(service_cls)
        service.upsert("Parser", name="Parser")

        request = service._opener.request
        assert request.get_method() == "PUT"
        assert request.full_url == f"{BASE_URL}{ENDPOINTS[service_cls]}Parser"
        assert request.get_header("X-api-key") == "test-private-key"
        assert request.get_header("Content-type") == "application/json"

    def test_key_escaped_per_segment_slash_literal(self, service_cls):
        service = build(service_cls)
        service.upsert("Parser#call/squish:L2")

        assert service._opener.request.full_url.endswith(
            "by_key/Parser%23call/squish%3AL2"
        )

    @pytest.mark.parametrize(
        ("key", "encoded"),
        [
            ("a?b", "a%3Fb"),
            ("100%/x", "100%25/x"),
            ("a\\b", "a%5Cb"),
            ("a b/c d", "a%20b/c%20d"),
            ("a/b c", "a/b%20c"),
        ],
    )
    def test_reserved_characters_escaped_per_segment(self, service_cls, key, encoded):
        service = build(service_cls)
        service.upsert(key)
        assert service._opener.request.full_url.endswith(f"by_key/{encoded}")

    def test_non_ascii_key_percent_encoded(self, service_cls):
        service = build(service_cls)
        service.upsert("Parser#call · L2")

        assert service._opener.request.full_url.endswith(
            "by_key/Parser%23call%20%C2%B7%20L2"
        )

    def test_only_given_fields_are_sent(self, service_cls):
        service = build(service_cls)
        service.upsert("k")
        assert service._opener.sent == {}

        service.upsert("k", name="N", reporter="app")
        assert service._opener.sent == {"name": "N", "reporter": "app"}

    def test_workload_description_sent(self):
        service = build(WorkloadService)
        service.upsert("W", name="N", description="D", reporter="app")
        assert service._opener.sent == {
            "name": "N",
            "description": "D",
            "reporter": "app",
        }

    def test_template_workload_id_flattened(self):
        service = build(TemplateService)
        service.upsert("W/t", workload={"id": "wl123"})
        assert service._opener.sent == {"workload_id": "wl123"}

    def test_template_workload_key_flattened(self):
        service = build(TemplateService)
        service.upsert("W/t", workload={"key": "W"}, name="N")
        assert service._opener.sent == {"workload_key": "W", "name": "N"}

    def test_template_workload_id_preferred_over_key(self):
        service = build(TemplateService)
        service.upsert("W/t", workload={"id": "wl123", "key": "W"})
        assert service._opener.sent == {"workload_id": "wl123"}

    def test_template_blank_workload_id_falls_back_to_key(self):
        service = build(TemplateService)
        service.upsert("W/t", workload={"id": " ", "key": "W"})
        assert service._opener.sent == {"workload_key": "W"}


class TestResponses:
    @pytest.mark.parametrize("status", [200, 201])
    def test_returns_record_for_created_and_updated(self, service_cls, status):
        service = build(service_cls, {"id": "abc", "name": "N"}, status=status)
        assert service.upsert("k") == {"id": "abc", "name": "N"}

    @pytest.mark.parametrize("status", [401, 404, 422, 500])
    def test_http_errors_raise_with_status(self, service_cls, status):
        service = build(service_cls, error=http_error(status, '{"error": "nope"}'))
        with pytest.raises(CoolhandAPIError) as raised:
            service.upsert("k")
        assert raised.value.status == status
        assert "nope" in str(raised.value)

    def test_transport_error_has_no_status(self, service_cls):
        service = build(service_cls, error=URLError("unreachable"))
        with pytest.raises(CoolhandAPIError) as raised:
            service.upsert("k")
        assert raised.value.status is None

    @pytest.mark.parametrize(
        "error",
        [
            TimeoutError("read timed out"),
            ConnectionResetError("reset"),
            http.client.IncompleteRead(b""),
            UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad"),
        ],
        ids=["timeout", "reset", "incomplete-read", "undecodable"],
    )
    def test_unwrapped_transport_failures_become_api_errors(self, service_cls, error):
        service = build(service_cls, error=error)
        with pytest.raises(CoolhandAPIError) as raised:
            service.upsert("k")
        assert raised.value.status is None

    @pytest.mark.parametrize("body", ["not json", "[]"])
    def test_non_object_body_raises(self, service_cls, body):
        service = build(service_cls, body)
        with pytest.raises(CoolhandAPIError):
            service.upsert("k")


class TestValidation:
    """Every case raises before a request is built, let alone sent."""

    @pytest.mark.parametrize(
        "key",
        ["", "  ", ".", "..", "a/./b", "a/../b", "/a", "a/", "a//b", " a", 5, None],
    )
    def test_bad_keys(self, service_cls, key):
        service = build(service_cls)
        with pytest.raises(ValueError):
            service.upsert(key)
        assert service._opener.requests == []

    @pytest.mark.parametrize(
        "workload", ["W", {}, {"id": " "}, {"name": "n"}, {"id": 5}]
    )
    def test_bad_template_workload(self, workload):
        service = build(TemplateService)
        with pytest.raises(ValueError, match="workload"):
            service.upsert("W/t", workload=workload)
        assert service._opener.requests == []

    @pytest.mark.parametrize("field", ["name", "reporter"])
    def test_non_string_fields(self, service_cls, field):
        service = build(service_cls)
        with pytest.raises(ValueError, match=field):
            service.upsert("k", **{field: 5})
        assert service._opener.requests == []

    @pytest.mark.parametrize("reporter", ["", "  "])
    def test_blank_reporter(self, service_cls, reporter):
        service = build(service_cls)
        with pytest.raises(ValueError, match="reporter"):
            service.upsert("k", reporter=reporter)
        assert service._opener.requests == []

    def test_non_string_description(self):
        service = build(WorkloadService)
        with pytest.raises(ValueError, match="description"):
            service.upsert("k", description=5)
        assert service._opener.requests == []

    def test_non_string_name(self, service_cls):
        service = build(service_cls)
        with pytest.raises(ValueError, match="name"):
            service.upsert("k", name=5)
        assert service._opener.requests == []

    def test_description_too_long(self):
        service = build(WorkloadService)
        with pytest.raises(ValueError, match="description"):
            service.upsert("k", description="d" * 1001)
        assert service._opener.requests == []


class TestAsyncAndWiring:
    @pytest.mark.asyncio
    async def test_aupsert_matches_upsert(self, service_cls):
        service = build(service_cls, {"id": "abc"}, status=201)
        assert await service.aupsert("k", name="N") == {"id": "abc"}
        assert service._opener.sent == {"name": "N"}

    def test_upserts_use_urllib_which_the_capture_patches_never_touch(
        self, service_cls
    ):
        """Capture patches httpx and requests only, so a urllib opener is never seen."""
        from urllib.request import OpenerDirector

        service = service_cls(api_key="k", base_url=BASE_URL)
        assert isinstance(service._opener, OpenerDirector)

    def test_coolhand_exposes_workload_service(
        self, mock_config, reset_global_instance
    ):
        instance = Coolhand(config=mock_config)
        assert isinstance(instance.workload_service, WorkloadService)
        assert instance.workload_service.api_key == mock_config["api_key"]
