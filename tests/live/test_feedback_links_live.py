"""End-to-end proof of the feedback link methods against a REAL Coolhand server.

Run with `make test-live`; not part of `make verify`. Needs, in the environment:

    COOLHAND_LIVE_BASE_URL, COOLHAND_LIVE_API_KEY (a private key),
    COOLHAND_LIVE_OPTIMIZATION_ID, COOLHAND_LIVE_FEEDBACK_IDS (comma-separated, 2+)

Links created by the single-link test are removed again; the bulk tests leave their
links in place (linking is idempotent).
"""

import os

import pytest

from coolhand import CoolhandAPIError, FeedbackLinkService

LIVE_BASE_URL = os.environ.get("COOLHAND_LIVE_BASE_URL", "")
LIVE_API_KEY = os.environ.get("COOLHAND_LIVE_API_KEY", "")
OPTIMIZATION_ID = os.environ.get("COOLHAND_LIVE_OPTIMIZATION_ID", "")
FEEDBACK_IDS = [
    i for i in os.environ.get("COOLHAND_LIVE_FEEDBACK_IDS", "").split(",") if i
]

if not (LIVE_BASE_URL and LIVE_API_KEY and OPTIMIZATION_ID and len(FEEDBACK_IDS) >= 2):
    raise RuntimeError(
        "Feedback link live tests need COOLHAND_LIVE_BASE_URL, COOLHAND_LIVE_API_KEY, "
        "COOLHAND_LIVE_OPTIMIZATION_ID and COOLHAND_LIVE_FEEDBACK_IDS (2+ ids)."
    )


def live_service(api_key: str = LIVE_API_KEY) -> FeedbackLinkService:
    return FeedbackLinkService(
        api_key=api_key, base_url=LIVE_BASE_URL, silent=True, timeout=120
    )


def link_first_free_feedback(service, note):
    """Link the first fixture feedback that is not already linked (422 means taken)."""
    for feedback_id in FEEDBACK_IDS:
        try:
            return feedback_id, service.link_feedback(
                OPTIMIZATION_ID, feedback_id, note=note
            )
        except CoolhandAPIError as error:
            if error.status != 422:
                raise
    raise AssertionError("Live fixture broken: every feedback is already linked.")


def test_link_then_duplicate_then_unlink():
    service = live_service()
    feedback_id, link = link_first_free_feedback(service, "live test")
    try:
        assert link["feedback_id"] == feedback_id
        assert link["optimization_id"] == OPTIMIZATION_ID
        assert link["note"] == "live test"
        assert isinstance(link["id"], str)

        with pytest.raises(CoolhandAPIError) as duplicate:
            service.link_feedback(OPTIMIZATION_ID, feedback_id)
        assert duplicate.value.status == 422
    finally:
        service.unlink_feedback(OPTIMIZATION_ID, link["id"])

    with pytest.raises(CoolhandAPIError) as gone:
        service.unlink_feedback(OPTIMIZATION_ID, link["id"])
    assert gone.value.status == 404


def test_bulk_counts_already_linked_and_not_found():
    service = live_service()

    result = service.bulk_link_feedback(
        OPTIMIZATION_ID, [*FEEDBACK_IDS[:1], "doesnotexist"]
    )

    assert result["errored"] == 0
    assert result["linked"] + result["already_linked"] == 1
    assert result["not_found"] == ["doesnotexist"]


def test_bulk_is_idempotent():
    service = live_service()
    service.bulk_link_feedback(OPTIMIZATION_ID, FEEDBACK_IDS)

    again = service.bulk_link_feedback(OPTIMIZATION_ID, FEEDBACK_IDS)

    assert again["linked"] == 0
    assert again["already_linked"] == len(FEEDBACK_IDS)
    assert again["not_found"] == []


def test_unknown_optimization_is_404():
    with pytest.raises(CoolhandAPIError) as excinfo:
        live_service().bulk_link_feedback("doesnotexist", FEEDBACK_IDS[:1])

    assert excinfo.value.status == 404


def test_bad_key_is_401():
    with pytest.raises(CoolhandAPIError) as excinfo:
        live_service(api_key="not-a-key").bulk_link_feedback(
            OPTIMIZATION_ID, FEEDBACK_IDS[:1]
        )

    assert excinfo.value.status == 401
