"""Tests for LogService."""

from datetime import datetime, timezone
from urllib.parse import urlparse

import pytest

from coolhand import Coolhand, CoolhandAPIError, LogService
from coolhand.log_service import LOGS_ENDPOINT

from .fake_transport import (
    BASE_URL,
    PAGINATION_HEADERS,
    FakeOpener,
    FakeResponse,
    query_of,
)
from .fake_transport import build_service as fake_build_service
from .fake_transport import http_error as fake_http_error

LOG_ROW = {
    "id": "log123abc456",
    "collector": "coolhand-python-0.8.0-auto",
    "source_api": "openai",
    "source_application": None,
    "metadata": {},
    "source_api_result": "success",
    "model": "gpt-4o",
    "template_id": None,
    "template_name": None,
    "input_tokens": 100,
    "output_tokens": 50,
    "latency_ms": 250,
    "created_at": "2026-09-01T00:00:00Z",
    "updated_at": "2026-09-01T00:00:00Z",
    "ingest_evidence": {},
    "cost": 0.0042,
}

LOG_CONTENT = {
    **LOG_ROW,
    "url": "/c/1/llm_request_logs/log123abc456",
    "cost_breakdown": {
        "total_cost": 0.0042,
        "input_cost": 0.003,
        "output_cost": 0.0012,
        "cached_input_cost": 0.0,
        "cache_creation_input_cost": 0.0,
        "reasoning_output_cost": 0.0,
    },
    "system_prompt": "Be brief.",
    "user_prompt": "Hi",
    "output": "Hello",
}

# The endpoint only sends X-Page / X-Per-Page unless include_total=true.
PAGE_ONLY_HEADERS = {"X-Page": "1", "X-Per-Page": "2"}


def build_service(body=None, headers=None, error=None, **config):
    return fake_build_service(LogService, body, headers, error, **config)


def http_error(status, body):
    return fake_http_error(f"{BASE_URL}{LOGS_ENDPOINT}", status, body)


class TestSearchLogsRequest:
    def test_targets_the_list_endpoint(self):
        service = build_service([], PAGINATION_HEADERS)

        service.search_logs()

        request = service._opener.request
        parsed = urlparse(request.full_url)
        assert parsed.path == LOGS_ENDPOINT
        assert parsed.query == ""
        assert request.get_method() == "GET"
        assert request.get_header("X-api-key") == "test-private-key"

    def test_maps_every_filter_onto_its_wire_param(self):
        service = build_service([], PAGINATION_HEADERS)

        service.search_logs(
            template_id="tmpl1",
            workload_id="wkld1",
            system_prompt_contains="sys",
            user_prompt_contains="usr",
            model="gpt-4o",
            source_api="openai",
            source_api_result="failed",
            project_path="/work/app",
            unmatched_only=True,
            days_back=7,
            since=datetime(2026, 9, 1, tzinfo=timezone.utc),
            until="2026-10-01",
            min_cost=0.5,
            order="cost_desc",
            include_prompts=True,
            sort="created_at desc",
            include_total=True,
            page=2,
            per=50,
        )

        assert query_of(service._opener.request) == {
            "template_id": ["tmpl1"],
            "workload_id": ["wkld1"],
            "system_prompt_contains": ["sys"],
            "user_prompt_contains": ["usr"],
            "model": ["gpt-4o"],
            "source_api": ["openai"],
            "source_api_result": ["failed"],
            "project_path": ["/work/app"],
            "unmatched_only": ["true"],
            "days_back": ["7"],
            "since": ["2026-09-01T00:00:00Z"],
            "until": ["2026-10-01"],
            "min_cost": ["0.5"],
            "order": ["cost_desc"],
            "include_prompts": ["true"],
            "q[s]": ["created_at desc"],
            "include_total": ["true"],
            "page": ["2"],
            "per": ["50"],
        }

    def test_min_cost_zero_is_sent(self):
        service = build_service([], PAGINATION_HEADERS)

        service.search_logs(min_cost=0, include_prompts=False)

        assert query_of(service._opener.request) == {
            "min_cost": ["0"],
            "include_prompts": ["false"],
        }

    def test_a_plus_offset_is_encoded_as_percent_2b(self):
        service = build_service([], PAGINATION_HEADERS)

        service.search_logs(since="2026-09-01T00:00:00+02:00")

        assert "%2B02%3A00" in service._opener.request.full_url

    def test_rejects_a_bad_bound_before_any_request(self):
        service = build_service([], PAGINATION_HEADERS)

        with pytest.raises(ValueError, match="since must be a datetime"):
            service.search_logs(since=3)

        assert service._opener.request is None


