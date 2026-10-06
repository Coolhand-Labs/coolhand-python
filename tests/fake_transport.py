"""Fake urllib transport shared by the read-service unit tests."""

import json
from email.message import Message
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse

BASE_URL = "https://test.coolhandlabs.com"

PAGINATION_HEADERS = {
    "X-Page": "1",
    "X-Per-Page": "25",
    "X-Total-Count": "1",
    "X-Total-Pages": "1",
}


class FakeResponse:
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


class FakeOpener:
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


def build_service(service_class, body=None, headers=None, error=None, **config):
    """Create `service_class` whose transport is a `FakeOpener`."""
    settings = {"api_key": "test-private-key", "base_url": BASE_URL, "silent": True}
    settings.update(config)
    service = service_class(**settings)
    response = None if error is not None else FakeResponse(body, headers)
    service._opener = FakeOpener(response=response, error=error)
    return service


def http_error(url, status, body):
    """Build an HTTPError whose body reads back, the way a real one does."""
    error = HTTPError(url=url, code=status, msg="error", hdrs=Message(), fp=None)
    error.read = lambda: body.encode("utf-8")
    return error


def query_of(request):
    return parse_qs(urlparse(request.full_url).query)
