"""Coolhand client for submitting API interactions."""

import atexit
import json
import logging
import os
import queue
import re
import threading
import time
import uuid
import weakref
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, unquote_plus, urlencode, urlparse, urlunparse
from urllib.request import Request

from . import httpx_interceptor
from ._body_limit import DEFAULT_MAX_BODY_BYTES, cap_body_sized
from ._config import (
    _DEFAULT_BASE_URL,
    _build_opener,
    _normalize_base_url,
    _resolve_max_queued_bytes,
    _resolve_request_timeout,
)
from .httpx_interceptor import DEFAULT_EXCLUDE_API_PATTERNS
from .types import Config, RequestData, ResponseData
from .version import __version__

logger = logging.getLogger(__name__)

# Refuses redirects so the X-API-Key header can never be replayed to a
# redirect target.
_opener = _build_opener()

# Bound on how many interactions may be waiting for delivery at once. Once full,
# flush() drops new items rather than blocking the caller (which may be an
# asyncio event loop thread).
_MAX_DISPATCH_QUEUE_SIZE = 1000

# Bound on the memory those queued interactions may hold, in serialized bytes
# (the captured bodies plus a fixed per-item allowance). The item count alone is
# not enough: with a 1 MiB body cap, 1000 near-cap interactions would hold gigabytes
# once parsed into Python objects while Coolhand is unreachable. Past this budget
# flush() drops (purges) new interactions, exactly as it does for a full queue. An
# interaction larger than the whole budget is still accepted into an empty queue.
# Override with the ``max_queued_bytes`` config option (``0``/``None`` disables).
DEFAULT_MAX_QUEUED_BYTES = 16 * 1024 * 1024
_ITEM_OVERHEAD_BYTES = 2048

# At most one "dropped" warning per this many seconds; the rest are counted into it.
_DROP_WARNING_INTERVAL = 30.0

# How long shutdown() waits for the worker thread to drain the dispatch queue
# before giving up. Bounded so a dead/unreachable backend can't hang process exit.
_SHUTDOWN_TIMEOUT = 5.0

# Sentinel telling the worker thread to stop after finishing queued work.
_SHUTDOWN_SENTINEL = object()

# Sensitive headers to mask
SENSITIVE_HEADERS = [
    "authorization",
    "api-key",
    "x-api-key",
    "openai-api-key",
    "anthropic-api-key",
    "x-goog-api-key",
    # Azure AI Services / APIM. Deliberately the bare suffix: "api-key" does not
    # match "ocp-apim-subscription-key", and APIM also accepts "subscription-key".
    "subscription-key",
    "cookie",
    "set-cookie",
    "proxy-authorization",
    "x-amz-security-token",
    "x-amz-signature",
    "x-amz-credential",
    # Common credential header spellings the substrings above miss ("apikey" is
    # not a substring of "api-key"). Deliberately not bare "token"/"secret", which
    # would also mask benign headers such as "x-ratelimit-remaining-tokens".
    "apikey",
    "api_key",
    "x-auth-token",
    "x-api-token",
    "x-access-token",
    "x-functions-key",
    "authentication",
    "bearer",
    "x-auth",
    "session-token",
    "csrf-token",
    # More spellings seen in the wild: Django's "x-csrftoken", Angular's
    # "x-xsrf-token", GitLab's "private-token", and any "*-secret" header
    # ("x-client-secret", "x-api-secret").
    "csrftoken",
    "xsrf-token",
    "private-token",
    "secret",
    # Vendor and generic spellings none of the above match: Helicone's
    # "Helicone-Auth", "X-Github-Token", "X-Vault-Token", "HF-Token", a bare
    # "Api-Token", "X-Client-Key", "X-Credential", and signature headers. Still
    # deliberately not bare "token", which would mask "x-ratelimit-remaining-tokens".
    # "-auth" is a substring match, so it also masks benign names such as
    # "x-author" and "www-authenticate"; over-redacting those is harmless. Bare
    # "credential" is avoided so "access-control-allow-credentials" stays readable.
    "-auth",
    "api-token",
    "github-token",
    "vault-token",
    "hf-token",
    "client-key",
    "auth-token",
    "access-key",
    "private-key",
    "x-credential",
    "signature",
]

SENSITIVE_QUERY_PARAMS = {
    "key",
    "api_key",
    "apikey",
    "token",
    "access_token",
    "secret",
    "subscription-key",
    "api-key",
    "password",
    "client_secret",
    "refresh_token",
    "id_token",
    # Pre-signed URL credentials (AWS SigV4 / Azure SAS / GCS signed URLs)
    "sig",
    "signature",
    "x-amz-signature",
    "x-amz-credential",
    "x-amz-security-token",
    "x-goog-api-key",
    "x-goog-signature",
    # Credential spellings the header list already treats as sensitive.
    "x-api-key",
    "x_api_key",
    "auth",
    # Short or alternate credential names the substring rules below do not reach.
    "app_key",
    "appkey",
    "pass",
    "otp",
    "pat",
    "sas",
    "ticket",
    "auth_key",
    "auth-key",
    "auth_token",
    "auth-token",
    "access-token",
    "api-token",
    "api_token",
    "authorization",
    "bearer",
    "x-auth-token",
    "x-access-token",
    "private_token",
    "private-token",
    "access_key",
    "access-key",
    "accesskey",
    "api_secret",
    "api-secret",
    "client-secret",
    "session_token",
    "session-token",
    "bearer_token",
    "bearer-token",
    "secret_key",
    "secret-key",
    "clientsecret",
    "pwd",
    "passwd",
    "code_verifier",
    "apitoken",
    "accesstoken",
    "secretkey",
    "subscription_key",
    "hf_token",
    "ocp-apim-subscription-key",
    "x-goog-credential",
}

