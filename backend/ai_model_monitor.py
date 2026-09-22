"""Monthly GPT model availability notice for the MacroWatch administrator."""

from __future__ import annotations

import json
import os
import smtplib
import ssl
from email.message import EmailMessage
from urllib.request import Request, urlopen


def monitor_request(action: str, **fields: object) -> dict:
    url = os.environ["SUPABASE_URL"].rstrip("/") + "/functions/v1/ai-model-monitor"
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    request = Request(
        url,
        data=json.dumps({"action": action, **fields}).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {key}",
            "apikey": key,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urlopen(request, timeout=60) as response:
        result = json.load(response)
    if not isinstance(result, dict):
        raise RuntimeError("AI model monitor response is invalid")
    return result


def send_model_email(model_ids: list[str], current: dict) -> None:
    account = os.environ["EMAIL_ADMIN"].strip()
    password = os.environ["EMAIL_APP_KEY"].strip()
    recipient = os.environ["EMAIL_RCV_ADDRESS"].strip()
    if not account or not password or not recipient:
        raise RuntimeError("Email notification secrets are missing")
    message = EmailMessage()
    message["From"] = account
    message["To"] = recipient
    message["Subject"] = "[MacroWatch] 새 GPT 모델 확인"
    message.set_content(
        "OpenAI API에서 현재 설정된 버전 이후의 GPT 모델이 확인됐습니다.\n"
        "모델은 자동 교체되지 않습니다. 관리자 화면에서 직접 선택해 주세요.\n\n"
        + "\n".join(f"- {model_id}" for model_id in model_ids)
        + f"\n\n현재 FOMC: {current['fomc']}\n현재 기타 분석: {current['standard']}\n"
        + "관리자 화면: https://hoorash4.github.io/macrowatch/admin.html\n"
    )
    with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as smtp:
        smtp.ehlo()
        smtp.starttls(context=ssl.create_default_context())
        smtp.ehlo()
        smtp.login(account, password)
        smtp.send_message(message)


def run() -> None:
    scan = monitor_request("scan")
    model_ids = scan.get("model_ids")
    if not isinstance(model_ids, list) or any(not isinstance(item, str) for item in model_ids):
        raise RuntimeError("AI model monitor scan response is invalid")
    if not model_ids:
        print("No newly available GPT version; no email sent.")
        return

    try:
        send_model_email(model_ids, scan)
    except Exception:
        monitor_request("report", model_ids=model_ids, success=False)
        raise
    monitor_request("report", model_ids=model_ids, success=True)
    print(f"GPT model notification email sent for {len(model_ids)} model(s).")


if __name__ == "__main__":
    run()
