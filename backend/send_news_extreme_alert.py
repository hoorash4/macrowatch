from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone

import requests


KST = timezone(timedelta(hours=9))
TIMEOUT = 30


def require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


class SupabaseRest:
    def __init__(self) -> None:
        self.url = require_env("SUPABASE_URL").rstrip("/")
        key = require_env("SUPABASE_SERVICE_ROLE_KEY")
        self.headers = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    def request(self, method: str, table: str, *, params: dict[str, str] | None = None, body: object | None = None, prefer: str | None = None):
        headers = dict(self.headers)
        if prefer:
            headers["Prefer"] = prefer
        response = requests.request(method, f"{self.url}/rest/v1/{table}", headers=headers, params=params, json=body, timeout=TIMEOUT)
        if not response.ok:
            raise RuntimeError(f"Supabase {table}: {response.status_code} {response.text[:500]}")
        return response.json() if response.content else None


def refresh_kakao_access_token() -> str:
    data = {"grant_type": "refresh_token", "client_id": require_env("KAKAO_REST_API_KEY"), "refresh_token": require_env("KAKAO_REFRESH_TOKEN")}
    client_secret = os.getenv("KAKAO_CLIENT_SECRET", "").strip()
    if client_secret:
        data["client_secret"] = client_secret
    response = requests.post("https://kauth.kakao.com/oauth/token", data=data, timeout=TIMEOUT)
    response.raise_for_status()
    access_token = response.json().get("access_token")
    if not access_token:
        raise RuntimeError("Kakao access token was not returned")
    return str(access_token)


def send_kakao_message(access_token: str, message: str) -> None:
    template = {
        "object_type": "text",
        "text": message,
        "link": {
            "web_url": "https://hoorash4.github.io/macrowatch/",
            "mobile_web_url": "https://hoorash4.github.io/macrowatch/",
        },
    }
    response = requests.post(
        "https://kapi.kakao.com/v2/api/talk/memo/default/send",
        headers={"Authorization": f"Bearer {access_token}"},
        data={"template_object": json.dumps(template, ensure_ascii=False)},
        timeout=TIMEOUT,
    )
    response.raise_for_status()


def message_for(decisive_count: int) -> str | None:
    return f"[MacroWatch] 오늘의 결정적 뉴스가 {decisive_count}건 있습니다." if decisive_count else None


def main() -> int:
    db = SupabaseRest()
    article_date = datetime.now(KST).date().isoformat()
    rows = db.request("GET", "news_daily_article_sentiment", params={"select": "decisive_news_count", "article_date": f"eq.{article_date}"}) or []
    row = rows[0] if rows else {}
    decisive_count = int(row.get("decisive_news_count") or 0)
    message = message_for(decisive_count)
    if not message:
        print(f"No extreme news signal for {article_date}.")
        return 0

    existing = db.request("GET", "news_extreme_alerts", params={"select": "status", "article_date": f"eq.{article_date}"}) or []
    if existing and existing[0].get("status") == "sent":
        print(f"Extreme news alert already sent for {article_date}.")
        return 0

    payload = {"article_date": article_date, "decisive_news_count": decisive_count, "status": "failed", "error_message": None, "sent_at": None, "updated_at": datetime.now(timezone.utc).isoformat()}
    try:
        send_kakao_message(refresh_kakao_access_token(), message)
        payload.update({"status": "sent", "sent_at": datetime.now(timezone.utc).isoformat()})
        db.request("POST", "news_extreme_alerts", params={"on_conflict": "article_date"}, body=payload, prefer="resolution=merge-duplicates,return=minimal")
        print(f"Extreme news alert sent for {article_date}.")
        return 0
    except Exception as exc:
        payload["error_message"] = str(exc)[:1000]
        db.request("POST", "news_extreme_alerts", params={"on_conflict": "article_date"}, body=payload, prefer="resolution=merge-duplicates,return=minimal")
        print(f"Extreme news alert failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
