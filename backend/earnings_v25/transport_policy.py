"""v2.5 financial-source diagnostics layered on the common request transport."""
from functools import partial

from earnings_common.http import bounded_request as common_request
from earnings_common.http import safe_request_failure as common_failure


def safe_request_failure(provider: str, operation: str, error: Exception) -> str:
    message = common_failure(provider, operation, error)
    response = getattr(error, "response", None)
    if getattr(response, "status_code", None) is not None:
        headers = getattr(response, "headers", {}) or {}
        stage = headers.get("X-Financial-Source-Stage")
        reason = headers.get("X-Financial-Source-Reason")
        details = ": ".join(value for value in (stage, reason) if value)
        if details:
            message += f" ({details})"
    return message


bounded_request = partial(common_request, failure_formatter=safe_request_failure)
