# Linking Feedback to an Optimization

`link_feedback`, `bulk_link_feedback` and `unlink_feedback` attach feedback to an
optimization as supporting evidence, via
`POST /api/v2/optimizations/{optimization_id}/feedback_links` (single and bulk share the
route) and `DELETE /api/v2/optimizations/{optimization_id}/feedback_links/{id}`.

These methods require your **private** API key; the public key gets a `401`.

```python
from coolhand import Coolhand

ch = Coolhand(api_key="your-private-api-key")

# One feedback. `link["id"]` is the link's hashid, not the feedback's.
link = ch.link_feedback("optimizationHashid", "feedbackHashid", note="why")

# Many feedbacks.
result = ch.bulk_link_feedback("optimizationHashid", ["fb1", "fb2", "fb3"])
# {"linked": 2, "already_linked": 1, "errored": 0, "not_found": []}

# Remove a link.
ch.unlink_feedback("optimizationHashid", link["id"])
```

The same methods are available on `FeedbackLinkService` if you do not want the
monitoring client.

## Bulk behavior

- The server accepts at most 100 ids per request. `bulk_link_feedback` splits longer
  lists into batches of 100, sums `linked` / `already_linked` / `errored` and
  concatenates `not_found`.
- Already-linked ids count as `already_linked`, not errors. Unknown, malformed and
  other-client ids are all reported in `not_found`.
- If a batch fails, the call raises and earlier batches stay applied. Repeating the call
  is safe.
- An empty list or a blank id raises `ValueError` before any request is made.

## Errors

Like the template read methods, these **raise** rather than returning `None`. A non-2xx
response raises `CoolhandAPIError` whose `.status` is the HTTP code: `401` missing or
public key, `404` unknown optimization, feedback or link, `422` invalid input,
already-linked (single mode) or a `note` that is too long.
