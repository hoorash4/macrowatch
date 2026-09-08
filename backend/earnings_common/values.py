from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal, InvalidOperation
from typing import Any

HUNDRED = Decimal("100")
MAX_SEASONAL_SAMPLES = 10


def decimal_value(value: Any) -> Decimal | None:
    text = "" if value is None else str(value).replace(",", "").replace(" ", "").strip()
    if text in {"", "-", "—", "–"}:
        return None
    if text.startswith("(") and text.endswith(")"):
        text = f"-{text[1:-1]}"
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def profit_margin(profit: Decimal | None, top_line: Decimal | None) -> Decimal | None:
    """개별 기업 이익률. 매출이 0 이하이면 값은 보존하되 비율은 계산하지 않는다."""
    if profit is None or top_line is None or top_line <= 0:
        return None
    return profit / top_line * HUNDRED


def conventional_growth(current: Decimal | None, previous: Decimal | None) -> tuple[Decimal | None, str]:
    if current is None or previous is None:
        return None, "missing_prior"
    if previous == 0:
        return None, "from_zero"
    if previous < 0 < current:
        return None, "black_turn"
    if previous > 0 > current:
        return None, "red_turn"
    # 적자 지속도 방향을 유지한 채 계산한다. -100 -> -70은 +30%,
    # -100 -> -130은 -30%다. 부호 전환만 비율 대신 상태로 남긴다.
    return (current - previous) / abs(previous) * HUNDRED, "normal"


def update_seasonal_window(
    sample_years: Iterable[int],
    sample_values: Iterable[Decimal],
    *,
    year: int,
    value: Decimal | None,
) -> tuple[list[int], list[Decimal]]:
    """같은 연도의 표본을 교체하고 최근 10개만 남긴다.

    정정으로 정상 QoQ가 아니게 된 경우 ``value=None``을 전달하면 해당
    연도 표본이 제거된다. 원본 분기 실적은 이 캐시 정리와 무관하게 보존한다.
    """
    samples = {int(sample_year): sample_value for sample_year, sample_value in zip(sample_years, sample_values)}
    if value is None:
        samples.pop(year, None)
    else:
        samples[year] = value
    retained = sorted(samples.items())[-MAX_SEASONAL_SAMPLES:]
    return [sample_year for sample_year, _ in retained], [sample_value for _, sample_value in retained]
