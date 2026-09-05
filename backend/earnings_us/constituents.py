from __future__ import annotations

import csv
import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from decimal import Decimal
from html import unescape
from typing import Any, Iterable
from urllib.parse import urlencode

from earnings_v2.http import bounded_request, provider_session, safe_request_failure

from .models import MarketSecurity
from .providers import ProviderError, SecEdgarClient, normalize_cik, ticker_candidates


NASDAQ_WEIGHTING_URL = "https://indexes.nasdaqomx.com/Index/WeightingData"
ISHARES_OEF_HOLDINGS_URL = "https://www.ishares.com/us/products/239723/ishares-sp-100-etf/latest-holdings.csv"
SEC_ARCHIVES = "https://www.sec.gov/Archives/edgar/data"
OEF_TRUST_CIK = "0001100663"
OEF_SERIES_ID = "S000004306"
QQQ_TRUST_CIK = "0001067839"
INDEX_TRADING_DAY_LOOKBACK_DAYS = 7

SourceHolding = tuple[str, str, Decimal | None]


def _plain_html(value: str) -> str:
    text = unescape(re.sub(r"(?is)<[^>]+>", " ", value))
    return re.sub(r"\s+", " ", text).strip()


def _issuer_name(value: str) -> str:
    """Remove fund-schedule annotations that are not part of an SEC issuer name."""
    value = value.strip()
    while True:
        suffix = re.search(r"\s*\(([^()]*)\)\s*$", value)
        if suffix is None or suffix.group(1).strip().lower() == "the":
            break
        value = value[:suffix.start()].rstrip(" ,")
    value = re.sub(r"(?i),?\s*(?:ADR|NEW\s+YORK\s+SHARES?)\b", " ", value)
    return re.sub(r"\s+", " ", value).strip(" ,")


def _normal_name(value: str) -> str:
    value = re.sub(r"\s*[\\/][A-Z]{2,}[\\/]?\s*$", "", _issuer_name(value), flags=re.IGNORECASE)
    value = re.sub(r"(?i)(\b(?:INC|CORP|CO|LTD|PLC|LLC))\.?\s+[a-z]\s*$", r"\1", value)
    value = re.sub(r"\s*\(the\)\s*$", "", value, flags=re.IGNORECASE)
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _name_match_score(query: str, candidate: str) -> int:
    """Score an SEC issuer name while permitting only clear word expansions.

    Historical index feeds often abbreviate a legal issuer name (for example,
    ``PHARM`` instead of ``PHARMACEUTICALS``).  This is deliberately narrower
    than fuzzy string matching: every query word must match exactly or by a
    four-character word prefix, and legal suffixes do not contribute.
    """
    ignored = {
        "a", "and", "b", "c", "cl", "class", "cm", "co", "company", "companies", "cos", "corp", "corporation",
        "inc", "incorporated", "ltd", "limited", "nv", "nvs", "ord", "ordinary", "plc", "sh",
        "share", "shares", "sr", "srs", "the",
    }
    aliases = {
        "comm": "communications", "gp": "group", "intl": "international",
        "pharm": "pharmaceuticals", "vntrs": "ventures",
    }

    def words(value: str) -> list[str]:
        value = re.sub(r"(?i)(?<=[a-z])['’]s\b", "", value)
        value = re.sub(r"(?i)int['’]?l", "intl", value)
        result = [word for word in re.findall(r"[a-z0-9]+", value.lower()) if word not in ignored]
        return [aliases.get(word, word) for word in result]

    query_words, candidate_words = words(query), words(candidate)
    if not query_words or not candidate_words:
        return 0
    query_compact = "".join(word for word in query_words if len(word) > 1)
    candidate_compact = "".join(word for word in candidate_words if len(word) > 1)
    if query_compact and query_compact == candidate_compact:
        return len(query_words) * 100
    matched = sum(
        any(word == item or (len(word) >= 4 and item.startswith(word)) or (len(item) >= 4 and word.startswith(item))
            for item in candidate_words)
        for word in query_words
    )
    if matched != len(query_words):
        return 0
    # Prefer the parent issuer whose meaningful name has no extra qualifiers;
    # this separates e.g. WHOLE FOODS MARKET from its named subsidiaries.
    return matched * 100 - max(len(candidate_words) - len(query_words), 0)


