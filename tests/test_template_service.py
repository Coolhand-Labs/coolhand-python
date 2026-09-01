"""Tests for TemplateService."""

import json
from email.message import Message
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import HTTPRedirectHandler

import pytest

from coolhand import (
    Coolhand,
    CoolhandAPIError,
    TemplateService,
    get_template_service,
)
from coolhand.template_service import (
    DEFAULT_TIMEOUT_SECONDS,
    TEMPLATES_ENDPOINT,
    _RefuseRedirects,
)

BASE_URL = "https://test.coolhandlabs.com"

SUMMARY_ROW = {
    "id": "tmpl123abc456",
    "name": "Summarize ticket",
    "status": "published",
    "version": "3",
    "group": "user_prompt_with_system_prompt",
    "workload_id": "wkld789xyz123",
    "workload_name": "Support",
    "system_template": False,
    "deprecated_at": None,
    "log_count": 42,
    "created_at": "2026-08-01T00:00:00Z",
    "updated_at": "2026-08-02T00:00:00Z",
}

DETAIL_BODY = {
    **SUMMARY_ROW,
    "user_prompt_pattern": "^Summarize: (.*)$",
    "system_prompt_pattern": None,
}

PAGINATION_HEADERS = {
    "X-Page": "1",
    "X-Per-Page": "25",
    "X-Total-Count": "1",
    "X-Total-Pages": "1",
}


class _FakeResponse:
    """Stands in for what urlopen yields: a context manager with read() and headers."""

    def __init__(self, body, headers=None):
        raw = body if isinstance(body, str) else json.dumps(body)
        self._body = raw.encode("utf-8")
        self.headers = Message()
        for key, value in (headers or {}).items():
            self.headers[key] = value

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _FakeOpener:
    """Records the Request it was handed, then returns a canned response or raises."""

    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.request = None
        self.timeout = None

    def open(self, request, timeout=None):
        self.request = request
        self.timeout = timeout
        if self.error is not None:
            raise self.error
        return self.response


def build_service(body=None, headers=None, error=None, **config):
    """Create a TemplateService whose transport is a `_FakeOpener`."""
    settings = {"api_key": "test-private-key", "base_url": BASE_URL, "silent": True}
    settings.update(config)
    service = TemplateService(**settings)
    response = None if error is not None else _FakeResponse(body, headers)
    service._opener = _FakeOpener(response=response, error=error)
    return service


def http_error(status, body):
    """Build an HTTPError whose body reads back, the way a real one does."""
    error = HTTPError(
        url=f"{BASE_URL}{TEMPLATES_ENDPOINT}",
        code=status,
        msg="error",
        hdrs=Message(),
        fp=None,
    )
    error.read = lambda: body.encode("utf-8")
    return error


def query_of(request):
    return parse_qs(urlparse(request.full_url).query)


@pytest.fixture
def reset_default_template_service():
    """Reset the module-level default service between tests."""
    import coolhand.template_service as ts

    original = ts._default_service
    ts._default_service = None
    yield
    ts._default_service = original


class TestSearchTemplatesRequest:
    """Test how search_templates builds its request."""

    def test_no_filters_sends_no_query_string(self):
        service = build_service([], PAGINATION_HEADERS)

        service.search_templates()

        assert urlparse(service._opener.request.full_url).query == ""

    def test_targets_the_list_endpoint(self):
        service = build_service([], PAGINATION_HEADERS)

        service.search_templates()

        parsed = urlparse(service._opener.request.full_url)
        assert parsed.path == TEMPLATES_ENDPOINT
        assert service._opener.request.get_method() == "GET"

    def test_maps_every_filter_onto_its_wire_param(self):
        service = build_service([], PAGINATION_HEADERS)

        service.search_templates(
            search="summar",
            workload_id="wkld789xyz123",
            status="published",
            include_deprecated=True,
            include_system=True,
            page=2,
            per=50,
        )

        assert query_of(service._opener.request) == {
            "search": ["summar"],
            "workload_id": ["wkld789xyz123"],
            "status": ["published"],
            "include_deprecated": ["true"],
            "include_system": ["true"],
            "page": ["2"],
            "per": ["50"],
        }

    def test_sends_per_not_per_page(self):
        service = build_service([], PAGINATION_HEADERS)

        service.search_templates(per=10)

        query = query_of(service._opener.request)
        assert query["per"] == ["10"]
        assert "per_page" not in query

    def test_booleans_go_over_the_wire_lowercase(self):
        # str(False) is "False", which Rails casts to true; only "false" reads as false.
        service = build_service([], PAGINATION_HEADERS)

        service.search_templates(include_system=False, include_deprecated=False)

        query = query_of(service._opener.request)
        assert query["include_system"] == ["false"]
        assert query["include_deprecated"] == ["false"]

    def test_omits_filters_left_at_none(self):
        service = build_service([], PAGINATION_HEADERS)

        service.search_templates(search="summar")

        assert query_of(service._opener.request) == {"search": ["summar"]}

    def test_rejects_a_client_id_the_caller_tries_to_supply(self):
        # The client comes from the API key; there is no client_id to pass through.
        service = build_service([], PAGINATION_HEADERS)

        with pytest.raises(TypeError):
            service.search_templates(client_id="cli123")

    def test_filters_are_keyword_only(self):
        service = build_service([], PAGINATION_HEADERS)

        with pytest.raises(TypeError):
            service.search_templates("summar")

    def test_sends_the_api_key_and_json_accept_headers(self):
        service = build_service([], PAGINATION_HEADERS)

        service.search_templates()

        headers = service._opener.request.headers
        assert headers["X-api-key"] == "test-private-key"
        assert headers["Accept"] == "application/json"
        assert "coolhand-python/" in headers["User-agent"]

    def test_sends_an_empty_api_key_header_when_unconfigured(self):
        # The server treats an empty header value as no key at all and answers 401.
        service = build_service([], PAGINATION_HEADERS, api_key="")

        service.search_templates()

        assert service._opener.request.headers["X-api-key"] == ""


