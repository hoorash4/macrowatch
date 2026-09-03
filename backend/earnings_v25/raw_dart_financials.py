"""Read V2.5 financial facts from an official DART filing archive.

The 2016-2018 structured financial endpoints can return status 013 even when
the accepted filing contains an income statement.  This module reuses the
well-tested legacy table mechanics without changing the legacy or V2 paths.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from io import BytesIO
import re
from zipfile import BadZipFile, ZipFile

from .legacy_dart_financials import (
    _STATEMENT_TITLE,
    _TableParser,
    _UNIT,
    _balanced_tables,
    _choose_cumulative_amount,
    _choose_standalone_amount,
    _decode,
    _matches_fiscal_year,
    _normalize,
    _scope_for_table,
)


ALIASES = {
    "top_line": {"매출액", "매출", "수익", "영업수익"},
    "operating_income": {"영업이익", "영업이익손실", "영업손익", "영업손실"},
    "net_income": {
        "순이익", "순이익손실", "당기순이익", "당기순이익손실",
        "당기순손익", "당기순손실", "당분기순이익", "당분기순이익손실",
        "분당기순이익", "분당기순이익손실", "당기연결순이익",
        "분기순이익", "분기순이익손실",
        "반기순이익", "반기순이익손실", "연결분기순이익",
        "연결반기순이익", "연결당기순이익",
    },
}


class RawDartParseError(ValueError):
    """The archive does not contain one deterministic income statement."""


@dataclass(frozen=True)
class RawDartStatement:
    consolidation_scope: str
    cumulative: dict[str, Decimal | None]
    standalone: dict[str, Decimal | None]
    statement_title_confirmed: bool


def _metric_for_label(value: str) -> str | None:
    # DART cells can append presentation-only note markers and reserve-adjusted
    # profit explanations to an otherwise exact account name. Remove only
    # those decorations; do not fuzzy-match or combine financial concepts.
    canonical = re.sub(r"[（(](당|분)[）)]", r"\1", value)
    canonical = re.split(
        r"&cr;|<\s*주석|[（(]\s*(?:주석|대손준비금|비상위험준비금)",
        canonical,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    label = _normalize(canonical)
    account_label = _normalize(re.split(r"[（(]", canonical, maxsplit=1)[0])
    for metric, aliases in ALIASES.items():
        if label in aliases or account_label in aliases:
            return metric
    return None


def _parse_document(document: str, report_code: str, fiscal_year: int) -> list[RawDartStatement]:
    statements: list[RawDartStatement] = []
    for start, table in _balanced_tables(document):
        plain = _normalize(re.sub(r"<[^>]+>", " ", table))
        if not any(alias in plain for alias in ALIASES["operating_income"]):
            continue
        if "점유율" in plain and "시장" in plain and "당사" in plain:
            continue
        local_prefix = document[max(0, start - 20000):start]
        if not _matches_fiscal_year(local_prefix, table, fiscal_year):
            continue
        units = _UNIT.findall(local_prefix + table[:1000])
        if not units:
            continue
        multiplier = {
            "백만원": Decimal("1000000"),
            "천원": Decimal("1000"),
            "원": Decimal("1"),
        }[units[-1]]
        parser = _TableParser()
        parser.feed(table)
        cumulative: dict[str, Decimal | None] = {metric: None for metric in ALIASES}
        standalone: dict[str, Decimal | None] = {metric: None for metric in ALIASES}
        for row_index, row in enumerate(parser.rows):
            for label_column, cell in enumerate(row[:4]):
                metric = _metric_for_label(cell)
                if metric is None or cumulative[metric] is not None:
                    continue
                amount = _choose_cumulative_amount(
                    parser.rows, row_index, label_column, report_code=report_code,
                )
                current = _choose_standalone_amount(
                    parser.rows, row_index, label_column, report_code=report_code,
                )
                cumulative[metric] = amount * multiplier if amount is not None else None
                standalone[metric] = current * multiplier if current is not None else None
                break
        if sum(value is not None for value in cumulative.values()) >= 2:
            title_text = re.sub(r"<[^>]+>", " ", local_prefix + table)
            statements.append(RawDartStatement(
                consolidation_scope=_scope_for_table(local_prefix, table),
                cumulative=cumulative,
                standalone=standalone,
                statement_title_confirmed=_STATEMENT_TITLE.search(title_text) is not None,
            ))
    return statements


def parse_raw_filing_archive(
    archive: bytes,
    *,
    report_code: str,
    fiscal_year: int,
) -> dict[str, RawDartStatement]:
    """Return at most one best statement per scope, rejecting ambiguity."""
    try:
        with ZipFile(BytesIO(archive)) as zipped:
            documents = [
                _decode(zipped.read(name))
                for name in zipped.namelist()
                if name.lower().endswith((".xml", ".html", ".htm"))
            ]
    except (BadZipFile, OSError, RuntimeError):
        raise RawDartParseError("DART filing archive is not a readable ZIP") from None

    grouped: dict[str, list[RawDartStatement]] = {"CFS": [], "OFS": []}
    for document in documents:
        for statement in _parse_document(document, report_code, fiscal_year):
            grouped[statement.consolidation_scope].append(statement)

    result: dict[str, RawDartStatement] = {}
    for scope, candidates in grouped.items():
        titled = [item for item in candidates if item.statement_title_confirmed]
        if titled:
            candidates = titled
        if not candidates:
            continue
        completeness = max(
            sum(value is not None for value in item.cumulative.values())
            for item in candidates
        )
        candidates = [
            item for item in candidates
            if sum(value is not None for value in item.cumulative.values()) == completeness
        ]
        unique = {
            tuple(item.cumulative[metric] for metric in ALIASES): item
            for item in candidates
        }
        if len(unique) == 1:
            result[scope] = next(iter(unique.values()))
    if not result:
        raise RawDartParseError("No unique income statement was found in the filing archive")
    return result

