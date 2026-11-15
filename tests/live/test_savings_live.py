"""End-to-end proof of `get_realized_savings` against a REAL Coolhand server.

Nothing here is mocked. Run it with `make test-live` (the environment it needs is
described in `test_templates_live.py`). It asserts shape and invariants, never the demo
database's figures, and every request is read-only.
"""

import pytest

from coolhand import CoolhandAPIError, SavingsService, WorkloadService

from .live_env import LIVE_API_KEY
from .live_env import live_service as _live_service

ROW_FIELDS = [
    "active_requests",
    "cost_at_previous_versions",
    "id",
    "name",
    "savings",
]


def live_service(api_key: str = LIVE_API_KEY) -> SavingsService:
    return _live_service(SavingsService, api_key)


def assert_row_shape(row: dict) -> None:
    assert sorted(row) == ROW_FIELDS
    assert isinstance(row["id"], str)
    assert row["id"]
    assert isinstance(row["name"], str)
    assert isinstance(row["savings"], (int, float))
    assert isinstance(row["cost_at_previous_versions"], (int, float))
    assert isinstance(row["active_requests"], int)


class TestGetRealizedSavingsLive:
    def test_returns_the_total_baseline_percent_and_breakdown_as_numbers(self):
        savings = live_service().get_realized_savings()

        assert sorted(savings) == [
            "cost_at_previous_versions",
            "percent",
            "total",
            "workloads",
        ]
        for field in ["total", "cost_at_previous_versions", "percent"]:
            assert isinstance(savings[field], (int, float))
        assert isinstance(savings["workloads"], list)
        for row in savings["workloads"]:
            assert_row_shape(row)

    def test_the_saving_stays_within_its_baseline_and_percent_agrees(self):
        savings = live_service().get_realized_savings()
        total = savings["total"]
        baseline = savings["cost_at_previous_versions"]

        assert total <= baseline
        expected = total / baseline * 100 if baseline > 0 else 0
        assert savings["percent"] == pytest.approx(expected, abs=0.1)

    def test_workload_rows_key_on_the_same_hashid_as_search_workloads(self):
        rows = live_service().get_realized_savings()["workloads"]
        result = _live_service(WorkloadService).search_workloads(
            include_metrics=True,
            days_back=28,
            include_archived=True,
            include_system=True,
            per=100,
        )

        # One page is enough only while the client has at most 100 workloads.
        assert result["pagination"]["has_next_page"] is False
        ids = {workload["id"] for workload in result["workloads"]}
        for row in rows:
            assert row["id"] in ids


class TestAuthenticationLive:
    def test_no_api_key_is_a_401(self):
        with pytest.raises(CoolhandAPIError) as excinfo:
            live_service(api_key="").get_realized_savings()

        assert excinfo.value.status == 401

    def test_an_invalid_api_key_is_a_401(self):
        with pytest.raises(CoolhandAPIError) as excinfo:
            live_service(
                api_key="ch_priv_definitely_not_a_real_key"
            ).get_realized_savings()

        assert excinfo.value.status == 401
