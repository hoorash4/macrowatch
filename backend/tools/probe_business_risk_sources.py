from __future__ import annotations

import json
import os
import re
from urllib.parse import urljoin

import requests


def get(url: str):
    r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0 MacroWatch/1.0"})
    print(json.dumps({"url": url, "status": r.status_code, "len": len(r.content), "type": r.headers.get("content-type")}, ensure_ascii=False))
    return r


key = os.environ["ECOS_API_KEY"]
for stat in ("141Y005", "801Y002"):
    url = f"https://ecos.bok.or.kr/api/StatisticItemList/{key}/json/kr/1/10000/{stat}"
    r = get(url)
    try:
        rows = ((r.json().get("StatisticItemList") or {}).get("row") or [])
    except Exception:
        rows = []
    print(json.dumps({"stat": stat, "items": rows[:80]}, ensure_ascii=False))

portal = get("https://portal.scourt.go.kr/pgp/index.on?m=PGP441M01&l=N&c=900")
text = portal.text
print(json.dumps({
    "court_head": text[:2500],
    "court_forms": re.findall(r'<form[^>]*action=[\"\']([^\"\']+)', text, flags=re.I)[:20],
    "court_urls": sorted(set(re.findall(r'[\"\']([^\"\']*(?:PGP|stat|Stat|ajax|Ajax)[^\"\']*)[\"\']', text)))[:80],
}, ensure_ascii=False))

news = get("https://www.epiqglobal.com/en-us/resource-center/news")
links = sorted(set(urljoin(news.url, x) for x in re.findall(r'href=[\"\']([^\"\']+)[\"\']', news.text, flags=re.I) if '/resource-center/news/' in x))
print(json.dumps({"epiq_links": links[:50]}, ensure_ascii=False))

for url in [
    "https://assets.equifax.com/marketing/US/assets/equifax-small-business-indices-october-2021.pdf",
    "https://assets.equifax.com/marketing/US/assets/Equifax.MonthlyStrategicInsights.June2022.pdf",
    "https://assets.equifax.com/marketing/US/assets/Equifax.MainStreetLendingReport.June2024.pdf",
    "https://assets.equifax.com/marketing/US/assets/main-street-lending-report-feb-2026.pdf",
]:
    get(url)