class TestSearchLogsResponse:
    def test_returns_the_bare_array_with_cost(self):
        service = build_service([LOG_ROW, {**LOG_ROW, "cost": None}], PAGE_ONLY_HEADERS)

        logs = service.search_logs(per=2)["logs"]

        assert [log["cost"] for log in logs] == [0.0042, None]

    def test_totals_come_from_headers_when_include_total_was_sent(self):
        headers = {
            "X-Page": "2",
            "X-Per-Page": "10",
            "X-Total-Count": "25",
            "X-Total-Pages": "3",
        }
        service = build_service([LOG_ROW], headers)

        pagination = service.search_logs(page=2, per=10, include_total=True)[
            "pagination"
        ]

        assert pagination == {
            "current_page": 2,
            "per_page": 10,
            "total_count": 25,
            "total_pages": 3,
            "has_next_page": True,
            "has_prev_page": True,
        }

    def test_totals_are_none_not_guessed_without_the_total_headers(self):
        service = build_service([LOG_ROW], PAGE_ONLY_HEADERS)

        pagination = service.search_logs(per=2)["pagination"]

        assert pagination["total_count"] is None
        assert pagination["total_pages"] is None
        assert pagination["current_page"] == 1
        assert pagination["per_page"] == 2

    def test_a_full_page_without_totals_may_have_a_next_page(self):
        service = build_service([LOG_ROW, LOG_ROW], PAGE_ONLY_HEADERS)

        pagination = service.search_logs(per=2)["pagination"]

        assert pagination["has_next_page"] is True
        assert pagination["has_prev_page"] is False

    def test_a_short_page_without_totals_has_no_next_page(self):
        service = build_service([LOG_ROW], PAGE_ONLY_HEADERS)

        assert service.search_logs(per=2)["pagination"]["has_next_page"] is False

    def test_has_prev_page_follows_the_page_number(self):
        service = build_service([], {"X-Page": "3", "X-Per-Page": "2"})

        pagination = service.search_logs(page=3, per=2)["pagination"]

        assert pagination["has_prev_page"] is True
        assert pagination["has_next_page"] is False

    def test_missing_total_pages_is_derived_from_the_count(self):
        headers = {"X-Page": "1", "X-Per-Page": "10", "X-Total-Count": "25"}
        service = build_service([LOG_ROW], headers)

        pagination = service.search_logs(include_total=True)["pagination"]

        assert pagination["total_pages"] == 3

    def test_a_non_array_body_raises(self):
        service = build_service({"logs": []}, PAGE_ONLY_HEADERS)

        with pytest.raises(CoolhandAPIError, match="not a JSON array") as caught:
            service.search_logs()

        assert caught.value.status is None


