"""Tests for SavingsService."""

from urllib.parse import urlparse

import pytest

from coolhand import Coolhand, CoolhandAPIError, SavingsService
from coolhand.savings_service import SAVINGS_ENDPOINT

from .fake_transport import BASE_URL, FakeOpener, FakeResponse
from .fake_transport import build_service as fake_build_service
from .fake_transport import http_error as fake_http_error

SAVINGS = {
    "total": 4.5372,
    "cost_at_previous_versions": 5.6912,
    "percent": 79.7,
    "workloads": [
        {
            "id": "xwjm0lgsdmy4",
            "name": "Help Center Article Suggester",
            "savings": 2.0437,
            "cost_at_previous_versions": 3.0402,
            "active_requests": 182,
        }
    ],
}

ZERO_SAVINGS = {
    "total": 0.0,
    "cost_at_previous_versions": 0.0,
    "percent": 0.0,
    "workloads": [],
}


def build_service(body=None, error=None, **config):
    return fake_build_service(SavingsService, body, None, error, **config)


def http_error(status, body):
    return fake_http_error(f"{BASE_URL}{SAVINGS_ENDPOINT}", status, body)


class TestGetRealizedSavingsRequest:
    def test_gets_the_endpoint_with_no_query_string_and_the_private_key(self):
        service = build_service(SAVINGS)

        service.get_realized_savings()

        request = service._opener.request
        parsed = urlparse(request.full_url)
        assert parsed.path == SAVINGS_ENDPOINT
        assert parsed.query == ""
        assert request.get_method() == "GET"
        assert request.get_header("X-api-key") == "test-private-key"
        assert request.get_header("Accept") == "application/json"

    def test_takes_no_arguments(self):
        service = build_service(SAVINGS)

        with pytest.raises(TypeError):
            service.get_realized_savings(client_id=1)  # type: ignore[call-arg]
        with pytest.raises(TypeError):
            service.get_realized_savings(days_back=30)  # type: ignore[call-arg]


class TestGetRealizedSavingsResponse:
    def test_returns_the_body_untouched_with_numbers_and_a_string_id(self):
        service = build_service(SAVINGS)

        result = service.get_realized_savings()

        assert result == SAVINGS
        assert isinstance(result["total"], float)
        assert isinstance(result["workloads"][0]["id"], str)
        assert isinstance(result["workloads"][0]["active_requests"], int)

    def test_zeros_and_an_empty_list_are_real_zero_savings(self):
        service = build_service(ZERO_SAVINGS)

        assert service.get_realized_savings() == ZERO_SAVINGS

    def test_a_null_workloads_is_returned_as_sent_rather_than_crashing(self):
        body = {**ZERO_SAVINGS, "workloads": None}
        service = build_service(body, silent=False)

        assert service.get_realized_savings() == body

    def test_a_non_object_body_raises_without_a_status(self):
        service = build_service([])

        with pytest.raises(CoolhandAPIError, match="not a JSON object") as caught:
            service.get_realized_savings()

        assert caught.value.status is None

    def test_a_non_json_body_raises_without_a_status(self):
        service = build_service("<html>502 Bad Gateway</html>")

        with pytest.raises(CoolhandAPIError, match="not valid JSON") as caught:
            service.get_realized_savings()

        assert caught.value.status is None


class TestErrors:
    def test_a_503_raises_with_the_status_and_never_returns_zeros(self):
        body = (
            '{"errors":{"system":["Realized savings could not be computed right now. '
            'Please retry."]}}'
        )
        service = build_service(error=http_error(503, body))

        with pytest.raises(CoolhandAPIError, match="could not be computed") as caught:
            service.get_realized_savings()

        assert caught.value.status == 503

    @pytest.mark.parametrize(
        "body", ['{"error":"API key is required"}', '{"error":"Invalid API key"}']
    )
    def test_a_401_raises_with_the_status_and_body(self, body):
        service = build_service(error=http_error(401, body))

        with pytest.raises(CoolhandAPIError) as caught:
            service.get_realized_savings()

        assert caught.value.status == 401
        assert body in str(caught.value)

    def test_a_malformed_key_never_reaches_the_error_text(self):
        key = "bad\nsecret-key-123"
        service = build_service(SAVINGS, api_key=key)
        service._opener = FakeOpener(error=ValueError(f"Invalid header value {key!r}"))

        with pytest.raises(CoolhandAPIError) as caught:
            service.get_realized_savings()

        assert "secret-key-123" not in str(caught.value)
        assert caught.value.status is None


class TestCoolhandDelegation:
    def test_exposes_the_savings_service(self, mock_config, reset_global_instance):
        instance = Coolhand(config=mock_config)

        assert isinstance(instance.savings_service, SavingsService)

    def test_get_realized_savings_delegates_to_the_service(
        self, mock_config, reset_global_instance
    ):
        instance = Coolhand(config=mock_config)
        instance._savings_service._opener = FakeOpener(response=FakeResponse(SAVINGS))

        result = instance.get_realized_savings()

        request = instance._savings_service._opener.request
        assert urlparse(request.full_url).path == SAVINGS_ENDPOINT
        assert result == SAVINGS
