from __future__ import annotations

import requests
import time
from typing import Any


TRANSIENT_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


def resilient_session() -> requests.Session:
    """Create the shared HTTP session used by the V2 collectors."""

    session = requests.Session()
    session.headers.update({"User-Agent": "MacroWatch-Earnings-V2/1.0"})
    return session


def get_with_retries(
    session: Any,
    url: str,
    *,
    attempts: int = 5,
    backoff_factor: float = 1.0,
    **kwargs: Any,
) -> Any:
    """GET with bounded retries for transient network/API failures."""

    if attempts < 1:
        raise ValueError("attempts must be at least 1")
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = session.get(url, **kwargs)
            status = int(getattr(response, "status_code", 0) or 0)
            if status not in TRANSIENT_STATUS_CODES or attempt == attempts - 1:
                return response
        except Exception as exc:  # requests can be stubbed by legacy tests.
            last_error = exc
            if attempt == attempts - 1:
                raise
        time.sleep(backoff_factor * (2 ** attempt))
    if last_error is not None:
        raise last_error
    raise RuntimeError("HTTP retry loop ended without a response")
