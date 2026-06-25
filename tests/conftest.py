"""Shared test configuration.

Disable rate limiting by default so the bulk of the suite (which fires many
requests from the same test client) is never throttled. The dedicated
rate-limit tests re-enable it with their own small limiter.
"""

import os

os.environ.setdefault("RATE_LIMIT_ENABLED", "false")
