"""Temporary one-off Redbook historical backfill. Delete after verified run."""
from __future__ import annotations

import json
from urllib.parse import quote

import requests


def probe(url: str) -> dict:
    try:
        response = requests.get(url, headers={"User-Agent": "MacroWatch/1.0"}, timeout=45)
        item = {
            "url": url,
            "status": response.status_code,
            "content_type": response.headers.get("content-type"),
            "length": len(response.content),
            "text_head": response.text[:500],
        }
        try:
            payload = response.json()
            item["json_type"] = type(payload).__name__
            item["json_len"] = len(payload) if isinstance(payload, (list, dict)) else None
            item["json_head"] = payload[:2] if isinstance(payload, list) else payload
        except Exception:
            pass
        return item
    except Exception as error:
        return {"url": url, "error": f"{error.__class__.__name__}: {error}"}


def main() -> None:
    country = quote("united states")
    indicator = quote("redbook index")
    urls = [
        f"https://api.tradingeconomics.com/historical/country/{country}/indicator/{indicator}/2015-01-01/2026-09-12",
        f"https://api.tradingeconomics.com/historical/country/{country}/indicator/{indicator}",
        f"https://api.tradingeconomics.com/calendar/country/{country}/2015-01-01/2026-09-12",
    ]
    print(json.dumps([probe(url) for url in urls], ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
