"""Fail-closed checks for the effective-dated earnings collection universe."""

from __future__ import annotations

from typing import Any, Iterable


class CollectionCoverageError(RuntimeError):
    """The collector input does not match the current index memberships."""


def validate_collection_universe(
    coverage: dict[str, Any],
    companies: Iterable[dict[str, Any]],
    *,
    company_id_key: str,
) -> int:
    """Return the dynamic union size or raise before a partial collection starts.

    Index target counts are checked independently, while the collector input is
    checked as a union of company identities. This deliberately avoids a fixed
    U.S. count: the overlap between the two top-100 universes changes over time.
    """
    indices = coverage.get("indices")
    if not isinstance(indices, list) or not indices:
        raise CollectionCoverageError("No active earnings indices were returned.")
    invalid_indices = []
    for index in indices:
        if not isinstance(index, dict):
            raise CollectionCoverageError("Earnings index coverage is invalid.")
        target = int(index.get("target_count") or 0)
        active = int(index.get("active_membership_count") or 0)
        if target <= 0 or active != target:
            invalid_indices.append(f"{index.get('index_id')}: {active}/{target}")
    if invalid_indices:
        raise CollectionCoverageError(
            "Earnings index membership count mismatch: " + ", ".join(invalid_indices)
        )

    missing_identifiers = coverage.get("missing_identifier_tickers") or []
    if missing_identifiers:
        tickers = ", ".join(str(value) for value in missing_identifiers[:20])
        raise CollectionCoverageError(f"Current earnings companies lack provider identifiers: {tickers}")

    expected = int(coverage.get("unique_companies") or 0)
    actual_ids = {
        str(company.get(company_id_key) or "").strip()
        for company in companies
        if str(company.get(company_id_key) or "").strip()
    }
    if expected <= 0 or len(actual_ids) != expected:
        raise CollectionCoverageError(
            f"Collector company union mismatch: {len(actual_ids)}/{expected}"
        )
    return expected
