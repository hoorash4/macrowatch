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

REPORT_QUARTERS = {"11013": 1, "11012": 2, "11014": 3, "11011": 4}


_TABLE_TAG = re.compile(r"</?table\b[^>]*>", re.IGNORECASE)
_UNIT = re.compile(r"단위\s*[:：]\s*(백만원|천원|원)", re.IGNORECASE)
# Legacy DART uses both ``(123)`` and ``(-)123`` for losses.  The latter is
# not accepted by Decimal directly and previously made the parser skip the
# current-period loss, leaving only the prior-period positive comparison.
_NUMBER = re.compile(r"^(?:\(-\)|-?|\(?)[\d,]+(?:\.\d+)?\)?$")
# A statement heading must end at the title.  Narrative phrases such as
# ``연결포괄손익계산서상`` occur in notes and must not relabel the next table.
_STATEMENT_TITLE = re.compile(
    r"(연\s*결\s*)?(?:포\s*괄\s*)?손\s*익\s*계\s*산\s*서(?![가-힣])"
)

_ALIASES = {
    "operating_income": {
        "영업이익", "영업이익손실", "영업손익",
    },
    "net_income": {
        "순이익", "순이익손실", "당기순이익", "당기순이익손실",
        "분기순이익", "분기순이익손실",
        "분기순이익분기포괄이익", "반기순이익", "반기순이익손실",
        "연결분기순이익", "연결반기순이익", "연결당기순이익",
    },
}


class LegacyDartParseError(ValueError):
    """An old filing cannot support a deterministic canonical quarter."""


@dataclass(frozen=True)
class LegacyCumulativeStatement:
    consolidation_scope: str
    operating_income: Decimal
    net_income: Decimal
    # Interim statements can publish both the current three-month period and
    # year-to-date columns.  Keep the former so Q2/Q3 remains recoverable when
    # the preceding cumulative filing is absent; these are official values,
    # not values inferred from another period.
    standalone_operating_income: Decimal | None = None
    standalone_net_income: Decimal | None = None
    # Old filings often repeat rounded figures in a summary table before the
    # actual income statement.  A nearby published statement heading marks the
    # authoritative table; the flag is internal and keeps legacy test/build
    # call sites backward compatible.
    statement_title_confirmed: bool = False


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
    prefix_negative = text.startswith("(-)")
    parenthesized_negative = text.startswith("(") and text.endswith(")")
    negative = prefix_negative or parenthesized_negative or text.startswith("-")
    if prefix_negative:
        text = text[3:]
    elif parenthesized_negative:
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
                fragment = document[start:match.end()]
                # Old DART XML frequently wraps several real statement tables
                # in a layout table. Parsing that wrapper mixes consolidated,
                # separate and comparison-period rows into one false candidate.
                # Only leaf tables represent one deterministic statement table.
                opening_tables = sum(
                    not tag.group(0).lstrip().startswith("</")
                    for tag in _TABLE_TAG.finditer(fragment)
                )
                if opening_tables == 1:
                    yield start, fragment
        else:
            starts.append(match.start())


def _metric_for_label(value: str) -> str | None:
    label = _normalize(value)
    # Older statements append a full basic/diluted EPS explanation to the net
    # income label in the same cell.  The parenthetical text is disclosure,
    # not part of the account name.  Retain the exact-match guard on both the
    # full label and its pre-parenthesis account name to avoid fuzzy matching
    # narrative rows.
    account_label = _normalize(re.split(r"[（(]", value, maxsplit=1)[0])
    for metric, aliases in _ALIASES.items():
        if label in aliases or account_label in aliases:
            return metric
    return None


def _column_context(rows: list[list[str]], row_index: int, column: int) -> str:
    return _normalize(" ".join(
        row[column] for row in rows[max(0, row_index - 8):row_index]
        if column < len(row)
    ))


def _row_amount_candidates(
    rows: list[list[str]], row_index: int, label_column: int,
) -> list[tuple[int, Decimal, str]]:
    """Return statement amounts after removing note-reference columns."""
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

    # A note reference occasionally survives a malformed header. It is a
    # small integer immediately before materially larger statement amounts.
    if len(candidates) >= 2:
        first = candidates[0][1]
        later = max(
            (abs(value) for _column, value, _context in candidates[1:]),
            default=Decimal(0),
        )
        if first == first.to_integral_value() and abs(first) <= 100 and later >= abs(first) * 100:
            candidates = candidates[1:]
    return candidates


def _has_standalone_and_ytd(rows: list[list[str]], row_index: int) -> bool:
    header = _normalize(" ".join(
        cell for header_row in rows[:row_index] for cell in header_row
    ))
    return (
        ("3개월" in header or "당분기" in header)
        and ("누적" in header or "6개월" in header or "9개월" in header)
    )


def _choose_cumulative_amount(
    rows: list[list[str]],
    row_index: int,
    label_column: int,
    *,
    report_code: str,
) -> Decimal | None:
    row = rows[row_index]
    candidates = _row_amount_candidates(rows, row_index, label_column)
    has_standalone_and_ytd = _has_standalone_and_ytd(rows, row_index)

    if not candidates:
        return None

    # Q1's standalone and cumulative periods are identical. Legacy filings
    # variously duplicate that value or print it once, so the first resolved
    # current-period amount is authoritative; a later ``누적`` context can be
    # the prior-year Q1 column because of shifted colspans.
    if report_code == "11013":
        return candidates[0][1]

    # The current period pair is commonly rendered as ``3개월 / 누적`` (or
    # ``당분기 / 누적``). Header colspans can shift the word ``누적`` one cell
    # left, so a per-column context check incorrectly selected the standalone
    # quarter as YTD for Samsung and other 2015 filings. Recognize the table's
    # paired-period layout before consulting those shifted column contexts.
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

    # A four-value row alone does not prove a standalone/YTD paired layout.
    # Older statements also publish current YTD, prior-year YTD and two annual
    # comparatives. In that structure the first value is the current period;
    # selecting the second silently copies the prior year into the new year.
    return candidates[0][1]


