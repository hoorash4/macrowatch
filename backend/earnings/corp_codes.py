"""Parse OpenDART's corporation-code archive into stable stock-code mappings."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import re
from typing import Iterable
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile


@dataclass(frozen=True)
class DartCorporation:
    corp_code: str
    company_name: str
    stock_code: str | None
    modified_on: str | None


def _text(element: ElementTree.Element, name: str) -> str:
    return (element.findtext(name) or "").strip()


def parse_corp_code_archive(content: bytes) -> list[DartCorporation]:
    """Read CORPCODE.xml without extracting untrusted archive paths to disk."""
    try:
        with ZipFile(BytesIO(content)) as archive:
            xml_names = [name for name in archive.namelist() if name.upper().endswith("CORPCODE.XML")]
            if len(xml_names) != 1:
                raise ValueError("OpenDART corp-code archive must contain one CORPCODE.xml file.")
            root = ElementTree.fromstring(archive.read(xml_names[0]))
    except (BadZipFile, ElementTree.ParseError) as error:
        raise ValueError("Invalid OpenDART corp-code archive.") from error

    companies: list[DartCorporation] = []
    seen_corp_codes: set[str] = set()
    for item in root.findall("list"):
        corp_code = _text(item, "corp_code")
        company_name = _text(item, "corp_name")
        raw_stock_code = _text(item, "stock_code")
        if not re.fullmatch(r"\d{8}", corp_code) or not company_name:
            continue
        if corp_code in seen_corp_codes:
            raise ValueError(f"Duplicate OpenDART corp_code: {corp_code}")
        stock_code = raw_stock_code if re.fullmatch(r"\d{6}", raw_stock_code) else None
        modified_on = _text(item, "modify_date") or None
        companies.append(DartCorporation(corp_code, company_name, stock_code, modified_on))
        seen_corp_codes.add(corp_code)
    return companies


def listed_corporations(companies: Iterable[DartCorporation]) -> dict[str, DartCorporation]:
    """Return one listed corporation per six-digit Korean stock code."""
    result: dict[str, DartCorporation] = {}
    for company in companies:
        if company.stock_code is None:
            continue
        previous = result.get(company.stock_code)
        if previous and previous.corp_code != company.corp_code:
            raise ValueError(f"Duplicate listed stock_code: {company.stock_code}")
        result[company.stock_code] = company
    return result
