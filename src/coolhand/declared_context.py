"""Ambient workload/template identity for captured LLM calls.

`with_context(...)` declares which workload (and optionally template) the LLM calls made
inside it belong to. The declaration is snapshotted when a call is *captured* and
travels in the payload as `llm_request_log.declared`, so the background sender never
needs to read the context. The server skips log-to-template matching for declared logs.

The declaration lives in a `contextvars.ContextVar`: it follows `await` and tasks
created by `asyncio`, but is **not** inherited by plain `threading.Thread` or
`ThreadPoolExecutor` workers unless the work runs under
`contextvars.copy_context()`.
"""

import contextvars
import copy
import functools
import inspect
import unicodedata
from collections.abc import Callable, Mapping
from types import TracebackType
from typing import Any, TypeVar

from .types import RequestData

F = TypeVar("F", bound=Callable[..., Any])

_declared: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "coolhand_declared", default=None
)

# The declarations to restore as blocks exit, innermost last. One shared, module-level
# variable: a ContextVar must never be created dynamically (every Context keeps a strong
# reference to each var it has ever set), and keeping the stack per context, not on the
# scope object, lets one scope be entered from several tasks or threads without them
# popping each other's entries.
_previous: contextvars.ContextVar[tuple[dict[str, Any] | None, ...]] = (
    contextvars.ContextVar("coolhand_declared_previous", default=())
)

MAX_KEY_LENGTH = 255
MAX_DESCRIPTION_LENGTH = 1000

# Only these fields are forwarded; the server rejects or ignores anything else.
_WORKLOAD_TEXT_FIELDS = ("name", "description")
_TEMPLATE_TEXT_FIELDS = ("name",)


def _require_encodable(value: str, label: str) -> None:
    """Reject strings that cannot be encoded as UTF-8, e.g. a lone surrogate."""
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError(
            f"{label} is not valid text (broken string encoding)"
        ) from None


def validate_key(key: Any, label: str = "key") -> str:
    """Check a workload/template key against the server's rules and return it.

    Raises `ValueError` for a non-string, empty or over-long key; one with leading or
    trailing whitespace; control or invisible-format characters (Unicode categories
    `Cc`/`Cf`); a string that cannot be encoded as UTF-8 (e.g. a lone surrogate); or a
    leading, trailing or doubled `/`. `#`, `:` and `·` are fine.
    """
    if not isinstance(key, str):
        raise ValueError(f"{label} must be a string")
    if not key:
        raise ValueError(f"{label} must not be blank")
    if len(key) > MAX_KEY_LENGTH:
        raise ValueError(f"{label} must be at most {MAX_KEY_LENGTH} characters")
    _require_encodable(key, label)
    if key != key.strip():
        raise ValueError(f"{label} must not have leading or trailing whitespace")
    if any(unicodedata.category(char) in ("Cc", "Cf") for char in key):
        raise ValueError(f"{label} must not contain control or invisible characters")
    if key.startswith("/") or key.endswith("/") or "//" in key:
        raise ValueError(f"{label} must not start or end with '/' or contain '//'")
    return key


