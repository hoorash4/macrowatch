from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

from common import SupabaseRest, refresh_kakao_access_token, send_kakao_text


KST = timezone(timedelta(hours=9))


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
        access_token = refresh_kakao_access_token(required=True)
        if not access_token:
            raise RuntimeError("Kakao access token was not returned")
        send_kakao_text(access_token, message)
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
