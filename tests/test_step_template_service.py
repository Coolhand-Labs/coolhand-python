"""Tests for StepTemplateService."""

from urllib.parse import urlparse

import pytest

from coolhand import Coolhand, CoolhandAPIError, StepTemplateService
from coolhand.step_template_service import STEP_TEMPLATES_ENDPOINT

from .fake_transport import (
    BASE_URL,
    PAGINATION_HEADERS,
    FakeOpener,
    FakeResponse,
    query_of,
)
from .fake_transport import build_service as fake_build_service
from .fake_transport import http_error as fake_http_error

COHORT = {
    "runs": 2,
    "avg_turns_after": 1.5,
    "avg_tool_calls": 3.0,
    "avg_requests": 4.0,
    "token_runs": 0,
    "tokens_available": False,
    "median_tokens": None,
    "p90_tokens": None,
    "tokens_per_tool_call": None,
}

METRICS = {
    "total_runs": 2,
    "tool_stats": [{"name": "Read", "pct_of_runs": 100.0, "avg_per_run": 2.5}],
    "turn_stats": {"avg": 1.5, "median": 1.5, "high": 2, "single_turn_pct": None},
    "efficiency": {
        "interactive": COHORT,
        "automated": None,
        "all": COHORT,
        "unavailable_runs": 0,
    },
    "notes": ["Token figures are unavailable."],
}

ROW = {
    "id": 14,
    "name": "/review",
    "step_type": "prompt_step",
    "status": "published",
    "user_turn_pattern": None,
    "created_at": "2026-10-01T00:00:00.000Z",
    "updated_at": "2026-10-02T00:00:00.000Z",
    "sessions_count": 2,
    "occurrences_count": 3,
    "last_seen_at": "2026-10-02T00:00:00.000Z",
}


def build_service(body=None, headers=None, error=None, **config):
    return fake_build_service(StepTemplateService, body, headers, error, **config)


def http_error(status, body):
    return fake_http_error(f"{BASE_URL}{STEP_TEMPLATES_ENDPOINT}", status, body)


class TestListStepTemplatesRequest:
    def test_targets_the_list_endpoint_with_the_private_key(self):
        service = build_service([], PAGINATION_HEADERS)

        service.list_step_templates()

        request = service._opener.request
        parsed = urlparse(request.full_url)
        assert parsed.path == STEP_TEMPLATES_ENDPOINT
        assert parsed.query == ""
        assert request.get_method() == "GET"
        assert request.get_header("X-api-key") == "test-private-key"

    def test_maps_every_filter_onto_its_wire_param(self):
        service = build_service([], PAGINATION_HEADERS)

        service.list_step_templates(
            search="rev",
            step_type="tool_step",
            include_metrics=True,
            page=2,
            per=10,
        )

        assert query_of(service._opener.request) == {
            "search": ["rev"],
            "step_type": ["tool_step"],
            "include_metrics": ["true"],
            "page": ["2"],
            "per": ["10"],
        }

    def test_include_metrics_false_is_sent_lowercase(self):
        service = build_service([], PAGINATION_HEADERS)

        service.list_step_templates(include_metrics=False)

        assert query_of(service._opener.request) == {"include_metrics": ["false"]}

    def test_sends_per_not_per_page(self):
        service = build_service([], PAGINATION_HEADERS)

        service.list_step_templates(per=10)

        assert "per_page" not in query_of(service._opener.request)


class TestListStepTemplatesResponse:
    def test_returns_step_templates_and_pagination_from_headers(self):
        headers = {
            "X-Page": "2",
            "X-Per-Page": "10",
            "X-Total-Count": "25",
            "X-Total-Pages": "3",
        }
        service = build_service([ROW, {**ROW, "id": 15}], headers)

        result = service.list_step_templates(page=2, per=10)

        assert [row["id"] for row in result["step_templates"]] == [14, 15]
        assert result["pagination"] == {
            "current_page": 2,
            "per_page": 10,
            "total_count": 25,
            "total_pages": 3,
            "has_next_page": True,
            "has_prev_page": True,
        }

    def test_reports_the_per_page_the_server_applied(self):
        headers = {**PAGINATION_HEADERS, "X-Per-Page": "25"}
        service = build_service([ROW], headers)

        pagination = service.list_step_templates(include_metrics=True, per=100)[
            "pagination"
        ]

        assert pagination["per_page"] == 25

    def test_rows_carry_metrics_when_the_server_sends_them(self):
        service = build_service([{**ROW, "metrics": METRICS}], PAGINATION_HEADERS)

        row = service.list_step_templates(include_metrics=True)["step_templates"][0]

        assert row["metrics"]["efficiency"]["automated"] is None
        assert row["metrics"]["efficiency"]["all"]["tokens_available"] is False

    def test_a_non_array_body_raises(self):
        service = build_service({"step_templates": []}, PAGINATION_HEADERS)

        with pytest.raises(CoolhandAPIError, match="not a JSON array") as caught:
            service.list_step_templates()

        assert caught.value.status is None


