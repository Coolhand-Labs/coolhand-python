"""Read-only access to the step template endpoints.

Wraps `GET /api/v2/step_templates` (list) and `GET /api/v2/step_templates/{id}` (show).
A step template is a skill or other recurring step Coolhand detects in captured agent
sessions. Both reads require the client's **private** API key; the public key is
rejected. There are no write endpoints.
"""

from typing import cast

from ._read_service import (
    QueryValue,
    ReadService,
    _pagination_from_headers,
    _with_query,
)
from .types import (
    ListStepTemplatesResponse,
    StepTemplate,
    StepTemplateType,
)

STEP_TEMPLATES_ENDPOINT = "/api/v2/step_templates"


class StepTemplateService(ReadService):
    """Read the skills and recurring steps detected in your captured agent sessions.

    Example:
        >>> from coolhand import StepTemplateService
        >>> service = StepTemplateService(api_key="your-private-api-key")
        >>> result = service.list_step_templates(step_type="prompt_step")
        >>> detail = service.get_step_template(result["step_templates"][0]["id"])
    """

    _resource_label = "Step template"

    def list_step_templates(
        self,
        *,
        search: str | None = None,
        step_type: StepTemplateType | None = None,
        include_metrics: bool | None = None,
        page: int | None = None,
        per: int | None = None,
    ) -> ListStepTemplatesResponse:
        """List the client's published step templates, newest first.

        There is deliberately no `client_id`: the client is always derived from the
        authenticating API key.

        Args:
            search: Case-insensitive substring match on the step template name.
            step_type: "prompt_step" (matches a user prompt such as a slash command) or
                "tool_step" (matches tool usage). Any other value returns 422.
            include_metrics: Add a `metrics` object to each row. Off by default; when
                set, the server caps `per` at 25 (`pagination["per_page"]` reports the
                size applied).
            page: Page number, 1-based.
            per: Page size (default 25, max 100, both enforced server-side).

        Returns:
            A dict with `step_templates` (newest first) and `pagination`, the latter
            read from the response headers rather than computed from the rows returned.

        Raises:
            CoolhandAPIError: On a non-2xx response, with `status` set — `401` for a
                missing/invalid/public key, `422` for a `step_type` other than
                "prompt_step"/"tool_step", and `504` when a query exceeds the server's
                statement timeout (retryable: narrow with `search` or a smaller `per`).
                Also raised, with `status` left `None`, on a transport failure or a
                body that is not the JSON array this endpoint returns.
        """
        filters: dict[str, QueryValue] = {
            "search": search,
            "step_type": step_type,
            "include_metrics": include_metrics,
            "page": page,
            "per": per,
        }
        url = _with_query(
            f"{self.config['base_url']}{STEP_TEMPLATES_ENDPOINT}", filters
        )

        body, headers = self._get_json_array(url, "Step template list")

        step_templates: list[StepTemplate] = body
        self._log(f"Fetched {len(step_templates)} step template(s)")
        return {
            "step_templates": step_templates,
            "pagination": _pagination_from_headers(headers, page, per),
        }

    def get_step_template(
        self,
        step_template_id: int,
        *,
        include_metrics: bool | None = None,
    ) -> StepTemplate:
        """Get one published step template by its integer id.

        Args:
            step_template_id: The integer `id` from `list_step_templates` (not a
                hashid).
            include_metrics: Server default is true, unlike the list. Pass `False` to
                skip the metrics queries and omit `metrics`.

        Returns:
            The step template, with `metrics` unless `include_metrics=False`.

        Raises:
            ValueError: If `step_template_id` is not a positive integer, before any
                request is made.
            CoolhandAPIError: On a non-2xx response, with `status` set — `401` for a
                missing/invalid/public key, `404` for an unknown id, a draft or failed
                step template, or one belonging to another client (existence is not
                disclosed), and `504` on a statement timeout. Also raised, with
                `status` left `None`, on a transport failure or a non-JSON-object body.
        """
        if (
            not isinstance(step_template_id, int)
            or isinstance(step_template_id, bool)
            or step_template_id <= 0
        ):
            raise ValueError(
                "get_step_template: step_template_id must be a positive integer"
            )
        url = _with_query(
            f"{self.config['base_url']}{STEP_TEMPLATES_ENDPOINT}/{step_template_id}",
            {"include_metrics": include_metrics},
        )

        body, _headers = self._get_json_object(url, "Step template")

        step_template = cast(StepTemplate, body)
        self._log(f"Fetched step template {step_template.get('id', 'unknown')}")
        return step_template
