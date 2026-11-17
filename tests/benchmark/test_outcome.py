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


def test_structural_cases_file_matches_generator() -> None:
    from tests.benchmark.structural import CASES_FILE, render_cases_yaml

    assert CASES_FILE.read_text(encoding="utf-8") == render_cases_yaml(), (
        "cases/structural.yaml이 생성기와 다르다. python -m tests.benchmark.structural 로 다시 만든다."
    )


def test_heldout_cases_file_matches_generator() -> None:
    from tests.benchmark.heldout import CASES_FILE, render_cases_yaml

    assert CASES_FILE.read_text(encoding="utf-8") == render_cases_yaml(), (
        "cases/heldout.yaml is stale. Rebuild it with python -m tests.benchmark.heldout."
    )


def test_heldout2_cases_file_matches_generator() -> None:
    from tests.benchmark.heldout2 import CASES_FILE, render_cases_yaml

    assert CASES_FILE.read_text(encoding="utf-8") == render_cases_yaml(), (
        "cases/heldout2.yaml is stale. Rebuild it with python -m tests.benchmark.heldout2."
    )


def test_heldout_runs_only_when_named() -> None:
    from tests.benchmark.schema import load_all_cases

    for domain in ("heldout", "heldout2"):
        assert not [c for c in load_all_cases() if c.domain == domain]
        named = load_all_cases(domains=[domain])
        assert named and all(c.domain == domain for c in named)


def test_cell_checks_ignore_table_shape() -> None:
    import pandas as pd

    from tests.benchmark.evaluate import _eval_cells

    wide = pd.DataFrame({"항목": ["매출"], "A": [1500.0], "B": [0.25]})
    assert _eval_cells({"cells": [1500], "labels": ["A", "매출"], "ratio_cells": [0.25]}, wide)[0]
    assert _eval_cells({"ratio_cells": [0.0025]}, wide)[0]  # 0.25%로 표시된 비율
    assert not _eval_cells({"absent_labels": ["매"]}, wide)[0]
    assert not _eval_cells({"absent_cells": [1500]}, wide)[0]
    assert not _eval_cells({"cells": [1501]}, wide)[0]


def test_empty_label_does_not_match_everything() -> None:
    import pandas as pd

    from tests.benchmark.evaluate import eval_execution
    from tests.benchmark.schema import ExpectedSpec

    garbage = pd.DataFrame({"11": ["", "내부흡수액"], "v": [1.0, 9.0]})
    ok, _, _ = eval_execution(ExpectedSpec(expected_result={"연구수당": 9.0}), garbage)
    assert ok is False


def test_pairs_require_label_and_value_together() -> None:
    import pandas as pd

    from tests.benchmark.evaluate import _eval_cells

    counts = pd.DataFrame({"부서": ["A팀", "B팀"], "수": [1.0, 2.0]})
    assert _eval_cells({"pairs": {"A팀": 1, "B팀": 2}}, counts)[0]
    assert not _eval_cells({"pairs": {"A팀": 2}}, counts)[0]  # 2는 다른 행에 있다
    wide = pd.DataFrame({"항목": ["비율"], "A팀": [25.0]})
    assert _eval_cells({"ratio_pairs": {"A팀": 0.25}}, wide)[0]  # 행·열 바꾼 표의 열
