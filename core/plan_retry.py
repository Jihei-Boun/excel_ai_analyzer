"""LLM 계획→실행→검증 재시도 루프 공통 헬퍼."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Generic, TypeVar

T = TypeVar("T")


@dataclass
class RetryAttempt(Generic[T]):
    """한 번의 계획 시도 결과."""

    ok: bool
    value: T | None = None
    errors: list[str] = field(default_factory=list)


def run_plan_retries(
    *,
    max_retries: int,
    attempt: Callable[[int, list[str]], RetryAttempt[T]],
) -> RetryAttempt[T]:
    """previous_errors를 넘기며 attempt를 반복한다.

    성공(ok=True)이면 즉시 반환하고, 소진 시 마지막 시도를 반환한다.
    attempt(attempt_index, previous_errors) → RetryAttempt
    """
    previous_errors: list[str] = []
    last: RetryAttempt[T] = RetryAttempt(ok=False)
    rounds = max(0, int(max_retries)) + 1
    for attempt_index in range(rounds):
        last = attempt(attempt_index, previous_errors)
        if last.ok:
            return last
        if last.errors:
            previous_errors = list(last.errors)
    return last
