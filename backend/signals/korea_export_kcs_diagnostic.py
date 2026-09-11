"""Temporary read-only diagnostic for the KCS press-release board."""
from __future__ import annotations

import re
import requests

URL = "https://www.customs.go.kr/kcs/na/ntt/selectNttList.do"


def main() -> None:
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 MacroWatch/1.0"})
    response = session.get(URL, params={"bbsId": "1362", "mi": "2891"}, timeout=30)
    print("status", response.status_code, "url", response.url, "chars", len(response.text))
    response.raise_for_status()
    text = response.text
    for pattern in (r'<form[^>]*>', r'<input[^>]*>', r'<a[^>]+selectNttInfo[^>]*>.*?</a>', r'pageIndex[^<]{0,120}', r'currPage[^<]{0,120}'):
        matches = re.findall(pattern, text, flags=re.I | re.S)
        print("PATTERN", pattern, "COUNT", len(matches))
        for match in matches[:30]:
            print(re.sub(r"\s+", " ", match)[:500])


if __name__ == "__main__":
    main()