def _filing_url(accession: str, document: str, cik: str = OEF_TRUST_CIK) -> str:
    accession_digits = re.sub(r"\D", "", accession)
    return f"{SEC_ARCHIVES}/{int(cik)}/{accession_digits}/{document}"


def _decode_filing(content: bytes) -> str:
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return content.decode("windows-1252", errors="replace")


def extract_oef_holdings(html: str) -> list[tuple[str, Decimal]]:
    """Extract issuer and reported holding value from OEF's legacy schedule."""
    start_match = re.search(
        r"(?is)Schedule\s+of\s+Investments.{0,1200}?iSHARES.*?S(?:&amp;|&)P\s*100\s+ETF",
        html,
    )
    if start_match is None:
        raise ProviderError("OEF filing did not include an S&P 100 schedule of investments")
    # The report is a combined iShares Trust filing. Restrict parsing to the
    # first schedule-sized window after OEF rather than its later fund sections.
    section = html[start_match.end():start_match.end() + 500_000]
    stock_start = re.search(r"(?is)COMMON\s+STOCKS", section)
    if stock_start is None:
        raise ProviderError("OEF schedule did not include common-stock holdings")
    section = section[stock_start.end():]
    stock_end = re.search(r"(?is)(?:SHORT[ -]TERM|CASH|TOTAL\s+INVESTMENTS)", section)
    if stock_end is not None:
        section = section[:stock_end.start()]

    holdings: list[tuple[str, Decimal]] = []
    for row in re.findall(r"(?is)<TR\b[^>]*>(.*?)</TR>", section):
        cells = re.findall(r"(?is)<TD\b[^>]*>(.*?)</TD>", row)
        if not cells:
            continue
        candidate = _plain_html(cells[0])
        if (
            not candidate
            or candidate.startswith("(")
            or candidate.upper() == candidate
            or "COMMON STOCK" in candidate.upper()
            or candidate.upper() == "TOTAL"
            or "%" in candidate
            or not re.search(r"[A-Za-z]", candidate)
        ):
            continue
        # Issuer rows have a numeric shares cell. Sector labels and table headings do not.
        if not re.search(r">\s*[\d,]+\s*<", row):
            continue
        numeric_cells: list[Decimal] = []
        for cell in cells[1:]:
            number = _plain_html(cell).replace(",", "").replace("$", "").strip()
            if re.fullmatch(r"\(?\d+(?:\.\d+)?\)?", number):
                numeric_cells.append(Decimal(number.strip("()")))
        if numeric_cells:
            holdings.append((candidate, numeric_cells[-1]))
    result = list(dict.fromkeys(holdings))
    if len(result) < 100:
        raise ProviderError(f"OEF schedule yielded only {len(result)} common-stock issuers")
    return result


def extract_nport_equity_holdings(document: str, series_pattern: str) -> list[tuple[str, str, Decimal]]:
    """Read common-equity issuer, optional ticker and weight from raw N-PORT XML."""
    try:
        root = ET.fromstring(document)
    except ET.ParseError as exc:
        raise ProviderError("N-PORT XML was malformed") from exc
    series = root.find(".//{*}seriesName")
    if series is None or not re.search(series_pattern, series.text or "", re.I):
        raise ProviderError("N-PORT filing was not the expected fund")
    rows: list[tuple[str, str, Decimal]] = []
    for investment in root.findall(".//{*}invstOrSec"):
        issuer = investment.find("{*}name")
        allocation = investment.find("{*}pctVal")
        asset_category = investment.find("{*}assetCat")
        ticker = investment.find(".//{*}ticker")
        name = (issuer.text or "").strip() if issuer is not None else ""
        # N-PORT also contains cash funds, derivatives and collateral. An
        # index-company universe is represented by equity-common (EC) rows.
        if asset_category is not None and (asset_category.text or "").strip() != "EC":
            continue
        symbol = ""
        if ticker is not None:
            symbol = str(ticker.get("value") or ticker.text or "").strip().upper()
            if not re.fullmatch(r"[A-Z][A-Z0-9./-]{0,9}", symbol):
                symbol = ""
        try:
            weight = Decimal((allocation.text or "").strip()) if allocation is not None else Decimal("NaN")
        except Exception:
            continue
        if name and weight.is_finite() and weight >= 0:
            rows.append((symbol, name, weight))
    result = list(dict.fromkeys(rows))
    if len(result) < 100:
        raise ProviderError(f"N-PORT yielded only {len(result)} equity holdings")
    return result


