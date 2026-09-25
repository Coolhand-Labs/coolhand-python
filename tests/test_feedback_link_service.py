"""Tests for FeedbackLinkService."""

import json
from email.message import Message
from urllib.error import HTTPError, URLError

import pytest

from coolhand import Coolhand, CoolhandAPIError, FeedbackLinkService
from coolhand.feedback_link_service import BULK_LINK_BATCH_SIZE

BASE_URL = "https://test.coolhandlabs.com"

LINK_BODY = {
    "id": "link123abc",
    "optimization_id": "opt123",
    "feedback_id": "fb123",
    "note": "why",
    "created_at": "2026-08-01T00:00:00Z",
}


class _FakeResponse:
    def __init__(self, body):
        raw = body if isinstance(body, str) else json.dumps(body)
        self._body = raw.encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _FakeOpener:
    """Records every Request; answers with queued responses, or raises the error."""

    def __init__(self, responses=None, error=None):
        self.responses = list(responses or [])
        self.error = error
        self.requests = []
        self.timeout = None

    def open(self, request, timeout=None):
        self.requests.append(request)
        self.timeout = timeout
        if self.error is not None:
            raise self.error
        return self.responses.pop(0)

    @property
    def request(self):
        return self.requests[-1]


def build_service(*bodies, error=None, **config):
    settings = {"api_key": "test-private-key", "base_url": BASE_URL, "silent": True}
    settings.update(config)
    service = FeedbackLinkService(**settings)
    service._opener = _FakeOpener([_FakeResponse(b) for b in bodies], error)
    return service


def http_error(status, body):
    error = HTTPError(url=BASE_URL, code=status, msg="error", hdrs=Message(), fp=None)
    error.read = lambda: body.encode("utf-8")
    return error


def bulk_result(linked=0, already_linked=0, errored=0, not_found=()):
    return {
        "linked": linked,
        "already_linked": already_linked,
        "errored": errored,
        "not_found": list(not_found),
    }


def sent(request):
    return json.loads(request.data)


class TestLinkFeedback:
    def test_posts_feedback_id_to_the_links_route(self):
        service = build_service(LINK_BODY)

        service.link_feedback("opt123", "fb123")

        request = service._opener.request
        assert request.get_method() == "POST"
        assert (
            request.full_url == f"{BASE_URL}/api/v2/optimizations/opt123/feedback_links"
        )
        assert sent(request) == {"feedback_id": "fb123"}

    def test_sends_private_key_header_and_json_content_type(self):
        service = build_service(LINK_BODY)

        service.link_feedback("opt123", "fb123")

        request = service._opener.request
        assert request.get_header("X-api-key") == "test-private-key"
        assert request.get_header("Content-type") == "application/json"

    def test_includes_note_when_given(self):
        service = build_service(LINK_BODY)

        service.link_feedback("opt123", "fb123", note="why")

        assert sent(service._opener.request) == {"feedback_id": "fb123", "note": "why"}

    def test_returns_the_link(self):
        service = build_service(LINK_BODY)

        assert service.link_feedback("opt123", "fb123") == LINK_BODY

    def test_encodes_the_optimization_id_into_one_path_segment(self):
        service = build_service(LINK_BODY)

        service.link_feedback("a/b?c", "fb123")

        assert (
            "/optimizations/a%2Fb%3Fc/feedback_links"
            in service._opener.request.full_url
        )

    @pytest.mark.parametrize("bad", ["", "  ", ".", ".."])
    def test_rejects_bad_optimization_id_before_any_request(self, bad):
        service = build_service()

        with pytest.raises(ValueError):
            service.link_feedback(bad, "fb123")

        assert service._opener.requests == []

    @pytest.mark.parametrize("bad", ["", "  ", None])
    def test_rejects_blank_feedback_id_before_any_request(self, bad):
        service = build_service()

        with pytest.raises(ValueError):
            service.link_feedback("opt123", bad)

        assert service._opener.requests == []

    def test_422_raises_with_status(self):
        service = build_service(error=http_error(422, '{"errors":["taken"]}'))

        with pytest.raises(CoolhandAPIError) as excinfo:
            service.link_feedback("opt123", "fb123")

        assert excinfo.value.status == 422
        assert "taken" in str(excinfo.value)

    def test_transport_failure_has_no_status(self):
        service = build_service(error=URLError("refused"))

        with pytest.raises(CoolhandAPIError) as excinfo:
            service.link_feedback("opt123", "fb123")

        assert excinfo.value.status is None

    def test_non_json_body_raises(self):
        service = build_service("<html>")

        with pytest.raises(CoolhandAPIError):
            service.link_feedback("opt123", "fb123")

    def test_non_object_body_raises(self):
        service = build_service([1])

        with pytest.raises(CoolhandAPIError):
            service.link_feedback("opt123", "fb123")


