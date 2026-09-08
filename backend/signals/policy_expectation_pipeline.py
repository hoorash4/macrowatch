"""무료 공식 일간 금리로 시장 내재 정책금리 기대 스프레드를 계산한다.

원자료와 파생값을 함께 보존해 가중치 변경이나 데이터 수정 시 다시 계산할 수
있게 한다. 세 시계열에 실제 관측값이 모두 있는 영업일만 저장하며 휴일 값을
임의로 복제하지 않는다.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone

from common import SupabaseRest, fetch_fred_observations, require_env


SERIES = {
    "treasury_3m_rate": "DGS3MO",
    "treasury_2y_rate": "DGS2",
    "effr_rate": "DFF",
}
NEAR_TERM_WEIGHT = 0.7
CYCLE_WEIGHT = 0.3
UPSERT_BATCH_SIZE = 500


def valid_daily_values(observations: list[dict]) -> dict[str, float]:
    """FRED의 결측치 표기('.')를 제외한 날짜별 실수값을 반환한다."""
    values: dict[str, float] = {}
    for observation in observations:
        period = str(observation.get("date", ""))
        raw_value = observation.get("value")
        try:
            values[period] = float(raw_value)
        except (TypeError, ValueError):
            continue
    return values


def build_rows(series_values: dict[str, dict[str, float]]) -> list[dict]:
    """세 원자료가 모두 존재하는 영업일에만 방향성 합성 스프레드를 만든다."""
    common_dates = set.intersection(*(set(values) for values in series_values.values()))
    updated_at = datetime.now(timezone.utc).isoformat()
    rows = []
    for observation_date in sorted(common_dates):
        treasury_3m = series_values["treasury_3m_rate"][observation_date]
        treasury_2y = series_values["treasury_2y_rate"][observation_date]
        effr = series_values["effr_rate"][observation_date]
        near_term_spread = (treasury_3m - effr) * 100
        cycle_spread = (treasury_2y - effr) * 100
        rows.append({
            "observation_date": observation_date,
            "treasury_3m_rate": round(treasury_3m, 6),
            "treasury_2y_rate": round(treasury_2y, 6),
            "effr_rate": round(effr, 6),
            "near_term_spread_bps": round(near_term_spread, 6),
            "cycle_spread_bps": round(cycle_spread, 6),
            "expectation_spread_bps": round(
                NEAR_TERM_WEIGHT * near_term_spread + CYCLE_WEIGHT * cycle_spread,
                6,
            ),
            "updated_at": updated_at,
        })
    return rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    window = parser.add_mutually_exclusive_group()
    window.add_argument("--start-year", type=int, help="Backfill starting year")
    window.add_argument("--days", type=int, default=21, help="Recent calendar-day refresh window")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    today = date.today()
    start = date(args.start_year, 1, 1) if args.start_year else today - timedelta(days=max(args.days, 7))
    fred_api_key = require_env("FRED_API_KEY")
    series_values = {
        field: valid_daily_values(fetch_fred_observations(
            series_id,
            fred_api_key,
            start=start.isoformat(),
            end=today.isoformat(),
        ))
        for field, series_id in SERIES.items()
    }
    rows = build_rows(series_values)
    if not rows:
        print(f"No complete policy expectation observations from {start} through {today}.")
        return
    database = SupabaseRest()
    for offset in range(0, len(rows), UPSERT_BATCH_SIZE):
        database.upsert(
            "policy_expectation_spreads",
            rows[offset:offset + UPSERT_BATCH_SIZE],
            conflict="observation_date",
        )
    print(f"Upserted {len(rows)} policy expectation observations from {rows[0]['observation_date']} through {rows[-1]['observation_date']}.")


if __name__ == "__main__":
    main()
