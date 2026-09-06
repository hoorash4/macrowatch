from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from typing import Callable, Iterable

from .models import USFinancialFact, market_period


_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7,
    "july": 7, "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12,
    "december": 12,
}
_NUMBER = re.compile(r"^\(?\s*[-+]?\s*(?:\d[\d,]*(?:\.\d+)?|\.\d+)\s*\)?$")
_DATE = re.compile(
    r"\b(" + "|".join(_MONTHS) + r")\.?\s+(\d{1,2}),?\s+(20\d{2})\b", re.I,
)
_SCALE = {"thousand": Decimal("1000"), "million": Decimal("1000000"), "billion": Decimal("1000000000")}


@dataclass(frozen=True)
class SixKFiling:
    accession: str
    filing_date: date
    report_date: date | None
    primary_document: str


@dataclass(frozen=True)
class SixKDocument:
    name: str
    text: str


class _FilingHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text: list[str] = []
        self.tables: list[list[list[str]]] = []
        self.links: list[tuple[str, str]] = []
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._href: str | None = None
        self._anchor: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "table":
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []
        elif tag == "a":
            self._href = values.get("href")
            self._anchor = []

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._cell is not None and self._row is not None:
            self._row.append(_clean(" ".join(self._cell)))
            self._cell = None
        elif tag == "tr" and self._row is not None and self._table is not None:
            if any(self._row):
                self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            if self._table:
                self.tables.append(self._table)
            self._table = None
        elif tag == "a" and self._anchor is not None:
            if self._href:
                self.links.append((self._href, _clean(" ".join(self._anchor))))
            self._href = None
            self._anchor = None

    def handle_data(self, data: str) -> None:
        value = _clean(data)
        if not value:
            return
        self.text.append(value)
        if self._cell is not None:
            self._cell.append(value)
        if self._anchor is not None:
            self._anchor.append(value)


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value).replace("\xa0", " ")).strip()


def parse_filing_html(content: str) -> tuple[str, list[list[list[str]]], list[tuple[str, str]]]:
    parser = _FilingHtmlParser()
    parser.feed(content)
    return _clean(" ".join(parser.text)), parser.tables, parser.links


def linked_financial_documents(content: str) -> list[str]:
    """Return local 6-K links likely to contain furnished financial results."""
    _, _, links = parse_filing_html(content)
    result: list[str] = []
    for href, label in links:
        target = href.split("?", 1)[0].split("#", 1)[0]
        haystack = f"{target} {label}".lower()
        if (
            target and not re.match(r"^[a-z]+://", target, re.I)
            and target.lower().endswith((".htm", ".html"))
            and any(term in haystack for term in (
                "99.1", "ex99", "ex-99", "press release", "financial result",
                "financial statement", "quarterly result", "interim result",
            ))
        ):
            result.append(target)
    return list(dict.fromkeys(result))


def _date_tokens(text: str) -> list[date]:
    result: list[date] = []
    for month, day, year in _DATE.findall(text):
        try:
            result.append(date(int(year), _MONTHS[month.lower().rstrip(".")], int(day)))
        except ValueError:
            continue
    return result


def _matches_target_period(value: date, target: tuple[int, int]) -> bool:
    """Map week-based quarter ends just after month-end to the intended quarter."""
    if market_period(value) == target:
        return True
    if value.month in {1, 4, 7, 10} and value.day <= 7:
        previous_day = date.fromordinal(value.replace(day=1).toordinal() - 1)
        return market_period(previous_day) == target
    return False


def _number(value: str) -> Decimal | None:
    value = _clean(value).replace("−", "-").replace("—", "").replace("–", "-")
    value = re.sub(r"(?:US\$|RMB|EUR|USD|CNY|€|\$)", "", value, flags=re.I).strip()
    if not _NUMBER.fullmatch(value):
        return None
    negative = value.startswith("(") and value.endswith(")")
    try:
        result = Decimal(value.strip("() ").replace(" ", "").replace(",", ""))
    except InvalidOperation:
        return None
    return -abs(result) if negative else result


