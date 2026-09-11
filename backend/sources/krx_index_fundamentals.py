"""Validated pykrx reader for KOSPI market-wide PER/PBR."""
from __future__ import annotations

import math
from datetime import date
from typing import Any

import requests
from pykrx import stock

KOSPI_INDEX_TICKER = "1001"
SOURCE = "PYKRX:KRX_INDEX_FUNDAMENTAL/KOSPI1001"
SERIES = {
    "KOSPI_PER": ("PER", "D"),
    "KOSPI_PBR": ("PBR", "D"),
}


class KrxIndexFundamentalError(RuntimeError):
    """Base error for KOSPI valuation collection."""


class KrxNetworkError(KrxIndexFundamentalError):
    """Network/HTTP failure while pykrx talks to KRX."""


class KrxResponseError(KrxIndexFundamentalError):
    """Unexpected or invalid pykrx/KRX response."""


def _getter():
    getter = getattr(stock, "get_index_fundamental_by_date", None)
    if getter is None:
        getter = getattr(stock, "get_index_fundamental", None)
    if getter is None:
        raise KrxResponseError(
            "pykrx KOSPI 지수 fundamental API를 찾을 수 없습니다. pykrx 구조 변경 가능성이 있습니다."
        )
    return getter


def _call_frame(start: date, end: date):
    try:
        return _getter()(start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), KOSPI_INDEX_TICKER)
    except (requests.Timeout, requests.ConnectionError, requests.HTTPError, TimeoutError, ConnectionError) as error:
        raise KrxNetworkError(
            f"pykrx/KRX 네트워크 오류 ({error.__class__.__name__}): {error}"
        ) from error
    except KrxIndexFundamentalError:
        raise
    except Exception as error:
        raise KrxResponseError(
            f"pykrx/KRX 응답 처리 오류 ({error.__class__.__name__}): {error}. 응답 구조 변경 가능성이 있습니다."
        ) from error


def _index_date(value: object) -> date:
    try:
        if hasattr(value, "date"):
            parsed = value.date()
            if isinstance(parsed, date):
                return parsed
        raw = str(value).strip()[:10]
        return date.fromisoformat(raw)
    except Exception as error:
        raise KrxResponseError(
            f"pykrx/KRX 날짜 파싱 실패: {value!r}. 응답 구조 변경 가능성이 있습니다."
        ) from error


def _number(value: object, field: str, observed: date) -> float:
    if value is None:
        raise KrxResponseError(f"KOSPI {field} 값이 null입니다: {observed.isoformat()}")
    text = str(value).strip().replace(",", "")
    if not text or text.lower() in {"nan", "none", "null"}:
        raise KrxResponseError(f"KOSPI {field} 값이 비어 있습니다: {observed.isoformat()}")
    try:
        numeric = float(text)
    except (TypeError, ValueError) as error:
        raise KrxResponseError(
            f"KOSPI {field} 값 파싱 실패: {observed.isoformat()} value={value!r}. 응답 구조 변경 가능성이 있습니다."
        ) from error
    if not math.isfinite(numeric):
        raise KrxResponseError(f"KOSPI {field} 값이 NaN/무한대입니다: {observed.isoformat()}")
    if numeric <= 0:
        raise KrxResponseError(f"KOSPI {field} 값이 비정상입니다: {observed.isoformat()} value={numeric}")
    return numeric


def _parse_frame(frame, *, required_date: date | None = None) -> dict[str, list[dict[str, Any]]]:
    if frame is None or bool(getattr(frame, "empty", True)):
        raise KrxResponseError("pykrx KOSPI PER/PBR 응답이 비어 있습니다.")
    columns = {str(column) for column in getattr(frame, "columns", [])}
    missing = {field for field, _ in SERIES.values()} - columns
    if missing:
        raise KrxResponseError(
            f"pykrx KOSPI PER/PBR 예상 컬럼 누락: {sorted(missing)}. 응답 구조 변경 가능성이 있습니다."
        )

    result = {code: [] for code in SERIES}
    seen_dates: set[date] = set()
    try:
        iterator = frame.iterrows()
    except Exception as error:
        raise KrxResponseError("pykrx KOSPI PER/PBR 행 반복 실패. 응답 구조 변경 가능성이 있습니다.") from error

    for index, row in iterator:
        observed = _index_date(index)
        seen_dates.add(observed)
        for code, (field, frequency) in SERIES.items():
            try:
                raw = row[field]
            except Exception as error:
                raise KrxResponseError(
                    f"pykrx KOSPI {field} 컬럼 읽기 실패. 응답 구조 변경 가능성이 있습니다."
                ) from error
            value = _number(raw, field, observed)
            result[code].append({
                "series_code": code,
                "observation_date": observed.isoformat(),
                "value": value,
                "frequency": frequency,
                "source": SOURCE,
            })

    if required_date is not None and required_date not in seen_dates:
        raise KrxResponseError(f"요청한 KOSPI PER/PBR 날짜가 응답에 없습니다: {required_date.isoformat()}")
    return result


def fetch_krx_kospi_fundamental_rows(start: date, end: date) -> dict[str, list[dict[str, Any]]]:
    if start > end:
        raise ValueError("start must be on or before end")
    return _parse_frame(_call_frame(start, end))


def fetch_krx_kospi_fundamental_day(target: date) -> dict[str, list[dict[str, Any]]]:
    return _parse_frame(_call_frame(target, target), required_date=target)


def is_krx_business_day(target: date) -> bool:
    if target.weekday() >= 5:
        return False
    helper = getattr(stock, "get_nearest_business_day_in_a_week", None)
    if helper is None:
        raise KrxResponseError(
            "pykrx KRX 영업일 판정 API를 찾을 수 없습니다. pykrx 구조 변경 가능성이 있습니다."
        )
    try:
        nearest = str(helper(target.strftime("%Y%m%d"), prev=True) or "").replace("-", "")
    except (requests.Timeout, requests.ConnectionError, requests.HTTPError, TimeoutError, ConnectionError) as error:
        raise KrxNetworkError(
            f"pykrx/KRX 영업일 판정 네트워크 오류 ({error.__class__.__name__}): {error}"
        ) from error
    except Exception as error:
        raise KrxResponseError(
            f"pykrx/KRX 영업일 판정 오류 ({error.__class__.__name__}): {error}. 응답 구조 변경 가능성이 있습니다."
        ) from error
    if len(nearest) != 8 or not nearest.isdigit():
        raise KrxResponseError(
            f"pykrx/KRX 영업일 응답 형식이 올바르지 않습니다: {nearest!r}. 응답 구조 변경 가능성이 있습니다."
        )
    return nearest == target.strftime("%Y%m%d")