class TestSearchTemplatesResponse:
    """Test what search_templates returns."""

    def test_returns_the_rows_the_server_sent(self):
        service = build_service([SUMMARY_ROW], PAGINATION_HEADERS)

        result = service.search_templates()

        assert result["templates"] == [SUMMARY_ROW]

    def test_list_rows_carry_no_prompt_patterns(self):
        service = build_service([SUMMARY_ROW], PAGINATION_HEADERS)

        result = service.search_templates()

        assert "user_prompt_pattern" not in result["templates"][0]
        assert "system_prompt_pattern" not in result["templates"][0]

    def test_pagination_comes_from_headers_not_from_row_count(self):
        service = build_service(
            [SUMMARY_ROW, SUMMARY_ROW],
            {
                "X-Page": "2",
                "X-Per-Page": "2",
                "X-Total-Count": "7",
                "X-Total-Pages": "4",
            },
        )

        pagination = service.search_templates(page=2, per=2)["pagination"]

        assert pagination == {
            "current_page": 2,
            "per_page": 2,
            "total_count": 7,
            "total_pages": 4,
            "has_next_page": True,
            "has_prev_page": True,
        }

    def test_reports_total_pages_as_sent_even_when_the_count_is_zero(self):
        # The live server sends X-Total-Pages: 1 alongside X-Total-Count: 0.
        service = build_service(
            [],
            {
                "X-Page": "1",
                "X-Per-Page": "25",
                "X-Total-Count": "0",
                "X-Total-Pages": "1",
            },
        )

        pagination = service.search_templates()["pagination"]

        assert pagination["total_count"] == 0
        assert pagination["total_pages"] == 1
        assert pagination["has_next_page"] is False
        assert pagination["has_prev_page"] is False

    def test_last_page_has_no_next_page(self):
        service = build_service(
            [SUMMARY_ROW],
            {
                "X-Page": "4",
                "X-Per-Page": "2",
                "X-Total-Count": "7",
                "X-Total-Pages": "4",
            },
        )

        pagination = service.search_templates(page=4, per=2)["pagination"]

        assert pagination["has_next_page"] is False
        assert pagination["has_prev_page"] is True

    def test_malformed_headers_fall_back_to_the_requested_page_and_size(self):
        service = build_service(
            [SUMMARY_ROW],
            {"X-Page": "not-a-number", "X-Per-Page": "", "X-Total-Count": "12.5"},
        )

        pagination = service.search_templates(page=3, per=10)["pagination"]

        assert pagination["current_page"] == 3
        assert pagination["per_page"] == 10
        assert pagination["total_count"] == 0

    def test_non_ascii_digit_headers_fall_back(self):
        # str.isdigit() is true for "١٢" and "²", but int() raises on the
        # latter — hence the isascii() guard rather than a bare int() in a try block.
        service = build_service(
            [SUMMARY_ROW], {"X-Total-Count": "١٢", "X-Total-Pages": "²"}
        )

        pagination = service.search_templates()["pagination"]

        assert pagination["total_count"] == 0
        assert pagination["total_pages"] == 0

    def test_missing_headers_fall_back_to_the_documented_defaults(self):
        service = build_service([SUMMARY_ROW], {})

        pagination = service.search_templates()["pagination"]

        assert pagination["current_page"] == 1
        assert pagination["per_page"] == 25

    def test_a_page_size_above_the_server_max_is_clamped_in_the_fallback(self):
        service = build_service([SUMMARY_ROW], {})

        pagination = service.search_templates(per=500)["pagination"]

        assert pagination["per_page"] == 100

    def test_rejects_a_body_that_is_not_an_array(self):
        service = build_service({"templates": []}, PAGINATION_HEADERS)

        with pytest.raises(CoolhandAPIError) as excinfo:
            service.search_templates()

        assert excinfo.value.status is None


