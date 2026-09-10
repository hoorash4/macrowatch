"""MacroWatch 한국 기업실적 자동수집 파이프라인."""

from . import automatic as _automatic

# Public market contract: KOSPI large-cap 100 / KOSDAQ 50.
# Keep this package-level guard until the legacy in-module target literal is removed.
_automatic.TARGETS["kr_largecap"] = 100
_automatic.TARGETS["kr_kosdaq"] = 50

KoreaEarningsV2AutomaticPipeline = _automatic.KoreaEarningsV2AutomaticPipeline

__all__ = ["KoreaEarningsV2AutomaticPipeline"]