def _choose_standalone_amount(
    rows: list[list[str]],
    row_index: int,
    label_column: int,
    *,
    report_code: str,
) -> Decimal | None:
    """Read an explicitly published current-quarter amount when available."""
    if report_code not in {"11012", "11014"}:
        return None
    if not _has_standalone_and_ytd(rows, row_index):
        return None
    candidates = _row_amount_candidates(rows, row_index, label_column)
    # Two current-period and two comparison-period amounts identify the
    # published ``3개월 / 누적`` layout without consulting another filing.
    if len(candidates) < 4:
        return None
    return candidates[0][1]


def _scope_from_prefix(prefix: str) -> str:
    matches = list(_STATEMENT_TITLE.finditer(re.sub(r"<[^>]+>", " ", prefix)))
    if matches:
        return "CFS" if matches[-1].group(1) else "OFS"
    return "OFS"


def _scope_for_table(prefix: str, table: str) -> str:
    """Prefer a statement title embedded in the table over prior context."""
    table_matches = list(_STATEMENT_TITLE.finditer(re.sub(r"<[^>]+>", " ", table)))
    if table_matches:
        return "CFS" if table_matches[-1].group(1) else "OFS"
    return _scope_from_prefix(prefix)


def _has_statement_title(prefix: str, table: str) -> bool:
    plain = re.sub(r"<[^>]+>", " ", prefix + table)
    return _STATEMENT_TITLE.search(plain) is not None


def _matches_fiscal_year(prefix: str, table: str, fiscal_year: int | None) -> bool:
    """Reject statement attachments that belong to a different fiscal year.

    A legacy DART archive can contain the submitted report plus older audit or
    comparison documents.  Their tables are structurally valid, so metric
    aliases alone cannot distinguish them.  The period heading immediately
    before a statement table identifies the year without relying on filenames
    or guessing from the amounts.
    """
    if fiscal_year is None:
        return True
    nearby_prefix = re.sub(r"<[^>]+>", " ", prefix)
    table_text = re.sub(r"<[^>]+>", " ", table)
    nearby_text = unescape(nearby_prefix[-3000:] + " " + table_text[:1500])
    return re.search(rf"(?<!\d){fiscal_year}(?!\d)", nearby_text) is not None


def _parse_document(
    document: str,
    report_code: str,
    fiscal_year: int | None = None,
) -> list[LegacyCumulativeStatement]:
    statements: list[LegacyCumulativeStatement] = []
    for start, table in _balanced_tables(document):
        plain = _normalize(re.sub(r"<[^>]+>", " ", table))
        if not any(alias in plain for alias in _ALIASES["operating_income"]):
            continue
        # Business-section market-share tables can contain market-wide profit
        # figures alongside the company's figures. They are not financial
        # statements and must never become a canonical quarter.
        if "점유율" in plain and "시장" in plain and "당사" in plain:
            continue
        # Twenty thousand source characters cover verbose DART markup between
        # a heading and its value table while avoiding unrelated earlier
        # sections of the filing.
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
        values: dict[str, Decimal] = {}
        standalone_values: dict[str, Decimal] = {}
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
                standalone_amount = _choose_standalone_amount(
                    parser.rows, row_index, label_column, report_code=report_code,
                )
                if standalone_amount is not None:
                    standalone_values[metric] = standalone_amount * multiplier
                break
        if all(metric in values for metric in _ALIASES):
            statements.append(LegacyCumulativeStatement(
                consolidation_scope=_scope_for_table(local_prefix, table),
                operating_income=values["operating_income"],
                net_income=values["net_income"],
                standalone_operating_income=standalone_values.get("operating_income"),
                standalone_net_income=standalone_values.get("net_income"),
                statement_title_confirmed=_has_statement_title(local_prefix, table),
            ))
    return statements


def parse_legacy_filing_archive(
    archive: bytes,
    *,
    report_code: str,
    fiscal_year: int | None = None,
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
        for statement in _parse_document(document, report_code, fiscal_year):
            by_scope[statement.consolidation_scope].append(statement)

    result: dict[str, LegacyCumulativeStatement] = {}
    for scope, candidates in by_scope.items():
        # Prefer the actual published income statement over rounded summary
        # financial information.  Untitled tables remain a fallback for older
        # filings that genuinely publish no recognizable statement heading.
        titled = [candidate for candidate in candidates if candidate.statement_title_confirmed]
        if titled:
            candidates = titled
        # An entirely zero profit pair is not enough to identify a unique
        # income statement; keep real reported profits and losses unchanged.
        candidates = [
            item for item in candidates
            if any(value != 0 for value in (item.operating_income, item.net_income))
        ]
        unique = {
            (item.operating_income, item.net_income): item
            for item in candidates
        }
        if len(unique) == 1:
            result[scope] = next(iter(unique.values()))
    if not result:
        raise LegacyDartParseError(
            "No unique complete income statement was found in the legacy filing."
        )
    return result