class TestGetStepTemplate:
    def test_targets_the_show_endpoint_without_a_query(self):
        service = build_service({**ROW, "metrics": METRICS})

        service.get_step_template(14)

        parsed = urlparse(service._opener.request.full_url)
        assert parsed.path == f"{STEP_TEMPLATES_ENDPOINT}/14"
        assert parsed.query == ""

    def test_include_metrics_false_is_sent_explicitly(self):
        service = build_service(ROW)

        service.get_step_template(14, include_metrics=False)

        assert query_of(service._opener.request) == {"include_metrics": ["false"]}

    def test_returns_the_step_template(self):
        service = build_service({**ROW, "metrics": METRICS})

        step_template = service.get_step_template(14)

        assert step_template["id"] == 14
        assert step_template["metrics"]["total_runs"] == 2

    @pytest.mark.parametrize("bad_id", [0, -1, True, "14", 1.5, None])
    def test_rejects_an_id_that_is_not_a_positive_integer(self, bad_id):
        service = build_service(ROW)

        with pytest.raises(ValueError, match="step_template_id"):
            service.get_step_template(bad_id)

        assert service._opener.request is None

    def test_a_non_object_body_raises(self):
        service = build_service([])

        with pytest.raises(CoolhandAPIError, match="not a JSON object"):
            service.get_step_template(14)


class TestErrors:
    @pytest.mark.parametrize(
        ("status", "body"),
        [
            (401, '{"error":"Unauthorized"}'),
            (422, '{"errors":{"step_type":["is invalid"]}}'),
            (504, '{"errors":{"system":["timed out"]}}'),
        ],
    )
    def test_list_raises_with_the_status_and_body(self, status, body):
        service = build_service(error=http_error(status, body))

        with pytest.raises(CoolhandAPIError) as caught:
            service.list_step_templates(step_type="nonsense")

        assert caught.value.status == status
        assert body in str(caught.value)

    def test_show_raises_with_the_status(self):
        service = build_service(error=http_error(404, '{"errors":{}}'))

        with pytest.raises(CoolhandAPIError) as caught:
            service.get_step_template(999)

        assert caught.value.status == 404


class TestCoolhandDelegation:
    def test_exposes_the_step_template_service(
        self, mock_config, reset_global_instance
    ):
        instance = Coolhand(config=mock_config)

        assert isinstance(instance.step_template_service, StepTemplateService)

    def test_list_step_templates_delegates_every_filter(
        self, mock_config, reset_global_instance
    ):
        instance = Coolhand(config=mock_config)
        instance._step_template_service._opener = FakeOpener(
            response=FakeResponse([ROW], PAGINATION_HEADERS)
        )

        result = instance.list_step_templates(
            search="rev", step_type="prompt_step", include_metrics=False, page=1, per=5
        )

        assert query_of(instance._step_template_service._opener.request) == {
            "search": ["rev"],
            "step_type": ["prompt_step"],
            "include_metrics": ["false"],
            "page": ["1"],
            "per": ["5"],
        }
        assert result["step_templates"] == [ROW]

    def test_get_step_template_delegates(self, mock_config, reset_global_instance):
        instance = Coolhand(config=mock_config)
        instance._step_template_service._opener = FakeOpener(response=FakeResponse(ROW))

        step_template = instance.get_step_template(14, include_metrics=False)

        assert step_template["id"] == 14
        request = instance._step_template_service._opener.request
        assert urlparse(request.full_url).path == f"{STEP_TEMPLATES_ENDPOINT}/14"
        assert query_of(request) == {"include_metrics": ["false"]}

    def test_get_step_template_validates_before_any_request(
        self, mock_config, reset_global_instance
    ):
        instance = Coolhand(config=mock_config)
        instance._step_template_service._opener = FakeOpener(response=FakeResponse(ROW))

        with pytest.raises(ValueError, match="step_template_id"):
            instance.get_step_template(0)

        assert instance._step_template_service._opener.request is None
