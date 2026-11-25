"""Read access to the realized-savings endpoint.

Wraps `GET /api/v2/savings`, a singleton keyed by the authenticating API key (no id, no
filters). It requires the client's **private** API key — the public key is rejected
exactly like an invalid one.
"""

from typing import cast

from ._read_service import ReadService
from .types import RealizedSavings

SAVINGS_ENDPOINT = "/api/v2/savings"


class SavingsService(ReadService):
    """Read what switching templates or models has saved you.

    Example:
        >>> from coolhand import SavingsService
        >>> service = SavingsService(api_key="your-private-api-key")
        >>> service.get_realized_savings()["total"]
    """

    _resource_label = "Savings"

    def get_realized_savings(self) -> RealizedSavings:
        """Get the savings this client has realized, in total and per workload.

        What the requests served by the current templates cost, versus what the same
        requests would have cost on the deprecated versions they replaced (deprecations
        in the last three months). It is the figure the dashboard shows. There is
        deliberately no `client_id` and no window: the client is derived from the API
        key and the window is fixed server-side.

        Returns:
            A dict with `total`, `cost_at_previous_versions`, `percent` and `workloads`.
            A client with nothing to compare gets zeros and an empty `workloads` list;
            that is real zero savings. Each `workloads[]["id"]` is the workload hashid,
            the same `id` as `search_workloads`.

        Raises:
            CoolhandAPIError: On a non-2xx response, with `status` set — `401` for a
                missing/invalid/public key, and `503` when the comparison could not be
                computed right now. A `503` is NOT zero savings; it is retryable, so
                never map it to zeros. Also raised, with `status` left `None`, on a
                transport failure or a body that is not a JSON object.
        """
        url = f"{self.config['base_url']}{SAVINGS_ENDPOINT}"
        body, _headers = self._get_json_object(url, "Savings")

        workloads = body.get("workloads")
        rows = len(workloads) if isinstance(workloads, list) else 0
        self._log(f"Fetched realized savings for {rows} workload(s)")
        return cast(RealizedSavings, body)