# Azure OpenAI "On Your Data" carries datastore credentials in the *request body*
# (data_sources[*].parameters.authentication.key, and the legacy dataSources[*]
# .parameters.key / .embeddingKey), where header and query-param redaction never
# sees them. Redaction is scoped to that config subtree so message content, tool
# schemas and other legitimate payload fields are logged verbatim.
# Matched as separator-insensitive substrings, not exact names: Azure keeps
# adding auth shapes (api_key, encoded_api_key, key_and_key_id, sas_token, ...)
# and spells them snake_case in the modern `data_sources` shape but camelCase in
# the legacy `dataSources` one. A miss here forwards a live credential, and
# over-redacting inside this subtree is harmless — so tokens are written without
# separators and compared against a key with its separators stripped.
SENSITIVE_BODY_KEY_TOKENS = (
    "key",
    "token",
    "secret",
    "password",
    "connectionstring",
    "sas",
)

DATA_SOURCE_FIELDS = ("data_sources", "dataSources")

# Credential-bearing body fields outside the Azure subtree, matched by exact name
# after lowercasing and stripping ``-``/``_``, so ``authorization_token``,
# ``authorizationToken`` and ``authorization-token`` all hit (the entries below
# are written in that normalized form). Deliberately NOT substring matching:
# "token" or "key" would mangle max_tokens, key_points and the like. Covers Anthropic
# ``mcp_servers[].authorization_token``, OpenAI Responses MCP tools'
# ``authorization``, and the ``client_secret`` of a realtime ephemeral key.
_SECRET_BODY_KEYS = frozenset(
    {
        "authorizationtoken",
        "authorization",
        "clientsecret",
        "apikey",
        "accesstoken",
        "bearertoken",
    }
)
# A ``headers`` dict beside one of these keys is an MCP server definition.
_MCP_URL_KEYS = frozenset({"serverurl", "url"})
# JSON Schema primitive type names. A ``type`` outside this set (e.g. "bearer",
# "oauth") marks a credential object, not a schema fragment.
_JSON_SCHEMA_TYPES = frozenset(
    {"string", "number", "integer", "boolean", "object", "array", "null"}
)
# Schema keywords that can hold a literal (possibly secret) value.
_SCHEMA_LITERAL_KEYWORDS = ("default", "const", "enum", "examples", "example")
_REDACTED = "[REDACTED]"
# Stands in for a body that could not be redacted or parsed safely.
_UNSANITIZABLE_BODY = "[REDACTED: body could not be sanitized]"
# Real payloads nest a few dozen levels at most (tool schemas); deeper than this is
# hostile or broken input, and recursing on it risks RecursionError.
_MAX_REDACT_DEPTH = 100


def _get_default_config() -> Config:
    """Get default configuration from environment."""
    return {
        "api_key": os.getenv("COOLHAND_API_KEY") or None,
        "base_url": os.getenv("COOLHAND_BASE_URL") or _DEFAULT_BASE_URL,
        "silent": os.getenv("COOLHAND_SILENT", "true").lower() == "true",
        "auto_submit": True,
        "session_id": f"session_{int(time.time() * 1000)}",
        "exclude_api_patterns": list(DEFAULT_EXCLUDE_API_PATTERNS),
        "max_body_bytes": DEFAULT_MAX_BODY_BYTES,
        "max_queued_bytes": DEFAULT_MAX_QUEUED_BYTES,
    }


def _mask_value(value: str) -> str:
    """Mask a sensitive value: fully if 24 chars or fewer, else keep first/last 4."""
    # Revealing 8 characters of a short secret leaks a large share of it.
    if len(value) <= 24:
        return "*" * len(value)
    return f"{value[:4]}{'*' * (len(value) - 8)}{value[-4:]}"


def _normalize_key_name(key: str) -> str:
    """Lowercase a field name and strip ``-``/``_`` separators."""
    return key.lower().replace("-", "").replace("_", "")


# Credential header names that carry none of the SENSITIVE_HEADERS substrings
# (``X-Token``, ``X-Id-Token``, ``Cf-Access-Jwt-Assertion``, ``X-Key``,
# ``X-Password``). ``tokens`` is excluded so rate-limit headers such as
# ``x-ratelimit-remaining-tokens`` stay readable.
_SENSITIVE_HEADER_RE = re.compile(
    r"token(?!s)|jwt|passw|passphrase|pwd|hmac"
    r"|(?:^|[-_])(?:key|auth|pass|pat|otp|totp|sas|cred|oauth|refresh|private)$"
)

# Substrings that mark a query parameter as a credential once ``-``/``_`` are
# stripped and the name is lowercased (``authToken``, ``sas_token``, ``privateKey``).
_SENSITIVE_QUERY_SUBSTRINGS = (
    "token",
    "secret",
    "password",
    "passwd",
    "apikey",
    "privatekey",
    "signature",
    "jwt",
    "passphrase",
    "hmac",
)
_SENSITIVE_QUERY_NORMALIZED = frozenset(
    _normalize_key_name(p) for p in SENSITIVE_QUERY_PARAMS
)


def _is_sensitive_query_param(name: str) -> bool:
    """Whether a query parameter name looks like it holds a credential."""
    lowered = name.lower()
    if lowered in SENSITIVE_QUERY_PARAMS:
        return True
    normalized = _normalize_key_name(name)
    if normalized in _SENSITIVE_QUERY_NORMALIZED:
        return True
    return not normalized.endswith("tokens") and any(
        s in normalized for s in _SENSITIVE_QUERY_SUBSTRINGS
    )


def _sanitize_headers(headers: dict[str, str]) -> dict[str, str]:
    """Sanitize sensitive headers."""
    result = {}
    for key, value in headers.items():
        lowered = key.lower()
        if any(s in lowered for s in SENSITIVE_HEADERS) or _SENSITIVE_HEADER_RE.search(
            lowered
        ):
            result[key] = _mask_value(str(value))
        else:
            result[key] = value
    return result


def _redact_delimited_pairs(text: str, separators: str) -> tuple[str, bool]:
    """Redact the value of each credential-named ``name=value`` pair in ``text``.

    ``text`` is split on any character in ``separators`` (kept in the output), so
    ``a=1;api_key=S`` and ``/x;api_key=S`` are handled the way a server that treats
    ``;`` as a separator reads them. Returns the new text and whether anything changed.
    """
    pieces = re.split(f"([{re.escape(separators)}])", text)
    changed = False
    for index in range(0, len(pieces), 2):
        name, eq, _value = pieces[index].partition("=")
        if eq and _is_sensitive_query_param(unquote_plus(name)):
            pieces[index] = f"{name}=[REDACTED]"
            changed = True
    return "".join(pieces), changed


