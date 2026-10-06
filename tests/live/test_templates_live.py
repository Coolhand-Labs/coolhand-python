"""End-to-end proof of the template read methods against a REAL Coolhand server.

Nothing here is mocked — every assertion is about a response that came off the wire.

Run it with `make test-live`; it is not part of `make verify`, because it needs a
reachable server and a real private API key that CI does not have:

    COOLHAND_LIVE_BASE_URL=http://127.0.0.1:3111 \
    COOLHAND_LIVE_API_KEY=<your private key> \
    make test-live

The key is read from the environment and never written down here — it is a live
credential.

Every request is read-only. Nothing in this file creates, updates or deletes a record,
so it is safe to point at a shared development database.
"""

from datetime import datetime, timezone

import pytest

from coolhand import CoolhandAPIError, TemplateService

from .live_env import LIVE_API_KEY, assert_metrics_shape
from .live_env import live_service as _live_service

# Every client is created with these two system buckets, and they are hidden from the
# list unless include_system is passed — which makes them a real fixture for that flag.
SYSTEM_TEMPLATE_NAMES = ["Ignored API Calls", "Unmatched"]

NULLABLE_STRING_FIELDS = ["status", "version", "group", "deprecated_at"]


def live_service(api_key: str = LIVE_API_KEY) -> TemplateService:
    return _live_service(TemplateService, api_key)


def assert_summary_shape(template: dict) -> None:
    assert isinstance(template["id"], str)
    assert template["id"]
    assert isinstance(template["name"], str)
    assert isinstance(template["workload_id"], str)
    assert isinstance(template["workload_name"], str)
    assert isinstance(template["system_template"], bool)
    assert isinstance(template["log_count"], int)
    assert isinstance(template["created_at"], str)
    assert isinstance(template["updated_at"], str)
    for field in NULLABLE_STRING_FIELDS:
        assert template[field] is None or isinstance(template[field], str)
    # Prompt patterns come from `show` only — a list row must not carry them.
    assert "user_prompt_pattern" not in template
    assert "system_prompt_pattern" not in template


@pytest.fixture(scope="module")
def system_templates():
    """The two system buckets, fetched once — the tests below need a real id."""
    result = live_service().search_templates(include_system=True)
    templates = [t for t in result["templates"] if t["system_template"]]
    assert templates, "Live fixture broken: include_system returned no system template."
    return templates


class TestSearchTemplatesLive:
    """search_templates against the live server."""

    def test_hides_the_system_buckets_by_default(self):
        result = live_service().search_templates()

        # A client whose only templates are the two system buckets legitimately returns
        # []. That is the include_system default working, not an empty database.
        for template in result["templates"]:
            assert template["system_template"] is False

    def test_pagination_headers_are_always_present(self):
        pagination = live_service().search_templates()["pagination"]

        assert pagination["current_page"] == 1
        assert pagination["per_page"] == 25
        assert pagination["total_count"] >= 0
        assert pagination["has_prev_page"] is False

    def test_include_system_returns_both_buckets(self, system_templates):
        assert sorted(t["name"] for t in system_templates) == SYSTEM_TEMPLATE_NAMES

    def test_every_row_matches_the_api_definitions_shape(self, system_templates):
        for template in system_templates:
            assert_summary_shape(template)

    def test_total_count_comes_from_the_header_not_the_row_count(self):
        full = live_service().search_templates(include_system=True)
        first_page = live_service().search_templates(include_system=True, per=1)

        assert len(first_page["templates"]) == 1
        assert first_page["pagination"]["per_page"] == 1
        # The page holds one row but the total still describes the whole collection.
        assert (
            first_page["pagination"]["total_count"] == full["pagination"]["total_count"]
        )

    def test_an_unrecognized_status_is_a_422_not_an_empty_list(self):
        with pytest.raises(CoolhandAPIError) as excinfo:
            live_service().search_templates(status="nonsense")

        assert excinfo.value.status == 422

    def test_an_undecodable_workload_hashid_is_a_422_not_an_empty_list(self):
        with pytest.raises(CoolhandAPIError) as excinfo:
            live_service().search_templates(workload_id="not-a-hashid")

        assert excinfo.value.status == 422


@pytest.fixture(scope="module")
def any_template():
    rows = live_service().search_templates(include_system=True, per=1)["templates"]
    assert rows, "Live fixture broken: the server has no template."
    return rows[0]


