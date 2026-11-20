"""heldout5: expectations recomputed in code must accept the tables the user wrote by hand.

The tables below are copied from the user's answers, with their formatting
("94.81%", "+3,655,000", "한여울 · 56개월", "11/23"). Each must grade as correct,
and changing one value must grade as wrong. No app run is involved.
"""

from __future__ import annotations

import pandas as pd
import pytest

from tests.benchmark.evaluate import _eval_cells
from tests.benchmark.heldout5 import CASES_FILE, build_cases, render_cases_yaml

ITEMS = ["시험 재료", "장비 사용료", "외부 분석비", "기술 세미나", "소프트웨어", "팀 합계"]
BUDGET = pd.DataFrame(
    [
        ["편성 예산", "5,200", "7,800", "3,500", "1,200", "4,300", "22,000"],
        ["집행액", "4,930", "7,420", "3,620", "860", "4,180", "21,010"],
        ["잔액", "270", "380", "-120", "340", "120", "990"],
        ["집행률", "94.81%", "95.13%", "103.43%", "71.67%", "97.21%", "95.50%"],
        ["재배분액", "0", "-120", "+120", "0", "0", "0"],
        ["재배분 후 잔액", "270", "260", "0", "340", "120", "990"],
    ],
    columns=["지표", *ITEMS],
)

SALES = pd.DataFrame(
    [
        ["대리점", "3,516,000", "7,171,000", "+3,655,000", "+103.95%", "45.36%", "0.49%", "신호 변환기", "5,776,000"],
        ["온라인", "4,643,000", "5,977,000", "+1,334,000", "+28.73%", "37.81%", "0.00%", "산업용 허브", "4,185,000"],
        ["직판", "4,164,000", "2,662,000", "-1,502,000", "-36.07%", "16.84%", "1.77%", "환경센서 모듈", "1,560,000"],
        ["전체", "12,323,000", "15,810,000", "+3,487,000", "+28.30%", "100.00%", "0.52%", "신호 변환기", "5,776,000"],
    ],
    columns=["판매 채널", "전반기 순매출", "후반기 순매출", "증감액", "증감률", "후반기 비중",
             "후반기 할인율", "후반기 1위 품목", "1위 순매출"],
)

STAFF = pd.DataFrame(
    [
        ["경영지원팀", "3명", "2명", "1명", "0명", "1명", "27.3개월", "한여울 · 56개월"],
        ["제품개발팀", "4명", "2명", "1명", "1명", "1명", "39.7개월", "차도겸 · 85개월"],
        ["품질관리팀", "2명", "2명", "0명", "0명", "0명", "29.0개월", "송재온 · 46개월"],
        ["사업기획팀", "3명", "2명", "1명", "0명", "1명", "39.0개월", "백해솔 · 76개월"],
        ["전체", "12명", "8명", "3명", "1명", "3명", "34.2개월", ""],
    ],
    columns=["부서", "등록", "재직 정규직", "재직 계약직", "휴직", "재직 1년 미만", "재직 평균 근속", "멘토"],
)

STOCK = pd.DataFrame(
    [
        [1, "센서 커넥터", "가-01", 24, 36, "33.33%", 12, 36, 0],
        [2, "전원 어댑터", "나-02", 59, 78, "24.36%", 19, 78, 0],
        [3, "고정 브래킷", "나-01", 52, 64, "18.75%", 9, 61, 3],
        [4, "방열 패드", "나-01", 54, 64, "15.63%", 0, 54, 10],
        [5, "케이블 클립", "가-02", 49, 50, "2.00%", 0, 49, 1],
        [None, "가-01", None, None, None, None, 12, None, 0],
        [None, "가-02", None, None, None, None, 0, None, 1],
        [None, "나-01", None, None, None, None, 9, None, 13],
        [None, "나-02", None, None, None, None, 19, None, 0],
        [None, "전체", None, None, None, None, 40, None, 14],
    ],
    columns=["순위", "품목", "보관 위치", "기말 재고", "안전 재고", "부족률", "배정", "배정 후 재고", "남은 부족"],
)

