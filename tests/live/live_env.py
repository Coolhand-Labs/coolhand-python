"""Environment and service factory shared by the live test modules."""

import os
from typing import TypeVar

LIVE_BASE_URL = os.environ.get("COOLHAND_LIVE_BASE_URL", "")
LIVE_API_KEY = os.environ.get("COOLHAND_LIVE_API_KEY", "")

if not LIVE_BASE_URL or not LIVE_API_KEY:
    raise RuntimeError(
        "Live tests need COOLHAND_LIVE_BASE_URL and COOLHAND_LIVE_API_KEY (a private "
        "API key) in the environment. Set both and re-run `make test-live`."
    )

# Round trips from the host to a containerised local server go through port forwarding,
# which on Windows adds ~15-20s per request even when the server itself answers in
# ~400ms. The SDK default of 30s would fail these for environmental reasons that have
# nothing to do with the wrapper.
LIVE_TIMEOUT_SECONDS = 120

ServiceT = TypeVar("ServiceT")


def live_service(
    service_class: type[ServiceT], api_key: str = LIVE_API_KEY
) -> ServiceT:
    return service_class(
        api_key=api_key,
        base_url=LIVE_BASE_URL,
        silent=True,
        timeout=LIVE_TIMEOUT_SECONDS,
    )


METRICS_FIELDS = [
    "days_back",
    "since",
    "until",
    "request_count",
    "failure_count",
    "error_rate",
    "error_rate_change",
    "avg_cost_per_request",
    "total_cost",
    "priced_request_count",
    "long_context_request_count",
    "total_input_tokens",
    "total_output_tokens",
    "avg_input_tokens",
    "avg_output_tokens",
    "avg_latency_ms",
    "correctness_score",
    "sentiment_score",
    "revision_score",
    "first_request_at",
    "last_request_at",
]


def assert_metrics_shape(metrics: dict) -> None:
    """Every field the definition marks required is present with a plausible type."""
    assert sorted(metrics) == sorted(METRICS_FIELDS)
    for field in [
        "request_count",
        "failure_count",
        "priced_request_count",
        "long_context_request_count",
        "total_input_tokens",
        "total_output_tokens",
    ]:
        assert isinstance(metrics[field], int)
    assert isinstance(metrics["since"], str)
    assert isinstance(metrics["until"], str)
    assert metrics["days_back"] is None or isinstance(metrics["days_back"], int)
    for field in ["total_cost", "avg_cost_per_request", "error_rate"]:
        assert metrics[field] is None or isinstance(metrics[field], (int, float))
