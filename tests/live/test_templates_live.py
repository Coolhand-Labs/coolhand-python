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

import os

import pytest

from coolhand import CoolhandAPIError, TemplateService

LIVE_BASE_URL = os.environ.get("COOLHAND_LIVE_BASE_URL", "")
LIVE_API_KEY = os.environ.get("COOLHAND_LIVE_API_KEY", "")

if not LIVE_BASE_URL or not LIVE_API_KEY:
    raise RuntimeError(
        "Live tests need COOLHAND_LIVE_BASE_URL and COOLHAND_LIVE_API_KEY (a private "
        "API key) in the environment. Set both and re-run `make test-live`."
    )

# Round trips from the host to a containerised local server go through port forwarding,
# which on Windows adds ~15-20s per request even when the server itself answers in
# ~400ms. The SDK default of 30s would fail these for environmental reasons that have
# nothing to do with the wrapper.
LIVE_TIMEOUT_SECONDS = 120

# Every client is created with these two system buckets, and they are hidden from the
# list unless include_system is passed — which makes them a real fixture for that flag.
SYSTEM_TEMPLATE_NAMES = ["Ignored API Calls", "Unmatched"]

NULLABLE_STRING_FIELDS = ["status", "version", "group", "deprecated_at"]


def live_service(api_key: str = LIVE_API_KEY) -> TemplateService:
    return TemplateService(
        api_key=api_key,
        base_url=LIVE_BASE_URL,
        silent=True,
        timeout=LIVE_TIMEOUT_SECONDS,
    )


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
