"""Second held-out structural benchmark, made before the fixes it will judge.

The first held-out set (heldout.py) has been looked at, so it now only serves as
a development check. This set replaces it as the unseen check. Its layouts
appear neither in structural.py nor in heldout.py:

- a group column whose labels are vertically merged cells
- group label rows, each group closed by its own subtotal row, then a grand total
- a day-by-column table under a title, with an average row and a total column
- a two-row header over month columns, with amounts stored as text and notes below

Do not look at these results while developing a fix. Run them only at the end.
Nothing in the app may be tuned to these files or questions.

  python -m tests.benchmark.heldout2                          # rewrite xlsx + cases/heldout2.yaml
  python -m tests.benchmark.runner --live --domain heldout2   # run only at the end
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import yaml
from openpyxl import Workbook

from tests.benchmark import CASES_DIR, DATASETS_DIR

CASES_FILE = CASES_DIR / "heldout2.yaml"
DOMAIN = "heldout2"


def _r(value: float) -> float:
    return round(float(value), 6)


# ---------------------------------------------------------------------------
# G1. Group column made of vertically merged cells
# ---------------------------------------------------------------------------

CATEGORIES = [
    ("음료", [("탄산수", 420, 1_200), ("녹차", 310, 1_500), ("두유", 260, 1_800)]),
    ("과자", [("감자칩", 380, 1_700), ("쿠키", 290, 2_300)]),
    ("간편식", [("삼각김밥", 510, 1_300), ("샌드위치", 240, 3_200), ("컵라면", 450, 1_400)]),
]


def write_merged_category(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "판매"
    ws.append(["분류", "상품", "판매수량", "단가"])
    row = 2
    for category, items in CATEGORIES:
        first = row
        for item in items:
            ws.cell(row, 2, item[0])
            ws.cell(row, 3, item[1])
            ws.cell(row, 4, item[2])
            row += 1
        ws.cell(first, 1, category)
        if row - 1 > first:
            ws.merge_cells(start_row=first, start_column=1, end_row=row - 1, end_column=1)
    wb.save(path)


def merged_category_cases() -> list[dict[str, Any]]:
    items = [i for _, group in CATEGORIES for i in group]
    qty_by_cat = {c: sum(i[1] for i in g) for c, g in CATEGORIES}
    sales_by_cat = {c: sum(i[1] * i[2] for i in g) for c, g in CATEGORIES}
    top = max(items, key=lambda i: i[1])
    return [
        _case("g1_category_qty", "merged_category_sales.xlsx", "분류별 판매수량 합계를 구해줘",
              pairs=qty_by_cat),
        _case("g1_category_revenue", "merged_category_sales.xlsx", "분류별 매출액(판매수량×단가) 합계를 구해줘",
              pairs=sales_by_cat),
        _case("g1_qty_total", "merged_category_sales.xlsx", "전체 판매수량을 구해줘",
              cells=[sum(i[1] for i in items)]),
        _case("g1_top_item", "merged_category_sales.xlsx", "판매수량이 가장 많은 상품을 알려줘",
              labels=[top[0]], max_rows=1),
    ]


# ---------------------------------------------------------------------------
# G2. Group label rows, a subtotal row closing each group, a grand total row
# ---------------------------------------------------------------------------

TEAMS = [
    ("영업1팀", [("김도윤", 4_200), ("이서연", 3_800), ("박지후", 5_100)]),
    ("영업2팀", [("최하준", 2_900), ("정예린", 3_300)]),
    ("영업3팀", [("한시우", 4_700), ("오지아", 3_600), ("윤태민", 2_800), ("서유나", 3_900)]),
]


def _label(team: str) -> str:
    """The group label row as written in the sheet; answers use this exact text."""
    return f"[{team}]"


def write_team_subtotals(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "실적"
    ws.append(["성명", "계약금액(천원)"])
    grand = 0
    for team, members in TEAMS:
        ws.append([_label(team)])
        for member in members:
            ws.append(list(member))
        subtotal = sum(m[1] for m in members)
        grand += subtotal
        ws.append([f"{team} 계", subtotal])
    ws.append(["총계", grand])
    wb.save(path)


def team_cases() -> list[dict[str, Any]]:
    members = [m for _, group in TEAMS for m in group]
    total = sum(m[1] for m in members)
    team_mean = {_label(t): _r(sum(m[1] for m in g) / len(g)) for t, g in TEAMS}
    top = max(members, key=lambda m: m[1])
    return [
        _case("g2_total", "team_subtotals.xlsx", "계약금액 전체 합계를 구해줘",
              cells=[total], absent_cells=[2 * total, 3 * total]),
        _case("g2_team_mean", "team_subtotals.xlsx", "팀별 1인당 평균 계약금액을 구해줘",
              pairs=team_mean),
        _case("g2_member_count", "team_subtotals.xlsx", "팀별 인원 수를 구해줘",
              pairs={_label(t): len(g) for t, g in TEAMS}),
        _case("g2_top_member", "team_subtotals.xlsx", "계약금액이 가장 큰 사람을 알려줘",
              labels=[top[0]], max_rows=1),
    ]


# ---------------------------------------------------------------------------
# G3. Day columns under a title row, an average row at the bottom, a total column
# ---------------------------------------------------------------------------

DAYS = ["월", "화", "수", "목", "금"]
STORES = [
    ("광화문점", (210, 185, 196, 204, 260)),
    ("강남점", (340, 310, 298, 325, 402)),
    ("판교점", (150, 162, 171, 158, 149)),
    ("송도점", (95, 102, 88, 110, 131)),
]


def write_daily_visitors(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "방문객"
    ws["A1"] = "11월 둘째 주 매장 방문객"
    ws.append([])
    ws.append(["매장", *DAYS, "주간 합계"])
    for name, counts in STORES:
        ws.append([name, *counts, sum(counts)])
    means = [sum(s[1][k] for s in STORES) / len(STORES) for k in range(len(DAYS))]
    ws.append(["평균", *means, sum(sum(s[1]) for s in STORES) / len(STORES)])
    wb.save(path)


def daily_cases() -> list[dict[str, Any]]:
    tue_thu = {s[0]: sum(s[1][1:4]) for s in STORES}
    fri_total = sum(s[1][4] for s in STORES)
    store_mean = {s[0]: _r(sum(s[1]) / len(DAYS)) for s in STORES}
    lowest_wed = min(STORES, key=lambda s: s[1][2])[0]
    return [
        _case("g3_tue_to_thu", "daily_visitors.xlsx", "매장별 화요일부터 목요일까지 방문객 합계를 구해줘",
              pairs=tue_thu),
        _case("g3_friday_total", "daily_visitors.xlsx", "금요일 전체 방문객 수를 구해줘",
              cells=[fri_total]),
        _case("g3_store_daily_mean", "daily_visitors.xlsx", "매장별 하루 평균 방문객을 구해줘",
              pairs=store_mean, absent_labels=["평균"]),
        _case("g3_wed_lowest", "daily_visitors.xlsx", "수요일 방문객이 가장 적은 매장을 알려줘",
              labels=[lowest_wed], max_rows=1),
    ]


# ---------------------------------------------------------------------------
# G4. Two-row header over month columns, amounts stored as text, notes below
# ---------------------------------------------------------------------------

H2_MONTHS = ["7월", "8월", "9월", "10월", "11월", "12월"]
COSTS = [
    ("임차료", (5_400, 5_400, 5_400, 5_600, 5_600, 5_600)),
    ("전기료", (1_850, 2_120, 1_640, 1_320, 1_480, 1_910)),
    ("통신비", (430, 445, 438, 452, 447, 460)),
    ("소모품비", (720, 380, 910, 540, 660, 1_230)),
]


def write_cost_text(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "운영비"
    ws["A1"] = "항목"
    ws.merge_cells("A1:A2")
    ws["B1"] = "3분기"
    ws.merge_cells("B1:D1")
    ws["E1"] = "4분기"
    ws.merge_cells("E1:G1")
    for j, month in enumerate(H2_MONTHS, start=2):
        ws.cell(2, j, month)
    for i, (item, values) in enumerate(COSTS, start=3):
        ws.cell(i, 1, item)
        for j, value in enumerate(values, start=2):
            ws.cell(i, j, f"{value:,}")
    note_row = 3 + len(COSTS) + 1
    ws.cell(note_row, 1, "※ 단위: 천원, 부가세 별도")
    ws.cell(note_row + 1, 1, "※ 10월부터 임차료 인상 반영")
    wb.save(path)


def cost_cases() -> list[dict[str, Any]]:
    sep_nov = {c[0]: sum(c[1][2:5]) for c in COSTS}
    q4_total = sum(sum(c[1][3:6]) for c in COSTS)
    item_mean = {c[0]: _r(sum(c[1]) / len(H2_MONTHS)) for c in COSTS}
    return [
        _case("g4_sep_to_nov", "cost_text_2row.xlsx", "항목별 9월부터 11월까지 비용 합계를 구해줘",
              pairs=sep_nov),
        _case("g4_q4_total", "cost_text_2row.xlsx", "4분기 운영비 전체 합계를 구해줘",
              cells=[q4_total]),
        _case("g4_item_monthly_mean", "cost_text_2row.xlsx", "항목별 월평균 비용을 구해줘",
              pairs=item_mean),
        _case("g4_august_power", "cost_text_2row.xlsx", "8월 전기료를 알려줘",
              cells=[COSTS[1][1][1]]),
    ]


# ---------------------------------------------------------------------------

WRITERS: dict[str, Callable[[Path], None]] = {
    "merged_category_sales.xlsx": write_merged_category,
    "team_subtotals.xlsx": write_team_subtotals,
    "daily_visitors.xlsx": write_daily_visitors,
    "cost_text_2row.xlsx": write_cost_text,
}


def _case(case_id: str, dataset: str, question: str, **result: Any) -> dict[str, Any]:
    return {
        "id": f"heldout2_{case_id}",
        "domain": DOMAIN,
        "profile": "generic",
        "dataset": dataset,
        "question": question,
        "tags": ["heldout2"],
        "live_only": True,
        "expected": {"route": "analysis_plan", "expected_result": result},
    }


def build_cases() -> list[dict[str, Any]]:
    return merged_category_cases() + team_cases() + daily_cases() + cost_cases()


def render_cases_yaml() -> str:
    header = "# Generated file: rebuild with python -m tests.benchmark.heldout2. Do not edit by hand.\n"
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
