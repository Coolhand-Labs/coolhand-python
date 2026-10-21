"""Read-only access to the Coolhand help center (the `public_content` endpoints).

Wraps `GET /api/v2/public_content`, `GET /api/v2/public_content/{slug}` and
`GET /api/v2/public_content/categories`: the pages served at `/help/:slug` and
`/updates/:slug`. "Help center" is the user-facing name, "public content" the wire
resource. Creating, updating and archiving articles is Coolhand-internal and not
wrapped.

Unlike every other service these reads need no API key.
"""

from typing import cast

from ._read_service import (
    QueryValue,
    ReadService,
    _encode_path_id,
    _pagination_from_headers,
    _with_query,
)
from .types import (
    HelpArticle,
    HelpArticleSummary,
    HelpCategory,
    SearchHelpArticlesResponse,
)

PUBLIC_CONTENT_ENDPOINT = "/api/v2/public_content"


class PublicContentService(ReadService):
    """Read the Coolhand help center, with or without an API key.

    Anonymous callers are rate limited to 30 requests per minute per IP (`429` beyond
    that). A key, from `api_key` or else `COOLHAND_API_KEY`, is sent as `X-API-Key` and
    lifts the limit; a key the server does not recognise is a `401`, never a silent
    fallback to anonymous. With no key, no header is sent.

    Example:
        >>> from coolhand import PublicContentService
        >>> service = PublicContentService()
        >>> result = service.search_help_articles(query="match rate", per_page=5)
        >>> article = service.get_help_article(result["articles"][0]["slug"])
    """

    _resource_label = "Help center"
    _keyless = True

    def search_help_articles(
        self,
        *,
        query: str | None = None,
        category: str | None = None,
        page: int | None = None,
        per_page: int | None = None,
    ) -> SearchHelpArticlesResponse:
        """Search or list published articles.

        With `query`, results are relevance-ranked (the Postgres full-text search the
        website uses) and each carries a `search_headline`; without it, the full
        listing.

        The endpoint has no `category` parameter, so `category` is sent in its
        structured form, `q[help_category_slug_eq]=<category>`, with `query` moving to
        `q[full_text]`. The plain string `q` and the `q[...]` form cannot be mixed,
        hence the switch.

        Args:
            query: Relevance search; supports quoted phrases, `-exclusions` and `or`.
                Blank is treated as omitted.
            category: Restrict to one section by slug (see `list_help_categories`).
                Blank is treated as omitted.
            page: Page number, 1-based.
            per_page: Page size (default 25, max 100, both enforced server-side).

        Returns:
            A dict with `articles` and `pagination`, the latter read from the `X-Page`,
            `X-Per-Page`, `X-Total-Count` and `X-Total-Pages` response headers.

        Raises:
            ValueError: If `query` or `category` is not a string, before any request
                is made.
            CoolhandAPIError: On a non-2xx response, with `status` set (`401` for an
                invalid key, `429` once the anonymous rate limit is exceeded). Also
                raised, with `status` left `None`, on a transport failure or a body
                that is not the JSON array this endpoint returns.
        """
        for name, value in (("query", query), ("category", category)):
            if value is not None and not isinstance(value, str):
                raise ValueError(f"search_help_articles: {name} must be a string")
        text = (query or "").strip() or None
        section = (category or "").strip() or None
        filters: dict[str, QueryValue]
        if section is not None:
            filters = {"q[help_category_slug_eq]": section, "q[full_text]": text}
        else:
            filters = {"q": text}
        filters.update({"page": page, "per_page": per_page})
        url = _with_query(
            f"{self.config['base_url']}{PUBLIC_CONTENT_ENDPOINT}", filters
        )

        body, headers = self._get_json_array(url, "Help center search")

        articles = cast(list[HelpArticleSummary], body)
        self._log(f"Fetched {len(articles)} help article(s)")
        return {
            "articles": articles,
            "pagination": _pagination_from_headers(headers, page, per_page),
        }

    def get_help_article(self, slug: str) -> HelpArticle:
        """Fetch one published article by slug, with its markdown `body` and `toc`.

        Args:
            slug: The article's `slug`, as returned by `search_help_articles`.

        Raises:
            ValueError: If `slug` is blank, not a string, or a relative path segment,
                before any request is made.
            CoolhandAPIError: On a non-2xx response, with `status` set (`404` for an
                unknown or unpublished slug, `401`, `429`).
        """
        encoded = _encode_path_id(slug, "slug", "get_help_article")
        url = f"{self.config['base_url']}{PUBLIC_CONTENT_ENDPOINT}/{encoded}"

        body, _ = self._get_json_object(url, "Help article")
        return cast(HelpArticle, body)

    def list_help_categories(self) -> list[HelpCategory]:
        """List every section articles are filed under, with its tag vocabulary.

        Use a section's `slug` as `category` on `search_help_articles`.

        Raises:
            CoolhandAPIError: On a non-2xx response, with `status` set (`401`, `429`).
        """
        url = f"{self.config['base_url']}{PUBLIC_CONTENT_ENDPOINT}/categories"
        body, _ = self._get_json_array(url, "Help center categories")
        return cast(list[HelpCategory], body)
