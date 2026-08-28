"""Small, rate-limited client for SEC public company-facts data."""

from __future__ import annotations

import os
import time
from typing import Any, Callable

import requests


SEC_COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"


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