def extract_oef_nport_holdings(html: str) -> list[tuple[str, str, Decimal]]:
    """Read issuer, ticker and reported holding weight from structured N-PORT."""
    if re.search(r"<(?:\w+:)?invstOrSec\b", html):
        return extract_nport_equity_holdings(html, r"iShares\s+S&P\s+100\s+ETF")

    if not re.search(r"(?is)Name\s+of\s+Series.*?iShares\s+S(?:&amp;|&)P\s+100\s+ETF", html):
        raise ProviderError("N-PORT filing is not the iShares S&P 100 ETF series")
    rows: list[tuple[str, str, Decimal]] = []
    blocks = re.split(r"(?is)Item\s+C\.1\.\s+Identification\s+of\s+investment", html)
    for block in blocks[1:]:
        issuer = re.search(r"(?is)Name\s+of\s+issuer.*?<div[^>]*fakeBox[^>]*>(.*?)<span", block)
        ticker = re.search(r"(?is)Ticker\s+\(if.*?<div[^>]*fakeBox[^>]*>(.*?)<span", block)
        allocation = re.search(
            r"(?is)Percentage\s+value\s+compared\s+to\s+net\s+assets.*?"
            r"<div[^>]*fakeBox[^>]*>(.*?)<span",
            block,
        )
        if issuer is None or ticker is None or allocation is None:
            continue
        name, symbol = _plain_html(issuer.group(1)), _plain_html(ticker.group(1)).upper()
        try:
            weight = Decimal(_plain_html(allocation.group(1)))
        except Exception:
            continue
        if name and weight >= 0 and re.fullmatch(r"[A-Z][A-Z0-9./-]{0,9}", symbol):
            rows.append((symbol, name, weight))
    result = list(dict.fromkeys(rows))
    if len(result) < 100:
        raise ProviderError(f"OEF N-PORT yielded only {len(result)} tickered holdings")
    return result


def extract_oef_series_accessions(atom: str, reference_date: date) -> list[str]:
    """Select OEF series filings whose normal filing window can cover a report date."""
    try:
        root = ET.fromstring(atom)
    except ET.ParseError as exc:
        raise ProviderError("SEC OEF series feed was malformed") from exc
    namespace = {"atom": "http://www.w3.org/2005/Atom"}
    earliest, latest = reference_date + timedelta(days=30), reference_date + timedelta(days=120)
    result: list[str] = []
    for entry in root.findall("atom:entry", namespace):
        filed_text = entry.findtext("atom:content/atom:filing-date", namespaces=namespace)
        accession = entry.findtext("atom:content/atom:accession-number", namespaces=namespace)
        try:
            filed = date.fromisoformat(str(filed_text))
        except ValueError:
            continue
        if accession and earliest <= filed <= latest:
            result.append(accession)
    return result


def extract_series_accessions(atom: str) -> set[str]:
    """Return filing accessions from a SEC series-specific Atom feed."""
    try:
        root = ET.fromstring(atom)
    except ET.ParseError as exc:
        raise ProviderError("SEC series feed was malformed") from exc
    namespace = {"atom": "http://www.w3.org/2005/Atom"}
    return {
        accession
        for entry in root.findall("atom:entry", namespace)
        for accession in [entry.findtext("atom:content/atom:accession-number", namespaces=namespace)]
        if accession
    }


def archive_covers_filing_window(entry: dict[str, Any], reference_date: date) -> bool:
    """Return whether a submissions shard can contain a filing for the report date."""
    try:
        filing_from = date.fromisoformat(str(entry.get("filingFrom")))
        filing_to = date.fromisoformat(str(entry.get("filingTo")))
    except ValueError:
        return False
    earliest = reference_date + timedelta(days=30)
    latest = reference_date + timedelta(days=120)
    return filing_from <= latest and filing_to >= earliest


def archive_covers_legacy_window(entry: dict[str, Any], reference_date: date) -> bool:
    """Return whether a shard can hold the nearest legacy fund report."""
    try:
        filing_from = date.fromisoformat(str(entry.get("filingFrom")))
        filing_to = date.fromisoformat(str(entry.get("filingTo")))
    except ValueError:
        return False
    earliest = reference_date - timedelta(days=120)
    latest = reference_date + timedelta(days=120)
    return filing_from <= latest and filing_to >= earliest


