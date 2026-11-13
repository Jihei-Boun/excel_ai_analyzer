"""사용자 입장 결과 분류(classify_outcome). 모델은 부르지 않는다."""

from __future__ import annotations

import pytest

from tests.benchmark.metrics import classify_outcome


def _outcome(**kw) -> str:
    base = dict(
        crashed=False,
        expect_safe_failure=False,
        ok=False,
        exec_ok=False,
        has_result=True,
        route_observed="analysis_plan",
    )
    base.update(kw)
    return classify_outcome(**base)


def test_crash_wins() -> None:
    assert _outcome(crashed=True, exec_ok=True) == "crash"


def test_right_numbers_are_correct_even_if_plan_check_fails() -> None:
    assert _outcome(ok=False, exec_ok=True) == "correct"


def test_no_golden_result_uses_overall_ok() -> None:
    assert _outcome(exec_ok=None, ok=True) == "correct"
    assert _outcome(exec_ok=None, ok=False) == "silent_wrong"


def test_wrong_without_table_is_honest() -> None:
    assert _outcome(has_result=False) == "honest_failure"


@pytest.mark.parametrize("route", ["legacy_fallback", "pandasai", "retrieval", "unknown"])
def test_wrong_from_unverified_route_is_flagged(route: str) -> None:
    assert _outcome(route_observed=route) == "flagged_wrong"


def test_wrong_from_validated_plan_is_silent() -> None:
    assert _outcome(route_observed="analysis_plan") == "silent_wrong"


def test_expected_safe_failure() -> None:
    assert _outcome(expect_safe_failure=True, ok=True) == "correct"
    assert _outcome(expect_safe_failure=True, ok=False) == "silent_wrong"
