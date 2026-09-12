"""Temporary one-off Redbook historical backfill. Delete after verified run."""
from __future__ import annotations

import html
import json
import re
from datetime import datetime

import requests

URL = "https://fxverify.com/tools/economic-calendar/redbook-%28anual%29-6489d5b59a2596ec5c56dcb4"


def main() -> None:
    response = requests.get(URL, headers={"User-Agent": "Mozilla/5.0 MacroWatch/1.0"}, timeout=45)
    response.raise_for_status()
    text = html.unescape(response.text)
    probes = ["Jan 05, 2016", "Jan 04, 2017", "Jan 02, 2019", "Dec 29, 2015"]
    out = {"status": response.status_code, "length": len(text), "probes": {p: text.find(p) for p in probes}}
    snippets = {}
    for p in probes:
        idx = text.find(p)
        if idx >= 0:
            snippets[p] = re.sub(r"\s+", " ", text[max(0, idx-250):idx+500])
    out["snippets"] = snippets
    # Broad diagnostic: date strings near percent values.
    patt = re.compile(r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},\s+20\d{2}", re.I)
    out["date_count"] = len(patt.findall(text))
    out["first_dates"] = patt.findall(text)[:5]
    out["last_dates"] = patt.findall(text)[-5:]
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
