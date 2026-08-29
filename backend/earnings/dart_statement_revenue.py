"""Derive a financial company's gross operating revenue from a DART statement.

OpenDART's compact account APIs do not expose a single revenue fact for many
financial companies.  Their published income statement still presents gross
revenue and gross expense branches whose difference is operating income.  This
module reads that presentation deterministically, removes parent/child
duplicates, and accepts a derived value only after the statement reconciles.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from html import escape, unescape
from html.parser import HTMLParser
from io import BytesIO
import re
from typing import Any, Iterable
from urllib.parse import unquote
import xml.etree.ElementTree as ET
from zipfile import BadZipFile, ZipFile


_NODE_TEXT = re.compile(r"node\d+\['text'\]\s*=\s*\"([^\"]+)\"")
_NODE_FIELD = {
    field: re.compile(rf"node\d+\['{field}'\]\s*=\s*\"([^\"]+)\"")
    for field in ("dcmNo", "eleId", "offset", "length", "dtd")
}
_NUMBER = re.compile(r"^\(?-?[\d,]+(?:\.\d+)?\)?$")
_LEADING_NUMBER = re.compile(r"^[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫIVXLC\d.()\s]+")
_TABLE_TAG = re.compile(r"</?table\b[^>]*>", re.IGNORECASE)
_ARCHIVE_UNIT = re.compile(
    r"단위\s*[:：]\s*(백만원|천원|원|USD|달러)", re.IGNORECASE
)
_STATEMENT_HEADING = re.compile(r"(?:연결\s*)?(?:포괄\s*)?손익계산서")
MAX_TREE_DIAGNOSTIC_ROWS = 80
MAX_STATEMENT_HEADING_CANDIDATES = 24


class DartRevenueDerivationError(ValueError):
    """The public statement cannot safely support a gross-revenue value."""


@dataclass(frozen=True)
class StatementDocument:
    title: str
    document_number: str
    element_id: str
    offset: str
    length: str
    dtd: str


@dataclass(frozen=True)
class StatementRow:
    depth: int
    label: str
    normalized_label: str
    values: tuple[Decimal | None, ...]


@dataclass(frozen=True)
class GrossRevenueAmounts:
    current_revenue: Decimal | None
    cumulative_revenue: Decimal | None
    current_expense: Decimal | None
    cumulative_expense: Decimal | None
    current_operating_income: Decimal | None
    cumulative_operating_income: Decimal | None


def _decode_document(content: bytes) -> str:
    for encoding in ("utf-8", "euc-kr"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            pass
    return content.decode("utf-8", errors="replace")


def filing_html(content: bytes) -> str:
    """Decode DART HTML while keeping the operation easy to fake in tests."""
    return _decode_document(content)


def find_income_statement_document(
    filing_page: str,
    *,
    consolidation_scope: str,
) -> StatementDocument:
    """Locate the consolidated or separate income-statement viewer node."""
    lines = filing_page.splitlines()
    nodes: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in lines:
        match = _NODE_TEXT.search(line)
        if match:
            if current:
                nodes.append(current)
            current = {"title": unescape(match.group(1))}
        for field, pattern in _NODE_FIELD.items():
            field_match = pattern.search(line)
            if field_match:
                current[field] = field_match.group(1)
    if current:
        nodes.append(current)

    scope = consolidation_scope.strip().upper()
    if scope not in {"CFS", "OFS"}:
        raise DartRevenueDerivationError("Financial revenue requires a CFS or OFS statement.")

    candidates: list[dict[str, str]] = []
    for node in nodes:
        title = node.get("title", "")
        if "손익계산서" not in title and "포괄손익계산서" not in title:
            continue
        is_consolidated = "연결" in title
        if (scope == "CFS") != is_consolidated:
            continue
        if all(node.get(field) for field in ("dcmNo", "eleId", "offset", "length")):
            candidates.append(node)
    if not candidates:
        raise DartRevenueDerivationError(f"DART filing lacks a {scope} income statement node.")

    # A leaf such as "2-2. 연결 포괄손익계산서" is more specific than the
    # surrounding "2. 연결재무제표" section and therefore has the longer title.
    chosen = max(candidates, key=lambda node: (len(node["title"]), int(node["eleId"])))
    return StatementDocument(
        title=chosen["title"],
        document_number=chosen["dcmNo"],
        element_id=chosen["eleId"],
        offset=chosen["offset"],
        length=chosen["length"],
        dtd=chosen.get("dtd") or "dart4.xsd",
    )


class _StatementTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self.row_depths: list[int | None] = []
        self.all_text: list[str] = []
        self._in_row = False
        self._in_cell = False
        self._row: list[str] = []
        self._cell: list[str] = []
        self._row_depth: int | None = None

    def _capture_depth(self, attrs: list[tuple[str, str | None]]) -> None:
        if not self._in_cell or self._row:
            return
        values = {str(key).lower(): str(value or "") for key, value in attrs}
        for key in ("aindent", "indent", "level", "depth"):
            match = re.search(r"\d+", values.get(key, ""))
            if match:
                self._row_depth = int(match.group(0))
                return
        style = values.get("style", "")
        match = re.search(r"(?:padding|margin)-left\s*:\s*(\d+)", style, re.IGNORECASE)
        if match:
            self._row_depth = int(match.group(1)) // 20

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        if lowered == "tr":
            self._in_row = True
            self._row = []
            self._row_depth = None
        elif lowered in {"td", "th"} and self._in_row:
            self._in_cell = True
            self._cell = []
            self._capture_depth(attrs)
        elif lowered in {"p", "span"}:
            self._capture_depth(attrs)
        elif lowered == "br" and self._in_cell:
            self._cell.append(" ")

    def handle_data(self, data: str) -> None:
        self.all_text.append(data)
        if self._in_cell:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in {"td", "th"} and self._in_cell:
            self._row.append(unescape("".join(self._cell)))
            self._in_cell = False
        elif lowered == "tr" and self._in_row:
            if self._row:
                self.rows.append(self._row)
                self.row_depths.append(self._row_depth)
            self._in_row = False


def _parse_amount(value: str) -> Decimal | None:
    text = " ".join(value.split()).replace(",", "")
    if text in {"", "-", "—", "–"}:
        return None
    if not _NUMBER.match(text):
        return None
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    try:
        amount = Decimal(text)
    except InvalidOperation:
        return None
    return -amount if negative else amount


def _normalized_label(label: str) -> str:
    text = re.sub(r"\(주[^)]*\)", "", label)
    text = _LEADING_NUMBER.sub("", text)
    return re.sub(r"\s+", "", text)


def _unit_multiplier(text: str) -> Decimal:
    normalized = re.sub(r"\s+", "", text)
    match = re.search(r"단위[:：](백만원|천원|원|USD|달러)", normalized, re.IGNORECASE)
    if not match:
        raise DartRevenueDerivationError("DART statement unit is not identifiable.")
    unit = match.group(1).upper()
    return {
        "백만원": Decimal("1000000"),
        "천원": Decimal("1000"),
        "원": Decimal("1"),
        "USD": Decimal("1"),
        "달러": Decimal("1"),
    }[unit]


def _balanced_tables(document: str) -> Iterable[tuple[int, int, str]]:
    """Yield complete tables even when DART XML nests layout tables."""
    starts: list[int] = []
    for match in _TABLE_TAG.finditer(document):
        if match.group(0).lstrip().startswith("</"):
            if not starts:
                continue
            start = starts.pop()
            yield start, match.end(), document[start:match.end()]
        else:
            starts.append(match.start())


def parse_statement_rows(statement_page: str) -> tuple[list[StatementRow], Decimal]:
    parser = _StatementTableParser()
    parser.feed(statement_page)
    multiplier = _unit_multiplier(" ".join(parser.all_text))
    result: list[StatementRow] = []
    for row_index, cells in enumerate(parser.rows):
        if len(cells) < 2:
            continue
        raw_label = cells[0].replace("\xa0", " ")
        label = raw_label.strip()
        if not label:
            continue
        prefix = raw_label[:len(raw_label) - len(raw_label.lstrip())]
        # DART uses one ideographic space for each visual tree level.  Some
        # older filings use pairs of non-breaking/ASCII spaces instead.
        ideographic = prefix.count("\u3000")
        remaining = len(prefix.replace("\u3000", ""))
        explicit_depth = parser.row_depths[row_index]
        depth = explicit_depth if explicit_depth is not None else ideographic + remaining // 2
        values = tuple(
            amount * multiplier if amount is not None else None
            for amount in (_parse_amount(cell) for cell in cells[1:])
        )
        result.append(StatementRow(depth, label, _normalized_label(label), values))
    if not result:
        raise DartRevenueDerivationError("DART income statement contains no numeric rows.")
    return result, multiplier


def _is_operating_income(label: str) -> bool:
    return label in {"영업이익", "영업이익(손실)", "영업손익"}


def _matching_column(values: tuple[Decimal | None, ...], target: Decimal | None) -> int | None:
    if target is None:
        return None
    matches = [index for index, value in enumerate(values) if value == target]
    return matches[0] if matches else None


def _classification(label: str, *, has_child: bool, value: Decimal) -> str | None:
    """Classify one selected tree node as gross revenue, expense, net or subtotal."""
    if _is_operating_income(label) or any(token in label for token in (
        "영업이익전", "영업이익(손실)전", "영업이익반영전", "반영전영업이익",
        "총영업이익", "순영업이익", "순영업손익",
    )):
        return "subtotal"

    # Mixed credit-loss captions need ordering rather than a simple word
    # search: 전입(환입) is an expense row, while 환입(전입) is revenue-side.
    if "전입" in label and "환입" in label:
        if label.index("전입") < label.index("환입"):
            return "expense"
        return "revenue" if value >= 0 else "expense"
    if "환입" in label and "전입" not in label:
        return "revenue"

    is_net = (
        label.startswith("순")
        or label.endswith("손익")
        or label.endswith("결과")
        or "수익(비용)" in label
    )
    revenue_word = (
        "매출액" in label
        or "수익" in label
        or label.endswith("이익")
        or "관련이익" in label
        or "배당" in label
    )
    expense_word = any(token in label for token in (
        "비용", "손실", "매출원가", "영업원가", "전입", "관리비", "인건비",
        "손상차손", "상각비", "대손",
    ))
    if has_child and is_net:
        return "net_parent"
    if revenue_word and not expense_word:
        return "revenue"
    if expense_word and not revenue_word:
        return "expense"
    if is_net and not has_child:
        return "revenue" if value >= 0 else "expense"
    return None


def _operating_section(rows: list[StatementRow]) -> list[StatementRow]:
    result: list[StatementRow] = []
    for row in rows:
        label = row.normalized_label
        if result and (
            label.startswith("영업외")
            or "법인세비용차감전" in label
            or label.startswith("관계기업이익")
        ):
            break
        result.append(row)
    return result


def _gross_sides(
    rows: list[StatementRow],
    *,
    column: int,
    diagnostics: list[tuple[str, str, int, bool]] | None = None,
) -> tuple[Decimal, Decimal]:
    revenue = Decimal(0)
    expense = Decimal(0)
    skip_descendants_of: int | None = None
    section = _operating_section(rows)
    for index, row in enumerate(section):
        if skip_descendants_of is not None:
            if row.depth > skip_descendants_of:
                continue
            skip_descendants_of = None
        value = row.values[column] if column < len(row.values) else None
        if value is None:
            continue
        has_child = index + 1 < len(section) and section[index + 1].depth > row.depth
        kind = _classification(row.normalized_label, has_child=has_child, value=value)
        if kind is None and diagnostics is not None and len(diagnostics) < 30:
            diagnostics.append((
                row.normalized_label, str(value), row.depth, has_child,
            ))
        if kind in {"subtotal", "net_parent", None}:
            continue
        if kind == "revenue":
            revenue += abs(value)
        elif kind == "expense":
            expense += abs(value)
        if has_child:
            skip_descendants_of = row.depth

    # A net parent can disclose only part of its gross children. Preserve the
    # disclosed gross branches, then allocate the exact undisclosed remainder
    # by the parent's sign so the expanded tree still equals its published net.
    for index, row in enumerate(section):
        value = row.values[column] if column < len(row.values) else None
        has_child = (
            index + 1 < len(section)
            and section[index + 1].depth > row.depth
        )
        if value is None or not has_child:
            continue
        kind = _classification(
            row.normalized_label, has_child=True, value=value
        )
        if kind != "net_parent":
            continue
        end = index + 1
        while end < len(section) and section[end].depth > row.depth:
            end += 1
        child_revenue, child_expense = _gross_sides(
            section[index + 1:end], column=column
        )
        undisclosed = value - (child_revenue - child_expense)
        if undisclosed > 0:
            revenue += undisclosed
        elif undisclosed < 0:
            expense += abs(undisclosed)
    return revenue, expense


def _validate_reconciliation(
    revenue: Decimal,
    expense: Decimal,
    operating_income: Decimal,
    *,
    unit_multiplier: Decimal,
) -> None:
    residual = revenue - expense - operating_income
    tolerance = max(unit_multiplier, abs(operating_income) * Decimal("0.000000001"))
    if abs(residual) > tolerance:
        raise DartRevenueDerivationError(
            "DART gross revenue and expense do not reconcile to operating income "
            f"(revenue={revenue}, expense={expense}, "
            f"operating_income={operating_income}, residual={residual})."
        )


def derive_gross_revenue(
    statement_page: str,
    *,
    operating_current: Decimal | None,
    operating_cumulative: Decimal | None,
) -> GrossRevenueAmounts:
    """Return gross revenue only when both available periods reconcile."""
    rows, multiplier = parse_statement_rows(statement_page)
    operating_rows = [row for row in rows if _is_operating_income(row.normalized_label)]
    if not operating_rows:
        raise DartRevenueDerivationError("DART statement lacks an operating-income row.")

    current_column = cumulative_column = None
    current_row = cumulative_row = None
    for row in operating_rows:
        if current_column is None:
            match = _matching_column(row.values, operating_current)
            if match is not None:
                current_column, current_row = match, row
        if cumulative_column is None:
            match = _matching_column(row.values, operating_cumulative)
            if match is not None:
                cumulative_column, cumulative_row = match, row
    if operating_current is not None and current_column is None:
        raise DartRevenueDerivationError("DART statement current operating income does not match API data.")
    if operating_cumulative is not None and cumulative_column is None:
        raise DartRevenueDerivationError("DART statement cumulative operating income does not match API data.")

    current_revenue = current_expense = None
    if current_column is not None and current_row is not None:
        current_diagnostics: list[tuple[str, str, int, bool]] = []
        current_revenue, current_expense = _gross_sides(
            rows, column=current_column, diagnostics=current_diagnostics,
        )
        current_op = current_row.values[current_column]
        assert current_op is not None
        try:
            _validate_reconciliation(
                current_revenue, current_expense, current_op,
                unit_multiplier=multiplier,
            )
        except DartRevenueDerivationError as error:
            raise DartRevenueDerivationError(
                f"{error} unclassified={current_diagnostics}"
            ) from None
    else:
        current_op = None

    cumulative_revenue = cumulative_expense = None
    if cumulative_column is not None and cumulative_row is not None:
        cumulative_diagnostics: list[tuple[str, str, int, bool]] = []
        cumulative_revenue, cumulative_expense = _gross_sides(
            rows, column=cumulative_column, diagnostics=cumulative_diagnostics,
        )
        cumulative_op = cumulative_row.values[cumulative_column]
        assert cumulative_op is not None
        try:
            _validate_reconciliation(
                cumulative_revenue, cumulative_expense, cumulative_op,
                unit_multiplier=multiplier,
            )
        except DartRevenueDerivationError as error:
            raise DartRevenueDerivationError(
                f"{error} unclassified={cumulative_diagnostics}"
            ) from None
    else:
        cumulative_op = None

    return GrossRevenueAmounts(
        current_revenue=current_revenue,
        cumulative_revenue=cumulative_revenue,
        current_expense=current_expense,
        cumulative_expense=cumulative_expense,
        current_operating_income=current_op,
        cumulative_operating_income=cumulative_op,
    )



_ACCOUNT_ROW_EXCLUDED_ID_PARTS = (
    "comprehensiveincomeattributable",
    "shareofothercomprehensiveincome",
    "profitloss",
    "profitlossbeforetax",
    "incometaxexpense",
    "nonoperatingprofitloss",
    "shareofprofitloss",
    "basicearningsloss",
    "dilutedearningsloss",
)
_ACCOUNT_ROW_EXCLUDED_ID_PREFIXES = (
    "ifrs-full_comprehensiveincome",
    "ifrs-full_othercomprehensiveincome",
    "dart_othercomprehensiveincomenetoftax",
)


def _gross_sides_from_account_rows(
    raw_rows: Iterable[dict[str, Any]],
    *,
    amount_field: str,
    operating_income: Decimal,
) -> tuple[Decimal, Decimal]:
    """Sum gross CIS sides while suppressing net parents with components."""
    prepared: list[tuple[str, str, Decimal]] = []
    for raw in raw_rows:
        if str(raw.get("sj_div") or "").strip().upper() not in {"IS", "CIS"}:
            continue
        label = _normalized_label(str(raw.get("account_nm") or ""))
        account_id = str(raw.get("account_id") or "").strip().lower()
        amount = _parse_amount(str(raw.get(amount_field) or ""))
        if not label or amount is None or _is_operating_income(label):
            continue
        if account_id.startswith(_ACCOUNT_ROW_EXCLUDED_ID_PREFIXES):
            continue
        if any(token in account_id for token in _ACCOUNT_ROW_EXCLUDED_ID_PARTS):
            continue
        prepared.append((label, account_id, amount))

    explicit: list[tuple[str, Decimal, str]] = []
    net_rows: list[tuple[str, Decimal]] = []
    for label, _account_id, amount in prepared:
        if "손익" in label or "수익(비용)" in label:
            net_rows.append((label, amount))
            continue
        kind = _classification(label, has_child=False, value=amount)
        if kind in {"revenue", "expense"}:
            explicit.append((label, amount, kind))

    revenue = sum(
        (abs(amount) for _label, amount, kind in explicit if kind == "revenue"),
        Decimal(0),
    )
    expense = sum(
        (abs(amount) for _label, amount, kind in explicit if kind == "expense"),
        Decimal(0),
    )
    explicit_labels = [label for label, _amount, _kind in explicit]
    for label, amount in net_rows:
        stem = label.removeprefix("순").replace("손익", "").replace("관련", "")
        has_components = len(stem) >= 2 and any(
            stem in explicit_label for explicit_label in explicit_labels
        )
        if has_components:
            continue
        if amount >= 0:
            revenue += amount
        else:
            expense += abs(amount)

    _validate_reconciliation(
        revenue, expense, operating_income, unit_multiplier=Decimal(1)
    )
    return revenue, expense


def derive_gross_revenue_from_account_rows(
    raw_rows: Iterable[dict[str, Any]],
    *,
    operating_current: Decimal | None,
    operating_cumulative: Decimal | None,
) -> GrossRevenueAmounts:
    """Derive gross financial revenue from scoped OpenDART CIS account rows."""
    rows = list(raw_rows)
    current_revenue = current_expense = None
    if operating_current is not None:
        current_revenue, current_expense = _gross_sides_from_account_rows(
            rows,
            amount_field="thstrm_amount",
            operating_income=operating_current,
        )
    cumulative_revenue = cumulative_expense = None
    if operating_cumulative is not None:
        cumulative_revenue, cumulative_expense = _gross_sides_from_account_rows(
            rows,
            amount_field="thstrm_add_amount",
            operating_income=operating_cumulative,
        )
    return GrossRevenueAmounts(
        current_revenue=current_revenue,
        cumulative_revenue=cumulative_revenue,
        current_expense=current_expense,
        cumulative_expense=cumulative_expense,
        current_operating_income=operating_current,
        cumulative_operating_income=operating_cumulative,
    )



_XLINK = "{http://www.w3.org/1999/xlink}"


def _xbrl_concept_key(href: str) -> str:
    return unquote(str(href).rsplit("#", 1)[-1]).strip().lower()


def _derive_gross_revenue_candidate(
    statement_page: str,
    *,
    operating_current: Decimal | None,
    operating_cumulative: Decimal | None,
) -> GrossRevenueAmounts:
    """Return a fully classified presentation-tree candidate without expense veto."""
    rows, _multiplier = parse_statement_rows(statement_page)
    operating_rows = [
        row for row in rows if _is_operating_income(row.normalized_label)
    ]
    current_column = cumulative_column = None
    current_row = cumulative_row = None
    for row in operating_rows:
        if current_column is None:
            match = _matching_column(row.values, operating_current)
            if match is not None:
                current_column, current_row = match, row
        if cumulative_column is None:
            match = _matching_column(row.values, operating_cumulative)
            if match is not None:
                cumulative_column, cumulative_row = match, row
    if operating_current is not None and current_column is None:
        raise DartRevenueDerivationError(
            "Presentation candidate lacks current operating income."
        )
    if operating_cumulative is not None and cumulative_column is None:
        raise DartRevenueDerivationError(
            "Presentation candidate lacks cumulative operating income."
        )

    current_revenue = current_expense = current_op = None
    if current_column is not None and current_row is not None:
        diagnostics: list[tuple[str, str, int, bool]] = []
        current_revenue, current_expense = _gross_sides(
            rows, column=current_column, diagnostics=diagnostics,
        )
        if diagnostics:
            raise DartRevenueDerivationError(
                f"Presentation candidate has unclassified accounts: {diagnostics}"
            )
        current_op = current_row.values[current_column]

    cumulative_revenue = cumulative_expense = cumulative_op = None
    if cumulative_column is not None and cumulative_row is not None:
        diagnostics = []
        cumulative_revenue, cumulative_expense = _gross_sides(
            rows, column=cumulative_column, diagnostics=diagnostics,
        )
        if diagnostics:
            raise DartRevenueDerivationError(
                f"Presentation candidate has unclassified accounts: {diagnostics}"
            )
        cumulative_op = cumulative_row.values[cumulative_column]

    return GrossRevenueAmounts(
        current_revenue=current_revenue,
        cumulative_revenue=cumulative_revenue,
        current_expense=current_expense,
        cumulative_expense=cumulative_expense,
        current_operating_income=current_op,
        cumulative_operating_income=cumulative_op,
    )


def derive_gross_revenue_from_xbrl_presentation(
    archive: bytes,
    raw_rows: Iterable[dict[str, Any]],
    *,
    operating_current: Decimal | None,
    operating_cumulative: Decimal | None,
) -> GrossRevenueAmounts:
    """Use official XBRL facts, labels and presentation relationships."""
    rows = list(raw_rows)
    raw_by_concept = {
        str(row.get("account_id") or "").strip().lower(): row
        for row in rows
        if str(row.get("account_id") or "").strip()
    }
    operating_concepts = {
        account_id
        for account_id, row in raw_by_concept.items()
        if _is_operating_income(
            _normalized_label(str(row.get("account_nm") or ""))
        )
    }
    if not operating_concepts:
        raise DartRevenueDerivationError(
            "OpenDART XBRL rows lack an operating-income concept."
        )
    try:
        with ZipFile(BytesIO(archive)) as zipped:
            documents = [
                zipped.read(name)
                for name in zipped.namelist()
                if name.lower().endswith((".xml", ".xbrl"))
            ]
    except (BadZipFile, OSError, RuntimeError):
        raise DartRevenueDerivationError(
            "OpenDART financial XBRL archive is not a readable ZIP."
        ) from None

    parsed_roots: list[ET.Element] = []
    for content in documents:
        try:
            parsed_roots.append(ET.fromstring(content))
        except ET.ParseError:
            continue

    def concept_local(concept: str) -> str:
        for separator in ("_", ":"):
            if separator in concept:
                return concept.split(separator, 1)[1].lower()
        return concept.lower()

    # Label linkbases cover company extension concepts omitted from the JSON
    # all-accounts response. Prefer a Korean standard label when several exist.
    concept_labels: dict[str, tuple[int, str]] = {}
    for root in parsed_roots:
        for link in root.iter():
            if link.tag.rsplit("}", 1)[-1] != "labelLink":
                continue
            locators: dict[str, str] = {}
            resources: dict[str, tuple[int, str]] = {}
            arcs: list[tuple[str, str]] = []
            for element in list(link):
                local = element.tag.rsplit("}", 1)[-1]
                xlink_label = str(element.attrib.get(f"{_XLINK}label") or "")
                if local == "loc":
                    href = str(element.attrib.get(f"{_XLINK}href") or "")
                    if xlink_label and href:
                        locators[xlink_label] = _xbrl_concept_key(href)
                elif local == "label":
                    language = str(element.attrib.get(
                        "{http://www.w3.org/XML/1998/namespace}lang"
                    ) or "")
                    role = str(element.attrib.get(f"{_XLINK}role") or "")
                    value = " ".join("".join(element.itertext()).split())
                    score = (
                        0 if language.lower().startswith("ko") else 10
                    ) + (0 if role.endswith("/label") else 1)
                    if xlink_label and value:
                        resources[xlink_label] = (score, value)
                elif local == "labelArc":
                    source = str(element.attrib.get(f"{_XLINK}from") or "")
                    target = str(element.attrib.get(f"{_XLINK}to") or "")
                    if source and target:
                        arcs.append((source, target))
            for source, target in arcs:
                concept = locators.get(source)
                resource = resources.get(target)
                if concept and resource and (
                    concept not in concept_labels
                    or resource[0] < concept_labels[concept][0]
                ):
                    concept_labels[concept] = resource

    # Locate current and cumulative contexts by the reported operating-income
    # fact itself. This avoids guessing context dates or dimensional members.
    fact_values: dict[tuple[str, str], Decimal] = {}
    context_fact_counts: dict[str, int] = {}
    for root in parsed_roots:
        for element in root.iter():
            context = str(element.attrib.get("contextRef") or "")
            if not context or element.text is None:
                continue
            value = _parse_amount(element.text)
            if value is None:
                continue
            scale_text = str(element.attrib.get("scale") or "0")
            try:
                scale = int(scale_text)
            except ValueError:
                scale = 0
            if scale:
                value *= Decimal(10) ** scale
            local = element.tag.rsplit("}", 1)[-1].lower()
            fact_values[(local, context)] = value
            context_fact_counts[context] = context_fact_counts.get(context, 0) + 1

    operating_locals = {concept_local(value) for value in operating_concepts}

    def target_context(target: Decimal | None) -> str | None:
        if target is None:
            return None
        candidates = {
            context
            for (local, context), value in fact_values.items()
            if local in operating_locals and value == target
        }
        return max(
            candidates,
            key=lambda context: context_fact_counts.get(context, 0),
            default=None,
        )

    current_context = target_context(operating_current)
    cumulative_context = target_context(operating_cumulative)

    matches: dict[
        tuple[Decimal | None, Decimal | None, Decimal | None, Decimal | None],
        GrossRevenueAmounts,
    ] = {}
    classified_fallbacks: dict[
        tuple[Decimal | None, Decimal | None],
        GrossRevenueAmounts,
    ] = {}
    presentation_roles = 0
    role_failures: list[str] = []
    for root in parsed_roots:
        for link in root.iter():
            if link.tag.rsplit("}", 1)[-1] != "presentationLink":
                continue
            presentation_roles += 1
            role_uri = str(link.attrib.get(f"{_XLINK}role") or "")
            locators: dict[str, str] = {}
            arcs: list[tuple[str, str, Decimal]] = []
            for element in list(link):
                local = element.tag.rsplit("}", 1)[-1]
                if local == "loc":
                    label = str(element.attrib.get(f"{_XLINK}label") or "")
                    href = str(element.attrib.get(f"{_XLINK}href") or "")
                    if label and href:
                        locators[label] = _xbrl_concept_key(href)
                elif local == "presentationArc":
                    source = str(element.attrib.get(f"{_XLINK}from") or "")
                    target = str(element.attrib.get(f"{_XLINK}to") or "")
                    try:
                        order = Decimal(str(element.attrib.get("order") or "0"))
                    except InvalidOperation:
                        order = Decimal(0)
                    if source and target:
                        arcs.append((source, target, order))
            if not operating_concepts.intersection(locators.values()):
                continue
            children: dict[str, list[tuple[Decimal, str]]] = {}
            targets: set[str] = set()
            for source, target, order in arcs:
                children.setdefault(source, []).append((order, target))
                targets.add(target)
            roots = [label for label in locators if label not in targets]
            ordered: list[tuple[int, str]] = []
            visited: set[str] = set()

            def walk(locator: str, depth: int) -> None:
                if locator in visited:
                    return
                visited.add(locator)
                concept = locators.get(locator)
                if concept:
                    ordered.append((depth, concept))
                for _order, child in sorted(children.get(locator, [])):
                    walk(child, depth + 1)

            for root_locator in roots:
                walk(root_locator, 0)
            for locator in locators:
                walk(locator, 0)

            statement_rows: list[str] = []
            for depth, concept in ordered:
                raw = raw_by_concept.get(concept)
                label = (
                    str(raw.get("account_nm") or concept)
                    if raw else concept_labels.get(concept, (99, concept))[1]
                )
                local = concept_local(concept)
                current_value = (
                    fact_values.get((local, current_context))
                    if current_context else None
                )
                cumulative_value = (
                    fact_values.get((local, cumulative_context))
                    if cumulative_context else None
                )
                if raw:
                    if current_value is None:
                        current_value = _parse_amount(
                            str(raw.get("thstrm_amount") or "")
                        )
                    if cumulative_value is None:
                        cumulative_value = _parse_amount(
                            str(raw.get("thstrm_add_amount") or "")
                        )
                current = (
                    format(current_value, "f")
                    if current_value is not None else "-"
                )
                cumulative = (
                    format(cumulative_value, "f")
                    if cumulative_value is not None else "-"
                )
                statement_rows.append(
                    "<TR><TD>"
                    + ("  " * depth)
                    + escape(label)
                    + "</TD><TD>"
                    + current
                    + "</TD><TD>"
                    + cumulative
                    + "</TD></TR>"
                )
            if not statement_rows:
                continue
            statement = "(단위 : 원)<TABLE>" + "".join(statement_rows) + "</TABLE>"
            try:
                amounts = derive_gross_revenue(
                    statement,
                    operating_current=operating_current,
                    operating_cumulative=operating_cumulative,
                )
            except DartRevenueDerivationError as error:
                if role_uri.endswith("320005"):
                    try:
                        fallback = _derive_gross_revenue_candidate(
                            statement,
                            operating_current=operating_current,
                            operating_cumulative=operating_cumulative,
                        )
                        classified_fallbacks[(
                            fallback.current_revenue,
                            fallback.cumulative_revenue,
                        )] = fallback
                    except DartRevenueDerivationError:
                        pass
                reason = f"{role_uri}: {error}"
                if reason not in role_failures and len(role_failures) < 5:
                    role_failures.append(reason)
                continue
            key = (
                amounts.current_revenue,
                amounts.cumulative_revenue,
                amounts.current_expense,
                amounts.cumulative_expense,
            )
            matches[key] = amounts
    if not matches and len(classified_fallbacks) == 1:
        return next(iter(classified_fallbacks.values()))
    if not matches:
        raise DartRevenueDerivationError(
            "No official XBRL presentation role reconciles to operating income "
            f"(roles={presentation_roles}, reasons={role_failures})."
        )
    if len(matches) > 1:
        raise DartRevenueDerivationError(
            "Multiple XBRL presentation roles produce different gross revenue values."
        )
    return next(iter(matches.values()))


def derive_gross_revenue_from_archive(
    archive: bytes,
    *,
    operating_current: Decimal | None,
    operating_cumulative: Decimal | None,
    consolidation_scope: str | None = None,
) -> GrossRevenueAmounts:
    """Find uniquely reconciling periods in a DART ZIP.

    Interim original filings may present only the year-to-date column even
    though the account API also supplies a standalone three-month amount.
    Each available period is therefore reconciled independently; the worker
    subtracts the previously reconciled cumulative revenue when necessary.
    Account lines split across sibling layout tables are recombined from their
    nearest statement title before the same reconciliation is applied.
    """
    try:
        with ZipFile(BytesIO(archive)) as zipped:
            documents = [
                _decode_document(zipped.read(name))
                for name in zipped.namelist()
                if name.lower().endswith((".xml", ".html", ".htm"))
            ]
    except (BadZipFile, OSError, RuntimeError):
        raise DartRevenueDerivationError(
            "OpenDART filing archive is not a readable ZIP."
        ) from None

    current_matches: dict[Decimal, GrossRevenueAmounts] = {}
    cumulative_matches: dict[Decimal, GrossRevenueAmounts] = {}
    table_count = candidate_count = 0
    failures: list[str] = []
    operating_samples: list[str] = []
    # Bounded public-statement diagnostics make a new layout debuggable
    # without logging the complete filing archive.
    tree_sample: list[tuple[int, str, str | None]] = []
    for document in documents:
        lowered_document = document.lower()
        table_spans = sorted(
            _balanced_tables(document), key=lambda span: (span[0], span[1])
        )
        for table_index, (table_start, table_end, fragment) in enumerate(table_spans):
            table_count += 1
            normalized = _normalized_label(re.sub(r"<[^>]+>", " ", fragment))
            if not any(label in normalized for label in ("영업이익", "영업손익")):
                continue
            candidate_count += 1
            # The unit caption normally sits just before the table rather than
            # inside it. Copy only the nearest caption so an earlier table
            # cannot contaminate the candidate's row tree.
            prefix = document[max(0, table_start - 3000):table_start]
            prefix_text = _normalized_label(re.sub(r"<[^>]+>", " ", prefix))
            headings = re.findall(r"(연결)?(?:포괄)?손익계산서", prefix_text)
            if headings and consolidation_scope in {"CFS", "OFS"}:
                is_consolidated = bool(headings[-1])
                if (consolidation_scope == "CFS") != is_consolidated:
                    continue
            units = _ARCHIVE_UNIT.findall(prefix)
            candidate = f"(단위 : {units[-1]}){fragment}" if units else fragment
            if len(operating_samples) < 3:
                try:
                    sample_rows, _sample_unit = parse_statement_rows(candidate)
                    sample_values = [
                        [str(value) if value is not None else None for value in row.values[:6]]
                        for row in sample_rows
                        if _is_operating_income(row.normalized_label)
                    ][:2]
                    if sample_values:
                        operating_samples.append(str(sample_values))
                except DartRevenueDerivationError:
                    pass
            try:
                target_rows, _target_unit = parse_statement_rows(candidate)
                candidate_operating_values = {
                    value
                    for row in target_rows
                    if _is_operating_income(row.normalized_label)
                    for value in row.values
                    if value is not None
                }
            except DartRevenueDerivationError:
                candidate_operating_values = set()
            targets = (
                ("current", operating_current),
                ("cumulative", operating_cumulative),
            )
            for target_kind, target in targets:
                if target is None or target not in candidate_operating_values:
                    continue
                amounts = None
                error = None
                statement_headings = list(
                    _STATEMENT_HEADING.finditer(document, 0, table_start)
                )
                section_starts = [
                    heading.start()
                    for heading in statement_headings[
                        -MAX_STATEMENT_HEADING_CANDIDATES:
                    ]
                ]
                if not section_starts:
                    title_start = lowered_document.rfind("<title", 0, table_start)
                    if title_start >= 0:
                        section_starts.append(title_start)
                # Original XML can place account rows in many sibling layout
                # tables before a repeated statement title. Expand backwards
                # geometrically; the exact operating-income reconciliation
                # rejects a window that includes unrelated statement rows.
                for table_window in (4, 8, 16, 32, 64, 128, 256):
                    window_start = max(0, table_index - table_window)
                    section_starts.append(table_spans[window_start][0])
                statement_candidates = [candidate]
                for section_start in reversed(section_starts):
                    combined = document[section_start:table_end]
                    if units and not _ARCHIVE_UNIT.search(combined):
                        combined = f"(단위 : {units[-1]}){combined}"
                    if combined not in statement_candidates:
                        statement_candidates.append(combined)
                for statement_candidate in statement_candidates:
                    try:
                        amounts = derive_gross_revenue(
                            statement_candidate,
                            operating_current=target if target_kind == "current" else None,
                            operating_cumulative=target if target_kind == "cumulative" else None,
                        )
                        break
                    except DartRevenueDerivationError as candidate_error:
                        error = candidate_error
                if amounts is None:
                    assert error is not None
                    if str(error) not in failures and len(failures) < 3:
                        failures.append(str(error))
                    if "do not reconcile" in str(error):
                        try:
                            # Prefer the recombined section in diagnostics: the
                            # single table often contains only the operating-income
                            # subtotal, while account rows live in sibling tables.
                            debug_rows, _debug_unit = parse_statement_rows(
                                max(statement_candidates, key=len)
                            )
                            debug_row_index, debug_column = next(
                                (row_index, column)
                                for row_index, row in enumerate(debug_rows)
                                if _is_operating_income(row.normalized_label)
                                for column, value in enumerate(row.values)
                                if value == target
                            )
                            nearby_rows = debug_rows[
                                max(
                                    0,
                                    debug_row_index - MAX_TREE_DIAGNOSTIC_ROWS,
                                ):debug_row_index + 1
                            ]
                            candidate_tree_sample = [
                                (
                                    row.depth,
                                    row.normalized_label,
                                    str(row.values[debug_column])
                                    if debug_column < len(row.values)
                                    and row.values[debug_column] is not None
                                    else None,
                                )
                                for row in nearby_rows
                            ]
                            if len(candidate_tree_sample) > len(tree_sample):
                                tree_sample = candidate_tree_sample
                        except (DartRevenueDerivationError, StopIteration):
                            pass
                    continue
                if target_kind == "current" and amounts.current_revenue is not None:
                    current_matches[amounts.current_revenue] = amounts
                if target_kind == "cumulative" and amounts.cumulative_revenue is not None:
                    cumulative_matches[amounts.cumulative_revenue] = amounts
    if not current_matches and not cumulative_matches:
        raise DartRevenueDerivationError(
            "No income-statement table in the OpenDART archive reconciles to operating income "
            f"(tables={table_count}, candidates={candidate_count}, reasons={failures}, "
            f"operating_samples={operating_samples}, "
            f"tree_sample={tree_sample}, "
            f"targets={[str(operating_current), str(operating_cumulative)]})."
        )
    if len(current_matches) > 1 or len(cumulative_matches) > 1:
        raise DartRevenueDerivationError(
            "Multiple OpenDART statement tables produce different gross revenue values."
        )
    current = next(iter(current_matches.values()), None)
    cumulative = next(iter(cumulative_matches.values()), None)
    return GrossRevenueAmounts(
        current_revenue=current.current_revenue if current else None,
        cumulative_revenue=cumulative.cumulative_revenue if cumulative else None,
        current_expense=current.current_expense if current else None,
        cumulative_expense=cumulative.cumulative_expense if cumulative else None,
        current_operating_income=current.current_operating_income if current else None,
        cumulative_operating_income=(
            cumulative.cumulative_operating_income if cumulative else None
        ),
    )


def selected_labels(rows: Iterable[StatementRow]) -> tuple[str, ...]:
    """Expose normalized labels for secret-free diagnostics and focused tests."""
    return tuple(row.normalized_label for row in rows)
