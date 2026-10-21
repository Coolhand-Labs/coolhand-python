"""Tests for HelpCenterDocs."""

from urllib.parse import parse_qs, urlparse

import pytest

from coolhand import Coolhand, CoolhandAPIError, HelpCenterDocs
from coolhand.help_center_docs import PUBLIC_CONTENT_ENDPOINT

from .fake_transport import (
    BASE_URL,
    PAGINATION_HEADERS,
    FakeOpener,
    FakeResponse,
    query_of,
)
from .fake_transport import build_service as fake_build_service
from .fake_transport import http_error as fake_http_error

SUMMARY = {
    "slug": "what-is-feedback-match-rate",
    "title": "What is feedback match rate?",
    "answer": None,
    "article_kind": "concept",
    "tags": ["feedback"],
    "category": "feedback-and-prompts",
    "url": "https://coolhandlabs.com/help/what-is-feedback-match-rate",
    "markdown_url": "https://coolhandlabs.com/help/what-is-feedback-match-rate.md",
    "reading_time_minutes": None,
    "search_headline": "<mark>Match</mark> rate is ...",
    "published_at": "2026-08-30T13:57:52Z",
    "updated_content_at": None,
}

ARTICLE = {
    **{key: value for key, value in SUMMARY.items() if key != "search_headline"},
    "body": "# Match rate\n\nFull markdown.",
    "noindex": False,
    "toc": [{"id": "match-rate", "text": "Match rate", "level": 2, "children": []}],
}

CATEGORY = {
    "slug": "providers",
    "name": "Provider guides",
    "description": "How each provider is captured",
    "url_prefix": "/help",
    "allowed_tags": None,
}


def build_service(body=None, headers=None, error=None, **config):
    return fake_build_service(HelpCenterDocs, body, headers, error, **config)


def keyless_service(body=None, headers=None, error=None):
    return build_service(body, headers, error, api_key="")


def http_error(status, body, path=PUBLIC_CONTENT_ENDPOINT):
    return fake_http_error(f"{BASE_URL}{path}", status, body)


class TestSearchRequest:
    def test_a_plain_listing_sends_no_query(self):
        service = build_service([], PAGINATION_HEADERS)

        service.search_help_articles()

        request = service._opener.request
        parsed = urlparse(request.full_url)
        assert parsed.path == PUBLIC_CONTENT_ENDPOINT
        assert parsed.query == ""
        assert request.get_method() == "GET"

    def test_a_query_alone_is_the_plain_q_string(self):
        service = build_service([], PAGINATION_HEADERS)

        service.search_help_articles(query="match rate")

        assert query_of(service._opener.request) == {"q": ["match rate"]}

    def test_a_category_alone_uses_the_structured_form(self):
        service = build_service([], PAGINATION_HEADERS)

        service.search_help_articles(category="providers")

        request = service._opener.request
        assert (
            urlparse(request.full_url).query == "q%5Bhelp_category_slug_eq%5D=providers"
        )

    def test_query_and_category_use_the_structured_form_for_both(self):
        service = build_service([], PAGINATION_HEADERS)

        service.search_help_articles(query="anthropic", category="providers")

        request = service._opener.request
        assert urlparse(request.full_url).query == (
            "q%5Bhelp_category_slug_eq%5D=providers&q%5Bfull_text%5D=anthropic"
        )

    @pytest.mark.parametrize("blank", ["", "   "])
    def test_a_blank_query_or_category_is_omitted(self, blank):
        service = build_service([], PAGINATION_HEADERS)

        service.search_help_articles(query=blank, category=blank)

        assert urlparse(service._opener.request.full_url).query == ""

    def test_a_blank_query_with_a_category_sends_only_the_category(self):
        service = build_service([], PAGINATION_HEADERS)

        service.search_help_articles(query=" ", category="providers")

        assert query_of(service._opener.request) == {
            "q[help_category_slug_eq]": ["providers"]
        }

    def test_page_and_per_page_are_sent_under_their_wire_names(self):
        service = build_service([], PAGINATION_HEADERS)

        service.search_help_articles(page=2, per_page=10)

        assert query_of(service._opener.request) == {"page": ["2"], "per_page": ["10"]}

    def test_never_sends_a_category_or_corpus_param(self):
        service = build_service([], PAGINATION_HEADERS)

        service.search_help_articles(query="x", category="providers")

        query = query_of(service._opener.request)
        assert "category" not in query
        assert "corpus" not in query


