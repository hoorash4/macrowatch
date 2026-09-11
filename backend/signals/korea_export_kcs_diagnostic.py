"""Temporary read-only diagnostic for the KCS press-release board."""
from __future__ import annotations

import html
import re
import requests

URL = "https://www.customs.go.kr/kcs/na/ntt/selectNttList.do"


def clean(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", value))).strip()


def inspect_page(session: requests.Session, page: int) -> None:
    response = session.post(URL, data={"bbsId": "1362", "mi": "2891", "currPage": str(page)}, timeout=30)
    print("PAGE", page, "status", response.status_code, "url", response.url, "chars", len(response.text))
    response.raise_for_status()
    text = response.text
    anchors = re.findall(r'<a\b[^>]*>.*?</a>', text, flags=re.I | re.S)
    found = 0
    for anchor in anchors:
        label = clean(anchor)
        if "수출입" not in label:
            continue
        found += 1
        print("ANCHOR", re.sub(r"\s+", " ", anchor)[:1000])
    print("EXPORT_ANCHORS", found)
    for needle in ("2026년 9월", "수출입 현황", "fnView", "nttSn"):
        pos = text.find(needle)
        if pos >= 0:
            print("AROUND", needle, re.sub(r"\s+", " ", text[max(0, pos-600):pos+1200])[:1800])


def main() -> None:
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 MacroWatch/1.0"})
    inspect_page(session, 1)
    inspect_page(session, 2)


if __name__ == "__main__":
    main()
