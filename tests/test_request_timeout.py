"""Tests for the `request_timeout` option shared by every Coolhand API call."""

from unittest.mock import MagicMock, patch

import pytest

from coolhand import (
    Coolhand,
    CoolhandClient,
    FeedbackLinkService,
    FeedbackService,
    TemplateService,
)
from coolhand._config import DEFAULT_REQUEST_TIMEOUT_SECONDS, _resolve_request_timeout


@pytest.fixture
def mock_feedback_urlopen():
    with patch("coolhand.feedback_service._opener.open") as mock:
        response = MagicMock()
        response.status = 201
        response.read.return_value = b'{"id": "f"}'
        response.__enter__ = MagicMock(return_value=response)
        response.__exit__ = MagicMock(return_value=False)
        mock.return_value = response
        yield mock


INVALID_TIMEOUTS = [
    0,
    -1,
    -0.5,
    True,
    False,
    "5",
    [5],
    float("nan"),
    float("inf"),
    2**31,
    1e300,
    10**400,
]

SERVICES = [CoolhandClient, FeedbackService, TemplateService, FeedbackLinkService]


class TestResolveRequestTimeout:
    def test_defaults_to_thirty_seconds(self):
        assert DEFAULT_REQUEST_TIMEOUT_SECONDS == 30.0
        assert _resolve_request_timeout({}) == 30.0

    def test_none_falls_back_to_the_default(self):
        assert _resolve_request_timeout({"request_timeout": None}) == 30.0

    def test_accepts_the_upper_bound(self):
        assert _resolve_request_timeout({"request_timeout": 2**31 - 1}) == 2**31 - 1

    @pytest.mark.parametrize("value", [1, 2.5, 120])
    def test_accepts_positive_numbers(self, value):
        assert _resolve_request_timeout({"request_timeout": value}) == float(value)

    def test_timeout_is_honoured_as_the_older_spelling(self):
        assert _resolve_request_timeout({"timeout": 45}) == 45.0

    def test_request_timeout_wins_over_timeout(self):
        config = {"request_timeout": 60, "timeout": 5}
        assert _resolve_request_timeout(config) == 60.0

    @pytest.mark.parametrize("value", INVALID_TIMEOUTS)
    def test_rejects_invalid_request_timeout(self, value):
        with pytest.raises(ValueError, match="request_timeout"):
            _resolve_request_timeout({"request_timeout": value})

    def test_invalid_request_timeout_is_not_masked_by_a_valid_legacy_timeout(self):
        with pytest.raises(ValueError, match="request_timeout"):
            _resolve_request_timeout({"request_timeout": 0, "timeout": 7})

    @pytest.mark.parametrize("value", INVALID_TIMEOUTS)
    def test_rejects_invalid_legacy_timeout(self, value):
        with pytest.raises(ValueError, match=r"^timeout must be"):
            _resolve_request_timeout({"timeout": value})


@pytest.mark.usefixtures("reset_global_instance")
class TestConstruction:
    @pytest.mark.parametrize("service", SERVICES)
    def test_default_is_resolved_onto_the_config(self, service):
        assert service(api_key="k").config["request_timeout"] == 30.0

    @pytest.mark.parametrize("service", SERVICES)
    def test_request_timeout_is_stored(self, service):
        assert service(api_key="k", request_timeout=45).config["request_timeout"] == 45

    @pytest.mark.parametrize("service", SERVICES)
    def test_legacy_timeout_is_resolved_to_request_timeout(self, service):
        assert service(api_key="k", timeout=7).config["request_timeout"] == 7

    @pytest.mark.parametrize("service", SERVICES)
    @pytest.mark.parametrize("value", INVALID_TIMEOUTS)
    def test_invalid_value_fails_at_construction(self, service, value):
        with pytest.raises(ValueError, match="request_timeout"):
            service(api_key="k", request_timeout=value)

    @pytest.mark.parametrize(
        "kwargs, expected",
        [
            ({"timeout": 7}, 7),
            ({"request_timeout": 60, "timeout": 5}, 60),
            ({"request_timeout": None, "timeout": 7}, 7),
        ],
    )
    def test_coolhand_resolves_legacy_and_precedence(self, kwargs, expected):
        client = Coolhand(api_key="k", silent=True, **kwargs)
        try:
            assert client.config["request_timeout"] == expected
            assert client.feedback_service.request_timeout == expected
            assert client.template_service.timeout == expected
            assert client._feedback_link_service.timeout == expected
        finally:
            client.stop_monitoring()

    def test_coolhand_propagates_to_every_service(self):
        client = Coolhand(api_key="k", request_timeout=45, silent=True)
        try:
            assert client.feedback_service.request_timeout == 45
            assert client.template_service.timeout == 45
            assert client._feedback_link_service.timeout == 45
        finally:
            client.stop_monitoring()


