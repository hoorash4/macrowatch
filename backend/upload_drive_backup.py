from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import requests
from google.auth.transport.requests import Request
from google.oauth2.service_account import Credentials


DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"


def require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def download_file(access_token: str, file_id: str) -> bytes:
    response = requests.get(
        f"https://www.googleapis.com/drive/v3/files/{file_id}",
        params={"alt": "media"},
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=120,
    )
    if not response.ok:
        raise RuntimeError(
            f"Google Drive download failed: {response.status_code} {response.text[:1000]}"
        )
    return response.content


def replace_file(access_token: str, file_id: str, content: bytes) -> dict[str, str]:
    response = requests.patch(
        f"https://www.googleapis.com/upload/drive/v3/files/{file_id}",
        params={"uploadType": "media", "fields": "id,name,modifiedTime,size"},
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/octet-stream",
        },
        data=content,
        timeout=120,
    )
    if not response.ok:
        raise RuntimeError(
            f"Google Drive upload failed: {response.status_code} {response.text[:1000]}"
        )
    return response.json()


def main() -> int:
    if len(sys.argv) != 2:
        raise RuntimeError("Usage: upload_drive_backup.py <backup-file>")

    backup_path = Path(sys.argv[1])
    if not backup_path.is_file() or backup_path.stat().st_size == 0:
        raise RuntimeError(f"Backup file is missing or empty: {backup_path}")

    service_account = json.loads(require_env("GOOGLE_SERVICE_ACCOUNT_JSON"))
    credentials = Credentials.from_service_account_info(
        service_account,
        scopes=[DRIVE_SCOPE],
    )
    credentials.refresh(Request())

    latest_file_id = require_env("GOOGLE_DRIVE_LATEST_FILE_ID")
    previous_file_id = require_env("GOOGLE_DRIVE_PREVIOUS_FILE_ID")

    previous_content = download_file(credentials.token, latest_file_id)
    previous_result = replace_file(credentials.token, previous_file_id, previous_content)
    result = replace_file(credentials.token, latest_file_id, backup_path.read_bytes())

    print(
        "Google Drive previous backup updated: "
        f"{previous_result.get('name', previous_file_id)} "
        f"({previous_result.get('size', '?')} bytes)"
    )
    print(
        "Google Drive latest backup updated: "
        f"{result.get('name', latest_file_id)} ({result.get('size', '?')} bytes, "
        f"{result.get('modifiedTime', 'unknown time')})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
