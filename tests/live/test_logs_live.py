"""End-to-end proof of `search_logs` and `get_log` against a REAL Coolhand server.

Nothing here is mocked. Run it with `make test-live` (the environment it needs is
described in `test_templates_live.py`). Every request is read-only.
"""

from datetime import datetime, timezone

import pytest

from coolhand import CoolhandAPIError, LogService

from .live_env import LIVE_API_KEY
from .live_env import live_service as _live_service

COST_BREAKDOWN_FIELDS = [
    "total_cost",
    "input_cost",
    "output_cost",
    "cached_input_cost",
    "cache_creation_input_cost",
    "reasoning_output_cost",
]

# Bounded so min_cost / order=cost_desc cannot trip the statement timeout on a large
# history, as the endpoint's own description advises.
WINDOW = {"since": datetime(2026, 9, 1, tzinfo=timezone.utc)}


def live_service(api_key: str = LIVE_API_KEY) -> LogService:
    return _live_service(LogService, api_key)


def assert_log_shape(log: dict) -> None:
    assert isinstance(log["id"], str)
    assert isinstance(log["created_at"], str)
    assert isinstance(log["metadata"], dict)
    assert isinstance(log["ingest_evidence"], dict)
    assert "cost" in log
    assert log["cost"] is None or isinstance(log["cost"], (int, float))


@pytest.fixture(scope="module")
def priced_logs():
    """The most expensive logs in the window, so a real priced log is on hand."""
    logs = live_service().search_logs(order="cost_desc", per=5, **WINDOW)["logs"]
    assert logs, "Live fixture broken: no priced log in the window."
    return logs


class TestSearchLogsLive:
    def test_rows_match_the_definitions_shape_and_carry_cost(self):
        result = live_service().search_logs(per=5, days_back=60)

        assert result["logs"]
        for log in result["logs"]:
            assert_log_shape(log)

    def test_totals_are_absent_not_fabricated_without_include_total(self):
        pagination = live_service().search_logs(per=2, days_back=60)["pagination"]

        assert pagination["total_count"] is None
        assert pagination["total_pages"] is None
        assert pagination["per_page"] == 2
        assert pagination["has_next_page"] is True

    def test_include_total_adds_real_totals(self):
        pagination = live_service().search_logs(per=2, include_total=True)["pagination"]

        assert isinstance(pagination["total_count"], int)
        assert pagination["total_count"] > 2
        assert pagination["total_pages"] >= 2

    def test_order_cost_desc_sorts_by_cost_highest_first(self, priced_logs):
        costs = [log["cost"] for log in priced_logs]

        assert all(cost is not None for cost in costs)
        assert costs == sorted(costs, reverse=True)

    def test_min_cost_keeps_only_logs_at_or_above_it(self, priced_logs):
        threshold = priced_logs[-1]["cost"]

        logs = live_service().search_logs(min_cost=threshold, per=25, **WINDOW)["logs"]

        assert logs
        assert all(log["cost"] >= threshold for log in logs)

    def test_min_cost_zero_is_sent_and_excludes_unpriceable_logs(self):
        logs = live_service().search_logs(min_cost=0, per=10, **WINDOW)["logs"]

        assert logs
        assert all(log["cost"] is not None for log in logs)

    def test_since_and_until_bound_created_at(self):
        logs = live_service().search_logs(
            since="2026-09-10", until="2026-09-12", per=25
        )["logs"]

        for log in logs:
            assert "2026-09-10" <= log["created_at"] < "2026-09-12"

    def test_a_plus_offset_string_is_accepted_not_a_422(self):
        result = live_service().search_logs(
            since="2026-09-10T02:00:00+02:00", until="2026-09-11", per=1
        )

        assert isinstance(result["logs"], list)

    def test_include_prompts_adds_the_prompt_fields(self):
        logs = live_service().search_logs(include_prompts=True, per=3, **WINDOW)["logs"]

        assert logs
        assert all("system_prompt" in log and "user_prompt" in log for log in logs)

    @pytest.mark.parametrize(
        "bad",
        [
            {"since": "bad"},
            {"since": "2026-10-01", "until": "2026-09-01"},
            {"min_cost": -1},
            {"order": "nonsense"},
            {"template_id": "not-a-hashid"},
        ],
    )
    def test_invalid_input_is_a_422(self, bad):
        with pytest.raises(CoolhandAPIError) as excinfo:
            live_service().search_logs(per=1, **bad)

        assert excinfo.value.status == 422


class TestGetLogLive:
    def test_returns_cost_and_the_breakdown_for_a_priced_log(self, priced_logs):
        listed = priced_logs[0]

        log = live_service().get_log(listed["id"])

        assert log["id"] == listed["id"]
        assert log["cost"] == pytest.approx(listed["cost"])
        assert sorted(log["cost_breakdown"]) == sorted(COST_BREAKDOWN_FIELDS)
        assert log["cost_breakdown"]["total_cost"] == pytest.approx(log["cost"])

    def test_max_chars_limits_every_content_field(self, priced_logs):
        log = live_service().get_log(priced_logs[0]["id"], max_chars=5)

        for field in ["system_prompt", "user_prompt", "output"]:
            assert len(log[field] or "") <= 5

    def test_search_query_returns_matches_instead_of_content(self, priced_logs):
        log = live_service().get_log(priced_logs[0]["id"], search_query="the")

        assert "matches" in log
        assert log["search_query"] == "the"

    def test_a_non_positive_max_chars_is_a_422(self, priced_logs):
        with pytest.raises(CoolhandAPIError) as excinfo:
            live_service().get_log(priced_logs[0]["id"], max_chars=0)

        assert excinfo.value.status == 422

    def test_an_unknown_id_is_a_404(self):
        with pytest.raises(CoolhandAPIError) as excinfo:
            live_service().get_log("nosuchid00000")

        assert excinfo.value.status == 404


class TestAuthenticationLive:
    def test_no_api_key_is_a_401_on_search_and_show(self):
        service = live_service(api_key="")

        with pytest.raises(CoolhandAPIError) as search_error:
            service.search_logs()
        with pytest.raises(CoolhandAPIError) as show_error:
            service.get_log("whatever")

        assert search_error.value.status == 401
        assert show_error.value.status == 401

    def test_an_invalid_api_key_is_a_401(self):
        with pytest.raises(CoolhandAPIError) as excinfo:
            live_service(api_key="ch_priv_definitely_not_a_real_key").search_logs()

        assert excinfo.value.status == 401
