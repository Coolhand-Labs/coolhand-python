"""Tests for WorkloadService."""

from datetime import datetime, timezone
from urllib.parse import urlparse

import pytest

from coolhand import Coolhand, CoolhandAPIError, WorkloadService
from coolhand.workload_service import WORKLOADS_ENDPOINT

from .fake_transport import (
    BASE_URL,
    PAGINATION_HEADERS,
    FakeOpener,
    FakeResponse,
    query_of,
)
from .fake_transport import build_service as fake_build_service
from .fake_transport import http_error as fake_http_error

METRICS = {
    "days_back": None,
    "since": "2026-09-01T00:00:00Z",
    "until": "2026-10-01T00:00:00Z",
    "request_count": 10,
    "failure_count": 2,
    "error_rate": 0.2,
    "error_rate_change": None,
    "avg_cost_per_request": 0.01,
    "total_cost": 0.08,
    "priced_request_count": 8,
    "long_context_request_count": 1,
    "total_input_tokens": 1000,
    "total_output_tokens": 500,
    "avg_input_tokens": 125,
    "avg_output_tokens": 62,
    "avg_latency_ms": 250.5,
    "correctness_score": None,
    "sentiment_score": None,
    "revision_score": None,
    "first_request_at": "2026-08-01T00:00:00Z",
    "last_request_at": "2026-09-30T00:00:00Z",
}

WORKLOAD_ROW = {
    "id": "j35494sql6yd",
    "name": "Agent Engineering",
    "description": None,
    "archived": False,
    "system": False,
    "merged": False,
    "template_count": 2,
    "draft_template_count": 1,
    "log_count": 160,
    "last_activity": "2026-08-25T20:03:47Z",
}


ALL_ARGS = {
    "search": "agent",
    "include_archived": True,
    "include_system": False,
    "include_templates": True,
    "include_metrics": True,
    "days_back": 30,
    "since": datetime(2026, 9, 1, tzinfo=timezone.utc),
    "until": "2026-10-01T00:00:00Z",
    "page": 2,
    "per": 50,
}

ALL_QUERY = {
    "search": ["agent"],
    "include_archived": ["true"],
    "include_system": ["false"],
    "include_templates": ["true"],
    "include_metrics": ["true"],
    "days_back": ["30"],
    "since": ["2026-09-01T00:00:00Z"],
    "until": ["2026-10-01T00:00:00Z"],
    "page": ["2"],
    "per": ["50"],
}


def build_service(body=None, headers=None, error=None, **config):
    return fake_build_service(WorkloadService, body, headers, error, **config)


def http_error(status, body):
    return fake_http_error(f"{BASE_URL}{WORKLOADS_ENDPOINT}", status, body)


class TestSearchWorkloadsRequest:
    def test_targets_the_list_endpoint_with_the_private_key(self):
        service = build_service([], PAGINATION_HEADERS)

        service.search_workloads()

        request = service._opener.request
        parsed = urlparse(request.full_url)
        assert parsed.path == WORKLOADS_ENDPOINT
        assert parsed.query == ""
        assert request.get_method() == "GET"
        assert request.get_header("X-api-key") == "test-private-key"

    def test_maps_every_param_onto_its_wire_name(self):
        service = build_service([], PAGINATION_HEADERS)

        service.search_workloads(**ALL_ARGS)

        assert query_of(service._opener.request) == ALL_QUERY

    def test_sends_per_not_per_page(self):
        service = build_service([], PAGINATION_HEADERS)

        service.search_workloads(per=10)

        query = query_of(service._opener.request)
        assert query["per"] == ["10"]
        assert "per_page" not in query

    def test_a_plus_offset_is_encoded_as_percent_2b(self):
        service = build_service([], PAGINATION_HEADERS)

        service.search_workloads(
            include_metrics=True, since="2026-09-01T00:00:00+02:00"
        )

        assert "%2B02%3A00" in service._opener.request.full_url

    def test_rejects_a_bad_bound_before_any_request(self):
        service = build_service([], PAGINATION_HEADERS)

        with pytest.raises(ValueError, match="until must be a datetime"):
            service.search_workloads(until=20260901)

        assert service._opener.request is None


class TestSearchWorkloadsResponse:
    def test_returns_the_bare_array_and_header_pagination(self):
        headers = {
            "X-Page": "2",
            "X-Per-Page": "10",
            "X-Total-Count": "25",
            "X-Total-Pages": "3",
        }
        service = build_service([WORKLOAD_ROW], headers)

        result = service.search_workloads(page=2, per=10)

        assert result["workloads"] == [WORKLOAD_ROW]
        assert result["workloads"][0]["id"] == "j35494sql6yd"
        assert result["pagination"] == {
            "current_page": 2,
            "per_page": 10,
            "total_count": 25,
            "total_pages": 3,
            "has_next_page": True,
            "has_prev_page": True,
        }

    def test_pagination_is_not_computed_from_the_page_length(self):
        headers = {**PAGINATION_HEADERS, "X-Total-Count": "500", "X-Total-Pages": "20"}
        service = build_service([WORKLOAD_ROW], headers)

        pagination = service.search_workloads()["pagination"]

        assert pagination["total_count"] == 500
        assert pagination["total_pages"] == 20

    def test_returns_metrics_including_the_nullable_days_back(self):
        service = build_service(
            [{**WORKLOAD_ROW, "metrics": METRICS}], PAGINATION_HEADERS
        )

        metrics = service.search_workloads(include_metrics=True)["workloads"][0][
            "metrics"
        ]

        assert metrics["days_back"] is None
        assert metrics["failure_count"] == 2
        assert metrics["total_input_tokens"] == 1000

    def test_a_non_array_body_raises(self):
        service = build_service({"errors": {}}, PAGINATION_HEADERS)

        with pytest.raises(CoolhandAPIError, match="not a JSON array") as caught:
            service.search_workloads()

        assert caught.value.status is None


class TestErrors:
    @pytest.mark.parametrize(
        ("status", "body"),
        [
            (401, '{"error":"Unauthorized"}'),
            (422, '{"errors":{"since":["is invalid"]}}'),
            (504, '{"errors":{"system":["timed out"]}}'),
        ],
    )
    def test_a_non_2xx_raises_with_the_status_and_body(self, status, body):
        service = build_service(error=http_error(status, body))

        with pytest.raises(CoolhandAPIError) as caught:
            service.search_workloads()

        assert caught.value.status == status
        assert body in str(caught.value)

    def test_a_non_json_body_raises_without_a_status(self):
        service = build_service("<html>", PAGINATION_HEADERS)

        with pytest.raises(CoolhandAPIError, match="not valid JSON") as caught:
            service.search_workloads()

        assert caught.value.status is None


class TestCoolhandDelegation:
    def test_exposes_the_workload_service(self, mock_config, reset_global_instance):
        instance = Coolhand(config=mock_config)

        assert isinstance(instance.workload_service, WorkloadService)

    def test_search_workloads_delegates_every_param(
        self, mock_config, reset_global_instance
    ):
        instance = Coolhand(config=mock_config)
        instance._workload_service._opener = FakeOpener(
            response=FakeResponse([WORKLOAD_ROW], PAGINATION_HEADERS)
        )

        result = instance.search_workloads(**ALL_ARGS)

        assert query_of(instance._workload_service._opener.request) == ALL_QUERY
        assert result["workloads"] == [WORKLOAD_ROW]
