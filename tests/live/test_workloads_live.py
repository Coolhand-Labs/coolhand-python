"""End-to-end proof of `search_workloads` against a REAL Coolhand server.

Nothing here is mocked. Run it with `make test-live` (the environment it needs is
described in `test_templates_live.py`). Every request is read-only.
"""

from datetime import datetime, timezone

import pytest

from coolhand import CoolhandAPIError, WorkloadService

from .live_env import LIVE_API_KEY, assert_metrics_shape
from .live_env import live_service as _live_service


def live_service(api_key: str = LIVE_API_KEY) -> WorkloadService:
    return _live_service(WorkloadService, api_key)


def assert_workload_shape(workload: dict) -> None:
    assert isinstance(workload["id"], str)
    assert workload["id"]
    assert isinstance(workload["name"], str)
    assert workload["description"] is None or isinstance(workload["description"], str)
    for field in ["archived", "system", "merged"]:
        assert isinstance(workload[field], bool)
    for field in ["template_count", "draft_template_count", "log_count"]:
        assert isinstance(workload[field], int)
    assert workload["last_activity"] is None or isinstance(
        workload["last_activity"], str
    )


class TestSearchWorkloadsLive:
    def test_returns_workloads_with_header_pagination(self):
        result = live_service().search_workloads(per=3)

        assert 0 < len(result["workloads"]) <= 3
        for workload in result["workloads"]:
            assert_workload_shape(workload)
            assert "metrics" not in workload
            assert "templates" not in workload
        pagination = result["pagination"]
        assert pagination["per_page"] == 3
        assert pagination["total_count"] >= len(result["workloads"])
        assert pagination["has_prev_page"] is False

    def test_total_count_describes_the_collection_not_the_page(self):
        one = live_service().search_workloads(per=1)

        assert len(one["workloads"]) == 1
        assert one["pagination"]["total_count"] > 1
        assert one["pagination"]["has_next_page"] is True

    def test_workloads_are_ordered_by_name_across_pages(self):
        first = live_service().search_workloads(per=2, page=1)["workloads"]
        second = live_service().search_workloads(per=2, page=2)["workloads"]

        names = [w["name"] for w in first + second]
        assert names == sorted(names, key=str.lower) or names == sorted(names)

    def test_include_metrics_adds_the_full_object(self):
        workloads = live_service().search_workloads(include_metrics=True, per=3)[
            "workloads"
        ]

        assert workloads
        for workload in workloads:
            assert_metrics_shape(workload["metrics"])
            assert workload["metrics"]["days_back"] == 28

    def test_a_since_window_nulls_days_back_and_echoes_the_bounds(self):
        workloads = live_service().search_workloads(
            include_metrics=True,
            since=datetime(2026, 9, 1, tzinfo=timezone.utc),
            until="2026-10-01",
            per=2,
        )["workloads"]

        assert workloads
        for workload in workloads:
            assert workload["metrics"]["days_back"] is None
            assert workload["metrics"]["since"] == "2026-09-01T00:00:00Z"
            assert workload["metrics"]["until"] == "2026-10-01T00:00:00Z"

    def test_include_templates_embeds_the_active_templates(self):
        workloads = live_service().search_workloads(include_templates=True, per=25)[
            "workloads"
        ]

        embedded = [t for w in workloads for t in w["templates"]]
        assert embedded
        assert {"id", "name", "status"} <= set(embedded[0])

    def test_a_workload_id_is_the_hashid_string(self):
        workload = live_service().search_workloads(per=1)["workloads"][0]

        assert isinstance(workload["id"], str)

    @pytest.mark.parametrize("window", [{"since": "bad"}, {"until": "bad"}])
    def test_a_malformed_bound_is_a_422(self, window):
        with pytest.raises(CoolhandAPIError) as excinfo:
            live_service().search_workloads(include_metrics=True, **window)

        assert excinfo.value.status == 422
        assert next(iter(window)) in str(excinfo.value)

    def test_an_inverted_window_is_a_422(self):
        with pytest.raises(CoolhandAPIError) as excinfo:
            live_service().search_workloads(
                include_metrics=True, since="2026-10-01", until="2026-09-01"
            )

        assert excinfo.value.status == 422


class TestAuthenticationLive:
    def test_no_api_key_is_a_401(self):
        with pytest.raises(CoolhandAPIError) as excinfo:
            live_service(api_key="").search_workloads()

        assert excinfo.value.status == 401

    def test_an_invalid_api_key_is_a_401(self):
        with pytest.raises(CoolhandAPIError) as excinfo:
            live_service(api_key="ch_priv_definitely_not_a_real_key").search_workloads()

        assert excinfo.value.status == 401