class TestSearchResponse:
    def test_returns_the_bare_array_and_header_pagination(self):
        headers = {
            "X-Page": "2",
            "X-Per-Page": "10",
            "X-Total-Count": "25",
            "X-Total-Pages": "3",
        }
        service = build_service([SUMMARY], headers)

        result = service.search_help_articles(page=2, per_page=10)

        assert result["articles"] == [SUMMARY]
        assert result["articles"][0]["answer"] is None
        assert result["pagination"] == {
            "current_page": 2,
            "per_page": 10,
            "total_count": 25,
            "total_pages": 3,
            "has_next_page": True,
            "has_prev_page": True,
        }

    def test_a_non_array_body_raises(self):
        service = build_service({"error": "x"}, PAGINATION_HEADERS)

        with pytest.raises(CoolhandAPIError, match="not a JSON array") as caught:
            service.search_help_articles()

        assert caught.value.status is None


class TestGetHelpArticle:
    def test_fetches_by_slug_and_returns_the_body(self):
        service = build_service(ARTICLE)

        article = service.get_help_article("what-is-feedback-match-rate")

        path = urlparse(service._opener.request.full_url).path
        assert path == f"{PUBLIC_CONTENT_ENDPOINT}/what-is-feedback-match-rate"
        assert article["body"].startswith("# Match rate")
        assert article["toc"][0]["level"] == 2
        assert "search_headline" not in article

    def test_the_slug_is_percent_encoded_as_one_path_segment(self):
        service = build_service(ARTICLE)

        service.get_help_article("a/b c")

        path = urlparse(service._opener.request.full_url).path
        assert path == f"{PUBLIC_CONTENT_ENDPOINT}/a%2Fb%20c"

    @pytest.mark.parametrize("slug", ["", "   ", ".", ".."])
    def test_rejects_a_bad_slug_before_any_request(self, slug):
        service = build_service(ARTICLE)

        with pytest.raises(ValueError, match="get_help_article: slug"):
            service.get_help_article(slug)

        assert service._opener.request is None

    def test_a_non_object_body_raises(self):
        service = build_service([])

        with pytest.raises(CoolhandAPIError, match="not a JSON object"):
            service.get_help_article("x")


class TestListHelpCategories:
    def test_targets_the_categories_route_and_returns_the_array(self):
        service = build_service([CATEGORY])

        categories = service.list_help_categories()

        path = urlparse(service._opener.request.full_url).path
        assert path == f"{PUBLIC_CONTENT_ENDPOINT}/categories"
        assert categories == [CATEGORY]
        assert categories[0]["allowed_tags"] is None


class TestApiKey:
    def test_sends_x_api_key_when_one_is_configured(self):
        service = build_service([], PAGINATION_HEADERS, api_key="any-key")

        service.search_help_articles()

        assert service._opener.request.get_header("X-api-key") == "any-key"

    def test_sends_no_header_at_all_without_a_key(self):
        service = keyless_service([], PAGINATION_HEADERS)

        service.search_help_articles()

        request = service._opener.request
        assert not request.has_header("X-api-key")
        assert "X-api-key" not in dict(request.header_items())

    def test_works_with_no_arguments_and_no_environment_key(self):
        service = HelpCenterDocs()
        service._opener = FakeOpener(response=FakeResponse([CATEGORY]))

        assert service.list_help_categories() == [CATEGORY]
        assert not service._opener.request.has_header("X-api-key")

    def test_falls_back_to_the_environment_key(self, monkeypatch):
        monkeypatch.setenv("COOLHAND_API_KEY", "env-key")
        service = HelpCenterDocs()
        service._opener = FakeOpener(response=FakeResponse([CATEGORY]))

        service.list_help_categories()

        assert service._opener.request.get_header("X-api-key") == "env-key"

    def test_an_explicit_key_beats_the_environment(self, monkeypatch):
        monkeypatch.setenv("COOLHAND_API_KEY", "env-key")
        service = HelpCenterDocs(api_key="explicit")
        service._opener = FakeOpener(response=FakeResponse([CATEGORY]))

        service.list_help_categories()

        assert service._opener.request.get_header("X-api-key") == "explicit"


