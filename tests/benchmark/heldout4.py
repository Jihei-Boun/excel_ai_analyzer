"""Fourth held-out benchmark, made before the plan-check audit it will judge.

heldout.py, heldout2.py and heldout3.py have all been looked at and now serve as
development checks. This set is the unseen check for changes to the plan
validator. The tables are plain, so the questions carry the difficulty:

- values computed from two aggregates (a per-unit figure, an achievement rate)
- a range of columns that includes the middle ones
- arithmetic between columns of one row (a product, a difference)
- questions that name some values of a column and must keep only those
- questions that name nothing and must use every row

Some of these questions need the current checks and some may be blocked by them;
the set is meant to show both.

Do not look at these results while developing a fix. Run them only at the end.
Nothing in the app may be tuned to these files or questions.

  python -m tests.benchmark.heldout4                          # rewrite xlsx + cases/heldout4.yaml
  python -m tests.benchmark.runner --live --domain heldout4   # run only at the end
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import yaml
from openpyxl import Workbook

from tests.benchmark import CASES_DIR, DATASETS_DIR

CASES_FILE = CASES_DIR / "heldout4.yaml"
DOMAIN = "heldout4"


def _r(value: float) -> float:
    return round(float(value), 6)


# ---------------------------------------------------------------------------
# L1. Long format: one row per class and month, subjects repeat
# ---------------------------------------------------------------------------

CLASSES = [
    # class, subject, month, students, fees (won)
    ("A반", "수학", "3월", 18, 3_240_000),
    ("B반", "수학", "3월", 14, 2_380_000),
    ("C반", "영어", "3월", 21, 3_570_000),
    ("D반", "과학", "3월", 9, 1_710_000),
    ("A반", "수학", "4월", 20, 3_600_000),
    ("B반", "수학", "4월", 13, 2_210_000),
    ("C반", "영어", "4월", 19, 3_230_000),
    ("D반", "과학", "4월", 11, 2_090_000),
    ("E반", "영어", "4월", 8, 1_440_000),
]


def write_class_fees(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "수강"
    ws.append(["반", "과목", "월", "수강생수", "수강료"])
    for row in CLASSES:
        ws.append(list(row))
    wb.save(path)


def class_cases() -> list[dict[str, Any]]:
    students: dict[str, int] = {}
    fees: dict[str, int] = {}
    by_month: dict[str, int] = {}
    for _, subject, month, n, fee in CLASSES:
        students[subject] = students.get(subject, 0) + n
        fees[subject] = fees.get(subject, 0) + fee
        by_month[month] = by_month.get(month, 0) + fee
    per_student = {s: _r(fees[s] / students[s]) for s in students}
    return [
        _case("l1_fee_per_student", "class_fees.xlsx", "과목별 수강생 1인당 수강료를 구해줘",
              pairs=per_student, max_rows=len(students)),
        _case("l1_math_english_fees", "class_fees.xlsx", "수학과 영어 수강료를 합치면 얼마야?",
              cells=[fees["수학"] + fees["영어"]], absent_cells=[sum(fees.values())]),
        _case("l1_science_students", "class_fees.xlsx", "과학 수강생 수 합계를 구해줘",
              cells=[students["과학"]], max_rows=1),
        _case("l1_month_fees", "class_fees.xlsx", "월별 수강료 합계를 구해줘",
              pairs=by_month),
        _case("l1_total_students", "class_fees.xlsx", "전체 수강생 수를 구해줘",
              cells=[sum(students.values())]),
    ]


# ---------------------------------------------------------------------------
# L2. Quarter columns plus a yearly target column
# ---------------------------------------------------------------------------

QUARTERS = ["1분기", "2분기", "3분기", "4분기"]
BRANCHES = [
    # branch, quarterly sales, yearly target
    ("수원지점", (1_420, 1_515, 1_380, 1_690), 6_200),
    ("천안지점", (980, 1_040, 1_115, 1_060), 4_000),
    ("청주지점", (760, 705, 830, 910), 3_400),
    ("원주지점", (540, 610, 585, 640), 2_500),
]


def write_branch_quarters(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "분기실적"
    ws.append(["지점", *QUARTERS, "연간목표"])
    for name, sales, target in BRANCHES:
        ws.append([name, *sales, target])
    wb.save(path)


def branch_cases() -> list[dict[str, Any]]:
    q2_q4 = {b[0]: sum(b[1][1:4]) for b in BRANCHES}
    first_half = {b[0]: sum(b[1][0:2]) for b in BRANCHES}
    achieved = {b[0]: _r(sum(b[1]) / b[2]) for b in BRANCHES}
    q3_total = sum(b[1][2] for b in BRANCHES)
    top_q4 = max(BRANCHES, key=lambda b: b[1][3])[0]
    return [
        _case("l2_q2_to_q4", "branch_quarters.xlsx", "지점별 2분기부터 4분기까지 매출 합계를 구해줘",
              pairs=q2_q4),
        _case("l2_first_half", "branch_quarters.xlsx", "지점별 상반기 매출을 구해줘",
              pairs=first_half),
        _case("l2_target_rate", "branch_quarters.xlsx", "지점별 연간목표 대비 연간 매출 달성률을 구해줘",
              ratio_pairs=achieved, max_rows=len(BRANCHES)),
        _case("l2_q3_total", "branch_quarters.xlsx", "3분기 전체 매출을 구해줘",
              cells=[q3_total]),
        _case("l2_top_q4", "branch_quarters.xlsx", "4분기 매출이 가장 높은 지점을 알려줘",
              labels=[top_q4], max_rows=1),
    ]


# ---------------------------------------------------------------------------
# L3. Flat product table: quantity, unit cost, unit price
# ---------------------------------------------------------------------------

PRODUCTS = [
    # product, produced, unit cost, unit price
    ("텀블러", 1_200, 4_300, 9_900),
    ("머그컵", 2_500, 2_100, 5_500),
    ("보온병", 800, 7_800, 15_900),
    ("물병", 3_100, 1_400, 3_900),
    ("도시락통", 950, 5_200, 11_500),
]


def write_product_costs(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "원가"
    ws.append(["제품", "생산량", "단위원가", "판매가"])
    for row in PRODUCTS:
        ws.append(list(row))
    wb.save(path)


def product_cases() -> list[dict[str, Any]]:
    total_cost = {p[0]: p[1] * p[2] for p in PRODUCTS}
    margin = {p[0]: p[3] - p[2] for p in PRODUCTS}
    priciest = max(PRODUCTS, key=lambda p: p[3])[0]
    return [
        _case("l3_total_cost", "product_costs.xlsx", "제품별 총원가(생산량×단위원가)를 구해줘",
              pairs=total_cost),
        _case("l3_unit_margin", "product_costs.xlsx", "제품별 개당 이익(판매가-단위원가)을 구해줘",
              pairs=margin),
        _case("l3_all_cost", "product_costs.xlsx", "전 제품의 총원가 합계를 구해줘",
              cells=[sum(total_cost.values())]),
        _case("l3_priciest", "product_costs.xlsx", "판매가가 가장 높은 제품을 알려줘",
              labels=[priciest], max_rows=1),
    ]


# ---------------------------------------------------------------------------
# L4. Long format with a kind column: one row per department and kind
# ---------------------------------------------------------------------------

DEPTS = [
    # department, planned, spent
    ("기획부", 48_000, 41_300),
    ("영업부", 92_000, 87_650),
    ("생산부", 135_000, 128_900),
    ("총무부", 37_000, 30_150),
]


def write_dept_kinds(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "부서비용"
    ws.append(["부서", "구분", "금액"])
    for dept, planned, spent in DEPTS:
        ws.append([dept, "계획", planned])
        ws.append([dept, "집행", spent])
    wb.save(path)


def dept_cases() -> list[dict[str, Any]]:
    spent = {d[0]: d[2] for d in DEPTS}
    sales_dept = next(d for d in DEPTS if d[0] == "영업부")
    remaining = {d[0]: d[1] - d[2] for d in DEPTS}
    return [
        _case("l4_spent_by_dept", "dept_kinds.xlsx", "부서별 집행 금액을 구해줘",
              pairs=spent, max_rows=len(DEPTS)),
        _case("l4_sales_rate", "dept_kinds.xlsx", "영업부의 계획 대비 집행률을 구해줘",
              ratio_cells=[_r(sales_dept[2] / sales_dept[1])]),
        _case("l4_planned_total", "dept_kinds.xlsx", "전체 계획 금액 합계를 구해줘",
              cells=[sum(d[1] for d in DEPTS)], absent_cells=[sum(d[1] + d[2] for d in DEPTS)]),
        _case("l4_remaining", "dept_kinds.xlsx", "부서별로 계획에서 집행을 뺀 잔액을 구해줘",
              pairs=remaining),
    ]


# ---------------------------------------------------------------------------

WRITERS: dict[str, Callable[[Path], None]] = {
    "class_fees.xlsx": write_class_fees,
    "branch_quarters.xlsx": write_branch_quarters,
    "product_costs.xlsx": write_product_costs,
    "dept_kinds.xlsx": write_dept_kinds,
}


def _case(case_id: str, dataset: str, question: str, **result: Any) -> dict[str, Any]:
    return {
        "id": f"heldout4_{case_id}",
        "domain": DOMAIN,
        "profile": "generic",
        "dataset": dataset,
        "question": question,
        "tags": ["heldout4"],
        "live_only": True,
        "expected": {"route": "analysis_plan", "expected_result": result},
    }


def build_cases() -> list[dict[str, Any]]:
    return class_cases() + branch_cases() + product_cases() + dept_cases()


def render_cases_yaml() -> str:
    header = "# Generated file: rebuild with python -m tests.benchmark.heldout4. Do not edit by hand.\n"
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