class TestGetTemplate:
    """Test get_template."""

    def test_targets_the_show_endpoint(self):
        service = build_service(DETAIL_BODY)

        service.get_template("tmpl123abc456")

        parsed = urlparse(service._opener.request.full_url)
        assert parsed.path == f"{TEMPLATES_ENDPOINT}/tmpl123abc456"

    def test_returns_both_prompt_patterns(self):
        service = build_service(DETAIL_BODY)

        template = service.get_template("tmpl123abc456")

        assert template["user_prompt_pattern"] == "^Summarize: (.*)$"
        assert template["system_prompt_pattern"] is None

    def test_url_encodes_the_id(self):
        service = build_service(DETAIL_BODY)

        service.get_template("a b/c?d#e")

        parsed = urlparse(service._opener.request.full_url)
        assert parsed.path == f"{TEMPLATES_ENDPOINT}/a%20b%2Fc%3Fd%23e"
        # The "?" must not have opened a query string on the show request.
        assert parsed.query == ""

    @pytest.mark.parametrize("template_id", ["", "   ", "\t"])
    def test_rejects_a_blank_id(self, template_id):
        service = build_service(DETAIL_BODY)

        with pytest.raises(ValueError, match="non-empty string"):
            service.get_template(template_id)

    @pytest.mark.parametrize("template_id", [".", "..", " .. "])
    def test_rejects_a_dot_segment_that_would_retarget_the_request(self, template_id):
        service = build_service(DETAIL_BODY)

        with pytest.raises(ValueError, match="relative path segment"):
            service.get_template(template_id)

    def test_rejects_a_non_string_id(self):
        service = build_service(DETAIL_BODY)

        with pytest.raises(ValueError, match="non-empty string"):
            service.get_template(123)

    def test_rejects_a_body_that_is_not_an_object(self):
        service = build_service([DETAIL_BODY])

        with pytest.raises(CoolhandAPIError) as excinfo:
            service.get_template("tmpl123abc456")

        assert excinfo.value.status is None


class TestErrorHandling:
    """Test the read-path error convention: raise, carrying the HTTP status."""

    @pytest.mark.parametrize("status", [401, 404, 422, 504])
    def test_a_non_2xx_response_raises_with_its_status_attached(self, status):
        service = build_service(error=http_error(status, '{"error":"nope"}'))

        with pytest.raises(CoolhandAPIError) as excinfo:
            service.search_templates()

        assert excinfo.value.status == status

    def test_a_504_is_distinguishable_without_matching_on_the_message(self):
        # log_count aggregates over llm_request_logs and can exceed the statement
        # timeout; a caller has to branch on that and retry with a narrower query.
        service = build_service(
            error=http_error(504, '{"errors":{"system":["timeout"]}}')
        )

        with pytest.raises(CoolhandAPIError) as excinfo:
            service.search_templates()

        assert excinfo.value.status == 504

    def test_the_error_message_carries_the_server_body(self):
        service = build_service(
            error=http_error(
                422,
                '{"errors":{"status":["must be one of: draft, published, failure"]}}',
            )
        )

        with pytest.raises(CoolhandAPIError, match="must be one of"):
            service.search_templates()

    def test_get_template_also_raises_with_its_status(self):
        service = build_service(error=http_error(404, '{"errors":{}}'))

        with pytest.raises(CoolhandAPIError) as excinfo:
            service.get_template("tmpl123abc456")

        assert excinfo.value.status == 404

    def test_a_transport_failure_raises_with_no_status(self):
        service = build_service(error=URLError("connection refused"))

        with pytest.raises(CoolhandAPIError) as excinfo:
            service.search_templates()

        assert excinfo.value.status is None

    def test_a_non_json_body_raises_with_no_status(self):
        service = build_service("<html>gateway</html>", PAGINATION_HEADERS)

        with pytest.raises(CoolhandAPIError) as excinfo:
            service.search_templates()

        assert excinfo.value.status is None

    def test_an_unreadable_error_body_still_reports_the_status(self):
        error = http_error(500, "{}")
        error.read = lambda: (_ for _ in ()).throw(OSError("stream closed"))
        service = build_service(error=error)

        with pytest.raises(CoolhandAPIError) as excinfo:
            service.search_templates()

        assert excinfo.value.status == 500


