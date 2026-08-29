"""Small, rate-limited client for SEC public company-facts data."""

from __future__ import annotations

import os
import time
from collections import defaultdict
from typing import Any, Callable

import requests


SEC_COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
DOLTHUB_QUERY_URL = "https://www.dolthub.com/api/v1alpha1/deeleeramone/sec-company-facts/main"

SEC_TAGS = (
    "RevenuesNetOfInterestExpense",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
    "SalesRevenueGoodsNet",
    "OperatingIncomeLoss",
    "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
    "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
    "NetIncomeLoss",
    "ProfitLoss",
    "NetIncomeLossAvailableToCommonStockholdersBasic",
)


class SecEdgarClient:
    def __init__(
        self,
        user_agent: str,
        *,
        session: Any | None = None,
        request_interval_seconds: float = 0.2,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.user_agent = user_agent.strip()
        if not self.user_agent:
            raise ValueError("SEC User-Agent is required.")
        self.session = session or requests.Session()
        self.request_interval_seconds = max(0.11, request_interval_seconds)
        self.sleeper = sleeper
        self._last_request_at = 0.0

    @classmethod
    def from_env(cls, **kwargs: Any) -> "SecEdgarClient":
        user_agent = os.getenv(
            "SEC_USER_AGENT",
            "MacroWatch hoorash4@users.noreply.github.com",
        )
        interval = float(os.getenv("SEC_REQUEST_INTERVAL_SECONDS", "0.2"))
        return cls(user_agent, request_interval_seconds=interval, **kwargs)

    def fetch_company_facts(self, cik: str) -> dict[str, Any]:
        normalized = cik.strip().zfill(10)
        if len(normalized) != 10 or not normalized.isdigit():
            raise ValueError("SEC CIK must be ten digits.")
        elapsed = time.monotonic() - self._last_request_at
        if self._last_request_at and elapsed < self.request_interval_seconds:
            self.sleeper(self.request_interval_seconds - elapsed)
        response = self.session.get(
            SEC_COMPANY_FACTS_URL.format(cik=normalized),
            headers={
                "User-Agent": self.user_agent,
                "Accept-Encoding": "gzip, deflate",
                "Host": "data.sec.gov",
            },
            timeout=60,
        )
        self._last_request_at = time.monotonic()
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("facts"), dict):
            raise ValueError("SEC company-facts response is invalid.")
        return payload