def optional_text(value: Any, label: str, max_length: int | None = None) -> str | None:
    """Return `value` if it is a valid string, or `None` if it is `None`.

    Raises `ValueError` for a non-string, a string over `max_length`, or text that
    cannot be encoded as UTF-8.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    if max_length is not None and len(value) > max_length:
        raise ValueError(f"{label} must be at most {max_length} characters")
    _require_encodable(value, label)
    return value


def _clean_identity(kind: str, value: Any, fields: tuple[str, ...]) -> dict[str, str]:
    """Validate a `workload`/`template` mapping and keep only the permitted fields."""
    if not isinstance(value, Mapping):
        raise ValueError(f"{kind} must be a mapping with an 'id' or a 'key'")

    cleaned: dict[str, str] = {}

    identifier = value.get("id")
    if identifier is not None:
        if not isinstance(identifier, str):
            raise ValueError(f"{kind}.id must be a string")
        # A blank id counts as not given.
        if identifier.strip():
            cleaned["id"] = identifier.strip()

    if value.get("key") is not None:
        cleaned["key"] = validate_key(value["key"], f"{kind}.key")

    if not cleaned:
        raise ValueError(f"{kind} needs an 'id' or a 'key'")

    for field in fields:
        max_length = MAX_DESCRIPTION_LENGTH if field == "description" else None
        text = optional_text(value.get(field), f"{kind}.{field}", max_length)
        if text is not None:
            cleaned[field] = text

    return cleaned


def optional_reporter(reporter: Any, label: str = "reporter") -> str | None:
    """Like `optional_text`, and also rejects blank (whitespace-only) text."""
    text = optional_text(reporter, label)
    if text is not None and not text.strip():
        raise ValueError(f"{label} must not be blank")
    return text


class _Scope:
    """Context manager + decorator returned by `with_context`."""

    def __init__(
        self,
        reporter: str | None,
        workload: dict[str, str] | None,
        template: dict[str, str] | None,
    ) -> None:
        self._reporter = reporter
        self._workload = workload
        self._template = template

    def _merged(self) -> dict[str, Any]:
        merged: dict[str, Any] = dict(_declared.get() or {})
        if self._reporter is not None:
            merged["reporter"] = self._reporter
        if self._workload is not None:
            merged["workload"] = dict(self._workload)
            if self._template is None:
                # The outer template belongs to the outer workload; pairing it with
                # this one would attribute the call to a template of another workload.
                merged.pop("template", None)
        if self._template is not None:
            merged["template"] = dict(self._template)

        template = merged.get("template")
        if template and "id" not in template and "workload" not in merged:
            raise ValueError(
                "template.key needs a workload in this or an enclosing "
                "with_context (a template id does not)"
            )
        return merged

    def __enter__(self) -> "_Scope":
        merged = self._merged()
        _previous.set((*_previous.get(), _declared.get()))
        _declared.set(merged)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        stack = _previous.get()
        if not stack:
            # Exiting in a context that never entered (e.g. an async generator
            # finalized by the event loop): nothing of ours to restore.
            return
        _previous.set(stack[:-1])
        _declared.set(stack[-1])

    def __call__(self, func: F) -> F:
        if inspect.isasyncgenfunction(func):

            @functools.wraps(func)
            async def asyncgen_wrapper(*args: Any, **kwargs: Any) -> Any:
                # Declared only while the generator body runs, not while it is
                # suspended at a yield, so the consumer's own calls are not attributed.
                gen = func(*args, **kwargs)
                sent = None
                try:
                    while True:
                        with self:
                            try:
                                item = await gen.asend(sent)
                            except StopAsyncIteration:
                                return
                        sent = yield item
                finally:
                    await gen.aclose()

            return asyncgen_wrapper  # type: ignore[return-value]

        if inspect.isgeneratorfunction(func):

            @functools.wraps(func)
            def generator_wrapper(*args: Any, **kwargs: Any) -> Any:
                gen = func(*args, **kwargs)
                sent = None
                try:
                    while True:
                        with self:
                            try:
                                item = gen.send(sent)
                            except StopIteration as stop:
                                return stop.value
                        sent = yield item
                finally:
                    gen.close()

            return generator_wrapper  # type: ignore[return-value]

        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                with self:
                    return await func(*args, **kwargs)

            return async_wrapper  # type: ignore[return-value]

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            with self:
                return func(*args, **kwargs)

        return wrapper  # type: ignore[return-value]


def with_context(
    *,
    reporter: str | None = None,
    workload: Mapping[str, Any] | None = None,
    template: Mapping[str, Any] | None = None,
) -> _Scope:
    """Declare the workload/template for LLM calls captured inside the block.

    Usable as a context manager or a decorator (plain, `async def` or generator
    functions). Nested blocks override per top-level field; a `workload` or `template`
    mapping replaces the outer one wholesale, and a block that sets a `workload`
    without a `template` does not inherit the outer template.

    A decorated generator is declared only while its body runs, not while it is
    suspended at a `yield`. `send()` and the return value pass through; `throw()` does
    not reach the inner generator, and its `close()`-time cleanup runs undeclared.

    Args:
        reporter: Free-text name of the system declaring this identity.
        workload: `{"id": ...}` or `{"key": ...}`, optionally with `name` and
            `description`. An `id` references an existing workload; a `key` is
            find-or-created server-side.
        template: `{"id": ...}` or `{"key": ...}`, optionally with `name`. A template
            `key` needs a workload in this or an enclosing block.

    Raises:
        ValueError: At call time (for a malformed argument) or on entering the block
            (for a template `key` with no workload), before any code inside runs.

    Example:
        >>> with with_context(
        ...     reporter="my-app",
        ...     workload={"key": "Parser#call"},
        ...     template={"key": "Parser#call/squish:L2"},
        ... ):
        ...     client.chat.completions.create(...)
    """
    return _Scope(
        optional_reporter(reporter),
        _clean_identity("workload", workload, _WORKLOAD_TEXT_FIELDS)
        if workload is not None
        else None,
        _clean_identity("template", template, _TEMPLATE_TEXT_FIELDS)
        if template is not None
        else None,
    )


def snapshot() -> dict[str, Any] | None:
    """Copy of the active declaration, or `None` when no context is active."""
    current = _declared.get()
    return copy.deepcopy(current) if current else None


def attach_snapshot(req_data: RequestData) -> None:
    """Record the active declaration on `req_data` at capture time.

    Called by the interceptors on the caller's own context, before anything is queued,
    because the background sender runs on a thread that cannot see the context.
    """
    declared = snapshot()
    if declared:
        req_data["declared"] = declared
