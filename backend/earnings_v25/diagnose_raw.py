"""Inspect historical DART filing archives without writing to the database."""

from __future__ import annotations

import argparse
import json
import re
from datetime import timedelta
from io import BytesIO
from typing import Any
from zipfile import BadZipFile, ZipFile

from .legacy_dart_financials import (
    _STATEMENT_TITLE,
    _TableParser,
    _UNIT,
    _amount,
    _balanced_tables,
    _decode,
    _matches_fiscal_year,
    _normalize,
    _scope_for_table,
)

from .pipeline import filing_period, quarter_end, quarter_resolution_end
from .providers import OpenDartClient, REPORT_CODES
from .raw_dart_financials import RawDartParseError, _metric_for_label, parse_raw_filing_archive
from .repository import EarningsV2Repository


SUPPORTED_YEARS = range(2016, 2019)
RELEVANT_TOKENS = (
    "매출", "수익", "영업이익", "영업손익", "영업손실",
    "당기순", "분기순", "반기순", "순이익", "순손익", "순손실",
)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Dump raw DART income-statement labels for pending V2.5 companies",
    )
    result.add_argument("--year", type=int, required=True)
    result.add_argument("--quarter", type=int, choices=(1, 2, 3, 4), required=True)
    return result


def _diagnostic_row(row: list[str]) -> dict[str, Any] | None:
    for label_column, cell in enumerate(row[:4]):
        normalized = _normalize(cell)
        if not any(token in normalized for token in RELEVANT_TOKENS):
            continue
        amounts = [
            {"raw": value, "parsed": str(parsed)}
            for value in row[label_column + 1:]
            if (parsed := _amount(value)) is not None
        ]
        return {
            "label": cell,
            "normalized_label": normalized,
            "recognized_metric": _metric_for_label(cell),
            "amounts": amounts,
        }
    return None


def inspect_raw_archive(
    archive: bytes,
    *,
    report_code: str,
    fiscal_year: int,
) -> dict[str, Any]:
    """Return relevant raw rows and the current parser's final decision."""
    try:
        with ZipFile(BytesIO(archive)) as zipped:
            documents = [
                (name, _decode(zipped.read(name)))
                for name in zipped.namelist()
                if name.lower().endswith((".xml", ".html", ".htm"))
            ]
    except (BadZipFile, OSError, RuntimeError):
        return {"archive_error": "DART filing archive is not a readable ZIP"}

    tables: list[dict[str, Any]] = []
    for document_name, document in documents:
        for table_index, (start, table) in enumerate(_balanced_tables(document), start=1):
            local_prefix = document[max(0, start - 20000):start]
            if not _matches_fiscal_year(local_prefix, table, fiscal_year):
                continue
            table_parser = _TableParser()
            table_parser.feed(table)
            rows = [
                diagnostic
                for row in table_parser.rows
                if (diagnostic := _diagnostic_row(row)) is not None
            ]
            if not rows:
                continue
            units = _UNIT.findall(local_prefix + table[:1000])
            title_text = re.sub(r"<[^>]+>", " ", local_prefix + table)
            title_matches = list(_STATEMENT_TITLE.finditer(title_text))
            tables.append({
                "document": document_name,
                "table_index": table_index,
                "scope": _scope_for_table(local_prefix, table),
                "unit": units[-1] if units else None,
                "statement_title_confirmed": _STATEMENT_TITLE.search(title_text) is not None,
                "statement_title_distance": (
                    len(title_text) - title_matches[-1].end() if title_matches else None
                ),
                "eligible_for_current_parser": (
                    bool(units)
                    and any(row["recognized_metric"] == "operating_income" for row in rows)
                ),
                "rows": rows,
            })

    try:
        parsed = parse_raw_filing_archive(
            archive,
            report_code=report_code,
            fiscal_year=fiscal_year,
        )
        parser_result: dict[str, Any] = {
            scope: {
                "cumulative": statement.cumulative,
                "standalone": statement.standalone,
                "statement_title_confirmed": statement.statement_title_confirmed,
            }
            for scope, statement in parsed.items()
        }
        parser_error = None
    except RawDartParseError as error:
        parser_result = {}
        parser_error = str(error)

    return {
        "document_count": len(documents),
        "relevant_table_count": len(tables),
        "tables": tables,
        "parser_result": parser_result,
        "parser_error": parser_error,
    }


def _corp_code(row: dict[str, Any]) -> str | None:
    company_id = str(row.get("company_id") or "")
    match = re.fullmatch(r"kr:(\d{8})", company_id)
    return match.group(1) if match else None


def main() -> None:
    args = parser().parse_args()
    if args.year not in SUPPORTED_YEARS:
        raise SystemExit("V2.5 diagnostics only allow fiscal years 2016 through 2018")

    repository = EarningsV2Repository.from_env()
    dart = OpenDartClient.from_env()
    identities = {
        row["company_id"]: row
        for market in ("kr_largecap", "kr_kosdaq")
        for row in repository.universe(market, args.year, args.quarter)
    }
    pending = [
        {**row, "company_name": identities[row["company_id"]].get("company_name")}
        for row in repository.company_periods(identities, [(args.year, args.quarter)])
        if row.get("is_pending")
    ]
    completed = 0
    errors = 0

    for row in pending:
        company = str(row.get("company_name") or row.get("company_id") or "unknown")
        corp_code = _corp_code(row)
        if corp_code is None:
            errors += 1
            print(json.dumps({
                "stage": "raw_diagnostic_company",
                "company": company,
                "error": "corp_code_not_found",
            }, ensure_ascii=False), flush=True)
            continue
        try:
            filings = [
                filing for filing in dart.periodic_filings(
                    quarter_end(args.year, args.quarter),
                    quarter_resolution_end(args.year, args.quarter) + timedelta(days=14),
                    corp_code=corp_code,
                )
                if filing_period(filing) == (args.year, args.quarter)
            ]
            if not filings:
                raise RuntimeError("periodic_filing_not_found")
            filing = max(filings, key=lambda item: (item.received_on, item.receipt_no))
            diagnostic = inspect_raw_archive(
                dart.filing_archive(filing.receipt_no),
                report_code=REPORT_CODES[args.quarter],
                fiscal_year=args.year,
            )
            completed += 1
            print(json.dumps({
                "stage": "raw_diagnostic_company",
                "company": company,
                "corp_code": corp_code,
                "receipt_no": filing.receipt_no,
                "filing_date": filing.received_on,
                **diagnostic,
            }, ensure_ascii=False, default=str), flush=True)
        except Exception as error:
            errors += 1
            print(json.dumps({
                "stage": "raw_diagnostic_company",
                "company": company,
                "corp_code": corp_code,
                "error": f"{type(error).__name__}: {error}",
            }, ensure_ascii=False), flush=True)

    print(json.dumps({
        "stage": "raw_diagnostic_summary",
        "period": f"{args.year}Q{args.quarter}",
        "pending_company_count": len(pending),
        "completed_company_count": completed,
        "error_company_count": errors,
        "open_dart_request_count": dart.request_count,
    }, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