SCHEDULE = pd.DataFrame(
    [
        ["설치", "초기 설정 및 통신 연동", "고세온", "11/23", "11/23", "1일", "없음"],
        ["검증", "기준 시편 반복 측정", "오나린", "11/23", "11/25", "3일", "없음"],
        ["검증", "검사 결과 편차 분석", "고세온", "11/25", "11/26", "2일", "없음"],
        ["검증", "운영자 교육", "민태율", "11/26", "11/27", "2일", "없음"],
        ["검증", "최종 인수 검토", "나이현", "11/27", "11/27", "1일", "없음"],
        ["", "고세온", "11/23, 11/25, 11/26", "", "", "3일", "3일"],
        ["", "오나린", "11/23, 11/24, 11/25", "", "", "3일", "3일"],
        ["", "민태율", "11/26, 11/27", "", "", "2일", "2일"],
        ["", "나이현", "11/27", "", "", "1일", "1일"],
    ],
    columns=["단계", "작업명", "담당자", "해당 주 시작", "해당 주 종료", "작업일 수", "충돌 여부"],
)

USER_TABLES = {
    "heldout5_budget_reallocation": BUDGET,
    "heldout5_sales_channel_halves": SALES,
    "heldout5_staff_tenure_mentor": STAFF,
    "heldout5_stock_restock_allocation": STOCK,
    "heldout5_schedule_week_clashes": SCHEDULE,
}


def _expected(case_id: str) -> dict:
    return next(c for c in build_cases() if c["id"] == case_id)["expected"]["expected_result"]


def test_cases_file_matches_generator() -> None:
    assert CASES_FILE.read_text(encoding="utf-8") == render_cases_yaml(), (
        "cases/heldout5.yaml is stale. Rebuild it with python -m tests.benchmark.heldout5."
    )


@pytest.mark.parametrize("case_id", sorted(USER_TABLES))
def test_users_answer_table_is_graded_correct(case_id: str) -> None:
    ok, detail, _ = _eval_cells(_expected(case_id), USER_TABLES[case_id])
    assert ok, detail


def test_budget_transposed_back_is_still_correct() -> None:
    """Items as rows instead of columns is the same table."""
    flipped = BUDGET.set_index("지표").T.reset_index().rename(columns={"index": "항목"})
    assert _eval_cells(_expected("heldout5_budget_reallocation"), flipped)[0]


@pytest.mark.parametrize(
    ("case_id", "row", "col", "wrong"),
    [
        ("heldout5_budget_reallocation", 4, "장비 사용료", "-130"),     # wrong amount moved
        ("heldout5_budget_reallocation", 4, "외부 분석비", "0"),         # nothing moved in
        ("heldout5_sales_channel_halves", 0, "후반기 할인율", "0.50%"),  # discount rate off
        ("heldout5_sales_channel_halves", 2, "후반기 1위 품목", "산업용 허브"),
        ("heldout5_staff_tenure_mentor", 1, "멘토", "강소하 · 66개월"),  # on leave, not a mentor
        ("heldout5_staff_tenure_mentor", 4, "재직 평균 근속", "34.4개월"),
        ("heldout5_stock_restock_allocation", 2, "배정", 12),            # over-allocated
        ("heldout5_schedule_week_clashes", 0, "해당 주 시작", "11/19"),  # not clipped to the week
    ],
)
def test_one_wrong_value_is_graded_wrong(case_id: str, row: int, col: str, wrong: object) -> None:
    table = USER_TABLES[case_id].copy()
    table.loc[row, col] = wrong
    assert not _eval_cells(_expected(case_id), table)[0]


def test_sales_order_is_checked() -> None:
    swapped = SALES.iloc[[1, 0, 2, 3]].reset_index(drop=True)
    assert not _eval_cells(_expected("heldout5_sales_channel_halves"), swapped)[0]


def test_other_teams_and_finished_tasks_must_be_left_out() -> None:
    budget = BUDGET.assign(**{"시설 관리비": ["6,100", "5,960", "140", "97.70%", "0", "140"]})
    assert not _eval_cells(_expected("heldout5_budget_reallocation"), budget)[0]
    extra = pd.DataFrame([["설치", "장비 반입 및 배치", "민태율", "11/18", "11/19", "2일", "없음"]],
                         columns=SCHEDULE.columns)
    schedule = pd.concat([SCHEDULE, extra], ignore_index=True)
    assert not _eval_cells(_expected("heldout5_schedule_week_clashes"), schedule)[0]
