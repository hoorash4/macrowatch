"""Calendar-quarter boundaries shared by Korean collection policies."""
from datetime import date

def quarter_end(year: int, quarter: int) -> date:
    return date(year, quarter * 3, 31 if quarter in {1, 4} else 30)

def quarter_start(year: int, quarter: int) -> date:
    return date(year, (quarter - 1) * 3 + 1, 1)

def quarter_resolution_end(year: int, quarter: int) -> date:
    """해당 분기 실적이 통상 확정되는 시점까지 최종 상폐공시를 찾는다."""
    if quarter == 1:
        return date(year, 5, 15)
    if quarter == 2:
        return date(year, 8, 14)
    if quarter == 3:
        return date(year, 11, 14)
    return date(year + 1, 3, 31)

def previous_period(year: int, quarter: int) -> tuple[int, int]:
    return (year - 1, 4) if quarter == 1 else (year, quarter - 1)

def latest_completed_quarter(today: date) -> tuple[int, int]:
    current_quarter = (today.month - 1) // 3 + 1
    return previous_period(today.year, current_quarter)