def is_quarter_end_report_date(value: str, reference_date: date) -> bool:
    """Accept the last fund reporting day when calendar quarter-end is not a trading day."""
    try:
        report_date = date.fromisoformat(value)
    except ValueError:
        return False
    lag = (reference_date - report_date).days
    return report_date.year == reference_date.year and report_date.month == reference_date.month and 0 <= lag <= 7


def legacy_report_date(value: str, reference_date: date) -> date | None:
    """Accept the latest legacy fund report no more than one quarter old."""
    try:
        report_date = date.fromisoformat(value)
    except ValueError:
        return None
    lag = (reference_date - report_date).days
    return report_date if 0 <= lag <= 100 else None


class USIndexConstituentClient:
    """Official index/ETF membership sources, normalized to SEC company identities."""

    def __init__(self, sec: SecEdgarClient, *, session: Any | None = None) -> None:
        self.sec = sec
        self.session = session or provider_session()
        self.request_count = 0
        self._name_cik_cache: dict[tuple[str, int | None], str | None] = {}
        self._oef_legacy_accessions_cache: set[str] | None = None

    def _json(self, method: str, url: str, operation: str, **kwargs: Any) -> dict[str, Any]:
        try:
            payload = bounded_request(
                self.session, method, url, provider="U.S. index source", operation=operation,
                total_timeout=90, attempt_timeout=30, connect_timeout=8, read_timeout=45, **kwargs,
            )
        except Exception as exc:
            raise ProviderError(safe_request_failure("U.S. index source", operation, exc)) from None
        self.request_count += 1
        if not isinstance(payload, dict):
            raise ProviderError(f"U.S. index source {operation} returned invalid JSON")
        return payload

    def _binary(self, url: str, operation: str) -> bytes:
        try:
            payload = bounded_request(
                self.session, "GET", url, provider="U.S. index source", operation=operation,
                headers={"User-Agent": self.sec.user_agent, "Accept-Encoding": "gzip, deflate"},
                total_timeout=180, attempt_timeout=60, connect_timeout=8, read_timeout=90, binary=True,
            )
        except Exception as exc:
            raise ProviderError(safe_request_failure("U.S. index source", operation, exc)) from None
        self.request_count += 1
        return bytes(payload)

    def _fund_nport_rows(self, reference_date: date, *, cik_or_series: str, cik: str,
                         series_pattern: str, operation: str) -> list[SourceHolding] | None:
        feed_url = "https://www.sec.gov/cgi-bin/browse-edgar?" + urlencode({
            "action": "getcompany", "CIK": cik_or_series, "type": "NPORT-P",
            "owner": "exclude", "count": "100", "output": "atom",
        })
        atom = _decode_filing(self._binary(feed_url, f"{operation} filing feed"))
        for accession in extract_oef_series_accessions(atom, reference_date):
            document = _decode_filing(self._binary(_filing_url(accession, "primary_doc.xml", cik),
                                                   f"{operation} N-PORT {reference_date.isoformat()}"))
            report_date = re.search(r"<(?:\w+:)?repPdDate>(\d{4}-\d{2}-\d{2})</", document)
            if report_date is None or not is_quarter_end_report_date(report_date.group(1), reference_date):
                continue
            return extract_nport_equity_holdings(document, series_pattern)
        return None

    def _oef_nport_rows(self, reference_date: date) -> list[SourceHolding] | None:
        return self._fund_nport_rows(
            reference_date, cik_or_series=OEF_SERIES_ID, cik=OEF_TRUST_CIK,
            series_pattern=r"iShares\s+S&P\s+100\s+ETF", operation="OEF",
        )

    def _qqq_nport_rows(self, reference_date: date) -> list[SourceHolding] | None:
        return self._fund_nport_rows(
            reference_date, cik_or_series=QQQ_TRUST_CIK, cik=QQQ_TRUST_CIK,
            series_pattern=r"Invesco\s+QQQ\s+Trust", operation="QQQ",
        )

    def _oef_legacy_accessions(self) -> set[str]:
        if self._oef_legacy_accessions_cache is not None:
            return set(self._oef_legacy_accessions_cache)
        result: set[str] = set()
        for form in ("N-Q", "N-CSR"):
            feed_url = "https://www.sec.gov/cgi-bin/browse-edgar?" + urlencode({
                "action": "getcompany", "CIK": OEF_SERIES_ID, "type": form,
                "owner": "exclude", "count": "100", "output": "atom",
            })
            atom = _decode_filing(self._binary(feed_url, f"OEF legacy {form} filing feed"))
            result.update(extract_series_accessions(atom))
        self._oef_legacy_accessions_cache = result
        return set(result)

    def _cik_for_name(self, name: str, reference_date: date | None = None) -> str | None:
        search_name = re.sub(r"\s*\(the\)\s*$", "", _issuer_name(name), flags=re.IGNORECASE)
        search_name = re.sub(r"\s*[\\/][A-Z]{2,}[\\/]?\s*$", "", search_name, flags=re.IGNORECASE)
        search_name = re.sub(
            r"(?i)\b(?:SRS?|SERIES|CL(?:ASS)?|ORD(?:INARY)?|SHS?|SHARES?|COMMON|CM|NVS)(?:\s+[A-Z])?\b",
            " ", search_name,
        )
        search_name = re.sub(r"\s+", " ", search_name).strip(" ,")
        normalized_name = _normal_name(search_name)
        key = (normalized_name, reference_date.year if reference_date else None)
        if key in self._name_cik_cache:
            return self._name_cik_cache[key]
        expanded = re.sub(r"(?i)INT['’]L", "INTL", search_name)
        for short, full in {
            r"\bCOMM\b": "COMMUNICATIONS", r"\bGP\b": "GROUP",
            r"\bINTL\b": "INTERNATIONAL", r"\bPHARM\b": "PHARMACEUTICALS",
            r"\bVNTRS\b": "VENTURES",
        }.items():
            expanded = re.sub(short, full, expanded, flags=re.IGNORECASE)
        expanded = re.sub(r"(?i)\b(?:SRS?|SERIES|CL(?:ASS)?|ORD(?:INARY)?|SHS?|CM)\s+[A-Z]\b", "", expanded)
        expanded = re.sub(r"(?i)\b(?:CM|ORD(?:INARY)?|SHS?)\b", "", expanded)
        expanded = re.sub(r"\s+", " ", expanded).strip()
        legal_expanded = re.sub(r"(?i)\bCO\.?$", "COMPANY", expanded)
        legal_expanded = re.sub(r"(?i)\bCORP\.?$", "CORPORATION", legal_expanded)
        legal_expanded = re.sub(r"(?i)\bINC\.?$", "INCORPORATED", legal_expanded)
        queries = list(dict.fromkeys((search_name, expanded, legal_expanded)))
        parts = search_name.split()
        if len(parts) >= 4 and re.fullmatch(r"[A-Za-z]{1,2}", parts[0]):
            without_initials = " ".join(parts[1:])
            queries.append(without_initials)
            queries.append(re.sub(
                r"\s+(?:&\s*)?(?:CORP(?:ORATION)?|INC(?:ORPORATED)?|LTD|PLC|CO(?:MPANY)?)\.?$",
                "", without_initials, flags=re.IGNORECASE,
            ))
        shorter = re.sub(
            r"\s+(?:&\s*)?(?:CORP(?:ORATION)?|INC(?:ORPORATED)?|LTD|PLC|CO(?:MPANY)?)\.?$",
            "", search_name, flags=re.IGNORECASE,
        )
        if shorter != search_name:
            queries.append(shorter)
        candidates: list[tuple[str, str, str]] = []
        for query in queries:
            try:
                payload = self._json(
                    "GET", "https://efts.sec.gov/LATEST/search-index", f"SEC issuer search {query}",
                    params={
                        "q": query,
                        **({
                            "dateRange": "custom",
                            "startdt": f"{reference_date.year - 1}-01-01",
                            "enddt": f"{reference_date.year + 1}-12-31",
                            "forms": "10-K,20-F,40-F",
                        } if reference_date else {"dateRange": "all"}),
                    },
                    headers={"User-Agent": self.sec.user_agent, "Accept-Encoding": "gzip, deflate"},
                )
            except ProviderError:
                continue
            for hit in payload.get("hits", {}).get("hits", []) if isinstance(payload.get("hits"), dict) else ():
                source = hit.get("_source") if isinstance(hit, dict) else None
                if not isinstance(source, dict):
                    continue
                for display in source.get("display_names", []) if isinstance(source.get("display_names"), list) else ():
                    display_text = str(display)
                    cik_match = re.search(r"CIK\s+(\d+)", display_text)
                    if cik_match:
                        raw_name = re.sub(
                            r"\s*[\\/][A-Z]{2,}[\\/]?\s*$", "",
                            display_text.split("(", 1)[0].strip(), flags=re.IGNORECASE,
                        )
                        candidates.append((raw_name, _normal_name(raw_name), normalize_cik(cik_match.group(1)) or ""))
            if any(normalized == normalized_name and cik for _, normalized, cik in candidates):
                break
        exact_ciks = {cik for _, normalized, cik in candidates if normalized == normalized_name and cik}
        exact = next(iter(exact_ciks)) if len(exact_ciks) == 1 else None
        if exact:
            value = exact
        else:
            scored = [
                (score, cik)
                for raw_name, _, cik in candidates if cik
                for score in [max(_name_match_score(query, raw_name) for query in queries)] if score
            ]
            best = max((score for score, _ in scored), default=0)
            best_ciks = {cik for score, cik in scored if score == best}
            value = next(iter(best_ciks)) if len(best_ciks) == 1 else None
        self._name_cik_cache[key] = value
        return value

    def _cik_for_ticker(self, ticker: str, reference_date: date | None = None) -> str | None:
        value = ticker.strip().upper()
        if not value:
            return None
        try:
            payload = self._json(
                "GET", "https://efts.sec.gov/LATEST/search-index", f"SEC issuer search {value}",
                params={
                    "q": value,
                    **({
                            "dateRange": "custom",
                            "startdt": f"{reference_date.year - 1}-01-01",
                            "enddt": f"{reference_date.year + 1}-12-31",
                            "forms": "10-K,20-F,40-F",
                        } if reference_date else {"dateRange": "all"}),
                },
                headers={"User-Agent": self.sec.user_agent, "Accept-Encoding": "gzip, deflate"},
            )
        except ProviderError:
            return None
        pattern = re.compile(rf"\(\s*{re.escape(value)}\s*(?:,|\))", re.IGNORECASE)
        hits = payload.get("hits", {}).get("hits", []) if isinstance(payload.get("hits"), dict) else ()
        for hit in hits:
            source = hit.get("_source") if isinstance(hit, dict) else None
            if not isinstance(source, dict):
                continue
            for display in source.get("display_names", []) if isinstance(source.get("display_names"), list) else ():
                match = re.search(r"CIK\s+(\d+)", str(display))
                if match and pattern.search(str(display)):
                    return normalize_cik(match.group(1))
        # A delisted historical ticker is often absent from the issuer's modern
        # display name.  With a tight filing-date window and annual-report form
        # filter, the first exact-ticker search hit is the filing issuer.
        if reference_date:
            for hit in hits[:3]:
                source = hit.get("_source") if isinstance(hit, dict) else None
                source_ciks = source.get("ciks", []) if isinstance(source, dict) else []
                unique = {normalize_cik(item) for item in source_ciks if normalize_cik(item)}
                if len(unique) == 1:
                    return next(iter(unique))
        return None

    def _securities(self, market_id: str, reference_date: date, rows: Iterable[SourceHolding], directory: dict[str, str]) -> list[MarketSecurity]:
        by_company: dict[str, tuple[MarketSecurity, Decimal, bool]] = {}
        ticker_by_cik: dict[str, str] = {}
        for ticker, cik in directory.items():
            if ticker and (cik not in ticker_by_cik or ticker < ticker_by_cik[cik]):
                ticker_by_cik[cik] = ticker
        unresolved: list[str] = []
        pending: list[tuple[SourceHolding, str | None]] = []
        issuer_directory: dict[str, set[str]] = {}
        issuer_rows = [(title, cik) for _, title, cik in self.sec.company_ticker_rows()]
        issuer_titles_by_cik: dict[str, list[str]] = {}
        for title, cik in issuer_rows:
            issuer_directory.setdefault(_normal_name(title), set()).add(cik)
            issuer_titles_by_cik.setdefault(cik, []).append(title)

        def current_directory_cik(name: str) -> str | None:
            exact = issuer_directory.get(_normal_name(name), set())
            if len(exact) == 1:
                return next(iter(exact))
            scored = [(score, cik) for title, cik in issuer_rows for score in [_name_match_score(name, title)] if score]
            best = max((score for score, _ in scored), default=0)
            matches = {cik for score, cik in scored if score == best}
            return next(iter(matches)) if best >= 100 and len(matches) == 1 else None

        def store(ticker: str, name: str, cik: str, selection_value: Decimal | None) -> None:
            security = MarketSecurity(ticker=ticker or ticker_by_cik.get(cik, ""), name=name, cik=cik, market_cap=Decimal(0), rank=0,
                                      reference_date=reference_date, market_id=market_id)
            current = by_company.get(security.company_id)
            value = selection_value or Decimal(0)
            if current is None:
                by_company[security.company_id] = (security, value, selection_value is not None)
                return
            representative, total, has_value = current
            if security.ticker and (not representative.ticker or security.ticker < representative.ticker):
                representative = security
            by_company[security.company_id] = (
                representative,
                total + value,
                has_value and selection_value is not None,
            )

        for ticker, name, selection_value in rows:
            cik = next((directory[item] for item in ticker_candidates(ticker) if item in directory), None) if ticker else None
            # A ticker can be reused by a different issuer after a spin-off or
            # reorganization.  Trust today's ticker directory for a historical
            # row only when its issuer name still describes the source company.
            current_titles = issuer_titles_by_cik.get(cik, ()) if cik is not None else ()
            if cik is not None and name and current_titles and not any(
                _name_match_score(name, title) >= 100 for title in current_titles
            ):
                pending.append(((ticker, name, selection_value), cik))
                continue
            if cik is None:
                cik = current_directory_cik(name)
            if cik is None:
                pending.append(((ticker, name, selection_value), None))
                continue
            store(ticker, name, cik, selection_value)

        # Current SEC's ticker directory intentionally omits delisted historic
        # symbols. Resolve only that small remainder concurrently, staying well
        # below the SEC's public request-rate limit.
        def resolve(item: tuple[SourceHolding, str | None]) -> tuple[str, str, Decimal | None, str | None]:
            (ticker, name, selection_value), fallback_cik = item
            cik = self._cik_for_ticker(ticker, reference_date) or self._cik_for_name(name, reference_date) or fallback_cik
            return ticker, name, selection_value, cik

        with ThreadPoolExecutor(max_workers=4) as executor:
            resolved = executor.map(resolve, pending)
            for ticker, name, selection_value, cik in resolved:
                if cik is None:
                    unresolved.append(f"{ticker or name}")
                    continue
                store(ticker, name, cik, selection_value)
        if unresolved:
            raise ProviderError(f"{market_id} could not map {len(unresolved)} issuer(s) to SEC CIK: {', '.join(unresolved[:20])}")
        if len(by_company) < 100:
            raise ProviderError(f"{market_id} resolved to {len(by_company)}/100 distinct companies")
        weighted = all(has_value for _, _, has_value in by_company.values())
        if len(by_company) > 100 and not weighted:
            identifiers = ", ".join(
                f"{security.ticker}:{company_id}"
                for company_id, (security, _, _) in sorted(by_company.items())
            )
            raise ProviderError(
                f"{market_id} returned {len(by_company)} companies without source weights for selection: {identifiers}"
            )
        ranked = sorted(
            by_company.values(),
            key=(lambda item: (-item[1], item[0].ticker)) if weighted else (lambda item: (item[0].ticker, item[0].name)),
        )[:100]
        return [MarketSecurity(**{**item[0].__dict__, "rank": index}) for index, item in enumerate(ranked, start=1)]

    def nasdaq100(self, reference_date: date, directory: dict[str, str]) -> list[MarketSecurity]:
        rows: list[SourceHolding] = []
        # Calendar quarter-end may be a weekend or market holiday. The first
        # valid response while walking backward is the nearest trading day;
        # the bound prevents a provider outage from silently selecting stale data.
        for lag in range(INDEX_TRADING_DAY_LOOKBACK_DAYS + 1):
            trading_date = reference_date - timedelta(days=lag)
            payload = self._json(
                "POST", NASDAQ_WEIGHTING_URL, f"Nasdaq-100 constituents {trading_date.isoformat()}",
                data={"id": "NDX", "tradeDate": trading_date.isoformat(), "timeOfDay": "close"},
                headers={"User-Agent": self.sec.user_agent, "Accept": "application/json"},
            )
            rows = [(str(item.get("Symbol") or "").strip().upper(), str(item.get("Name") or "").strip(), None)
                    for item in payload.get("aaData", []) if isinstance(item, dict)]
            rows = [(ticker, name, value) for ticker, name, value in rows if ticker and name]
            if len(rows) >= 100:
                break
        if len(rows) < 100:
            qqq_rows = self._qqq_nport_rows(reference_date)
            if qqq_rows is None:
                raise ProviderError(f"Nasdaq-100 source returned {len(rows)}/100 securities and QQQ had no weights")
            return self._securities("us_nasdaq100", reference_date, qqq_rows, directory)
        selection_error: ProviderError | None = None
        try:
            return self._securities("us_nasdaq100", reference_date, rows, directory)
        except ProviderError as exc:
            if "without source weights for selection" not in str(exc):
                raise
            selection_error = exc
        qqq_rows = self._qqq_nport_rows(reference_date)
        if qqq_rows is None:
            raise ProviderError(
                f"Nasdaq-100 could not select 100 companies for {reference_date}; {selection_error}"
            )
        return self._securities("us_nasdaq100", reference_date, qqq_rows, directory)

    def sp100_current(self, reference_date: date, directory: dict[str, str]) -> list[MarketSecurity]:
        content = self._binary(ISHARES_OEF_HOLDINGS_URL, "OEF current holdings")
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ProviderError("OEF current holdings were not UTF-8 CSV") from exc
        reader = csv.DictReader(line for line in text.splitlines() if line and not line.startswith("#"))
        rows: list[SourceHolding] = []
        for row in reader:
            ticker = str(row.get("Ticker") or "").strip().upper()
            name = str(row.get("Name") or "").strip()
            raw_value = str(row.get("Market Value") or row.get("Weight (%)") or "").replace(",", "").strip()
            try:
                selection_value = Decimal(raw_value)
            except Exception:
                selection_value = None
            if ticker and name and ticker != "-" and "CASH" not in name.upper():
                rows.append((ticker, name, selection_value))
        return self._securities("us_sp100", reference_date, rows, directory)

    def sp100_historical(self, reference_date: date, directory: dict[str, str]) -> list[MarketSecurity]:
        nport_rows = self._oef_nport_rows(reference_date)
        if nport_rows is not None:
            return self._securities("us_sp100", reference_date, nport_rows, directory)

        legacy_accessions = self._oef_legacy_accessions()
        submissions = self.sec.submissions(OEF_TRUST_CIK)
        filings = submissions.get("filings", {}) if isinstance(submissions, dict) else {}
        all_sets = [filings.get("recent", {})] if isinstance(filings, dict) else []
        for entry in filings.get("files", []) if isinstance(filings, dict) else ():
            if not isinstance(entry, dict) or not entry.get("name"):
                continue
            # Archive metadata describes filing dates, not report dates. Select
            # only shards overlapping the fund's normal post-quarter filing window.
            if not archive_covers_legacy_window(entry, reference_date):
                continue
            payload = self._json("GET", f"https://data.sec.gov/submissions/{entry['name']}", "OEF archived submissions",
                                 headers={"User-Agent": self.sec.user_agent, "Accept-Encoding": "gzip, deflate"})
            all_sets.append(payload)
        target = reference_date.isoformat()
        candidates: list[tuple[date, str, str]] = []
        for data in all_sets:
            if not isinstance(data, dict):
                continue
            for form, report_date, accession, document in zip(
                data.get("form", []), data.get("reportDate", []), data.get("accessionNumber", []), data.get("primaryDocument", []), strict=False
            ):
                eligible_date = legacy_report_date(str(report_date), reference_date)
                if (
                    str(form) in {"N-CSR", "N-CSRS", "N-Q"}
                    and eligible_date is not None
                    and str(accession) in legacy_accessions
                    and accession and document
                ):
                    candidates.append((eligible_date, str(accession), str(document)))
        if not candidates:
            raise ProviderError(f"OEF has no SEC holdings filing for {target}")
        for _, accession, document in sorted(candidates, reverse=True):
            html = _decode_filing(self._binary(_filing_url(accession, document), f"OEF holdings {target}"))
            try:
                rows = (
                    extract_oef_nport_holdings(html)
                    if document.lower().endswith(".xml")
                    else [("", name, value) for name, value in extract_oef_holdings(html)]
                )
            except ProviderError:
                continue
            return self._securities("us_sp100", reference_date, rows, directory)
        raise ProviderError(f"OEF filings for {target} did not contain an S&P 100 holdings schedule")
