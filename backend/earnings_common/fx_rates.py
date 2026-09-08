"""Per-run lazy exchange-rate cache."""
from __future__ import annotations
from typing import Callable, Iterator, Mapping
from decimal import Decimal

class _LazyKrwRates(Mapping[str, Decimal]):
    """분기 외화 환율을 실제 사용 시점에 통화별 한 번만 조회한다."""

    def __init__(self, loader: Callable[[str], Decimal]) -> None:
        self._loader = loader
        self._rates: dict[str, Decimal] = {}

    def __getitem__(self, currency: str) -> Decimal:
        currency = currency.upper()
        if currency in self._rates:
            return self._rates[currency]
        rate = self._loader(currency)
        if rate <= 0:
            raise ValueError(f"{currency}/KRW rate must be positive")
        self._rates[currency] = rate
        return rate

    def __iter__(self) -> Iterator[str]:
        return iter(self._rates)

    def __len__(self) -> int:
        return len(self._rates)
