"""Extract cumulative income-statement values from pre-XBRL DART filings.

OpenDART's structured account APIs do not cover MacroWatch's full 2002+
history.  Older periodic filings are still available through the official
``document.xml`` archive.  This module reads only the published statement
tables and deliberately refuses to guess when the unit, period column, or
three required metrics cannot be identified.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from html import unescape
from html.parser import HTMLParser
from io import BytesIO
import re
from zipfile import BadZipFile, ZipFile

from earnings.open_dart_parser import REPORT_QUARTERS


_TABLE_TAG = re.compile(r"</?table\b[^>]*>", re.IGNORECASE)
_UNIT = re.compile(r"단위\s*[:：]\s*(백만원|천원|원)", re.IGNORECASE)
_NUMBER = re.compile(r"^\(?-?[\d,]+(?:\.\d+)?\)?$")
_STATEMENT_TITLE = re.compile(r"(연결\s*)?(?:포괄\s*)?손익계산서")

_ALIASES = {
    "revenue": {
        "매출액", "수익매출액", "영업수익", "수익", "총영업수익",
    },
    "operating_income": {
        "영업이익", "영업이익손실", "영업손익",
    },
    "net_income": {
        "당기순이익", "당기순이익손실", "분기순이익", "분기순이익손실",
        "반기순이익", "반기순이익손실",
    },
}


class LegacyDartParseError(ValueError):
    """An old filing cannot support a deterministic canonical quarter."""


@dataclass(frozen=True)
class LegacyCumulativeStatement:
    consolidation_scope: str
    revenue: Decimal
    operating_income: Decimal
    net_income: Decimal


def _decode(content: bytes) -> str:
    for encoding in ("utf-8", "euc-kr", "cp949"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def _normalize(value: str) -> str:
    text = unescape(value).replace("\xa0", " ")
    text = re.sub(r"\(주\s*\d+[^)]*\)", "", text)
    text = re.sub(r"^[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩIVXLC\d.()\s]+", "", text)
    return re.sub(r"[^0-9A-Za-z가-힣]", "", text).lower()


def _amount(value: str) -> Decimal | None:
    text = "".join(unescape(value).split()).replace(",", "")
    if text in {"", "-", "—", "–"} or not _NUMBER.fullmatch(text):
        return None
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    try:
        parsed = Decimal(text)
    except InvalidOperation:
        return None
    return -parsed if negative else parsed


class _TableParser(HTMLParser):
    """Keep a rectangular-enough table while expanding simple colspans."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self.all_text: list[str] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._colspan = 1

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        if lowered == "tr":
            self._row = []
        elif lowered in {"td", "th"} and self._row is not None:
            self._cell = []
            raw = dict(attrs).get("colspan") or "1"
            self._colspan = int(raw) if str(raw).isdigit() else 1
        elif lowered == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_data(self, data: str) -> None:
        self.all_text.append(data)
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in {"td", "th"} and self._cell is not None and self._row is not None:
            value = " ".join("".join(self._cell).split())
            self._row.extend([value] * max(1, self._colspan))
            self._cell = None
        elif lowered == "tr" and self._row is not None:
            if any(cell.strip() for cell in self._row):
                self.rows.append(self._row)
            self._row = None


def _balanced_tables(document: str):
    starts: list[int] = []
    for match in _TABLE_TAG.finditer(document):
        if match.group(0).lstrip().startswith("</"):
            if starts:
                start = starts.pop()
                yield start, document[start:match.end()]
        else:
            starts.append(match.start())


def _metric_for_label(value: str) -> str | None:
    label = _normalize(value)
    for metric, aliases in _ALIASES.items():
        if label in aliases:
            return metric
    return None


def _column_context(rows: list[list[str]], row_index: int, column: int) -> str:
    return _normalize(" ".join(
        row[column] for row in rows[max(0, row_index - 8):row_index]
        if column < len(row)
    ))