def _sanitize_url(url: str) -> str:
    """Redact sensitive query parameters from URLs.

    Matched case-insensitively, like header redaction: a query param's *name*
    has no fixed casing convention the way HTTP header names do, and a caller
    sending ``?API_KEY=...`` instead of ``?api_key=...`` should not bypass
    redaction — over-redacting a coincidentally-named benign param is
    harmless, missing a differently-cased credential is not.
    """
    try:
        parsed = urlparse(url)
        # Credentials embedded as ``https://user:pass@host/`` are dropped too.
        host = parsed.netloc.rpartition("@")[2]
        redacted = host != parsed.netloc
        if redacted:
            parsed = parsed._replace(netloc=host)
        # A fragment is never sent to the server, but httpx keeps it in
        # ``str(request.url)`` and ``#access_token=...`` would be forwarded.
        if parsed.fragment:
            parsed = parsed._replace(fragment="")
            redacted = True
        # ``;`` is a query (and path-parameter) separator for some servers, so
        # ``?a=1;api_key=S`` and ``/v1/x;api_key=S`` would otherwise hide a credential
        # inside another value or in the path.
        if ";" in parsed.query:
            query, changed = _redact_delimited_pairs(parsed.query, "&;")
            if changed:
                parsed = parsed._replace(query=query)
                redacted = True
        # urlparse moves the last path segment's ``;params`` into ``.params``.
        for part in ("path", "params"):
            value = getattr(parsed, part)
            if ";" in value or (part == "params" and value):
                new_value, changed = _redact_delimited_pairs(value, ";")
                if changed:
                    parsed = parsed._replace(**{part: new_value})
                    redacted = True
        if parsed.query and ";" not in parsed.query:
            params = parse_qs(parsed.query, keep_blank_values=True)
            query_redacted = False
            # Every spelling is checked: "Key=a&key=b" must redact both, so do not
            # collapse keys by their lowercased form.
            for actual_key in params:
                if _is_sensitive_query_param(actual_key):
                    params[actual_key] = ["[REDACTED]"]
                    query_redacted = True
            if query_redacted:
                parsed = parsed._replace(query=urlencode(params, doseq=True))
                redacted = True
        return urlunparse(parsed) if redacted else url
    except Exception:
        # Fail closed: if redaction itself breaks, drop the query string rather
        # than risk forwarding an unredacted secret in it.
        # Userinfo (``user:pass@``) is stripped too, since it is a credential.
        return re.sub(
            r"^([a-z][a-z0-9+.-]*://)[^/?#]*@",
            r"\1",
            url.split("?", 1)[0].split("#", 1)[0],
            flags=re.IGNORECASE,
        )


def _is_sensitive_body_key(key: str) -> bool:
    """Whether a body field name looks like it holds a credential.

    Separators are stripped before matching so ``connection_string``,
    ``connectionString`` and ``connection-string`` all resolve alike.
    """
    normalized = _normalize_key_name(key)
    return any(token in normalized for token in SENSITIVE_BODY_KEY_TOKENS)


def _is_schema_fragment(value: Any) -> bool:
    """Whether ``value`` is a JSON Schema fragment, e.g. ``{"type": "string"}``.

    A fragment that carries a literal value (``default``, ``const``, ``enum``,
    ``examples``) is not one: the literal could be a real credential, so the
    caller redacts the whole fragment.
    """
    if not isinstance(value, dict):
        return False
    if any(value.get(keyword) is not None for keyword in _SCHEMA_LITERAL_KEYWORDS):
        return False
    schema_type = value.get("type")
    if isinstance(schema_type, str):
        return schema_type in _JSON_SCHEMA_TYPES
    return (
        isinstance(schema_type, list)
        and bool(schema_type)
        and all(isinstance(t, str) and t in _JSON_SCHEMA_TYPES for t in schema_type)
    )


def _is_nonempty_dict(value: Any) -> bool:
    """Whether ``value`` is a dict with at least one entry."""
    return isinstance(value, dict) and bool(value)


def _is_mcp_server(obj: dict) -> bool:
    """Whether a dict carries a ``server_url``/``url`` field, as MCP servers do."""
    return any(
        _normalize_key_name(k) in _MCP_URL_KEYS for k in obj if isinstance(k, str)
    )


