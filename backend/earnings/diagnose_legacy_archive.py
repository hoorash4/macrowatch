"""Print public, credential-safe parser candidates for one legacy DART filing."""

from __future__ import annotations

from io import BytesIO
import json
import os
import re
from zipfile import ZipFile

from earnings.legacy_dart_financials import (
    _STATEMENT_TITLE,
    _TableParser,
    _UNIT,
    _balanced_tables,
    _choose_cumulative_amount,
    _choose_standalone_amount,
    _decode,
    _metric_for_label,
    _parse_document,
)
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
                table_titles = [
                    match.group(0) for match in _STATEMENT_TITLE.finditer(table_text)
                ]
                prefix_titles = [
                    match.group(0) for match in _STATEMENT_TITLE.finditer(prefix_text)
                ]
                parser = _TableParser()
                parser.feed(table)
                recognized: dict[str, dict[str, str | None]] = {}
                for row_index, row in enumerate(parser.rows):
                    for label_column, cell in enumerate(row[:4]):
                        metric = _metric_for_label(cell)
                        if not metric or metric in recognized:
                            continue
                        cumulative = _choose_cumulative_amount(
                            parser.rows, row_index, label_column,
                            report_code=report_code,
                        )
                        standalone = _choose_standalone_amount(
                            parser.rows, row_index, label_column,
                            report_code=report_code,
                        )
                        recognized[metric] = {
                            "label": cell,
                            "cumulative": (
                                format(cumulative, "f") if cumulative is not None else None
                            ),
                            "standalone": (
                                format(standalone, "f") if standalone is not None else None
                            ),
                        }
                        break
                contexts.append({
                    "document": name,
                    "table": position,
                    "prefix_tail": prefix_text[-1200:],
                    "table_head": table_text[:6000],
                    "prefix_titles": prefix_titles[-5:],
                    "table_titles": table_titles,
                    "units": _UNIT.findall(prefix_text + table_text[:1000])[-3:],
                    "recognized_metrics": recognized,
                })
            for position, statement in enumerate(_parse_document(document, report_code), start=1):
                rows.append({
                    "document": name,
                    "candidate": position,
                    "scope": statement.consolidation_scope,
                    "revenue": format(statement.revenue, "f"),
                    "operating_income": format(statement.operating_income, "f"),
                    "net_income": format(statement.net_income, "f"),
                    "standalone_revenue": (
                        format(statement.standalone_revenue, "f")
                        if statement.standalone_revenue is not None else None
                    ),
                    "standalone_operating_income": (
                        format(statement.standalone_operating_income, "f")
                        if statement.standalone_operating_income is not None else None
                    ),
                    "standalone_net_income": (
                        format(statement.standalone_net_income, "f")
                        if statement.standalone_net_income is not None else None
                    ),
                })
    print(json.dumps({
        "receipt": receipt,
        "candidates": rows,
        "contexts": contexts,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
