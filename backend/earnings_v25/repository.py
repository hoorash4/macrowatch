from earnings_common.repository import EarningsRepository
from .transport_policy import safe_request_failure


class StoreError(RuntimeError):
    pass


class EarningsV2Repository(EarningsRepository):
    """Historical backfill storage with its own cursor and source diagnostics."""

    state_source = "korea_v25"
    failure_formatter = staticmethod(safe_request_failure)
    error_type = StoreError