def _currency(text: str) -> str:
    lowered = text.lower()
    if "us$" in lowered or re.search(r"\busd\b", lowered):
        return "USD"
    if "rmb" in lowered or re.search(r"\bcny\b", lowered):
        return "CNY"
    if "€" in text or re.search(r"\beur\b", lowered):
        return "EUR"
    return "USD"


def _scale(text: str) -> Decimal:
    lowered = text.lower()
    matches = [
        (match.start(), multiplier)
        for label, multiplier in _SCALE.items()
        if (match := re.search(
            rf"\bin(?:\s+(?:rmb|cny|usd|eur|us\$|\$|€))?\s+{label}s?\b", lowered,
        )) is not None
    ]
    return min(matches, default=(0, Decimal(1)))[1]


_METRIC_LABELS = {
    "top_line": (
        "total consolidated net revenues", "total net revenues", "total net sales",
        "total revenues", "net revenues", "net revenue", "net sales", "total revenue", "revenues", "revenue",
    ),
    "operating_income": (
        "reported operating profit", "gaap operating income", "income from operations", "operating income",
        "income loss from operations", "operating profit", "loss from operations", "operating loss",
    ),
    "net_income": (
        "net income attributable to ordinary shareholders", "net income attributable to shareholders",
        "net income attributable to the company", "net income attributable to stockholders",
        "net loss attributable to ordinary shareholders", "net loss attributable to shareholders",
        "net loss attributable to the company", "net loss attributable to stockholders",
        "net income", "net loss", "profit after tax", "profit for the period", "profit attributable to owners",
    ),
}


def _metric_for_label(label: str, *, relaxed: bool = False) -> str | None:
    normalized = _clean(label).lower()
    if relaxed:
        normalized = re.sub(r"\boperating\s*\(loss\)\s*/\s*profit\b", "operating profit", normalized)
        normalized = re.sub(r"\boperating\s+profit\s*/\s*\(loss\)\b", "operating profit", normalized)
        normalized = re.sub(r"\bnet\s*\(loss\)\s*/\s*income\b", "net income", normalized)
        normalized = re.sub(r"\bnet\s+income\s*/\s*\(loss\)\b", "net income", normalized)
        normalized = re.sub(r"\bprofit\s*/\s*\(loss\)\s+for the period\b", "profit for the period", normalized)
        normalized = re.sub(r"\bnet earnings\b", "net income", normalized)
        normalized = re.sub(r"\bprofit after taxes\b", "profit after tax", normalized)
        normalized = re.sub(r"\s*(?:\[\d+\]|\d+)$", "", normalized)
    # Issuers often append the statement unit to the row label (for example,
    # ``Revenue (€M)``).  It is presentation metadata, not a different metric.
    normalized = re.sub(
        r"\s*\((?:[^)]*(?:us\$|rmb|usd|cny|eur|gbp|jpy|€|\$)[^)]*)\)\s*$",
        "",
        normalized,
        flags=re.I,
    )
    normalized = re.sub(r"\s*/\s*\(?loss\)?", "", normalized)
    normalized = normalized.replace("(loss)", "")
    normalized = re.sub(r"(?:\s*\(\d+\))+$", "", normalized).rstrip(" :")
    if any(term in normalized for term in ("non-gaap", "adjusted", "segment", "margin", "per share", "cash flow")):
        return None
    for metric, labels in _METRIC_LABELS.items():
        if normalized in labels:
            return metric
    if re.match(r"^net (?:income|loss) attributable to\b", normalized):
        return "net_income"
    return None


def _auxiliary_metric(label: str) -> str | None:
    normalized = _clean(label).lower().rstrip(" :")
    if normalized == "gross profit":
        return "gross_profit"
    if normalized in {"total operating expenses", "operating expenses"}:
        return "operating_expenses"
    return None


def _metric_label_priority(metric: str, label: str) -> int:
    normalized = _clean(label).lower()
    if metric == "net_income" and "attributable to" in normalized:
        return 2
    return 1