class TestKeylessEdgeCases:
    @pytest.mark.parametrize("blank", ["", "  ", "\n"])
    def test_a_blank_key_sends_no_header(self, blank, monkeypatch):
        monkeypatch.delenv("COOLHAND_API_KEY", raising=False)
        service = HelpCenterDocs(api_key=blank)
        service._opener = FakeOpener(response=FakeResponse([CATEGORY]))

        service.list_help_categories()

        assert not service._opener.request.has_header("X-api-key")

    def test_a_blank_environment_key_sends_no_header(self, monkeypatch):
        monkeypatch.setenv("COOLHAND_API_KEY", "\n")
        service = HelpCenterDocs()
        service._opener = FakeOpener(response=FakeResponse([CATEGORY]))

        service.list_help_categories()

        assert not service._opener.request.has_header("X-api-key")

    @pytest.mark.parametrize("argument", ["query", "category"])
    def test_a_non_string_filter_raises_before_any_request(self, argument):
        service = build_service([], PAGINATION_HEADERS)

        with pytest.raises(ValueError, match=f"{argument} must be a string"):
            service.search_help_articles(**{argument: 5})

        assert service._opener.request is None


class TestFilterWhitespace:
    def test_surrounding_whitespace_is_trimmed_from_both_filters(self):
        service = build_service([], PAGINATION_HEADERS)

        service.search_help_articles(query="  feedback ", category=" providers ")

        params = parse_qs(urlparse(service._opener.request.full_url).query)
        assert params["q[help_category_slug_eq]"] == ["providers"]
        assert params["q[full_text]"] == ["feedback"]


class TestErrors:
    @pytest.mark.parametrize(
        ("status", "body"),
        [
            (401, '{"error":"Invalid API key."}'),
            (404, '{"error":"Not found"}'),
            (429, "Retry later"),
        ],
    )
    def test_a_non_2xx_raises_with_the_status_and_body(self, status, body):
        service = build_service(error=http_error(status, body), api_key="bad")

        with pytest.raises(CoolhandAPIError) as caught:
            service.get_help_article("x")

        assert caught.value.status == status
        assert body in str(caught.value)
        assert service._opener.request.get_header("X-api-key") == "bad"

    def test_a_401_is_not_retried_without_the_key(self):
        service = build_service(
            error=http_error(401, '{"error":"Invalid API key."}'), api_key="bad"
        )
        calls = []

        class CountingOpener(FakeOpener):
            def open(self, request, timeout=None):
                calls.append(request)
                return super().open(request, timeout)

        service._opener = CountingOpener(error=service._opener.error)

        with pytest.raises(CoolhandAPIError):
            service.list_help_categories()

        assert len(calls) == 1

    def test_a_non_json_body_raises_without_a_status(self):
        service = build_service("<html>")

        with pytest.raises(CoolhandAPIError, match="not valid JSON") as caught:
            service.list_help_categories()

        assert caught.value.status is None


class TestCoolhandDelegation:
    def test_exposes_the_help_center_docs(self, mock_config, reset_global_instance):
        instance = Coolhand(config=mock_config)

        assert isinstance(instance.help_center_docs, HelpCenterDocs)

    def test_the_three_methods_delegate_and_send_the_instance_key(
        self, mock_config, reset_global_instance
    ):
        instance = Coolhand(config=mock_config)
        service = instance.help_center_docs

        service._opener = FakeOpener(
            response=FakeResponse([SUMMARY], PAGINATION_HEADERS)
        )
        result = instance.search_help_articles(
            query="x", category="providers", page=2, per_page=5
        )
        assert query_of(service._opener.request) == {
            "q[help_category_slug_eq]": ["providers"],
            "q[full_text]": ["x"],
            "page": ["2"],
            "per_page": ["5"],
        }
        assert result["articles"] == [SUMMARY]
        assert service._opener.request.get_header("X-api-key") == mock_config["api_key"]

        service._opener = FakeOpener(response=FakeResponse(ARTICLE))
        assert instance.get_help_article("a")["body"] == ARTICLE["body"]

        service._opener = FakeOpener(response=FakeResponse([CATEGORY]))
        assert instance.list_help_categories() == [CATEGORY]
