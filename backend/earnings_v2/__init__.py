"""MacroWatch 기업실적 V2 수집 파이프라인.

V1 코드와 어떤 실행 경로도 공유하지 않는다. 외부 공급자 응답은 providers,
재무 의미 변환은 transform, 영속화는 repository, 실행 순서는 pipeline이 맡는다.
"""

from .pipeline import KoreaEarningsV2Pipeline

__all__ = ["KoreaEarningsV2Pipeline"]
