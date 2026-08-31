"""Print public, credential-safe parser candidates for one legacy DART filing."""

from __future__ import annotations

from io import BytesIO
import json
import os
import re
from zipfile import ZipFile

from earnings.legacy_dart_financials import _balanced_tables, _decode, _parse_document
from earnings.open_dart import OpenDartClient


def main() -> None:
    receipt = os.environ["LEGACY_DART_DIAGNOSTIC_RECEIPT"].strip()
    report_code = os.environ["LEGACY_DART_DIAGNOSTIC_REPORT_CODE"].strip()
    response = OpenDartClient.from_env().fetch_filing_archive(receipt)
    rows: list[dict[str, str | int]] = []
    contexts: list[dict[str, str | int]] = []
    with ZipFile(BytesIO(response.content)) as zipped:
        for name in zipped.namelist():
            if not name.lower().endswith((".xml", ".html", ".htm")):
                continue
            document = _decode(zipped.read(name))
            for position, (start, table) in enumerate(_balanced_tables(document), start=1):
                table_text = " ".join(re.sub(r"<[^>]+>", " ", table).split())
                if "영업이익" not in table_text and "영업손익" not in table_text:
                    continue
                prefix_text = " ".join(
                    re.sub(r"<[^>]+>", " ", document[max(0, start - 20000):start]).split()
                )
                contexts.append({
                    "document": name,
                    "table": position,
                    "prefix_tail": prefix_text[-1200:],
                    "table_head": table_text[:1200],
                })
            for position, statement in enumerate(_parse_document(document, report_code), start=1):
                rows.append({
                    "document": name,
                    "candidate": position,
                    "scope": statement.consolidation_scope,
                    "revenue": format(statement.revenue, "f"),
                    "operating_income": format(statement.operating_income, "f"),
                    "net_income": format(statement.net_income, "f"),
                })
    print(json.dumps({
        "receipt": receipt,
        "candidates": rows,
        "contexts": contexts,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
