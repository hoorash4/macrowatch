from __future__ import annotations

from contextlib import contextmanager
import signal
import threading
from typing import Any, Iterator

from .http import ExecutionDeadlineExceeded


@contextmanager
def execution_deadline(seconds: int) -> Iterator[None]:
    """외부 강제 종료 전에 예외를 내 파이프라인이 실패 상태를 기록하게 한다."""
    available = all(hasattr(signal, name) for name in ("SIGALRM", "setitimer", "ITIMER_REAL"))
    if not available or threading.current_thread() is not threading.main_thread():
        yield
        return
    previous_handler = signal.getsignal(signal.SIGALRM)

    def expire(_signum: int, _frame: Any) -> None:
        raise ExecutionDeadlineExceeded(
            f"earnings process exceeded {seconds}-second application deadline"
        )

    signal.signal(signal.SIGALRM, expire)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
