from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SourcePolicy:
    name: str
    requests_per_second: float
    durable_payload: bool = False
    checkpointed: bool = True

    @property
    def minimum_interval_seconds(self) -> float:
        return 1.0 / self.requests_per_second


# Conservative application limits. Provider adapters may run slower but must
# never silently raise these values. Raw responses are processed in memory and
# are not durable V2 storage.
SOURCE_POLICIES = {
    "open_dart": SourcePolicy("open_dart", requests_per_second=8.0),
    "sec_edgar": SourcePolicy("sec_edgar", requests_per_second=8.0),
    "kis": SourcePolicy("kis", requests_per_second=5.0),
    "krx": SourcePolicy("krx", requests_per_second=2.0),
}


def source_policy(name: str) -> SourcePolicy:
    try:
        return SOURCE_POLICIES[name]
    except KeyError:
        raise ValueError(f"Unsupported earnings V2 source: {name}") from None