class TestRedirectHandling:
    """Test that a redirect is refused rather than followed."""

    def test_the_opener_refuses_to_follow_a_redirect(self):
        service = TemplateService(api_key="k", base_url=BASE_URL, silent=True)

        handlers = [type(handler) for handler in service._opener.handlers]

        assert _RefuseRedirects in handlers
        # The stock handler must be displaced, not merely accompanied — if urllib still
        # held one it would follow the 3xx and carry X-API-Key to the new host.
        assert HTTPRedirectHandler not in handlers

    def test_refusing_a_redirect_means_returning_no_new_request(self):
        # Returning None is what makes urllib surface the 3xx instead of re-sending
        # X-API-Key to whatever host the redirect names.
        assert (
            _RefuseRedirects().redirect_request(
                None, None, 302, "Found", Message(), "https://evil.example.com"
            )
            is None
        )


class TestTimeout:
    """Test the read-path HTTP timeout."""

    def test_defaults_above_the_servers_statement_timeout(self):
        # The server's own budget is 10s and it answers 504 when it trips; a shorter
        # client timeout would abort the connection instead of surfacing that 504.
        assert DEFAULT_TIMEOUT_SECONDS > 10

        service = build_service([], PAGINATION_HEADERS)
        service.search_templates()

        assert service._opener.timeout == DEFAULT_TIMEOUT_SECONDS

    def test_is_configurable(self):
        service = build_service([], PAGINATION_HEADERS, timeout=120)

        service.search_templates()

        assert service._opener.timeout == 120


class TestTemplateServiceInit:
    """Test TemplateService initialization."""

    def test_normalizes_a_trailing_slash_on_the_base_url(self):
        service = TemplateService(api_key="k", base_url=f"{BASE_URL}/")

        assert service.config["base_url"] == BASE_URL

    def test_rejects_a_non_local_http_base_url(self):
        with pytest.raises(ValueError, match="must use https"):
            TemplateService(api_key="k", base_url="http://evil.example.com")

    def test_accepts_a_local_http_base_url(self):
        service = TemplateService(api_key="k", base_url="http://127.0.0.1:3111")

        assert service.config["base_url"] == "http://127.0.0.1:3111"

    def test_api_key_is_a_string_even_when_config_holds_none(self):
        service = TemplateService({"api_key": None, "base_url": BASE_URL})

        assert service.api_key == ""


class TestGetTemplateService:
    """Test the module-level service accessor."""

    def test_returns_the_cached_default_when_called_bare(
        self, reset_default_template_service
    ):
        first = get_template_service(api_key="k", base_url=BASE_URL)

        assert get_template_service() is first

    def test_a_config_always_builds_a_new_service(self, reset_default_template_service):
        first = get_template_service(api_key="k", base_url=BASE_URL)
        second = get_template_service(api_key="other", base_url=BASE_URL)

        assert second is not first
        assert second.api_key == "other"


class TestCoolhandDelegation:
    """Test that the Coolhand facade exposes both read methods."""

    def test_exposes_the_template_service(self, mock_config, reset_global_instance):
        instance = Coolhand(config=mock_config)

        assert isinstance(instance.template_service, TemplateService)

    def test_search_templates_delegates_with_its_filters(
        self, mock_config, reset_global_instance
    ):
        instance = Coolhand(config=mock_config)
        instance._template_service._opener = _FakeOpener(
            response=_FakeResponse([SUMMARY_ROW], PAGINATION_HEADERS)
        )

        result = instance.search_templates(include_system=True, per=5)

        query = query_of(instance._template_service._opener.request)
        assert query == {"include_system": ["true"], "per": ["5"]}
        assert result["templates"] == [SUMMARY_ROW]

    def test_get_template_delegates(self, mock_config, reset_global_instance):
        instance = Coolhand(config=mock_config)
        instance._template_service._opener = _FakeOpener(
            response=_FakeResponse(DETAIL_BODY)
        )

        template = instance.get_template("tmpl123abc456")

        assert template["id"] == "tmpl123abc456"
        assert "user_prompt_pattern" in template
