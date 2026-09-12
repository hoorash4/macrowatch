"""Temporary one-off Redbook historical backfill. Delete after verified run."""
from __future__ import annotations

import json
import re

import requests

URL = "https://www.investing.com/economic-calendar/Service/getCalendarFilteredData"


def main() -> None:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "*/*",
        "Referer": "https://www.investing.com/economic-calendar/",
        "Origin": "https://www.investing.com",
    }
    payload = {
        "country[]": "5",
        "dateFrom": "2019-01-01",
        "dateTo": "2019-03-31",
        "timeZone": "8",
        "timeFilter": "timeRemain",
        "currentTab": "custom",
        "submitFilters": "1",
        "limit_from": "0",
    }
    with requests.Session() as session:
        session.headers.update(headers)
        pre = session.get("https://www.investing.com/economic-calendar/", timeout=45)
        response = session.post(URL, data=payload, timeout=45)
    out = {
        "pre_status": pre.status_code,
        "status": response.status_code,
        "content_type": response.headers.get("content-type"),
        "length": len(response.content),
        "head": response.text[:1000],
    }
    try:
        data = response.json()
        body = str(data.get("data") or "") if isinstance(data, dict) else ""
        out["json_keys"] = list(data) if isinstance(data, dict) else []
        out["data_length"] = len(body)
        out["contains_redbook"] = "Redbook" in body
        out["redbook_snippets"] = [re.sub(r"\s+", " ", body[max(0, m.start()-300):m.start()+700]) for m in list(re.finditer("Redbook", body, re.I))[:5]]
    except Exception as error:
        out["json_error"] = f"{error.__class__.__name__}: {error}"
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
