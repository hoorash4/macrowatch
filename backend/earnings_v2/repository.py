from __future__ import annotations

from datetime import datetime, timedelta, timezone

from earnings_common.repository import EarningsRepository, StoreError

TOKEN_TIMEOUT = (5, 10)
KIS_TOKEN_CACHE_KEY = "kis_access_token_prod"


class EarningsV2Repository(EarningsRepository):
    """Korean v2 storage, including the shared KIS token cache."""

    def cached_kis_token(self) -> str | None:
        response = self.session.get(
            f"{self.url}/rest/v1/app_settings",
            headers=self.headers,
            params={"key": f"eq.{KIS_TOKEN_CACHE_KEY}", "select": "value", "limit": "1"},
            timeout=TOKEN_TIMEOUT,
        )
        if not response.ok:
            return None
        rows = response.json()
        value = rows[0].get("value", {}) if isinstance(rows, list) and rows else {}
        token = str(value.get("access_token") or "") if isinstance(value, dict) else ""
        expires = str(value.get("expires_at") or "") if isinstance(value, dict) else ""
        try:
            valid = datetime.fromisoformat(expires.replace("Z", "+00:00")) > datetime.now(timezone.utc) + timedelta(minutes=10)
        except ValueError:
            valid = False
        return token if token and valid else None

    def save_kis_token(self, token: str, expires_in: int) -> None:
        expires = datetime.now(timezone.utc) + timedelta(seconds=max(expires_in, 60))
        response = self.session.post(
            f"{self.url}/rest/v1/app_settings",
            headers={**self.headers, "Prefer": "resolution=merge-duplicates,return=minimal"},
            params={"on_conflict": "key"},
            json={"key": KIS_TOKEN_CACHE_KEY, "value": {"access_token": token, "expires_at": expires.isoformat()}, "updated_at": datetime.now(timezone.utc).isoformat(), "updated_by": None},
            timeout=TOKEN_TIMEOUT,
        )
        if not response.ok:
            raise StoreError("Could not persist the shared KIS access token")