class TestGetLog:
    def test_targets_the_show_endpoint(self):
        service = build_service(LOG_CONTENT)

        service.get_log("log123abc456")

        parsed = urlparse(service._opener.request.full_url)
        assert parsed.path == f"{LOGS_ENDPOINT}/log123abc456"
        assert parsed.query == ""

    def test_maps_the_options_onto_the_wire(self):
        service = build_service(LOG_CONTENT)

        service.get_log(
            "log123abc456", section="end", max_chars=2000, include_thinking=False
        )

        assert query_of(service._opener.request) == {
            "section": ["end"],
            "max_chars": ["2000"],
            "include_thinking": ["false"],
        }

    def test_sends_a_search_query(self):
        service = build_service({"id": "log123abc456", "matches": {}})

        service.get_log("log123abc456", search_query="timeout")

        assert query_of(service._opener.request) == {"search_query": ["timeout"]}

    def test_returns_cost_and_the_breakdown(self):
        service = build_service(LOG_CONTENT)

        log = service.get_log("log123abc456")

        assert log["cost"] == 0.0042
        assert log["cost_breakdown"]["input_cost"] == 0.003

    def test_cost_fields_may_be_null(self):
        service = build_service({**LOG_CONTENT, "cost": None, "cost_breakdown": None})

        log = service.get_log("log123abc456")

        assert log["cost"] is None
        assert log["cost_breakdown"] is None

    def test_url_encodes_the_id(self):
        service = build_service(LOG_CONTENT)

        service.get_log("a b/c?d")

        parsed = urlparse(service._opener.request.full_url)
        assert parsed.path == f"{LOGS_ENDPOINT}/a%20b%2Fc%3Fd"
        assert parsed.query == ""

    @pytest.mark.parametrize("log_id", ["", "  ", ".", "..", " .. "])
    def test_rejects_an_id_that_is_blank_or_a_dot_segment(self, log_id):
        service = build_service(LOG_CONTENT)

        with pytest.raises(ValueError, match="log_id"):
            service.get_log(log_id)

        assert service._opener.request is None

    @pytest.mark.parametrize("query", ["", "   "])
    def test_rejects_a_blank_search_query(self, query):
        service = build_service(LOG_CONTENT)

        with pytest.raises(ValueError, match="search_query"):
            service.get_log("log123abc456", search_query=query)

        assert service._opener.request is None

    def test_a_non_object_body_raises(self):
        service = build_service([])

        with pytest.raises(CoolhandAPIError, match="not a JSON object"):
            service.get_log("log123abc456")


class TestErrors:
    @pytest.mark.parametrize(
        ("status", "body"),
        [
            (401, '{"error":"Unauthorized"}'),
            (404, '{"errors":{"base":["not found"]}}'),
            (422, '{"errors":{"min_cost":["must be >= 0"]}}'),
            (504, '{"errors":{"system":["timed out"]}}'),
        ],
    )
    def test_a_non_2xx_raises_with_the_status_and_body(self, status, body):
        service = build_service(error=http_error(status, body))

        with pytest.raises(CoolhandAPIError) as caught:
            service.search_logs(min_cost=-1)

        assert caught.value.status == status
        assert body in str(caught.value)

    def test_get_log_raises_with_the_status_too(self):
        service = build_service(error=http_error(404, '{"errors":{}}'))

        with pytest.raises(CoolhandAPIError) as caught:
            service.get_log("missing")

        assert caught.value.status == 404


class TestCoolhandDelegation:
    def test_exposes_the_log_service(self, mock_config, reset_global_instance):
        instance = Coolhand(config=mock_config)

        assert isinstance(instance.log_service, LogService)

    def test_search_logs_delegates_with_its_filters(
        self, mock_config, reset_global_instance
    ):
        instance = Coolhand(config=mock_config)
        instance._log_service._opener = FakeOpener(
            response=FakeResponse([LOG_ROW], PAGE_ONLY_HEADERS)
        )

        result = instance.search_logs(min_cost=1, order="cost_desc", sort="id asc")

        assert query_of(instance._log_service._opener.request) == {
            "min_cost": ["1"],
            "order": ["cost_desc"],
            "q[s]": ["id asc"],
        }
        assert result["logs"] == [LOG_ROW]

    def test_get_log_delegates(self, mock_config, reset_global_instance):
        instance = Coolhand(config=mock_config)
        instance._log_service._opener = FakeOpener(response=FakeResponse(LOG_CONTENT))

        log = instance.get_log("log123abc456", max_chars=10)

        assert log["id"] == "log123abc456"
        assert query_of(instance._log_service._opener.request) == {"max_chars": ["10"]}