class TestBulkLinkFeedback:
    def test_posts_feedback_ids_to_the_same_route(self):
        service = build_service(bulk_result(linked=2))

        result = service.bulk_link_feedback("opt123", ["a", "b"], note="n")

        request = service._opener.request
        assert (
            request.full_url == f"{BASE_URL}/api/v2/optimizations/opt123/feedback_links"
        )
        assert sent(request) == {"feedback_ids": ["a", "b"], "note": "n"}
        assert result == bulk_result(linked=2)

    def test_omits_note_when_not_given(self):
        service = build_service(bulk_result(linked=1))

        service.bulk_link_feedback("opt123", ["a"])

        assert "note" not in sent(service._opener.request)

    def test_exactly_one_batch_at_the_cap(self):
        ids = [f"id{i}" for i in range(BULK_LINK_BATCH_SIZE)]
        service = build_service(bulk_result(linked=BULK_LINK_BATCH_SIZE))

        service.bulk_link_feedback("opt123", ids)

        assert len(service._opener.requests) == 1

    def test_chunks_and_merges_results(self):
        ids = [f"id{i}" for i in range(250)]
        service = build_service(
            bulk_result(linked=98, already_linked=1, errored=1, not_found=["x"]),
            bulk_result(linked=100),
            bulk_result(linked=40, already_linked=5, not_found=["y", "z"]),
        )

        result = service.bulk_link_feedback("opt123", ids, note="n")

        batches = [sent(r)["feedback_ids"] for r in service._opener.requests]
        assert [len(b) for b in batches] == [100, 100, 50]
        assert sum(batches, []) == ids
        assert all(sent(r)["note"] == "n" for r in service._opener.requests)
        assert result == bulk_result(
            linked=238, already_linked=6, errored=1, not_found=["x", "y", "z"]
        )

    def test_does_not_dedupe_ids(self):
        service = build_service(bulk_result(linked=1, already_linked=1))

        service.bulk_link_feedback("opt123", ["a", "a"])

        assert sent(service._opener.request)["feedback_ids"] == ["a", "a"]

    @pytest.mark.parametrize("bad", [[], None, "abc"])
    def test_rejects_empty_or_non_list_before_any_request(self, bad):
        service = build_service()

        with pytest.raises(ValueError):
            service.bulk_link_feedback("opt123", bad)

        assert service._opener.requests == []

    @pytest.mark.parametrize("bad_id", ["", "  ", None, 5])
    def test_rejects_blank_id_before_any_request(self, bad_id):
        service = build_service()

        with pytest.raises(ValueError):
            service.bulk_link_feedback("opt123", ["a", bad_id])

        assert service._opener.requests == []

    def test_rejects_bad_optimization_id_before_any_request(self):
        service = build_service()

        with pytest.raises(ValueError):
            service.bulk_link_feedback("..", ["a"])

        assert service._opener.requests == []

    def test_later_batch_failure_raises_after_earlier_batches_were_sent(self):
        ids = [f"id{i}" for i in range(150)]
        service = build_service(bulk_result(linked=100))
        original_open = service._opener.open

        def open_then_fail(request, timeout=None):
            if service._opener.requests:
                raise http_error(422, '{"errors":{"feedback_ids":["bad"]}}')
            return original_open(request, timeout)

        service._opener.open = open_then_fail

        with pytest.raises(CoolhandAPIError) as excinfo:
            service.bulk_link_feedback("opt123", ids)

        assert excinfo.value.status == 422
        assert len(service._opener.requests) == 1


class TestUnlinkFeedback:
    def test_deletes_the_link_by_id(self):
        service = build_service("")

        result = service.unlink_feedback("opt123", "link123abc")

        request = service._opener.request
        assert request.get_method() == "DELETE"
        assert request.full_url == (
            f"{BASE_URL}/api/v2/optimizations/opt123/feedback_links/link123abc"
        )
        assert request.get_header("X-api-key") == "test-private-key"
        assert result is None

    def test_encodes_the_link_id(self):
        service = build_service("")

        service.unlink_feedback("opt123", "a/b")

        assert service._opener.request.full_url.endswith("/feedback_links/a%2Fb")

    @pytest.mark.parametrize("bad", ["", " ", ".", ".."])
    def test_rejects_bad_link_id_before_any_request(self, bad):
        service = build_service()

        with pytest.raises(ValueError):
            service.unlink_feedback("opt123", bad)

        assert service._opener.requests == []

    def test_404_raises_with_status(self):
        service = build_service(error=http_error(404, '{"error":"not found"}'))

        with pytest.raises(CoolhandAPIError) as excinfo:
            service.unlink_feedback("opt123", "nope")

        assert excinfo.value.status == 404


class TestCoolhandFacade:
    def test_delegates_to_the_service(self):
        ch = Coolhand(api_key="k", base_url=BASE_URL, silent=True)
        try:
            ch.feedback_link_service._opener = _FakeOpener(
                [_FakeResponse(LINK_BODY), _FakeResponse(bulk_result(linked=1))]
            )

            assert ch.link_feedback("opt123", "fb123")["id"] == "link123abc"
            assert ch.bulk_link_feedback("opt123", ["a"])["linked"] == 1
        finally:
            ch.stop_monitoring()
