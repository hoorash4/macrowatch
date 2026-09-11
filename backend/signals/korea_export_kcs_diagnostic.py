"""Temporary read-only diagnostic for the KCS press-release board."""
from __future__ import annotations

import html
import re
import requests

URL = "https://www.customs.go.kr/kcs/na/ntt/selectNttList.do"


def clean(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", value))).strip()


def summary(text: str) -> tuple[str, list[str]]:
    page_info = "?"
    m = re.search(r'전체\s*<span>\s*([0-9,]+)</span>건\s*<span>([^<]+)</span>', text, flags=re.S)
    if m:
        page_info = f"total={m.group(1)} page={m.group(2).strip()}"
    titles = []
    for anchor in re.findall(r'<a\b[^>]*class="nttInfoBtn"[^>]*>.*?</a>', text, flags=re.I | re.S):
        label = clean(anchor)
        if "수출입" in label:
            titles.append(label)
    return page_info, titles


def main() -> None:
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 MacroWatch/1.0"})
    base = {"bbsId": "1362", "mi": "2891", "currPage": "1", "listCo": "50"}
    for search_type in ("", "title", "sj", "nttSj"):
        data = {**base, "searchValue": "수출입 현황"}
        if search_type:
            data["searchType"] = search_type
        response = session.post(URL, data=data, timeout=30)
        response.raise_for_status()
        info, titles = summary(response.text)
        print("SEARCH_TYPE", repr(search_type), info, "matching_titles", len(titles))
        for title in titles[:12]:
            print("TITLE", title)
    response = session.post(URL, data=base, timeout=30)
    response.raise_for_status()
    for select in re.findall(r'<select\b[^>]*>.*?</select>', response.text, flags=re.I | re.S):
        if "searchType" in select:
            print("SEARCH_SELECT", re.sub(r"\s+", " ", select))


if __name__ == "__main__":
    main()
