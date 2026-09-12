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
for stat, params in (
    ("141Y005", ("R4AB00", "X00", "0960")),
    ("801Y002", ("2100000",)),
):
    meta = get(f"https://ecos.bok.or.kr/api/StatisticItemList/{key}/json/kr/1/10000/{stat}")
    rows = ((meta.json().get("StatisticItemList") or {}).get("row") or [])
    print(json.dumps({"stat": stat, "selected": [x for x in rows if x.get("CYCLE") == "M" and x.get("ITEM_CODE") in params]}, ensure_ascii=False))
    url = f"https://ecos.bok.or.kr/api/StatisticSearch/{key}/json/kr/1/10000/{stat}/M/201912/202608/" + "/".join(params)
    r = get(url)
    try:
        search_rows = ((r.json().get("StatisticSearch") or {}).get("row") or [])
    except Exception:
        search_rows = []
    print(json.dumps({"stat": stat, "search_n": len(search_rows), "head": search_rows[:3], "tail": search_rows[-3:]}, ensure_ascii=False))

base = "https://portal.scourt.go.kr"
for path in (
    "/pgp/index.on?m=PGP441M01&l=N&c=900",
    "/pgp/ui/pageUserComm/main.xml",
    "/pgp/ui/pageUserComm/PGP441M01.xml",
    "/pgp/ui/pageUserComm/stat/PGP441M01.xml",
    "/pgp/ui/pageUserComm/statistics/PGP441M01.xml",
):
    r = get(base + path)
    text = r.text
    hits = sorted(set(re.findall(r'[\"\']([^\"\']*(?:PGP441|submission|service|\.on|\.xml)[^\"\']*)[\"\']', text, flags=re.I)))
    print(json.dumps({"court_path": path, "head": text[:1000], "hits": hits[:120]}, ensure_ascii=False))

boot = get(base + "/pgp/websquare/javascript.wq?q=/bootloader")
for token in ("PGP441", "PGP44", "submission", "service"):
    if token.lower() in boot.text.lower():
        print(json.dumps({"boot_token": token, "positions": [m.start() for m in re.finditer(token, boot.text, flags=re.I)][:20]}, ensure_ascii=False))

news = get("https://www.epiqglobal.com/en-us/resource-center/news")
for page in (1, 2, 5, 10, 20):
    u = news.url if page == 1 else news.url + f"?page={page}"
    r = get(u)
    links = sorted(set(urljoin(r.url, x) for x in re.findall(r'href=[\"\']([^\"\']+)[\"\']', r.text, flags=re.I) if '/resource-center/news/' in x))
    print(json.dumps({"epiq_page": page, "links": links[:40], "n": len(links)}, ensure_ascii=False))

try:
    from pypdf import PdfReader
    from io import BytesIO
    for url in [
        "https://assets.equifax.com/marketing/US/assets/equifax-small-business-indices-october-2021.pdf",
        "https://assets.equifax.com/marketing/US/assets/Equifax.MonthlyStrategicInsights.June2022.pdf",
        "https://assets.equifax.com/marketing/US/assets/main-street-lending-report-feb-2026.pdf",
    ]:
        r = get(url)
        text = "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(r.content)).pages)
        snippets = []
        for pat in ("31-90", "31–90", "91-180", "91–180", "SBDFI", "Defaults"):
            m = re.search(re.escape(pat), text, flags=re.I)
            if m:
                snippets.append(text[max(0, m.start()-300):m.start()+900])
        print(json.dumps({"pdf": url, "text_len": len(text), "snippets": snippets[:8]}, ensure_ascii=False))
except Exception as e:
    print(json.dumps({"pdf_error": repr(e)}, ensure_ascii=False))
