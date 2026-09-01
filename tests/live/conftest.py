"""Collection gate for the opt-in live suite.

The live tests need a reachable Coolhand server and a real *private* API key, neither of
which CI has. Without `COOLHAND_LIVE_BASE_URL` they are not collected at all, rather
than collected and skipped: a default `pytest` run should never report a green skip that
quietly proved nothing.

Once opted in, a missing or incomplete credential is a hard failure — see the test
module.
"""

import os

collect_ignore_glob = [] if os.environ.get("COOLHAND_LIVE_BASE_URL") else ["test_*.py"]