def _choose_cumulative_amount(
    rows: list[list[str]],
    row_index: int,
    label_column: int,
    *,
    report_code: str,
) -> Decimal | None:
    row = rows[row_index]
    candidates: list[tuple[int, Decimal, str]] = []
    for column in range(label_column + 1, len(row)):
        value = _amount(row[column])
        if value is None:
            continue
        context = _column_context(rows, row_index, column)
        if "주석" in context:
            continue
        candidates.append((column, value, context))
    if not candidates:
        return None

    # A note reference occasionally survives a malformed header.  It is a
    # small integer immediately before materially larger statement amounts.
    if len(candidates) >= 2:
        first = candidates[0][1]
        later = max((abs(value) for _column, value, _context in candidates[1:]), default=Decimal(0))
        if first == first.to_integral_value() and abs(first) <= 100 and later >= abs(first) * 100:
            candidates = candidates[1:]

    # The current period pair is commonly rendered as ``3개월 / 누적`` (or
    # ``당분기 / 누적``). Header colspans can shift the word ``누적`` one cell
    # left, so a per-column context check incorrectly selected the standalone
    # quarter as YTD for Samsung and other 2015 filings. Recognize the table's
    # paired-period layout before consulting those shifted column contexts.
    header = _normalize(" ".join(
        cell for header_row in rows[:row_index] for cell in header_row
    ))
    has_standalone_and_ytd = (
        ("3개월" in header or "당분기" in header)
        and ("누적" in header or "6개월" in header or "9개월" in header)
    )
    if (
        report_code in {"11012", "11014"}
        and len(candidates) >= 4
        and has_standalone_and_ytd
    ):
        return candidates[1][1]

    # Only use per-column header context after ruling out a paired interim
    # layout. Old DART colspans can shift ``누적`` onto the standalone value.
    cumulative = [candidate for candidate in candidates if "누적" in candidate[2]]
    if cumulative:
        return cumulative[0][1]

    # Some tables omit explicit period words but retain the same four-column
    # current-quarter/current-YTD/prior-quarter/prior-YTD layout.
    if report_code in {"11012", "11014"} and len(candidates) >= 4:
        return candidates[1][1]
    return candidates[0][1]


def _scope_from_prefix(prefix: str) -> str:
    matches = list(_STATEMENT_TITLE.finditer(re.sub(r"<[^>]+>", " ", prefix)))
    if matches:
        return "CFS" if matches[-1].group(1) else "OFS"
    return "OFS"


def _parse_document(document: str, report_code: str) -> list[LegacyCumulativeStatement]:
    statements: list[LegacyCumulativeStatement] = []
    for start, table in _balanced_tables(document):
        plain = _normalize(re.sub(r"<[^>]+>", " ", table))
        if not any(alias in plain for alias in _ALIASES["operating_income"]):
            continue
        prefix = document[max(0, start - 5000):start]
        units = _UNIT.findall(prefix + table[:1000])
        if not units:
            continue
        multiplier = {
            "백만원": Decimal("1000000"),
            "천원": Decimal("1000"),
            "원": Decimal("1"),
        }[units[-1]]
        parser = _TableParser()
        parser.feed(table)
        values: dict[str, Decimal] = {}
        for row_index, row in enumerate(parser.rows):
            for label_column, cell in enumerate(row[:4]):
                metric = _metric_for_label(cell)
                if not metric or metric in values:
                    continue
                amount = _choose_cumulative_amount(
                    parser.rows, row_index, label_column, report_code=report_code,
                )
                if amount is not None:
                    values[metric] = amount * multiplier
                break
        if all(metric in values for metric in _ALIASES):
            statements.append(LegacyCumulativeStatement(
                consolidation_scope=_scope_from_prefix(prefix),
                revenue=values["revenue"],
                operating_income=values["operating_income"],
                net_income=values["net_income"],
            ))
    return statements


def parse_legacy_filing_archive(
    archive: bytes,
    *,
    report_code: str,
) -> dict[str, LegacyCumulativeStatement]:
    """Return one deterministic cumulative statement per available scope."""
    if report_code not in REPORT_QUARTERS:
        raise ValueError(f"Unsupported DART report code: {report_code!r}")
    try:
        with ZipFile(BytesIO(archive)) as zipped:
            documents = [
                _decode(zipped.read(name))
                for name in zipped.namelist()
                if name.lower().endswith((".xml", ".html", ".htm"))
            ]
    except (BadZipFile, OSError, RuntimeError):
        raise LegacyDartParseError("DART filing archive is not a readable ZIP.") from None

    by_scope: dict[str, list[LegacyCumulativeStatement]] = {"CFS": [], "OFS": []}
    for document in documents:
        for statement in _parse_document(document, report_code):
            by_scope[statement.consolidation_scope].append(statement)

    result: dict[str, LegacyCumulativeStatement] = {}
    for scope, candidates in by_scope.items():
        # A published income statement for a listed operating company cannot
        # support a complete quarter when its parsed revenue is zero/negative.
        # Old HTML tables often expose repeated or comparison columns; reject
        # those candidates instead of silently turning them into zero quarters.
        candidates = [item for item in candidates if item.revenue > 0]
        unique = {
            (item.revenue, item.operating_income, item.net_income): item
            for item in candidates
        }
        if len(unique) == 1:
            result[scope] = next(iter(unique.values()))
    if not result:
        raise LegacyDartParseError(
            "No unique complete income statement was found in the legacy filing."
        )
    return result
