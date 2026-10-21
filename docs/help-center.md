# Reading the Help Center

`HelpCenterDocs` reads Coolhand's help center: the articles served at
`coolhandlabs.com/help/:slug` and the posts at `/updates/:slug`. "Help center" is the
user-facing name; on the wire the resource is `public_content`
(`GET /api/v2/public_content`, `/{slug}` and `/categories`). It is read-only: creating,
updating and archiving articles is Coolhand-internal and not wrapped.

**Auth:** none required. Unlike every other service, this one can be constructed with no
key, and without a `Coolhand` instance:

```python
from coolhand import HelpCenterDocs

help_center = HelpCenterDocs()                   # anonymous
keyed = HelpCenterDocs(api_key="any-key")        # lifts the rate limit
```

- No key is configured: no `X-API-Key` header is sent, and the calls work. Anonymous
  callers are limited to **30 requests per minute per IP**; beyond that the server answers
  `429`.
- A key is sent as `X-API-Key` when `api_key` is passed, else when `COOLHAND_API_KEY` is
  set. Callers with a valid key are not rate limited.
- A key the server does not recognise is a `401` (`{"error":"Invalid API key."}`) even
  though the endpoint is public. It is raised, not retried anonymously.

A `Coolhand` instance exposes the same methods (`ch.search_help_articles(...)` etc.) and
sends its own key.

## `search_help_articles(...)`

```python
result = help_center.search_help_articles(
    query="match rate",
    category="feedback-and-prompts",
    per_page=10,
)
result["articles"], result["pagination"]
```

All arguments are keyword-only and optional.

| Argument | Type | Notes |
|---|---|---|
| `query` | `str` | Relevance search (Postgres full text, the same as the website's). Supports quoted phrases, `-exclusions` and `or`. Omit for a full listing. A blank string is treated as omitted |
| `category` | `str` | Restrict to one section by slug (see `list_help_categories`). A blank string is treated as omitted |
| `page` | `int` | Page number, 1-based |
| `per_page` | `int` | Page size, default 25, max 100 (both enforced server-side) |

The endpoint has no `category` parameter. `category` is sent in its structured form
instead, which also moves the relevance search to a different key; the plain `q=...`
string and the `q[...]` form cannot be mixed:

| `query` | `category` | Sent |
|---|---|---|
| set | unset | `q=<query>` |
| unset | set | `q[help_category_slug_eq]=<category>` |
| set | set | `q[help_category_slug_eq]=<category>&q[full_text]=<query>` |

### Return value

A `SearchHelpArticlesResponse`: a dict with `articles` and `pagination`. Each article is a
`HelpArticleSummary`; the markdown `body` is omitted from lists:

```python
{
    "slug": "what-is-feedback-match-rate",
    "title": "What is feedback match rate?",
    "answer": "Match rate is the share of ...",   # str | None
    "article_kind": "concept",  # reference | concept | how_to | troubleshooting | faq_answer | release_note
    "tags": ["feedback", "matching"],
    "category": "feedback-and-prompts",           # section slug
    "url": "https://coolhandlabs.com/help/what-is-feedback-match-rate",
    "markdown_url": "https://coolhandlabs.com/help/what-is-feedback-match-rate.md",
    "reading_time_minutes": 10,                   # int | None
    "search_headline": "<mark>Match</mark> <mark>rate</mark> is ...",  # None on a plain listing
    "published_at": "2026-08-30T13:57:52Z",       # str | None
    "updated_content_at": "2026-08-30T13:57:52Z", # str | None
}
```

`search_headline` is the matching passage with `<mark>` tags around the matched terms, so
you can see why something matched without fetching the body.

`pagination` is read off the `X-Page`, `X-Per-Page`, `X-Total-Count` and `X-Total-Pages`
response headers and has the same shape as the other read methods (`current_page`,
`per_page`, `total_count`, `total_pages`, `has_next_page`, `has_prev_page`).

## `get_help_article(slug)`

```python
article = help_center.get_help_article("what-is-feedback-match-rate")
article["body"]  # full markdown
article["toc"]   # [{"id": ..., "text": ..., "level": ..., "children": [...]}]
```

Returns a `HelpArticle`: the summary fields minus `search_headline`, plus `body` (full
markdown), `noindex` (bool) and `toc` (the heading structure, h3s nested under their h2).
Raises `ValueError` before any request if `slug` is blank or a bare `.`/`..`. An unknown or
unpublished slug raises `CoolhandAPIError` with `status == 404`.

## `list_help_categories()`

```python
categories = help_center.list_help_categories()
# [{"slug": "providers", "name": "Provider guides", "description": "...",
#   "url_prefix": "/help", "allowed_tags": None}, ...]
```

Returns a list of `HelpCategory`: `slug` (the `category` filter above), `name`,
`description`, `url_prefix` (`/help` or `/updates`) and `allowed_tags` (the only tags the
section accepts; `None` means any tag).

## Errors

All three raise `CoolhandAPIError` on any failure, with the HTTP status on `.status`:

| Status | When |
|---|---|
| `401` | A key the server does not recognise |
| `404` | An unknown or unpublished slug (`get_help_article`) |
| `429` | The anonymous rate limit was exceeded; wait a minute, or send a key |

`.status` is `None` on a transport failure or a body that is not the expected JSON.

## Verifying against a live server

`tests/live/test_help_center_docs_live.py` runs against a real server with no key:

```bash
COOLHAND_LIVE_BASE_URL=http://127.0.0.1:3111 uv run --all-extras pytest tests/live/test_help_center_docs_live.py
```

It makes about thirteen anonymous requests, so running it repeatedly within a minute can
hit the `429`.