class SecCompanyFactsMirrorClient:
    """Read the nightly SEC Company Facts mirror when SEC blocks shared runners.

    The mirror stores the SEC's original XBRL facts. We rebuild the official
    Company Facts envelope so the normal parser remains the single source of
    quarter-selection and Q4 derivation rules.
    """

    def __init__(
        self,
        *,
        session: Any | None = None,
        request_interval_seconds: float = 0.25,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.session = session or requests.Session()
        self.request_interval_seconds = max(0.2, request_interval_seconds)
        self.sleeper = sleeper
        self._last_request_at = 0.0
        self._tag_ids: dict[int, str] | None = None

    def _query(self, sql: str) -> list[dict[str, Any]]:
        last_error: Exception | None = None
        for attempt in range(4):
            elapsed = time.monotonic() - self._last_request_at
            if self._last_request_at and elapsed < self.request_interval_seconds:
                self.sleeper(self.request_interval_seconds - elapsed)
            try:
                response = self.session.get(
                    DOLTHUB_QUERY_URL,
                    params={"q": sql},
                    headers={"User-Agent": "MacroWatch SEC mirror fallback"},
                    timeout=120,
                )
                self._last_request_at = time.monotonic()
                response.raise_for_status()
                payload = response.json()
                status = str(payload.get("query_execution_status") or "")
                if status not in {"Success", "RowLimit"}:
                    raise RuntimeError(str(payload.get("query_execution_message") or "DoltHub query failed"))
                rows = payload.get("rows") or []
                if not isinstance(rows, list):
                    raise ValueError("SEC mirror response rows are invalid.")
                return [row for row in rows if isinstance(row, dict)]
            except (requests.RequestException, RuntimeError, ValueError) as error:
                last_error = error
                if attempt < 3:
                    self.sleeper(2 ** attempt)
        raise last_error if last_error is not None else RuntimeError("SEC mirror query failed.")

    def _load_tag_ids(self) -> dict[int, str]:
        if self._tag_ids is not None:
            return self._tag_ids
        quoted = ",".join("'" + tag.replace("'", "''") + "'" for tag in SEC_TAGS)
        rows = self._query(
            "SELECT tag_id, tag FROM xbrl_tags "
            f"WHERE namespace = 'us-gaap' AND tag IN ({quoted})"
        )
        self._tag_ids = {int(row["tag_id"]): str(row["tag"]) for row in rows}
        missing = set(SEC_TAGS) - set(self._tag_ids.values())
        if missing:
            raise ValueError(f"SEC mirror taxonomy is incomplete: {sorted(missing)}")
        return self._tag_ids

    def _accessions(self, ids: set[int]) -> dict[int, str]:
        result: dict[int, str] = {}
        ordered = sorted(ids)
        for offset in range(0, len(ordered), 200):
            chunk = ordered[offset:offset + 200]
            rows = self._query(
                "SELECT accn_id, accn FROM accessions WHERE accn_id IN ("
                + ",".join(str(value) for value in chunk) + ")"
            )
            result.update({int(row["accn_id"]): str(row["accn"]) for row in rows})
        return result

    def fetch_company_facts(
        self,
        cik: str,
        *,
        first_year: int,
        expanded: bool = False,
    ) -> dict[str, Any]:
        normalized = cik.strip().zfill(10)
        if len(normalized) != 10 or not normalized.isdigit():
            raise ValueError("SEC CIK must be ten digits.")
        tag_ids: dict[int, tuple[str, str]] = {
            tag_id: ("us-gaap", tag)
            for tag_id, tag in self._load_tag_ids().items()
        }
        candidate_tags: list[str] = []
        if expanded:
            rows = self._query(
                "SELECT DISTINCT tags.tag_id, tags.namespace, tags.tag "
                "FROM xbrl_tags tags JOIN facts_enc facts ON facts.tag_id = tags.tag_id "
                f"WHERE facts.cik = '{normalized}' AND facts.end >= '{first_year}-01-01' "
                "AND facts.unit = 'USD' "
                "AND facts.form IN ('10-Q','10-Q/A','10-K','10-K/A') AND ("
                "LOWER(tags.tag) LIKE '%revenue%' "
                "OR LOWER(tags.tag) LIKE '%sales%' "
                "OR LOWER(tags.tag) LIKE '%operatingincome%' "
                "OR LOWER(tags.tag) LIKE '%incomeloss%' "
                "OR LOWER(tags.tag) LIKE '%netincome%' "
                "OR LOWER(tags.tag) LIKE '%profitloss%')"
            )
            for row in rows:
                tag_id = int(row["tag_id"])
                namespace = str(row.get("namespace") or "us-gaap")
                tag = str(row["tag"])
                tag_ids[tag_id] = (namespace, tag)
                candidate_tags.append(f"{namespace}:{tag}")

        facts: list[dict[str, Any]] = []
        last_id = 0
        while True:
            rows = self._query(
                "SELECT id, tag_id, unit, start, end, val, accn_id, fy, fp, form, filed, frame "
                "FROM facts_enc "
                f"WHERE cik = '{normalized}' AND id > {last_id} "
                f"AND tag_id IN ({','.join(str(value) for value in tag_ids)}) "
                "AND unit = 'USD' AND form IN ('10-Q','10-Q/A','10-K','10-K/A') "
                f"AND end >= '{first_year}-01-01' ORDER BY id LIMIT 1000"
            )
            if not rows:
                break
            facts.extend(rows)
            last_id = int(rows[-1]["id"])
            if len(rows) < 1000:
                break

        accession_map = self._accessions({int(row["accn_id"]) for row in facts if row.get("accn_id")})
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in facts:
            accession_id = int(row["accn_id"])
            accession = accession_map.get(accession_id)
            if not accession:
                continue
            tag_identity = tag_ids.get(int(row["tag_id"]))
            if not tag_identity:
                continue
            grouped[tag_identity].append({
                key: row.get(key)
                for key in ("start", "end", "val", "fy", "fp", "form", "filed", "frame")
            } | {"accn": accession})
        taxonomies: dict[str, dict[str, Any]] = defaultdict(dict)
        for (namespace, tag), rows in grouped.items():
            taxonomies[namespace][tag] = {"units": {"USD": rows}}
        return {
            "cik": int(normalized),
            "facts": dict(taxonomies),
            "metadata": {
                "transport": "dolthub_sec_company_facts_mirror",
                "expanded": expanded,
                "candidate_tags": sorted(candidate_tags),
            },
        }
