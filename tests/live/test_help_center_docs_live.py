"""End-to-end proof of the help center reads against a REAL Coolhand server.

Nothing is mocked, and unlike the other live modules this one needs no API key: the
reads are open, so the service is deliberately built without one. Needs only
`COOLHAND_LIVE_BASE_URL`. Anonymous callers are limited to 30 requests per minute per
IP and this module makes about 13, so re-running it back to back can hit a `429` that
has nothing to do with the wrapper.
"""

import os

import pytest

from coolhand import CoolhandAPIError, PublicContentService

LIVE_BASE_URL = os.environ.get("COOLHAND_LIVE_BASE_URL", "")
if not LIVE_BASE_URL:
    raise RuntimeError(
        "Live tests need COOLHAND_LIVE_BASE_URL in the environment. "
        "Set it and re-run `make test-live`."
    )
# Same ceiling as live_env.py, which cannot be imported here: it demands a key.
LIVE_TIMEOUT_SECONDS = 120

ARTICLE_KINDS = {
    "reference",
    "concept",
    "how_to",
    "troubleshooting",
    "faq_answer",
    "release_note",
}


@pytest.fixture
def anonymous(monkeypatch) -> PublicContentService:
    # The environment fallback would otherwise make a "no key" client send a key.
    monkeypatch.delenv("COOLHAND_API_KEY", raising=False)
    return PublicContentService(
        base_url=LIVE_BASE_URL, silent=True, timeout=LIVE_TIMEOUT_SECONDS
    )


def assert_nullable_str(value) -> None:
    assert value is None or isinstance(value, str)


def assert_summary_shape(article: dict) -> None:
    assert isinstance(article["slug"], str)
    assert isinstance(article["title"], str)
    assert_nullable_str(article["answer"])
    assert article["article_kind"] in ARTICLE_KINDS
    assert isinstance(article["tags"], list)
    assert isinstance(article["category"], str)
    assert isinstance(article["url"], str)
    assert isinstance(article["markdown_url"], str)
    assert article["reading_time_minutes"] is None or isinstance(
        article["reading_time_minutes"], int
    )
    assert_nullable_str(article["search_headline"])
    assert_nullable_str(article["published_at"])
    assert_nullable_str(article["updated_content_at"])


class TestPublicContentLive:
    def test_lists_articles_and_reads_pagination_from_the_headers(self, anonymous):
        result = anonymous.search_help_articles(per_page=2)

        articles = result["articles"]
        assert 0 < len(articles) <= 2
        for article in articles:
            assert_summary_shape(article)
            assert "body" not in article
            assert article["search_headline"] is None
        pagination = result["pagination"]
        assert pagination["current_page"] == 1
        assert pagination["per_page"] == 2
        assert pagination["total_count"] >= len(articles)
        assert pagination["has_prev_page"] is False

    def test_a_relevance_search_returns_mark_headlines(self, anonymous):
        result = anonymous.search_help_articles(query="match rate", per_page=2)

        assert result["articles"]
        assert result["pagination"]["per_page"] == 2
        assert any(
            "<mark>" in (article["search_headline"] or "")
            for article in result["articles"]
        )

    def test_pages_through_results(self, anonymous):
        first = anonymous.search_help_articles(per_page=1, page=1)
        second = anonymous.search_help_articles(per_page=1, page=2)

        assert second["pagination"]["current_page"] == 2
        assert second["pagination"]["has_prev_page"] is True
        assert second["articles"][0]["slug"] != first["articles"][0]["slug"]

    def test_filters_by_category_with_and_without_a_query(self, anonymous):
        categories = anonymous.list_help_categories()
        section = next(
            c
            for c in categories
            if c["url_prefix"] == "/help"
            and anonymous.search_help_articles(category=c["slug"], per_page=1)[
                "articles"
            ]
        )

        in_section = anonymous.search_help_articles(
            category=section["slug"], per_page=100
        )
        assert {a["category"] for a in in_section["articles"]} == {section["slug"]}
        everything = anonymous.search_help_articles(per_page=1)
        assert (
            in_section["pagination"]["total_count"]
            <= everything["pagination"]["total_count"]
        )

        combined = anonymous.search_help_articles(
            category=section["slug"], query="feedback"
        )
        assert (
            combined["pagination"]["total_count"]
            <= in_section["pagination"]["total_count"]
        )
        assert {a["category"] for a in combined["articles"]} <= {section["slug"]}

    def test_shows_one_article_with_its_body_and_toc(self, anonymous):
        listed = anonymous.search_help_articles(per_page=1)["articles"][0]

        article = anonymous.get_help_article(listed["slug"])

        assert article["slug"] == listed["slug"]
        assert article["body"]
        assert isinstance(article["noindex"], bool)
        assert isinstance(article["toc"], list)
        assert "search_headline" not in article

    def test_an_unknown_slug_is_a_404(self, anonymous):
        with pytest.raises(CoolhandAPIError) as caught:
            anonymous.get_help_article("definitely-not-a-real-article")

        assert caught.value.status == 404

    def test_lists_the_categories_with_their_tag_vocabulary(self, anonymous):
        categories = anonymous.list_help_categories()

        assert categories
        for category in categories:
            assert isinstance(category["slug"], str)
            assert isinstance(category["name"], str)
            assert isinstance(category["description"], str)
            assert category["url_prefix"] in {"/help", "/updates"}
            assert category["allowed_tags"] is None or isinstance(
                category["allowed_tags"], list
            )

    def test_an_invalid_key_is_a_401_not_an_anonymous_fallback(self):
        service = PublicContentService(
            api_key="not-a-real-key",
            base_url=LIVE_BASE_URL,
            silent=True,
            timeout=LIVE_TIMEOUT_SECONDS,
        )

        with pytest.raises(CoolhandAPIError) as caught:
            service.list_help_categories()

        assert caught.value.status == 401
