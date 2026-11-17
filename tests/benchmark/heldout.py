"""Held-out structural benchmark: table layouts kept out of development.

These workbooks use layouts that do not appear in structural.py:
- a three-row header
- group label rows instead of a group column
- a table that starts away from A1, with total rows and a total column
- a wide month-by-column table under a title row

These results were looked at when the LLM table-layout step was judged (PR #6),
so the set is no longer unseen. It stays out of default runs and now serves as a
development check; heldout2.py is the unseen set.

The runner leaves this domain out unless asked for it explicitly:

  python -m tests.benchmark.heldout                          # rewrite xlsx + cases/heldout.yaml
  python -m tests.benchmark.runner --live --domain heldout   # run only at the end
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import yaml
from openpyxl import Workbook

from tests.benchmark import CASES_DIR, DATASETS_DIR

CASES_FILE = CASES_DIR / "heldout.yaml"
DOMAIN = "heldout"


def _r(value: float) -> float:
    return round(float(value), 6)


# ---------------------------------------------------------------------------
# H1. Three-row header: year > quarter > plan/actual
# ---------------------------------------------------------------------------

CENTERS = [
    # center, (plan, actual) for Q1..Q4
    ("북부센터", ((120, 115), (130, 142), (125, 119), (140, 151))),
    ("남부센터", ((100, 96), (110, 104), (105, 117), (115, 109))),
    ("동부센터", ((90, 93), (95, 88), (100, 106), (105, 98))),
    ("서부센터", ((80, 71), (85, 90), (90, 84), (95, 102))),
    ("중앙센터", ((150, 162), (155, 149), (160, 171), (165, 158))),
    ("해안센터", ((60, 58), (65, 70), (70, 66), (75, 81))),
]


def write_quarter_plan(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "분기실적"
    ws["A1"] = "센터"
    ws.merge_cells("A1:A3")
    ws["B1"] = "2024년"
    ws.merge_cells("B1:I1")
    for q in range(4):
        start = 2 + 2 * q
        ws.cell(2, start, f"{q + 1}분기")
        ws.merge_cells(start_row=2, start_column=start, end_row=2, end_column=start + 1)
        ws.cell(3, start, "계획")
        ws.cell(3, start + 1, "실적")
    for i, (center, quarters) in enumerate(CENTERS, start=4):
        ws.cell(i, 1, center)
        for q, (plan, actual) in enumerate(quarters):
            ws.cell(i, 2 + 2 * q, plan)
            ws.cell(i, 3 + 2 * q, actual)
    wb.save(path)


def quarter_cases() -> list[dict[str, Any]]:
    yearly = {c: sum(a for _, a in qs) for c, qs in CENTERS}
    q2_ratio = {c: _r(qs[1][1] / qs[1][0]) for c, qs in CENTERS}
    top_q4 = max(CENTERS, key=lambda c: c[1][3][1])[0]
    return [
        _case("h1_center_yearly_actual", "quarter_plan_3row.xlsx", "센터별 1~4분기 실적을 모두 더한 연간 실적을 구해줘",
              pairs=yearly),
        _case("h1_q3_actual_total", "quarter_plan_3row.xlsx", "3분기 실적 합계를 구해줘",
              cells=[sum(qs[2][1] for _, qs in CENTERS)]),
        _case("h1_q2_achievement", "quarter_plan_3row.xlsx", "센터별 2분기 계획 대비 실적 비율을 구해줘",
              ratio_pairs=q2_ratio),
        _case("h1_q4_top_center", "quarter_plan_3row.xlsx", "4분기 실적이 가장 높은 센터를 알려줘",
              labels=[top_q4], max_rows=1),
    ]


# ---------------------------------------------------------------------------
# H2. Group label rows instead of a group column, blank rows between groups
# ---------------------------------------------------------------------------

GROUPS = [
    ("서울", [("복합기 임대", 3, 1_350_000), ("정수기 관리", 5, 400_000), ("방역 서비스", 2, 260_000)]),
    ("부산", [("복합기 임대", 2, 900_000), ("청소 용역", 4, 2_200_000)]),
    ("대전", [("정수기 관리", 3, 240_000), ("방역 서비스", 1, 130_000), ("경비 용역", 2, 3_100_000)]),
]


def write_grouped_contracts(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "계약"
    ws.append(["계약 항목", "건수", "월 금액"])
    for g, (region, items) in enumerate(GROUPS):
        if g:
            ws.append([])
        ws.append([region])
        for item in items:
            ws.append(list(item))
    wb.save(path)


def grouped_cases() -> list[dict[str, Any]]:
    items = [i for _, group in GROUPS for i in group]
    by_region = {region: sum(i[2] for i in group) for region, group in GROUPS}
    top = max(items, key=lambda i: i[2])
    return [
        _case("h2_amount_total", "grouped_contracts.xlsx", "월 금액 전체 합계를 구해줘",
              cells=[sum(i[2] for i in items)]),
        _case("h2_region_amount", "grouped_contracts.xlsx", "지역별 월 금액 합계를 구해줘",
              pairs=by_region),
        _case("h2_count_total", "grouped_contracts.xlsx", "계약 건수를 모두 더해줘",
              cells=[sum(i[1] for i in items)]),
        _case("h2_top_amount_item", "grouped_contracts.xlsx", "월 금액이 가장 큰 계약 항목을 알려줘",
              labels=[top[0]], max_rows=1),
    ]


# ---------------------------------------------------------------------------
# H3. Table starting at C3, a total row at the bottom and a total column on the right
# ---------------------------------------------------------------------------

WORKERS = [
    # name, weekly handled count (weeks 1-4)
    ("강하늘", (34, 41, 38, 45)),
    ("문채원", (28, 30, 35, 31)),
    ("배수지", (40, 37, 44, 42)),
    ("송민호", (22, 26, 24, 29)),
    ("임윤아", (31, 33, 29, 36)),
]


def write_weekly_offset(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "처리건수"
    header = ["담당자", "1주차", "2주차", "3주차", "4주차", "합계"]
    for j, name in enumerate(header):
        ws.cell(3, 3 + j, name)
    for i, (name, weeks) in enumerate(WORKERS, start=4):
        for j, value in enumerate([name, *weeks, sum(weeks)]):
            ws.cell(i, 3 + j, value)
    total_row = 4 + len(WORKERS)
    totals = [sum(w[1][k] for w in WORKERS) for k in range(4)]
    for j, value in enumerate(["합 계", *totals, sum(totals)]):
        ws.cell(total_row, 3 + j, value)
    wb.save(path)


def weekly_cases() -> list[dict[str, Any]]:
    week3 = [w[1][2] for w in WORKERS]
    per_worker_mean = {w[0]: _r(sum(w[1]) / 4) for w in WORKERS}
    lowest = min(WORKERS, key=lambda w: sum(w[1]))[0]
    return [
        _case("h3_week2_total", "weekly_offset_total.xlsx", "2주차 처리 건수 합계를 구해줘",
              cells=[sum(w[1][1] for w in WORKERS)], absent_cells=[2 * sum(w[1][1] for w in WORKERS)]),
        _case("h3_week3_mean", "weekly_offset_total.xlsx", "담당자 1명당 3주차 평균 처리 건수를 구해줘",
              cells=[_r(sum(week3) / len(week3))]),
        _case("h3_worker_weekly_mean", "weekly_offset_total.xlsx", "담당자별 주 평균 처리 건수를 구해줘",
              pairs=per_worker_mean, absent_labels=["합 계"]),
        _case("h3_lowest_worker", "weekly_offset_total.xlsx", "4주 동안 처리 건수가 가장 적은 담당자를 알려줘",
              labels=[lowest], max_rows=1),
    ]


# ---------------------------------------------------------------------------
# H4. Wide table: one column per month, title row and unit note above the header
# ---------------------------------------------------------------------------

MONTHS = ["1월", "2월", "3월", "4월", "5월", "6월"]
BUILDINGS = [
    ("본관", (18_200, 16_900, 15_400, 13_800, 14_600, 19_300)),
    ("별관", (9_400, 8_800, 8_100, 7_300, 7_900, 10_200)),
    ("연구동", (24_500, 23_100, 22_800, 21_900, 23_400, 27_600)),
    ("기숙사", (6_100, 5_900, 5_200, 4_700, 4_900, 6_800)),
    ("체육관", (3_300, 3_100, 2_900, 2_600, 3_000, 3_900)),
]


def write_monthly_wide(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "전력"
    ws["A1"] = "건물별 월간 전력 사용량"
    ws["A2"] = "(단위: kWh)"
    ws.append(["건물", *MONTHS])
    for name, values in BUILDINGS:
        ws.append([name, *values])
    wb.save(path)


def monthly_cases() -> list[dict[str, Any]]:
    halves = {b[0]: sum(b[1]) for b in BUILDINGS}
    monthly_mean = {b[0]: _r(sum(b[1]) / len(MONTHS)) for b in BUILDINGS}
    top_june = max(BUILDINGS, key=lambda b: b[1][5])[0]
    return [
        _case("h4_march_total", "monthly_power_wide.xlsx", "3월 전력 사용량 합계를 구해줘",
              cells=[sum(b[1][2] for b in BUILDINGS)]),
        _case("h4_building_half_total", "monthly_power_wide.xlsx", "건물별 1월부터 6월까지 사용량 합계를 구해줘",
              pairs=halves),
        _case("h4_building_monthly_mean", "monthly_power_wide.xlsx", "건물별 월평균 사용량을 구해줘",
              pairs=monthly_mean),
        _case("h4_june_top", "monthly_power_wide.xlsx", "6월 사용량이 가장 많은 건물을 알려줘",
              labels=[top_june], max_rows=1),
    ]


# ---------------------------------------------------------------------------

WRITERS: dict[str, Callable[[Path], None]] = {
    "quarter_plan_3row.xlsx": write_quarter_plan,
    "grouped_contracts.xlsx": write_grouped_contracts,
    "weekly_offset_total.xlsx": write_weekly_offset,
    "monthly_power_wide.xlsx": write_monthly_wide,
}


def _case(case_id: str, dataset: str, question: str, **result: Any) -> dict[str, Any]:
    return {
        "id": f"heldout_{case_id}",
        "domain": DOMAIN,
        "profile": "generic",
        "dataset": dataset,
        "question": question,
        "tags": ["heldout"],
        "live_only": True,
        "expected": {"route": "analysis_plan", "expected_result": result},
    }


def build_cases() -> list[dict[str, Any]]:
    return quarter_cases() + grouped_cases() + weekly_cases() + monthly_cases()


def render_cases_yaml() -> str:
    header = "# Generated file: rebuild with python -m tests.benchmark.heldout. Do not edit by hand.\n"
    return header + yaml.safe_dump({"cases": build_cases()}, allow_unicode=True, sort_keys=False)


def write_all(datasets_dir: Path | None = None) -> None:
    root = datasets_dir or DATASETS_DIR
    root.mkdir(parents=True, exist_ok=True)
    for name, writer in WRITERS.items():
        writer(root / name)
    CASES_FILE.write_text(render_cases_yaml(), encoding="utf-8")


if __name__ == "__main__":
    write_all()
    print(f"wrote {len(WRITERS)} datasets and {len(build_cases())} cases to {CASES_FILE}")
