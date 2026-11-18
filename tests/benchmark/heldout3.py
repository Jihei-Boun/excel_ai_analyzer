"""Third held-out structural benchmark, made before the fixes it will judge.

heldout.py and heldout2.py have both been looked at and now serve as development
checks. This set is the unseen check. Its layouts appear in none of
structural.py, heldout.py or heldout2.py:

- a total row directly under the header, above the records
- a numbered log with a mostly empty remarks column and sign-off lines below
- category and subcategory columns, subcategory subtotal rows, a spacer column
- a transposed table: one row per measure, one column per year, unit note at the top right

These results were looked at when PR #9 was judged, so the set is no longer
unseen. It stays out of default runs and now serves as a development check;
heldout4.py is the unseen set.

  python -m tests.benchmark.heldout3                          # rewrite xlsx + cases/heldout3.yaml
  python -m tests.benchmark.runner --live --domain heldout3   # run only at the end
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import yaml
from openpyxl import Workbook

from tests.benchmark import CASES_DIR, DATASETS_DIR

CASES_FILE = CASES_DIR / "heldout3.yaml"
DOMAIN = "heldout3"


def _r(value: float) -> float:
    return round(float(value), 6)


# ---------------------------------------------------------------------------
# K1. Total row directly under the header, records below it
# ---------------------------------------------------------------------------

ROUTES = [
    # route, region, trips, passengers
    ("101번", "북구", 64, 5_820),
    ("102번", "북구", 58, 4_910),
    ("205번", "남구", 72, 7_340),
    ("206번", "남구", 49, 3_260),
    ("207번", "남구", 55, 4_480),
    ("310번", "서구", 61, 6_150),
]


def write_total_on_top(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "노선"
    ws.append(["노선", "구역", "운행횟수", "승객수"])
    ws.append(["합계", None, sum(r[2] for r in ROUTES), sum(r[3] for r in ROUTES)])
    for route in ROUTES:
        ws.append(list(route))
    wb.save(path)


def total_on_top_cases() -> list[dict[str, Any]]:
    total = sum(r[3] for r in ROUTES)
    by_region: dict[str, int] = {}
    for r in ROUTES:
        by_region[r[1]] = by_region.get(r[1], 0) + r[3]
    per_trip = {r[0]: _r(r[3] / r[2]) for r in ROUTES}
    top = max(ROUTES, key=lambda r: r[3])[0]
    return [
        _case("k1_passenger_total", "total_on_top.xlsx", "전체 승객수를 구해줘",
              cells=[total], absent_cells=[2 * total]),
        _case("k1_region_passengers", "total_on_top.xlsx", "구역별 승객수 합계를 구해줘",
              pairs=by_region),
        _case("k1_per_trip", "total_on_top.xlsx", "노선별 운행 1회당 승객수를 구해줘",
              ratio_pairs=per_trip, max_rows=len(ROUTES)),
        _case("k1_top_route", "total_on_top.xlsx", "승객수가 가장 많은 노선을 알려줘",
              labels=[top], max_rows=1),
    ]


# ---------------------------------------------------------------------------
# K2. Numbered log, mostly empty remarks column, sign-off lines under the table
# ---------------------------------------------------------------------------

REPAIRS = [
    # no, date, equipment, cost, remark
    (1, "2024-03-04", "냉각기", 380_000, ""),
    (2, "2024-03-11", "컨베이어", 1_250_000, "부품 교체"),
    (3, "2024-03-15", "냉각기", 210_000, ""),
    (4, "2024-03-22", "포장기", 640_000, ""),
    (5, "2024-03-27", "컨베이어", 470_000, "재점검 필요"),
    (6, "2024-03-29", "포장기", 95_000, ""),
]


def write_repair_log(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "수리내역"
    ws.append(["No.", "수리일", "설비", "수리비", "비고"])
    for row in REPAIRS:
        ws.append([row[0], row[1], row[2], row[3], row[4] or None])
    ws.append([])
    ws.append(["작성: 설비관리팀"])
    ws.append(["확인: 공장장"])
    wb.save(path)


def repair_cases() -> list[dict[str, Any]]:
    by_equipment: dict[str, int] = {}
    count: dict[str, int] = {}
    for r in REPAIRS:
        by_equipment[r[2]] = by_equipment.get(r[2], 0) + r[3]
        count[r[2]] = count.get(r[2], 0) + 1
    return [
        _case("k2_cost_total", "repair_log.xlsx", "3월 수리비 총액을 구해줘",
              cells=[sum(r[3] for r in REPAIRS)]),
        _case("k2_equipment_cost", "repair_log.xlsx", "설비별 수리비 합계를 구해줘",
              pairs=by_equipment),
        _case("k2_equipment_count", "repair_log.xlsx", "설비별 수리 건수를 구해줘",
              pairs=count),
        _case("k2_mean_cost", "repair_log.xlsx", "수리 1건당 평균 수리비를 구해줘",
              cells=[_r(sum(r[3] for r in REPAIRS) / len(REPAIRS))]),
    ]


# ---------------------------------------------------------------------------
# K3. Category and subcategory columns, subcategory subtotal rows, a spacer column
# ---------------------------------------------------------------------------

STOCK = [
    # category, subcategory, item, quantity
    ("식품", "냉장", [("우유", 120), ("요거트", 85)]),
    ("식품", "상온", [("라면", 300), ("통조림", 140), ("과자", 210)]),
    ("생활", "세제", [("주방세제", 90), ("세탁세제", 60)]),
    ("생활", "위생", [("휴지", 250), ("물티슈", 180)]),
]


def write_stock_subtotals(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "재고"
    ws.append(["대분류", "중분류", None, "품목", "수량"])
    for category, sub, items in STOCK:
        for name, qty in items:
            ws.append([category, sub, None, name, qty])
        ws.append([category, f"{sub} 소계", None, None, sum(q for _, q in items)])
    wb.save(path)


def stock_cases() -> list[dict[str, Any]]:
    total = sum(q for _, _, items in STOCK for _, q in items)
    by_category: dict[str, int] = {}
    for category, _, items in STOCK:
        by_category[category] = by_category.get(category, 0) + sum(q for _, q in items)
    by_sub = {sub: sum(q for _, q in items) for _, sub, items in STOCK}
    lowest = min((i for _, _, items in STOCK for i in items), key=lambda i: i[1])[0]
    return [
        _case("k3_total", "stock_subtotals.xlsx", "전체 재고 수량을 구해줘",
              cells=[total], absent_cells=[2 * total]),
        _case("k3_category", "stock_subtotals.xlsx", "대분류별 재고 수량 합계를 구해줘",
              pairs=by_category),
        _case("k3_subcategory", "stock_subtotals.xlsx", "중분류별 재고 수량을 구해줘",
              pairs=by_sub),
        _case("k3_lowest_item", "stock_subtotals.xlsx", "재고가 가장 적은 품목을 알려줘",
              labels=[lowest], max_rows=1),
    ]


# ---------------------------------------------------------------------------
# K4. Transposed: one row per measure, one column per year, unit note top right
# ---------------------------------------------------------------------------

YEARS = ["2021년", "2022년", "2023년", "2024년"]
MEASURES = [
    ("매출", (820, 905, 980, 1_064)),
    ("원가", (510, 560, 615, 650)),
    ("판관비", (190, 205, 228, 241)),
]


def write_transposed_years(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "손익"
    ws["A1"] = "연도별 손익 요약"
    ws["E1"] = "(단위: 백만원)"
    ws.append([])
    ws.append(["구분", *YEARS])
    for name, values in MEASURES:
        ws.append([name, *values])
    wb.save(path)


def transposed_cases() -> list[dict[str, Any]]:
    sales = MEASURES[0][1]
    profit = [s - c - g for s, c, g in zip(MEASURES[0][1], MEASURES[1][1], MEASURES[2][1])]
    return [
        _case("k4_sales_total", "transposed_years.xlsx", "4년 동안 매출 합계를 구해줘",
              cells=[sum(sales)]),
        _case("k4_cost_2023", "transposed_years.xlsx", "2023년 원가를 알려줘",
              cells=[MEASURES[1][1][2]]),
        _case("k4_measure_mean", "transposed_years.xlsx", "구분별 연평균 금액을 구해줘",
              pairs={m[0]: _r(sum(m[1]) / len(YEARS)) for m in MEASURES}),
        _case("k4_profit_2024", "transposed_years.xlsx", "2024년 영업이익(매출-원가-판관비)을 구해줘",
              cells=[profit[3]]),
    ]


# ---------------------------------------------------------------------------

WRITERS: dict[str, Callable[[Path], None]] = {
    "total_on_top.xlsx": write_total_on_top,
    "repair_log.xlsx": write_repair_log,
    "stock_subtotals.xlsx": write_stock_subtotals,
    "transposed_years.xlsx": write_transposed_years,
}


def _case(case_id: str, dataset: str, question: str, **result: Any) -> dict[str, Any]:
    return {
        "id": f"heldout3_{case_id}",
        "domain": DOMAIN,
        "profile": "generic",
        "dataset": dataset,
        "question": question,
        "tags": ["heldout3"],
        "live_only": True,
        "expected": {"route": "analysis_plan", "expected_result": result},
    }


def build_cases() -> list[dict[str, Any]]:
    return total_on_top_cases() + repair_cases() + stock_cases() + transposed_cases()


def render_cases_yaml() -> str:
    header = "# Generated file: rebuild with python -m tests.benchmark.heldout3. Do not edit by hand.\n"
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
