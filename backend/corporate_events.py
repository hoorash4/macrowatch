from __future__ import annotations

import html
import io
import re
import zipfile
from dataclasses import dataclass
from datetime import date
from typing import Any, Mapping


@dataclass(frozen=True)
class AbsorbedMergerEvent:
    corp_code: str
    receipt_no: str
    received_on: date
    effective_on: date
    report_name: str


def _date_value(value: Any) -> date | None:
    digits = re.sub(r"[^0-9]", "", str(value or ""))
    if len(digits) < 8:
        return None
    try:
        return date(int(digits[:4]), int(digits[4:6]), int(digits[6:8]))
    except ValueError:
        return None


def _company_token(value: Any) -> str:
    # 구조화 API는 한글 회사명 다음 줄에 영문명을 함께 반환하기도 한다.
    text = str(value or "").splitlines()[0]
    text = re.sub(r"\(주\)|㈜|주식회사", "", text)
    return re.sub(r"[^0-9A-Za-z가-힣]", "", text).lower()


def _method_token(value: Any) -> str:
    text = re.sub(r"\(주\)|㈜|주식회사", "", str(value or ""))
    return re.sub(r"\s+", "", text).lower()


def parse_absorbed_merger(
    row: Mapping[str, Any], *, expected_corp_code: str,
) -> AbsorbedMergerEvent | None:
    """OpenDART 회사합병 결정에서 공시회사가 소멸하는 경우만 반환한다."""
    corp_code = str(row.get("corp_code") or "").strip()
    receipt_no = str(row.get("rcept_no") or "").strip()
    if corp_code != expected_corp_code or re.fullmatch(r"\d{14}", receipt_no) is None:
        return None

    company = _company_token(row.get("corp_name"))
    counterparty = _company_token(row.get("mgptncmp_cmpnm"))
    method = _method_token(row.get("mg_mth"))
    effective_on = _date_value(row.get("mgsc_mgdt"))
    received_on = _date_value(receipt_no)
    if not company or not counterparty or effective_on is None or received_on is None:
        return None

    # 공시회사와 상대회사의 방향이 명시된 흡수합병 문장만 인정한다.
    # 단순히 '흡수합병'이라는 단어가 있거나 회사가 존속하는 경우는 제외한다.
    absorbed_patterns = (
        rf"{re.escape(counterparty)}(?:가|이){re.escape(company)}(?:을|를)흡수합병",
        rf"{re.escape(company)}(?:은|는)?피흡수합병",
        rf"{re.escape(company)}(?:은|는)?.{{0,20}}(?:소멸|해산)",
    )
    survivor_patterns = (
        rf"{re.escape(company)}(?:가|이){re.escape(counterparty)}(?:을|를)흡수합병",
        rf"{re.escape(company)}(?:은|는)?.{{0,20}}존속",
    )
    if any(re.search(pattern, method) for pattern in survivor_patterns):
        return None
    if not any(re.search(pattern, method) for pattern in absorbed_patterns):
        return None

    return AbsorbedMergerEvent(
        corp_code=corp_code,
        receipt_no=receipt_no,
        received_on=received_on,
        effective_on=effective_on,
        report_name="회사합병 결정(피흡수합병)",
    )


def parse_absorbed_merger_archive(
    archive_bytes: bytes,
    *,
    expected_corp_code: str,
    corp_name: str,
    receipt_no: str,
) -> AbsorbedMergerEvent | None:
    """구조화 API에서 빠진 구형 합병 공시 원문을 명시 필드만으로 판정한다."""
    try:
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            documents = [archive.read(name) for name in archive.namelist()]
    except (OSError, KeyError, zipfile.BadZipFile):
        return None

    decoded: list[str] = []
    for content in documents:
        for encoding in ("utf-8", "cp949", "euc-kr"):
            try:
                decoded.append(content.decode(encoding))
                break
            except UnicodeDecodeError:
                continue
    text = html.unescape(" ".join(re.sub(r"<[^>]+>", " ", item) for item in decoded))
    text = re.sub(r"\s+", " ", text)
    company = _company_token(corp_name)
    compact = _company_token(text)
    if not company or not any(
        marker + company in compact for marker in ("소멸회사", "소멸법인")
    ):
        return None

    date_match = re.search(
        r"합병기일\D{0,20}(\d{4}\D{0,3}\d{1,2}\D{0,3}\d{1,2})",
        text,
    )
    effective_on = _date_value(date_match.group(1)) if date_match else None
    received_on = _date_value(receipt_no)
    if effective_on is None or received_on is None:
        return None
    return AbsorbedMergerEvent(
        corp_code=expected_corp_code,
        receipt_no=receipt_no,
        received_on=received_on,
        effective_on=effective_on,
        report_name="회사합병 결정(피흡수합병·원문)",
    )