def _redact_secret_keys(value: Any) -> Any:
    """Recursively redact secret-bearing keys inside a config subtree."""
    if isinstance(value, dict):
        return {
            k: (_REDACTED if _is_sensitive_body_key(k) else _redact_secret_keys(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact_secret_keys(v) for v in value]
    return value


def _redact_data_sources(body: dict) -> dict:
    """Redact Azure OpenAI data-source credentials in a dict body."""
    present = [f for f in DATA_SOURCE_FIELDS if f in body]
    if not present:
        return body
    try:
        sanitized = dict(body)
        for field in present:
            sanitized[field] = _redact_secret_keys(body[field])
        return sanitized
    except Exception:
        # Fail closed: if redaction itself breaks, drop the whole data-source
        # config rather than risk forwarding an unredacted datastore key.
        sanitized = dict(body)
        for field in present:
            sanitized[field] = _REDACTED
        return sanitized


# Raw field names already classified as harmless (neither a secret key nor ``headers``),
# so the common key (``role``, ``content``, ``type``) costs one set lookup per request
# instead of a normalization. A pure function of the name, so a stale or racing entry
# cannot change a result. Bounded in size and name length so a hostile payload full of
# distinct or huge keys cannot grow it.
_BENIGN_BODY_KEYS: set[str] = set()
_BENIGN_BODY_KEYS_MAX = 4096
_BENIGN_BODY_KEY_MAX_LEN = 64


def _redact_known_secret_fields(value: Any, depth: int = 0) -> Any:
    """Redact well-known credential fields anywhere in a parsed JSON body.

    Walks dicts and lists. Never mutates its input: a container is copied only
    when something inside it changed, so an ordinary body is returned as the same
    object. Raises ``ValueError`` when a container sits deeper than
    ``_MAX_REDACT_DEPTH`` so the caller fails closed rather than recursing on
    hostile input.
    """
    if not isinstance(value, (dict, list)):
        return value
    if depth > _MAX_REDACT_DEPTH:
        raise ValueError("body nested too deeply to redact")
    if isinstance(value, dict):
        out: dict | None = None
        for k, x in value.items():
            if k in _BENIGN_BODY_KEYS:
                # Known harmless name: only its value can matter, and only if nested.
                if not isinstance(x, (dict, list)):
                    continue
                new: Any = _redact_known_secret_fields(x, depth + 1)
            else:
                name = _normalize_key_name(k) if isinstance(k, str) else None
                if (
                    name is not None
                    and name not in _SECRET_BODY_KEYS
                    and name != "headers"
                    and len(k) <= _BENIGN_BODY_KEY_MAX_LEN
                    and len(_BENIGN_BODY_KEYS) < _BENIGN_BODY_KEYS_MAX
                ):
                    _BENIGN_BODY_KEYS.add(k)
                # A schema fragment is a tool/JSON-schema property that merely
                # shares a secret key's name, e.g. {"authorization": {"type":
                # "string"}}. It skips the first branch and is walked below, so
                # secrets nested in it are still caught.
                if name in _SECRET_BODY_KEYS and not _is_schema_fragment(x):
                    new = _REDACTED
                elif (
                    name == "headers" and _is_nonempty_dict(x) and _is_mcp_server(value)
                ):
                    # MCP server definition: every header value is a credential
                    # candidate (custom auth headers have no fixed name).
                    new = {header: _REDACTED for header in x}
                elif isinstance(x, (dict, list)):
                    new = _redact_known_secret_fields(x, depth + 1)
                else:
                    new = x
            if new is not x:
                if out is None:
                    out = dict(value)
                out[k] = new
        return value if out is None else out
    items = [
        _redact_known_secret_fields(i, depth + 1) if isinstance(i, (dict, list)) else i
        for i in value
    ]
    if all(n is o for n, o in zip(items, value, strict=True)):
        return value
    return items


def _sanitize_body(body: str | dict | list | None) -> str | dict | list | None:
    """Redact credentials embedded in a parsed JSON body.

    Two passes: the Azure ``data_sources`` subtree (any key that looks like a
    credential), then a narrow by-name pass over the whole body. Only parsed
    JSON is walked; a body that did not parse (a streamed/SSE transcript, plain
    text) is returned as is.
    """
    if not isinstance(body, (dict, list)):
        return body
    try:
        if isinstance(body, dict):
            body = _redact_data_sources(body)
        redacted: dict | list = _redact_known_secret_fields(body)
        return redacted
    except Exception:
        # Fail closed: if redaction itself breaks (e.g. hostile nesting), drop the
        # whole body rather than forward a partly redacted one.
        return _UNSANITIZABLE_BODY


_MAX_ERROR_CHARS = 1000
# Input is cut to this before any regex runs, so scrubbing cost is bounded no
# matter how long the exception text is (it runs on the caller's thread).
_MAX_ERROR_SCAN_CHARS = 4000
_ERROR_URL_RE = re.compile(r"https?://[^\s'\"<>)]+", re.IGNORECASE)
_ERROR_BEARER_RE = re.compile(
    r"\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE
)
_ERROR_KEY_RE = re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{8,}")
# ``name=value``, ``name: value``, ``"name": "value"`` and ``name='value'`` for a
# credential-looking name. The name part is length-bounded to keep matching linear.
_ERROR_PAIR_RE = re.compile(
    r"""(?P<name>[A-Za-z0-9_-]{0,64}(?:key|token|secret|password|passwd|signature|jwt)"""
    r"""[A-Za-z0-9_-]{0,64})(?P<quote1>["']?)(?P<sep>\s*[=:]\s*)"""
    r"""(?:"[^"]*"|'[^']*'|[^\s&,;'"}\]]+)""",
    re.IGNORECASE,
)


def _mask_error_pair(match: "re.Match[str]") -> str:
    name = match.group("name")
    # Already masked (the value stops at the closing bracket): leave it alone.
    if match.group(0).endswith("[REDACTED"):
        return match.group(0)
    # Counters such as ``max_tokens=100`` are not credentials.
    if _normalize_key_name(name).endswith("tokens"):
        return match.group(0)
    return f"{name}{match.group('quote1')}{match.group('sep')}[REDACTED]"


def _sanitize_error(error: str) -> str:
    """Make an exception message safe to forward: URLs lose query secrets and
    userinfo (``requests`` embeds the full URL in its errors), bearer tokens,
    ``sk-`` style keys and credential-named pairs are masked, and the text is
    truncated. Fails closed to a fixed string."""
    try:
        text = str(error)[:_MAX_ERROR_SCAN_CHARS]
        text = _ERROR_URL_RE.sub(lambda m: _sanitize_url(m.group(0)), text)
        text = _ERROR_BEARER_RE.sub(lambda m: f"{m.group(1)} [REDACTED]", text)
        text = _ERROR_KEY_RE.sub("[REDACTED]", text)
        text = _ERROR_PAIR_RE.sub(_mask_error_pair, text)
        return text[:_MAX_ERROR_CHARS]
    except Exception:
        return "[REDACTED: error could not be sanitized]"


def _parse_body(body: str | bytes | dict | list | None) -> str | dict | list | None:
    """Parse body to JSON object if possible, otherwise return as string."""
    if body is None:
        return None

    # Already a dict or list
    if isinstance(body, (dict, list)):
        return body

    # Convert bytes to string
    if isinstance(body, bytes):
        try:
            body = body.decode("utf-8")
        except Exception:
            return str(body)

    # Try to parse as JSON
    if isinstance(body, str):
        try:
            # A leading BOM makes json.loads reject valid JSON, which would then
            # be captured as unwalked text.
            # (A bare JSON scalar such as ``12`` also lands here; _sanitize_body
            # passes anything that is not a dict or list through untouched.)
            parsed: str | dict | list | None = json.loads(body.removeprefix("\ufeff"))
            return parsed
        except json.JSONDecodeError:
            return body
        except Exception:
            # Not a syntax error, so this may be valid JSON the parser refused
            # (nested past its recursion limit, an integer over Python's digit
            # cap). Returning the raw text would forward it unredacted.
            return _UNSANITIZABLE_BODY

    return str(body)


def _to_iso8601(timestamp: float) -> str:
    """Convert Unix timestamp to ISO 8601 string."""
    return (
        datetime.fromtimestamp(timestamp, tz=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _log_response_warnings(resp: Any) -> None:
    """Log any `warnings` the server attached to an accepted log. Never raises."""
    try:
        warnings = json.loads(resp.read()).get("warnings")
        if isinstance(warnings, list):
            for warning in warnings:
                # %r: a newline in server text must not forge extra log lines.
                logger.warning("Coolhand warning: %r", warning)
    except Exception:
        logger.debug(
            "Could not read warnings from the submission response", exc_info=True
        )


# Every live client, so a fork() child can reset all of them (see below).
_live_clients: "weakref.WeakSet[CoolhandClient]" = weakref.WeakSet()


def _reset_clients_after_fork() -> None:
    """Runs in a fork() child (``os.fork``, ``multiprocessing``'s fork start
    method, gunicorn/uWSGI preload). The child inherits each client's locks in
    whatever state another parent thread left them (a held lock stays held
    forever here) and the parent's pending interactions, which the parent still
    delivers. Without this, the child could hang on its first captured call or
    send every inherited interaction a second time. The Dramatiq middleware's
    explicit reset stays valid: it is a no-op once this has run."""
    for client in list(_live_clients):
        try:
            client._reset_after_fork()
        except Exception:
            pass


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_clients_after_fork)


class CoolhandClient:
    """Simple client for submitting interactions to Coolhand."""

    def __init__(self, config: Config | None = None, **kwargs):
        """Initialize the client."""
        self.config = _get_default_config()
        if config:
            self.config.update(config)
        self.config.update(kwargs)
        self.config["base_url"] = _normalize_base_url(
            self.config.get("base_url", _DEFAULT_BASE_URL)
        )
        self.config["request_timeout"] = _resolve_request_timeout(self.config)
        self.config["max_queued_bytes"] = _resolve_max_queued_bytes(self.config)

        self._init_runtime_state()
        _live_clients.add(self)

        if not self.config.get("silent"):
            self._configure_coolhand_logging()

        # Ensure queued interactions still get a delivery attempt on normal process
        # exit, even for a bare CoolhandClient() not wrapped by the Coolhand subclass.
        atexit.register(self.shutdown)

    def _init_runtime_state(self) -> None:
        """(Re)create the per-process delivery state: queues, locks, worker
        reference, counters. Shared by __init__ and _reset_after_fork() so a
        field added here can't be silently inherited across a fork."""
        self._queue: list[dict[str, Any]] = []
        self._interaction_count = 0

        # Delivery of queued interactions happens on a background worker thread so
        # that flush() never blocks the calling thread (which may own an asyncio
        # event loop) on network I/O. See _ensure_worker()/_worker_loop().
        self._dispatch_queue: queue.Queue[Any] = queue.Queue(
            maxsize=_MAX_DISPATCH_QUEUE_SIZE
        )
        self._dropped_count = 0
        # Serialized bytes currently held by items on the dispatch queue (or being
        # delivered); bounded by max_queued_bytes. Guarded by _state_lock.
        self._queued_bytes = 0
        self._dropped_since_warning = 0
        self._last_drop_warning = float("-inf")
        self._delivery_failure_count = 0
        self._worker_thread: threading.Thread | None = None
        self._worker_lock = threading.Lock()
        # Guards self._queue (the swap-and-clear in flush() — without it,
        # two concurrent flush() calls could both read the same list before
        # either replaces it, delivering every queued interaction twice) and
        # the plain counters (_interaction_count, _dropped_count,
        # _delivery_failure_count), all reachable from multiple threads.
        self._state_lock = threading.Lock()
        # Process that built this state, so a fork-inherited copy can be
        # recognised in the child (see _reset_after_fork()).
        self._pid = os.getpid()

    @staticmethod
    def _configure_coolhand_logging() -> None:
        """Make non-silent output visible without touching the root logger.

        Only the ``coolhand`` namespace is configured. A handler is attached
        only when neither it nor the root logger has one, so an app that has
        already set up logging sees coolhand records through its own handlers
        instead of getting them printed twice.
        """
        pkg_logger = logging.getLogger("coolhand")
        # Only raise verbosity: leave an explicit level, or a host root level
        # already at INFO/DEBUG, alone.
        if (
            pkg_logger.level == logging.NOTSET
            and pkg_logger.getEffectiveLevel() > logging.INFO
        ):
            pkg_logger.setLevel(logging.INFO)
        if not pkg_logger.handlers and not logging.getLogger().handlers:
            handler = logging.StreamHandler()
            # Same format logging.basicConfig used, so output is unchanged.
            handler.setFormatter(logging.Formatter(logging.BASIC_FORMAT))
            pkg_logger.addHandler(handler)

    @property
    def session_id(self) -> str:
        return self.config.get("session_id", "")

    def log_interaction(
        self,
        request: RequestData,
        response: ResponseData | None = None,
        error: str | None = None,
    ) -> None:
        """Log an API interaction in flat format matching Ruby/Node SDKs."""
        # Get timestamps
        req_timestamp = request.get("timestamp", time.time())
        res_timestamp = (
            response.get("timestamp", time.time()) if response else time.time()
        )
        duration_seconds = response.get("duration", 0.0) if response else 0.0

        max_body_bytes = self.config.get("max_body_bytes")
        request_body, request_size = cap_body_sized(
            _sanitize_body(_parse_body(request.get("body"))), max_body_bytes
        )
        if response:
            response_body, response_size = cap_body_sized(
                _sanitize_body(_parse_body(response.get("body"))),
                None if response.get("body_truncated") else max_body_bytes,
            )
        else:
            response_body, response_size = None, 0

        # Build flat interaction data (matching Ruby/Node format)
        interaction = {
            "id": str(uuid.uuid4()),
            "timestamp": _to_iso8601(req_timestamp),
            "method": request.get("method", "").lower(),
            "url": _sanitize_url(request.get("url", "")),
            "headers": _sanitize_headers(request.get("headers", {})),
            "request_body": request_body,
            "response_headers": (
                _sanitize_headers(response.get("headers", {})) if response else {}
            ),
            "response_body": response_body,
            "status_code": response.get("status_code", 0) if response else 0,
            "duration_ms": round(duration_seconds * 1000, 2),
            "completed_at": _to_iso8601(res_timestamp),
            "is_streaming": response.get("is_streaming", False) if response else False,
        }

        # Why a call failed (a transport error, a timeout). Scrubbed first: a
        # ``requests`` error message embeds the full URL, query string included.
        if error:
            interaction["error"] = _sanitize_error(error)

        # Only a strict true is acted on by the backend; completed streams omit it.
        if response and response.get("stream_incomplete") is True:
            interaction["stream_incomplete"] = True

        # The declaration was snapshotted on the caller's context when the call was
        # made. It is not part of raw_request: _send_one lifts it out to sit beside it.
        declared = request.get("declared")
        if declared:
            interaction["_declared"] = declared

        # Memory this interaction will hold while queued; popped before sending.
        interaction["_weight"] = request_size + response_size + _ITEM_OVERHEAD_BYTES

        # Debug output when not silent
        if not self.config.get("silent"):
            url = _sanitize_url(request.get("url", "unknown"))
            method = request.get("method", "unknown")
            status = response.get("status_code") if response else "error"
            logger.info(f"Captured: {method} {url} -> {status}")

        # Locked so a concurrent flush() can't swap self._queue out for a new
        # list between this reading the attribute and the append landing —
        # which would silently strand the interaction on the discarded list.
        # _interaction_count is incremented here too since it's the same
        # kind of plain read-modify-write, reachable from the same
        # concurrent callers. Released before flush() below, which takes the
        # same lock itself.
        with self._state_lock:
            self._interaction_count += 1
            self._queue.append(interaction)

        # Auto-submit if enabled
        if self.config.get("auto_submit"):
            self.flush()

    def _send_one(self, interaction: dict[str, Any]) -> bool:
        """POST a single interaction to the Coolhand API. Blocking; runs on the
        worker thread (or, in tests, is called directly)."""
        # Stripped: a trailing newline (common in env files / k8s secrets) makes
        # http.client reject the header, and the resulting ValueError message
        # contains the full key.
        api_key = (self.config.get("api_key") or "").strip()
        if not api_key:
            return False

        try:
            interaction = dict(interaction)
            interaction.pop("_weight", None)
            declared = interaction.pop("_declared", None)
            llm_request_log: dict[str, Any] = {
                "raw_request": interaction,
                "collector": f"coolhand-python-{__version__}-auto-monitor",
            }
            if declared:
                llm_request_log["declared"] = declared
            payload = {"llm_request_log": llm_request_log}

            request = Request(
                url=f"{self.config['base_url']}/api/v2/llm_request_logs",
                data=json.dumps(payload, default=str).encode("utf-8"),
                headers={
                    "X-API-Key": api_key,
                    "Content-Type": "application/json",
                    "User-Agent": f"coolhand-python/{__version__}",
                },
                method="POST",
            )

            with _opener.open(request, timeout=self.config["request_timeout"]) as resp:
                if 200 <= resp.status < 300:
                    if declared:
                        # Warnings (e.g. a declaration ignored over the public-key
                        # cap) only concern logs that carried a declaration.
                        _log_response_warnings(resp)
                    if not self.config.get("silent"):
                        method = interaction.get("method", "unknown")
                        url = interaction.get("url", "unknown")
                        logger.info(
                            f"Submitted interaction {interaction.get('id')} "
                            f"({method} {url})"
                        )
                    return True
                logger.warning(
                    f"Unexpected status submitting interaction: {resp.status}"
                )
                return False

        except (HTTPError, URLError) as e:
            logger.warning(f"Failed to submit interaction: {e}")
            return False
        except Exception as e:
            # Type only: the message of a malformed-header ValueError embeds the
            # offending value, which can be the API key.
            logger.warning(
                "Unexpected error submitting interaction: %s", type(e).__name__
            )
            return False

    def _reset_after_fork(self) -> bool:
        """Re-initialise this client's internals if it was inherited via fork().

        A forked child gets a copy of the parent's locks, dispatch queue,
        pending interactions and (dead) worker-thread reference. A lock that
        another parent thread held at fork time is never released in the
        child, so the locks are *replaced* rather than acquired, and the
        parent's pending interactions are dropped — the parent still owns and
        delivers them, so keeping them would re-deliver each once per child.
        Config and session are preserved.

        No-op (returns False) in the process that created the client, e.g.
        thread-based workers. Returns True if a reset was performed.

        Not itself thread-safe: call it at child boot, before any other
        thread in the child uses the client.
        """
        if os.getpid() == self._pid:
            return False

        # Lock-free reads, diagnostics only: Queue.qsize() would take the
        # queue's own mutex, which may be orphaned too. `.queue` is the
        # underlying deque.
        inherited = len(self._queue) + len(self._dispatch_queue.queue)
        self._init_runtime_state()
        logger.debug(
            "Reset fork-inherited client; discarded %d pending interactions", inherited
        )

        # Same idempotent re-arm as _ensure_worker_locked().
        atexit.unregister(self.shutdown)
        atexit.register(self.shutdown)
        return True

    def _ensure_worker(self) -> bool:
        """Start the background delivery thread if it isn't already running.

        Returns False if no worker is running and one could not be started
        (e.g. interpreter shutdown has begun), so callers can treat pending
        items as undeliverable rather than reporting them as queued.

        Public entry point: acquires _worker_lock itself. flush() instead
        calls _ensure_worker_locked() directly, holding _worker_lock across
        both the availability check *and* the enqueue that follows — see
        that method's docstring for why the two must be atomic together.

        No unlocked fast path here: this method's only caller, shutdown(),
        invokes it at most once per call, so there's nothing to optimize by
        checking self._worker_thread before acquiring the lock — and doing
        so would race _worker_loop's own clear of that attribute (also
        under _worker_lock): two unlocked reads could straddle the moment
        it flips to None, raising AttributeError on the second one.
        _ensure_worker_locked()'s own early-return check below is not an
        unlocked fast path either — it runs after the caller (this method,
        or flush()) has already acquired _worker_lock; it's just the
        already-locked "a worker is already running" case, cheaper than
        starting a new thread.
        """
        with self._worker_lock:
            return self._ensure_worker_locked()

    def _ensure_worker_locked(self) -> bool:
        """Body of _ensure_worker(). Caller must already hold _worker_lock."""
        if self._worker_thread is not None and self._worker_thread.is_alive():
            return True
        worker = threading.Thread(target=self._worker_loop, daemon=True)
        try:
            worker.start()
        except RuntimeError:
            # Can't create new threads once interpreter shutdown has begun
            # (e.g. a late atexit-triggered flush). Nothing to deliver to.
            logger.debug("Could not start delivery worker; interpreter is exiting")
            return False
        self._worker_thread = worker
        # (Re-)arm the atexit safety net so a final drain is attempted at
        # real process exit. unregister-then-register guarantees exactly
        # one entry regardless of whether __init__'s original
        # registration is still in place or an earlier shutdown() call
        # already removed it — atexit.unregister on an unregistered
        # callable is a documented no-op, so this is safe either way.
        atexit.unregister(self.shutdown)
        atexit.register(self.shutdown)
        return True

    def _worker_loop(self) -> None:
        """Deliver queued interactions to Coolhand until told to stop.

        On the sentinel, drains and delivers whatever else is already
        buffered before exiting (silently discarding any further sentinels
        found along the way) rather than exiting immediately. A shutdown()
        call that times out while this worker is still busy can leave its
        sentinel behind for a while, and shutdown() may be called more than
        once while the same worker stays busy, potentially queuing more than
        one sentinel — without draining, whichever sentinel this worker
        reaches first would strand every real item still behind it, and any
        second sentinel would go on to kill the *next* worker before it does
        any work at all.

        Committing to exit is itself guarded by _worker_lock, checked
        against the dispatch queue being genuinely empty at that moment —
        the same lock flush() holds across both its "is a worker available"
        check and the put_nowait() that follows (see
        _ensure_worker_locked()). Without sharing that lock, a flush() could
        see this worker as available (Thread.is_alive() is still True right
        up until the thread function returns) in the same narrow window
        this method has already decided to exit, enqueueing an item no
        worker will ever consume.
        """
        while True:
            item = self._dispatch_queue.get()
            if item is _SHUTDOWN_SENTINEL:
                while True:
                    try:
                        item = self._dispatch_queue.get_nowait()
                    except queue.Empty:
                        break
                    if item is not _SHUTDOWN_SENTINEL:
                        self._deliver(item)
                with self._worker_lock:
                    if not self._dispatch_queue.empty():
                        # Something was enqueued (holding this same lock,
                        # per _ensure_worker_locked()) between the drain
                        # above finishing and this check — keep running
                        # rather than exiting out from under it.
                        continue
                    if self._worker_thread is threading.current_thread():
                        self._worker_thread = None
                    return
            self._deliver(item)

    def _release_bytes(self, interaction: dict[str, Any]) -> None:
        """Give back the memory budget an interaction held while queued."""
        weight = interaction.get("_weight", 0) if isinstance(interaction, dict) else 0
        if weight:
            with self._state_lock:
                self._queued_bytes = max(0, self._queued_bytes - weight)

    def _deliver(self, interaction: dict[str, Any]) -> None:
        """Call _send_one, guarding the worker loop against it ever raising
        (it's total today, but a subclass override or monkeypatch could
        change that) — a single bad item must not silently kill delivery
        for every interaction still queued behind it."""
        try:
            delivered = self._send_one(interaction)
        except Exception:
            logger.exception("Unexpected error delivering interaction")
            delivered = False
        finally:
            # Held until the attempt ends: the item's memory is live until then.
            self._release_bytes(interaction)

        if not delivered:
            # Distinct from dropped_count: a drop means the item never made
            # it onto the dispatch queue (full, or no worker); this means it
            # did, but the POST itself failed (bad API key, non-2xx status,
            # network error) — without this, e.g. every request 401ing would
            # report dropped_count: 0 and read as "everything delivered".
            with self._state_lock:
                self._delivery_failure_count += 1

    def flush(self) -> bool:
        """Hand off queued interactions for background delivery to Coolhand.

        This never performs network I/O itself: it moves interactions onto a
        bounded dispatch queue that a worker thread drains, so callers on the
        critical path (including an asyncio event loop thread) are not blocked
        on the HTTP round trip. Safe to call again after shutdown() — a fresh
        worker is started as needed. Returns False if any item had to be
        dropped, either because the dispatch queue was full or because no
        worker could be started to receive it. If no API key is configured,
        items are discarded (not counted as dropped) and this still returns
        True — there's nowhere to submit them, so it's not a delivery
        failure; get_stats()["config"]["has_api_key"] tells you why nothing
        is ever being sent.
        """
        with self._state_lock:
            if not self._queue:
                return True
            items, self._queue = self._queue, []

        if not self.config.get("api_key"):
            logger.debug("No API key configured, skipping submission")
            return True

        # Held across both the availability check and every put_nowait()
        # below — not just the check — so a worker that's mid-exit (see
        # _worker_loop's own use of this lock) can't commit to exiting
        # between "a worker is available" and the item actually landing in
        # the queue. put_nowait() never blocks, so holding the lock across
        # it is cheap.
        budget = self.config.get("max_queued_bytes")
        with self._worker_lock:
            worker_available = self._ensure_worker_locked()

            if not worker_available:
                dropped = len(items)
            else:
                dropped = 0
                for interaction in items:
                    weight = interaction.get("_weight", 0)
                    with self._state_lock:
                        # An interaction bigger than the whole budget is still
                        # accepted into an empty queue, or it could never be sent.
                        over_budget = bool(
                            budget
                            and self._queued_bytes
                            and self._queued_bytes + weight > budget
                        )
                        if not over_budget:
                            self._queued_bytes += weight
                    if over_budget:
                        dropped += 1
                        continue
                    try:
                        self._dispatch_queue.put_nowait(interaction)
                    except queue.Full:
                        self._release_bytes(interaction)
                        dropped += 1

        if dropped:
            # Locked: flush() is called concurrently from multiple interceptor
            # threads, and this is a plain read-modify-write.
            now = time.monotonic()
            with self._state_lock:
                self._dropped_count += dropped
                self._dropped_since_warning += dropped
                warn = now - self._last_drop_warning >= _DROP_WARNING_INTERVAL
                if warn:
                    self._last_drop_warning = now
                    warned_total = self._dropped_since_warning
                    self._dropped_since_warning = 0
            # Rate-limited: during an outage every request would otherwise add a
            # line to the host application's logs.
            if warn:
                logger.warning(
                    f"Dropped {warned_total} interactions since the last warning "
                    "(dispatch queue full, over its memory budget, or delivery "
                    "worker unavailable)"
                )

        return dropped == 0

    def get_stats(self) -> dict[str, Any]:
        """Get client statistics."""
        # Check if httpx is patched
        patched_libs = []
        try:
            patched_libs += httpx_interceptor.patched_targets()
        except Exception:
            pass
        try:
            from . import copilot_interceptor

            if copilot_interceptor.is_patched():
                patched_libs += [
                    "JsonRpcClient.request",
                    "JsonRpcClient._handle_message",
                ]
        except Exception:
            pass

        return {
            "config": {
                "has_api_key": bool(self.config.get("api_key")),
            },
            "monitoring": {
                "enabled": True,
                "patched_libraries": patched_libs,
            },
            "logging": {
                "session_id": self.session_id,
                "interaction_count": self._interaction_count,
                "queue_size": len(self._queue),
                "dropped_count": self._dropped_count,
                # Serialized bytes held by interactions waiting for (or in) delivery.
                "queued_bytes": self._queued_bytes,
                # Distinct from dropped_count: this counts items that made it
                # onto the dispatch queue but whose POST failed (bad API key,
                # non-2xx status, network error).
                "delivery_failure_count": self._delivery_failure_count,
                # Approximate, not exact, in both directions:
                # - Over-counts: includes the internal shutdown sentinel
                #   while a shutdown() call is in flight. Normally consumed
                #   within that same call, but in the narrow window where
                #   Thread.is_alive() reports a worker as alive just after
                #   it has already committed to exiting, the sentinel can
                #   outlive that shutdown() call and inflate this count by
                #   one until a later flush()/shutdown() call starts a
                #   fresh worker to drain it — self-healing, but not
                #   instant.
                # - Under-counts: qsize() stops counting an item the moment
                #   the worker dequeues it, so while that item's POST is
                #   actually in flight (up to request_timeout) it's
                #   reflected nowhere — polling for `pending_delivery == 0`
                #   is not a reliable "everything is fully delivered"
                #   signal on its own; shutdown() (which actually waits) is.
                "pending_delivery": self._dispatch_queue.qsize(),
            },
        }

    def shutdown(self) -> None:
        """Flush and cleanup.

        Waits up to _SHUTDOWN_TIMEOUT for the background worker to deliver any
        interactions still in flight, then returns regardless — an unreachable
        backend must not hang process exit (this is called from atexit). Not a
        permanent stop switch: monitoring keeps working afterwards, and a later
        flush() simply starts a fresh worker if needed (e.g. if shutdown() was
        called mid-program rather than right before process exit).
        """
        logger.info("Shutting down Coolhand...")
        # Abandoned streams reach the handler through a reporter thread; hand over
        # any it has not delivered yet so the flush below includes them.
        try:
            httpx_interceptor.flush_abandoned()
        except Exception:
            logger.debug("Could not drain abandoned-stream records", exc_info=True)
        self.flush()

        # Guards a narrow race: something enqueued directly onto the dispatch
        # queue while the worker that would have drained it was exiting, so
        # self._worker_thread still points at a now-dead thread. Not reachable
        # through flush() alone (which always calls _ensure_worker_locked()
        # itself before enqueueing), but cheap to close off here too.
        if not self._dispatch_queue.empty():
            self._ensure_worker()

        worker = self._worker_thread
        if worker is not None and worker.is_alive():
            deadline = time.monotonic() + _SHUTDOWN_TIMEOUT
            try:
                # Blocking (not put_nowait): if the queue is full, give the
                # worker the shutdown budget to make room rather than losing
                # the sentinel outright and leaving the worker running past
                # shutdown() with an undelivered backlog.
                self._dispatch_queue.put(_SHUTDOWN_SENTINEL, timeout=_SHUTDOWN_TIMEOUT)
            except queue.Full:
                pass
            worker.join(timeout=max(0.0, deadline - time.monotonic()))

        # Explicit shutdown() calls shouldn't keep the client (and its queues)
        # alive in the atexit registry for the rest of the process — but only
        # once we're actually done: if a worker is still running (timed out
        # against a slow/unreachable backend, or a concurrent flush() from
        # another interceptor thread started a fresh one in the meantime) or
        # the dispatch queue still has something in it, leave the hook armed
        # so a final attempt still happens at real process exit.
        # self._worker_thread being None is the authoritative "no active
        # worker" signal — _worker_loop clears it itself right before
        # returning (see its docstring), rather than this relying on
        # Thread.is_alive(), which only flips after the OS finishes tearing
        # the thread down and would otherwise leave a brief window where a
        # worker that's committed to exiting still reports "alive". The
        # whole check-then-act sequence is held under _worker_lock — the
        # same lock _ensure_worker() and _worker_loop's own cleanup use — so
        # this can't interleave with either: it either completes first (and
        # a subsequent _ensure_worker() re-arms the hook for its new
        # worker), or it runs after one of them and correctly sees the
        # up-to-date state. Harmless no-op if this fires again from atexit
        # itself.
        with self._worker_lock:
            if self._worker_thread is None and self._dispatch_queue.empty():
                atexit.unregister(self.shutdown)

        logger.info("Coolhand shutdown complete")


# Global instance
_instance: CoolhandClient | None = None


def get_instance() -> CoolhandClient | None:
    """Get the global client instance."""
    return _instance


def set_instance(instance: CoolhandClient) -> None:
    """Set the global client instance."""
    global _instance
    _instance = instance


def initialize(config: Config | None = None, **kwargs) -> CoolhandClient:
    """Initialize the global client."""
    global _instance
    if _instance is None:
        _instance = CoolhandClient(config, **kwargs)
    return _instance
