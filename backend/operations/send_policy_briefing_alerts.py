from __future__ import annotations

import sys
from datetime import datetime, timezone

from common import SupabaseRest, refresh_kakao_access_token, send_kakao_text


MESSAGES = {
    "new": "통화정책 시그널에 새로운 FOMC 브리핑이 등록되었습니다.",
    "update": "통화정책 시그널에 업데이트된 FOMC 브리핑이 등록되었습니다.",
}


def main() -> int:
    """미발송 브리핑 알림을 오래된 순서대로 보내고 성공 건만 완료 처리한다."""
    db = SupabaseRest()
    rows = db.request(
        "GET",
        "policy_briefing_alerts",
        params={
            "select": "central_bank,meeting_date,revision,alert_kind,status",
            "status": "in.(pending,failed)",
            "order": "created_at.asc",
            "limit": "20",
        },
    ) or []
    if not rows:
        print("No pending FOMC briefing alerts.")
        return 0

    access_token = refresh_kakao_access_token(required=True)
    if not access_token:
        raise RuntimeError("Kakao access token was not returned")

    failures = 0
    for row in rows:
        now = datetime.now(timezone.utc).isoformat()
        match = {
            "central_bank": f"eq.{row['central_bank']}",
            "meeting_date": f"eq.{row['meeting_date']}",
            "revision": f"eq.{row['revision']}",
        }
        try:
            send_kakao_text(access_token, MESSAGES[row["alert_kind"]])
            db.request("PATCH", "policy_briefing_alerts", params=match, body={
                "status": "sent", "sent_at": now, "last_error": None, "updated_at": now,
            }, prefer="return=minimal")
            print(f"Sent {row['alert_kind']} FOMC briefing alert for {row['meeting_date']}.")
        except Exception as exc:
            failures += 1
            db.request("PATCH", "policy_briefing_alerts", params=match, body={
                "status": "failed", "last_error": str(exc)[:1000], "updated_at": now,
            }, prefer="return=minimal")
            print(f"Failed FOMC briefing alert: {exc}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
