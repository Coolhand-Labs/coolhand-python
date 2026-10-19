"""Tests for the shared read-service helpers."""

import logging
from datetime import datetime, timedelta, timezone

import pytest

from coolhand._read_service import (
    CoolhandAPIError,
    ReadService,
    _timestamp_param,
    _window_filters,
)
from coolhand.log_service import LogService
from coolhand.workload_service import WorkloadService

from .fake_transport import build_service as fake_build_service


def build_service(body):
    return fake_build_service(ReadService, body=body)


class TestTimestampParam:
    def test_none_stays_none(self):
        assert _timestamp_param(None, "since") is None

    def test_a_string_is_passed_through_unchanged(self):
        assert _timestamp_param("2026-09-10T02:00:00+02:00", "since") == (
            "2026-09-10T02:00:00+02:00"
        )

    def test_a_naive_datetime_is_read_as_utc(self):
        assert _timestamp_param(datetime(2026, 9, 1, 12, 30), "since") == (
            "2026-09-01T12:30:00Z"
        )

    def test_a_utc_datetime_is_rendered_with_a_z_suffix(self):
        value = datetime(2026, 9, 1, tzinfo=timezone.utc)

        assert _timestamp_param(value, "since") == "2026-09-01T00:00:00Z"

    def test_a_non_utc_offset_is_converted_to_utc(self):
        plus_two = timezone(timedelta(hours=2))
        value = datetime(2026, 9, 1, 1, 0, tzinfo=plus_two)

        assert _timestamp_param(value, "since") == "2026-08-31T23:00:00Z"

    @pytest.mark.parametrize("bad", [True, False, 0, 1, 1.5, 1788220800, ["x"]])
    def test_bools_ints_and_other_types_are_rejected(self, bad):
        with pytest.raises(ValueError, match="until must be a datetime"):
            _timestamp_param(bad, "until")


class TestWindowFilters:
    def test_unset_bounds_are_all_none(self):
        assert _window_filters(None, None, None) == {
            "days_back": None,
            "since": None,
            "until": None,
        }

    def test_a_datetime_works_as_until(self):
        filters = _window_filters(7, None, datetime(2026, 10, 1, tzinfo=timezone.utc))

        assert filters == {
            "days_back": 7,
            "since": None,
            "until": "2026-10-01T00:00:00Z",
        }

    def test_since_and_until_are_serialised_independently(self):
        filters = _window_filters(None, datetime(2026, 9, 1), "2026-10-01")

        assert filters["since"] == "2026-09-01T00:00:00Z"
        assert filters["until"] == "2026-10-01"

    def test_the_offending_bound_is_named_in_the_error(self):
        with pytest.raises(ValueError, match="since must be a datetime"):
            _window_filters(None, 5, None)
        with pytest.raises(ValueError, match="until must be a datetime"):
            _window_filters(None, None, True)


class TestJsonShapeHelpers:
    def test_get_json_array_returns_the_list_and_headers(self):
        service = build_service([{"id": "a"}])

        body, headers = service._get_json_array("https://x.test/y", "Thing list")

        assert body == [{"id": "a"}]
        assert headers is not None

    def test_get_json_array_rejects_an_object_naming_the_resource(self):
        service = build_service({"id": "a"})

        with pytest.raises(CoolhandAPIError, match="Thing list .* not a JSON array"):
            service._get_json_array("https://x.test/y", "Thing list")

    def test_get_json_object_returns_the_dict(self):
        service = build_service({"id": "a"})

        body, _headers = service._get_json_object("https://x.test/y", "Thing")

        assert body == {"id": "a"}

    def test_get_json_object_rejects_an_array_naming_the_resource(self):
        service = build_service([])

        with pytest.raises(CoolhandAPIError, match="Thing response .* JSON object"):
            service._get_json_object("https://x.test/y", "Thing")


class TestLogging:
    @pytest.mark.parametrize("service_class", [LogService, WorkloadService])
    def test_each_service_logs_under_its_own_module(self, service_class, caplog):
        service = fake_build_service(service_class, body=[], silent=False)

        with caplog.at_level(logging.INFO):
            service._log("hello")

        assert [(r.name, r.getMessage()) for r in caplog.records] == [
            (service_class.__module__, "hello")
        ]

    def test_silent_mode_logs_nothing(self, caplog):
        service = fake_build_service(LogService, body=[], silent=True)

        with caplog.at_level(logging.INFO):
            service._log("hello")

        assert caplog.records == []