@pytest.mark.usefixtures("reset_global_instance")
class TestWritePathsUseIt:
    INTERACTION = {"id": "i", "method": "post", "url": "https://api.openai.com/v1/chat"}

    def test_send_one_default(self, mock_urlopen):
        CoolhandClient(auto_submit=False, api_key="k")._send_one(self.INTERACTION)

        assert mock_urlopen.call_args.kwargs["timeout"] == 30.0

    def test_send_one_configured(self, mock_urlopen):
        client = CoolhandClient(auto_submit=False, api_key="k", request_timeout=90)

        client._send_one(self.INTERACTION)

        assert mock_urlopen.call_args.kwargs["timeout"] == 90.0

    def test_feedback_submit_default(self, mock_feedback_urlopen):
        FeedbackService(api_key="k").create_feedback({"client_unique_id": "c"})

        assert mock_feedback_urlopen.call_args.kwargs["timeout"] == 30.0

    def test_feedback_submit_configured(self, mock_feedback_urlopen):
        service = FeedbackService(api_key="k", request_timeout=90)

        service.create_feedback({"client_unique_id": "c"})

        assert mock_feedback_urlopen.call_args.kwargs["timeout"] == 90.0

    def test_feedback_submit_through_coolhand(self, mock_feedback_urlopen):
        client = Coolhand(api_key="k", request_timeout=12, silent=True)
        try:
            client.create_feedback({"client_unique_id": "c"})
        finally:
            client.stop_monitoring()

        assert mock_feedback_urlopen.call_args.kwargs["timeout"] == 12.0


class TestReadPathsUseIt:
    @pytest.mark.parametrize(
        "kwargs, expected",
        [({}, 30.0), ({"request_timeout": 45}, 45.0), ({"timeout": 7}, 7.0)],
    )
    def test_template_service(self, kwargs, expected):
        service = TemplateService(api_key="k", **kwargs)
        service._opener = MagicMock()
        service._opener.open.return_value.__enter__.return_value.read.return_value = (
            b"[]"
        )
        service._opener.open.return_value.__enter__.return_value.headers = {}

        service.search_templates()

        assert service._opener.open.call_args.kwargs["timeout"] == expected

    @pytest.mark.parametrize(
        "kwargs, expected",
        [({}, 30.0), ({"request_timeout": 45}, 45.0), ({"timeout": 7}, 7.0)],
    )
    def test_get_template(self, kwargs, expected):
        service = TemplateService(api_key="k", **kwargs)
        service._opener = MagicMock()
        service._opener.open.return_value.__enter__.return_value.read.return_value = (
            b'{"id": "t"}'
        )

        service.get_template("t")

        assert service._opener.open.call_args.kwargs["timeout"] == expected

    @pytest.mark.parametrize(
        "kwargs, expected",
        [({}, 30.0), ({"request_timeout": 45}, 45.0), ({"timeout": 7}, 7.0)],
    )
    @pytest.mark.parametrize("call", ["link", "bulk", "unlink"])
    def test_feedback_link_service(self, call, kwargs, expected):
        service = FeedbackLinkService(api_key="k", **kwargs)
        service._opener = MagicMock()
        response = service._opener.open.return_value.__enter__.return_value
        response.status = 200
        response.read.return_value = (
            b'{"id": "l", "optimization_id": "opt", "feedback_id": "fb", '
            b'"linked": 1, "already_linked": 0, "errored": 0, "not_found": []}'
        )

        if call == "link":
            service.link_feedback("opt", "fb")
        elif call == "bulk":
            service.bulk_link_feedback("opt", ["fb1", "fb2"])
        else:
            service.unlink_feedback("opt", "l")

        assert service._opener.open.call_args.kwargs["timeout"] == expected
