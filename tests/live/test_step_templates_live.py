"""End-to-end proof of the step template reads against a REAL Coolhand server.

Nothing here is mocked. Run it with `make test-live` (see the Makefile for the
environment it needs). The step template and log ids the tests use are read from the
environment, never hardcoded:

    COOLHAND_LIVE_STEP_TEMPLATE_ID  a published step template of the key's client
    COOLHAND_LIVE_TURN_LOG_ID       a log of that client carrying `turn_metrics`

Every request is read-only.
"""

import os

import pytest

from coolhand import CoolhandAPIError, LogService, StepTemplateService

from .live_env import LIVE_API_KEY
from .live_env import live_service as _live_service

STEP_TEMPLATE_ID = int(os.environ.get("COOLHAND_LIVE_STEP_TEMPLATE_ID", "0"))
TURN_LOG_ID = os.environ.get("COOLHAND_LIVE_TURN_LOG_ID", "")

ROW_FIELDS = [
    "id",
    "name",
    "step_type",
    "status",
    "user_turn_pattern",
    "created_at",
    "updated_at",
    "sessions_count",
    "occurrences_count",
    "last_seen_at",
]

METRICS_FIELDS = ["total_runs", "tool_stats", "turn_stats", "efficiency", "notes"]

TURN_METRIC_FIELDS = [
    "index",
    "human",
    "started_at",
    "ended_at",
    "assistant_requests",
    "tool_calls",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_creation_tokens",
]

if not STEP_TEMPLATE_ID or not TURN_LOG_ID:
    raise RuntimeError(
        "Step template live tests need COOLHAND_LIVE_STEP_TEMPLATE_ID and "
        "COOLHAND_LIVE_TURN_LOG_ID in the environment."
    )


def live_service(api_key: str = LIVE_API_KEY) -> StepTemplateService:
    return _live_service(StepTemplateService, api_key)


def assert_row_shape(row: dict) -> None:
    for field in ROW_FIELDS:
        assert field in row, field
    assert isinstance(row["id"], int)
    assert row["step_type"] in {"prompt_step", "tool_step"}
    assert isinstance(row["sessions_count"], int)
    assert isinstance(row["occurrences_count"], int)
    assert row["user_turn_pattern"] is None or isinstance(row["user_turn_pattern"], str)
    assert row["last_seen_at"] is None or isinstance(row["last_seen_at"], str)


def assert_metrics_shape(metrics: dict) -> None:
    assert sorted(metrics) == sorted(METRICS_FIELDS)
    assert isinstance(metrics["total_runs"], int)
    assert isinstance(metrics["notes"], list)
    assert metrics["tool_stats"] is None or isinstance(metrics["tool_stats"], list)
    efficiency = metrics["efficiency"]
    if efficiency is not None:
        assert isinstance(efficiency["unavailable_runs"], int)
        for cohort in (
            efficiency["interactive"],
            efficiency["automated"],
            efficiency["all"],
        ):
            if cohort is not None:
                assert isinstance(cohort["tokens_available"], bool)


class TestListStepTemplatesLive:
    def test_lists_rows_without_metrics_by_default(self):
        result = live_service().list_step_templates()

        assert result["step_templates"]
        for row in result["step_templates"]:
            assert_row_shape(row)
            assert "metrics" not in row
        pagination = result["pagination"]
        assert pagination["current_page"] == 1
        assert pagination["per_page"] == 25
        assert pagination["total_count"] >= len(result["step_templates"])

    def test_include_metrics_adds_a_metrics_object_per_row(self):
        rows = live_service().list_step_templates(include_metrics=True)[
            "step_templates"
        ]

        assert rows
        for row in rows:
            assert_row_shape(row)
            assert_metrics_shape(row["metrics"])

    def test_include_metrics_caps_per_at_25(self):
        pagination = live_service().list_step_templates(include_metrics=True, per=100)[
            "pagination"
        ]

        assert pagination["per_page"] == 25

    def test_per_is_honoured_without_metrics(self):
        result = live_service().list_step_templates(per=1)

        assert len(result["step_templates"]) == 1
        assert result["pagination"]["per_page"] == 1

    def test_step_type_filters_to_that_type(self):
        rows = live_service().list_step_templates(step_type="prompt_step")[
            "step_templates"
        ]

        assert rows
        assert all(row["step_type"] == "prompt_step" for row in rows)

    def test_search_matches_nothing_for_an_unknown_name(self):
        result = live_service().list_step_templates(search="no-such-step-template")

        assert result["step_templates"] == []

    def test_an_unknown_step_type_is_a_422(self):
        with pytest.raises(CoolhandAPIError) as excinfo:
            live_service().list_step_templates(step_type="nonsense")  # type: ignore[arg-type]

        assert excinfo.value.status == 422


class TestGetStepTemplateLive:
    def test_includes_metrics_by_default(self):
        step_template = live_service().get_step_template(STEP_TEMPLATE_ID)

        assert step_template["id"] == STEP_TEMPLATE_ID
        assert_row_shape(step_template)
        assert_metrics_shape(step_template["metrics"])

    def test_include_metrics_false_omits_metrics(self):
        step_template = live_service().get_step_template(
            STEP_TEMPLATE_ID, include_metrics=False
        )

        assert_row_shape(step_template)
        assert "metrics" not in step_template

    def test_an_unknown_id_is_a_404(self):
        with pytest.raises(CoolhandAPIError) as excinfo:
            live_service().get_step_template(2_000_000_000)

        assert excinfo.value.status == 404

    def test_a_non_positive_id_raises_before_any_request(self):
        with pytest.raises(ValueError):
            live_service().get_step_template(0)


class TestTurnMetricsLive:
    def test_turn_metrics_is_present_only_when_requested(self):
        service = _live_service(LogService, LIVE_API_KEY)

        without = service.get_log(TURN_LOG_ID)
        explicit_false = service.get_log(TURN_LOG_ID, include_turn_metrics=False)
        with_turns = service.get_log(TURN_LOG_ID, include_turn_metrics=True)

        assert "turn_metrics" not in without
        assert "turn_metrics" not in explicit_false
        assert with_turns["turn_metrics"]
        for turn in with_turns["turn_metrics"]:
            assert sorted(turn) == sorted(TURN_METRIC_FIELDS)
            assert isinstance(turn["human"], bool)
            assert isinstance(turn["assistant_requests"], int)


class TestAuthenticationLive:
    def test_no_key_is_a_401_on_list_and_show(self):
        service = live_service(api_key="")

        with pytest.raises(CoolhandAPIError) as list_error:
            service.list_step_templates()
        with pytest.raises(CoolhandAPIError) as show_error:
            service.get_step_template(STEP_TEMPLATE_ID)

        assert list_error.value.status == 401
        assert show_error.value.status == 401

    def test_an_invalid_key_is_a_401(self):
        with pytest.raises(CoolhandAPIError) as excinfo:
            live_service(
                api_key="ch_priv_definitely_not_a_real_key"
            ).list_step_templates()

        assert excinfo.value.status == 401