def _target_column(
    header: str, value_count: int, target: tuple[int, int], *, prefer_split_dates: bool = False,
) -> int | None:
    dates = _loose_period_dates(header, prefer_split=prefer_split_dates)
    if len(dates) > value_count:
        dates = dates[:value_count]
    matching = [index for index, item in enumerate(dates) if _matches_target_period(item, target)]
    if matching and len(dates) <= value_count:
        return matching[-1]
    years = [int(value) for value in re.findall(r"\b20\d{2}\b", header)]
    if len(years) > value_count:
        years = years[:value_count]
    if years and len(years) <= value_count:
        matching = [index for index, value in enumerate(years) if value == target[0]]
        if matching:
            return matching[-1]
    return 0 if value_count else None


def _row_numbers(cells: list[str]) -> list[Decimal]:
    result: list[Decimal] = []
    index = 0
    while index < len(cells):
        cell = cells[index]
        if cell.startswith("(") and not cell.endswith(")") and index + 1 < len(cells) and cells[index + 1] == ")":
            cell += ")"
            index += 1
        value = _number(cell)
        if value is not None:
            result.append(value)
        index += 1
    return result


def _three_month_columns(header: str, count: int) -> int:
    lowered = header.lower()
    if "three months ended" in lowered and any(
        label in lowered for label in ("six months ended", "nine months ended", "twelve months ended", "year ended")
    ):
        return max(1, count // 2)
    return count


def _column_currency(header: str, index: int, count: int, fallback: str) -> str:
    tokens = re.findall(r"US\$|RMB|USD|CNY|EUR|€|\$", header, re.I)
    if len(tokens) >= count and index < len(tokens):
        return _currency(tokens[index])
    return fallback


def _prefer_usd_column(header: str, index: int, count: int) -> int:
    tokens = re.findall(r"US\$|RMB|USD|CNY|EUR|€|\$", header, re.I)
    if len(tokens) >= count and index + 1 < count:
        current = _currency(tokens[index])
        following = _currency(tokens[index + 1])
        if current != "USD" and following == "USD":
            return index + 1
    return index


def _table_values(
    tables: Iterable[list[list[str]]], target: tuple[int, int], default_text: str = "",
    *, backfill_mode: bool = False,
) -> tuple[dict[str, Decimal], dict[str, str], date | None]:
    candidates: list[tuple[tuple[int, int], dict[str, Decimal], dict[str, str], date | None]] = []
    for table in tables:
        joined = _clean(" ".join(cell for row in table for cell in row))
        lowered = joined.lower()
        if not any(label in lowered for label in ("three months ended", "quarter ended", "quarterly")) and not re.search(
            r"\bq[1-4]\s+20\d{2}\b", lowered,
        ):
            continue
        header_rows: list[str] = []
        values: dict[str, Decimal] = {}
        currencies: dict[str, str] = {}
        priorities: dict[str, int] = {}
        auxiliary: dict[str, Decimal] = {}
        for row in table:
            label_index = next((
                i for i, cell in enumerate(row)
                if _metric_for_label(cell, relaxed=backfill_mode) or _auxiliary_metric(cell)
            ), None)
            if label_index is None:
                if not values:
                    header_rows.append(" ".join(row))
                continue
            metric = _metric_for_label(row[label_index], relaxed=backfill_mode)
            auxiliary_metric = _auxiliary_metric(row[label_index])
            header = " ".join(header_rows)
            numbers = _row_numbers(row[label_index + 1:])
            direct_count = _three_month_columns(header, len(numbers))
            direct_numbers = numbers[:direct_count]
            index = _target_column(
                header, len(direct_numbers), target, prefer_split_dates=backfill_mode,
            )
            if index is not None:
                index = _prefer_usd_column(header, index, direct_count)
            priority = _metric_label_priority(metric, row[label_index]) if metric is not None else 0
            if index is not None and index < len(numbers) and (
                metric not in values or (backfill_mode and priority > priorities.get(metric, 0))
            ):
                local_scale = _scale(joined)
                value = direct_numbers[index] * (local_scale if local_scale != 1 else _scale(default_text))
                currency = _column_currency(header, index, direct_count, _currency(joined))
                if metric is not None:
                    values[metric] = value
                    currencies[metric] = currency
                    priorities[metric] = priority
                elif auxiliary_metric is not None:
                    auxiliary[auxiliary_metric] = value
                    currencies[auxiliary_metric] = currency
        if "operating_income" not in values and {"gross_profit", "operating_expenses"} <= auxiliary.keys():
            if currencies.get("gross_profit") == currencies.get("operating_expenses"):
                values["operating_income"] = auxiliary["gross_profit"] - abs(auxiliary["operating_expenses"])
                currencies["operating_income"] = currencies["gross_profit"]
        header = " ".join(header_rows)
        dates = [
            item for item in _loose_period_dates(header, prefer_split=backfill_mode)
            if _matches_target_period(item, target)
        ]
        represented_end = dates[-1] if dates else None
        direct_period = int(bool(re.search(r"\b(?:for the )?(?:three months|quarter) ended\b", header, re.I)))
        candidates.append(((len(values), direct_period), values, currencies, represented_end))
    if not candidates:
        return {}, {}, None
    best = max(candidates, key=lambda item: item[0])
    if not backfill_mode or best[3] is None:
        return best[1], best[2], best[3]
    values, currencies = dict(best[1]), dict(best[2])
    for _, extra_values, extra_currencies, extra_end in candidates:
        if extra_end != best[3]:
            continue
        for metric, value in extra_values.items():
            if metric not in values:
                values[metric] = value
                currencies[metric] = extra_currencies.get(metric, "USD")
    return values, currencies, best[3]


def _half_year_values(
    tables: Iterable[list[list[str]]], target: tuple[int, int], default_text: str = "",
) -> tuple[dict[str, Decimal], dict[str, str], date | None]:
    """Read an IFRS half-year statement for backfill-only Q2 completion.

    Some foreign private issuers furnish an exact Q2 release for selected
    metrics but disclose the remaining GAAP metrics only for the first half.
    The historical backfill intentionally allocates that reported H1 total
    equally between Q1 and Q2 when no exact Q2 metric is available.
    """
    candidates: list[tuple[int, dict[str, Decimal], dict[str, str], date | None]] = []
    for table in tables:
        joined = _clean(" ".join(cell for row in table for cell in row))
        if "six months ended" not in joined.lower():
            continue
        header_rows: list[str] = []
        values: dict[str, Decimal] = {}
        currencies: dict[str, str] = {}
        priorities: dict[str, int] = {}
        for row in table:
            label_index = next((
                i for i, cell in enumerate(row)
                if _metric_for_label(cell, relaxed=True) is not None
            ), None)
            if label_index is None:
                if not values:
                    header_rows.append(" ".join(row))
                continue
            metric = _metric_for_label(row[label_index], relaxed=True)
            if metric is None:
                continue
            header = " ".join(header_rows)
            numbers = _row_numbers(row[label_index + 1:])
            index = _target_column(header, len(numbers), target, prefer_split_dates=True)
            priority = _metric_label_priority(metric, row[label_index])
            if index is None or index >= len(numbers) or priority < priorities.get(metric, 0):
                continue
            local_scale = _scale(joined)
            values[metric] = numbers[index] * (local_scale if local_scale != 1 else _scale(default_text))
            currencies[metric] = _column_currency(header, index, len(numbers), _currency(joined))
            priorities[metric] = priority
        header = " ".join(header_rows)
        dates = [item for item in _loose_period_dates(header, prefer_split=True) if _matches_target_period(item, target)]
        candidates.append((len(values), values, currencies, dates[-1] if dates else None))
    if not candidates:
        return {}, {}, None
    _, values, currencies, period_end = max(candidates, key=lambda item: item[0])
    return values, currencies, period_end


def _loose_period_dates(header: str, *, prefer_split: bool = False) -> list[date]:
    exact = _date_tokens(header)
    if exact and not prefer_split:
        return exact
    separator = r"\s*" if prefer_split else r"\s+"
    month_days = re.findall(
        r"\b(" + "|".join(_MONTHS) + rf")\.?{separator}(\d{{1,2}})\b", header, re.I,
    )
    years = [int(value) for value in re.findall(r"\b20\d{2}\b", header)]
    if len(month_days) != len(years):
        return exact
    result: list[date] = []
    for (month, day), year in zip(month_days, years, strict=False):
        try:
            result.append(date(year, _MONTHS[month.lower().rstrip(".")], int(day)))
        except ValueError:
            continue
    return result if len(result) > len(exact) else exact


def _flat_values(text: str, target: tuple[int, int]) -> tuple[dict[str, Decimal], dict[str, str], date | None]:
    """Read image-backed statements that expose an accessibility text layer."""
    anchors = [match.start() for match in re.finditer(r"three months ended", text, re.I)]
    best: tuple[int, dict[str, Decimal], dict[str, str], date | None] = (0, {}, {}, None)
    all_labels = sorted({label for labels in _METRIC_LABELS.values() for label in labels}, key=len, reverse=True)
    label_pattern = "|".join(re.escape(label) for label in all_labels)
    for start in anchors:
        block = text[start:start + 5000]
        first_label = re.search(label_pattern, block, re.I)
        if first_label is None:
            continue
        header = block[:first_label.start()]
        dates = _loose_period_dates(header)
        direct_count = _three_month_columns(header, len(dates))
        direct_dates = dates[:direct_count]
        matching = [index for index, item in enumerate(direct_dates) if _matches_target_period(item, target)]
        if not matching:
            continue
        column = matching[-1]
        values: dict[str, Decimal] = {}
        currencies: dict[str, str] = {}
        for metric, labels in _METRIC_LABELS.items():
            for label in sorted(labels, key=len, reverse=True):
                match = re.search(
                    rf"\b{re.escape(label)}\b\s+((?:\(?[-+]?\d[\d,]*(?:\.\d+)?\)?\s+){{{direct_count},12}})",
                    block, re.I,
                )
                if match is None:
                    continue
                numbers = [_number(value) for value in re.findall(r"\(?[-+]?\d[\d,]*(?:\.\d+)?\)?", match.group(1))]
                numbers = [value for value in numbers if value is not None]
                if column < len(numbers):
                    values[metric] = numbers[column] * _scale(header)
                    currencies[metric] = _currency(header)
                    break
        if len(values) > best[0]:
            best = (len(values), values, currencies, direct_dates[column])
    return best[1], best[2], best[3]


def _narrative_value(text: str, labels: tuple[str, ...], year: int) -> tuple[Decimal | None, str | None]:
    label_pattern = "|".join(re.escape(label) for label in sorted(labels, key=len, reverse=True))
    pattern = re.compile(
        rf"(?:{label_pattern}).{{0,220}}?(?:(US\$|RMB|USD|CNY|EUR|€|\$)\s*)"
        rf"(\(?\s*\d[\d,]*(?:\.\d+)?\s*\)?)\s*(million|billion)", re.I,
    )
    candidates: list[tuple[int, Decimal, str]] = []
    for match in pattern.finditer(text):
        context = text[max(0, match.start() - 40):match.end() + 180]
        if re.search(r"non-gaap|adjusted", context, re.I):
            continue
        value = _number(match.group(2))
        if value is None:
            continue
        currency = _currency(match.group(1))
        multiplier = _SCALE[match.group(3).lower()]
        score = 2 if f"{year}" in context else 1
        if currency == "USD":
            score += 1
        candidates.append((score, value * multiplier, currency))
        # Foreign releases commonly put a USD convenience translation directly
        # after the native amount. Prefer it when present.
        tail = text[match.end():match.end() + 80]
        translated = re.search(r"US\$\s*(\(?\s*\d[\d,]*(?:\.\d+)?\s*\)?)\s*(million|billion)", tail, re.I)
        if translated:
            usd = _number(translated.group(1))
            if usd is not None:
                candidates.append((score + 2, usd * _SCALE[translated.group(2).lower()], "USD"))
    if not candidates:
        return None, None
    _, value, currency = max(candidates, key=lambda item: item[0])
    return value, currency


def extract_six_k_fact(
    company_id: str,
    filing: SixKFiling,
    documents: Iterable[SixKDocument],
    year: int,
    quarter: int,
    fx_to_usd: Callable[[str, date], Decimal] | None = None,
    *,
    backfill_mode: bool = False,
) -> USFinancialFact | None:
    """Extract one exact quarter from a furnished 6-K earnings release."""
    target = (year, quarter)
    values: dict[str, Decimal] = {}
    currencies: dict[str, str] = {}
    represented_end: date | None = None
    for document in documents:
        text, tables, _ = parse_filing_html(document.text)
        lowered = text.lower()
        has_results_context = any(
            term in lowered for term in ("financial results", "quarterly results", "three months ended")
        ) or re.search(r"\b(?:first|second|third|fourth) quarter.{0,20}results\b", lowered[:700])
        if backfill_mode:
            has_results_context = has_results_context or "quarter ended" in lowered
        if not has_results_context:
            continue
        table_values, table_currencies, table_end = _table_values(
            tables, target, text, backfill_mode=backfill_mode,
        )
        if backfill_mode and set(table_values) != set(_METRIC_LABELS):
            half_values, half_currencies, half_end = _half_year_values(tables, target, text)
            for metric, value in half_values.items():
                if metric not in table_values:
                    table_values[metric] = value / 2
                    table_currencies[metric] = half_currencies.get(metric, "USD")
            table_end = table_end or half_end
        document_dates = _loose_period_dates(text)
        dates = [item for item in document_dates if _matches_target_period(item, target)]
        target_label = f"q{quarter} {year}"
        written_target = f"{('first', 'second', 'third', 'fourth')[quarter - 1]} quarter {year}"
        filing_name_has_quarter = re.search(
            rf"q{quarter}(?:results?|financial|[^a-z0-9]|$)", filing.primary_document.lower(),
        ) is not None
        if backfill_mode and table_end is not None and _matches_target_period(table_end, target):
            dates = [table_end]
        elif not dates and (
            target_label in lowered[:700] or written_target in lowered[:700]
            or (table_values and filing_name_has_quarter)
        ):
            dates = [date(year, quarter * 3, 31 if quarter in {1, 4} else 30)]
        elif backfill_mode and len(table_values) == 3:
            # Foreign issuers often split a day-month heading from its years,
            # leaving no globally parseable date even though the document has
            # one complete, target-selected quarterly income statement.
            dates = [date(year, quarter * 3, 31 if quarter in {1, 4} else 30)]
        elif not document_dates and filing.report_date and market_period(filing.report_date) == target:
            dates = [filing.report_date]
        if not dates:
            continue
        if not table_values:
            table_values, table_currencies, table_end = _flat_values(text, target)
        for metric, value in table_values.items():
            values.setdefault(metric, value)
            currencies.setdefault(metric, table_currencies.get(metric, "USD"))
        represented_end = table_end or max(dates)
        for metric, labels in _METRIC_LABELS.items():
            if metric in values:
                continue
            value, currency = _narrative_value(text, labels, year)
            if value is not None and currency:
                values[metric], currencies[metric] = value, currency
    if not values or represented_end is None:
        return None
    converted: dict[str, Decimal | None] = {}
    for metric in _METRIC_LABELS:
        value = values.get(metric)
        currency = currencies.get(metric, "USD")
        if value is not None and currency != "USD":
            if fx_to_usd is None:
                converted[metric] = None
                continue
            value *= fx_to_usd(currency, represented_end)
        converted[metric] = value
    top_line = converted["top_line"]
    if top_line not in {None, Decimal(0)}:
        # A tiny segment/footnote value can otherwise win the label match while
        # operating or net income comes from the consolidated statement.
        # Reject only extreme cross-statement mismatches; unusual but valid
        # loss-making quarters remain accepted.
        limit = abs(top_line) * Decimal(20)
        if any(
            value is not None and abs(value) > limit
            for value in (converted["operating_income"], converted["net_income"])
        ):
            return None
    return USFinancialFact(
        company_id=company_id, fiscal_year=year, fiscal_quarter=quarter,
        period_start=None, period_end=represented_end,
        top_line=converted["top_line"], operating_income=converted["operating_income"],
        net_income=converted["net_income"], source_filing_id=filing.accession,
        filing_date=filing.filing_date, is_pending=any(value is None for value in converted.values()),
    )