class TestTemplateMetricsLive:
    """include_metrics and the since/until window against the live server."""

    def test_metrics_are_omitted_unless_requested(self):
        rows = live_service().search_templates(include_system=True, per=5)["templates"]

        assert rows
        assert all("metrics" not in row for row in rows)

    def test_include_metrics_adds_the_full_metrics_object(self):
        rows = live_service().search_templates(
            include_system=True, include_metrics=True, per=5
        )["templates"]

        assert rows
        for row in rows:
            assert_metrics_shape(row["metrics"])
            assert row["metrics"]["days_back"] == 28

    def test_an_explicit_since_overrides_days_back_and_nulls_it(self):
        since = datetime(2026, 9, 1, tzinfo=timezone.utc)
        until = datetime(2026, 10, 1, tzinfo=timezone.utc)

        rows = live_service().search_templates(
            include_system=True,
            include_metrics=True,
            days_back=7,
            since=since,
            until=until,
            per=3,
        )["templates"]

        assert rows
        for row in rows:
            assert row["metrics"]["days_back"] is None
            assert row["metrics"]["since"] == "2026-09-01T00:00:00Z"
            assert row["metrics"]["until"] == "2026-10-01T00:00:00Z"

    def test_a_plus_offset_string_is_accepted_not_a_422(self):
        rows = live_service().search_templates(
            include_system=True,
            include_metrics=True,
            since="2026-09-01T02:00:00+02:00",
            until="2026-10-01",
            per=1,
        )["templates"]

        assert rows[0]["metrics"]["since"] == "2026-09-01T00:00:00Z"

    def test_a_malformed_since_is_a_422_on_the_since_key(self):
        with pytest.raises(CoolhandAPIError) as excinfo:
            live_service().search_templates(include_metrics=True, since="bad")

        assert excinfo.value.status == 422
        assert "since" in str(excinfo.value)

    def test_an_inverted_window_is_a_422(self):
        with pytest.raises(CoolhandAPIError) as excinfo:
            live_service().search_templates(
                include_metrics=True, since="2026-10-01", until="2026-09-01"
            )

        assert excinfo.value.status == 422

    def test_get_template_always_returns_metrics_over_the_window(self, any_template):
        detail = live_service().get_template(
            any_template["id"], since="2026-09-01", until="2026-10-01"
        )

        assert_metrics_shape(detail["metrics"])
        assert detail["metrics"]["days_back"] is None

    def test_get_template_with_a_malformed_window_is_a_422(self, any_template):
        with pytest.raises(CoolhandAPIError) as excinfo:
            live_service().get_template(any_template["id"], since="bad")

        assert excinfo.value.status == 422


class TestGetTemplateLive:
    """get_template against the live server."""

    def test_returns_both_prompt_patterns_for_a_template_from_the_list(
        self, system_templates
    ):
        listed = system_templates[0]

        detail = live_service().get_template(listed["id"])

        assert detail["id"] == listed["id"]
        assert detail["name"] == listed["name"]
        # Present as keys even when null — the difference between show and a list row.
        assert "user_prompt_pattern" in detail
        assert "system_prompt_pattern" in detail
        for field in ["user_prompt_pattern", "system_prompt_pattern"]:
            assert detail[field] is None or isinstance(detail[field], str)

    def test_reaches_a_system_template_by_id_with_no_flag(self, system_templates):
        detail = live_service().get_template(system_templates[0]["id"])

        assert detail["system_template"] is True
        assert detail["name"] in SYSTEM_TEMPLATE_NAMES

    def test_an_id_this_client_cannot_see_is_a_404_not_a_403(self):
        with pytest.raises(CoolhandAPIError) as excinfo:
            live_service().get_template("nosuchid00000")

        assert excinfo.value.status == 404


class TestAuthenticationLive:
    """The private key is required, and nothing else authenticates."""

    def test_no_api_key_is_rejected(self):
        with pytest.raises(CoolhandAPIError) as excinfo:
            live_service(api_key="").search_templates()

        assert excinfo.value.status == 401

    def test_an_invalid_api_key_is_rejected(self):
        with pytest.raises(CoolhandAPIError) as excinfo:
            live_service(api_key="ch_priv_definitely_not_a_real_key").search_templates()

        assert excinfo.value.status == 401
