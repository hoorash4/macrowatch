"""Print public, credential-safe parser candidates for one legacy DART filing."""

from __future__ import annotations

from io import BytesIO
import json
import os
from zipfile import ZipFile

from earnings.legacy_dart_financials import _decode, _parse_document
from earnings.open_dart import OpenDartClient


def main() -> None:
    receipt = os.environ["LEGACY_DART_DIAGNOSTIC_RECEIPT"].strip()
    report_code = os.environ["LEGACY_DART_DIAGNOSTIC_REPORT_CODE"].strip()
    response = OpenDartClient.from_env().fetch_filing_archive(receipt)
    rows: list[dict[str, str | int]] = []
    with ZipFile(BytesIO(response.content)) as zipped:
        for name in zipped.namelist():
            if not name.lower().endswith((".xml", ".html", ".htm")):
                continue
            document = _decode(zipped.read(name))
            for position, statement in enumerate(_parse_document(document, report_code), start=1):
                rows.append({
                    "document": name,
                    "candidate": position,
                    "scope": statement.consolidation_scope,
                    "revenue": format(statement.revenue, "f"),
                    "operating_income": format(statement.operating_income, "f"),
                    "net_income": format(statement.net_income, "f"),
                })
    print(json.dumps({"receipt": receipt, "candidates": rows}, ensure_ascii=False))


if __name__ == "__main__":
    main()
