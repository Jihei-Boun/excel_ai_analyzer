"""2단계 구조 벤치마크: 실제 업무 엑셀에서 흔한 표 구조를 합성 데이터로 재현한다.

예산 파일에서 범용 프로필이 틀린 원인(2단 머리글, 소계·총계 행, 표 위 제목 줄,
코드·이름 병합 열, 0~1로 저장한 비율, 영어 열 이름)을 예산이 아닌 분야로 만든다.
정답은 손으로 적지 않고 이 파일의 데이터에서 계산한다.

  python -m tests.benchmark.structural      # xlsx + cases/structural.yaml 다시 쓰기

질문은 특정 단어를 위한 규칙을 시험하려는 것이 아니다. 고칠 때 이 질문에 맞춘
하드코딩을 하지 않는다 (README의 Policy).
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

import yaml
from openpyxl import Workbook
from openpyxl.styles import Alignment

from tests.benchmark import CASES_DIR, DATASETS_DIR

CASES_FILE = CASES_DIR / "structural.yaml"
DOMAIN = "structural"


def _r(value: float) -> float:
    return round(float(value), 6)


# ---------------------------------------------------------------------------
# S1. 2단 머리글: 매출·비용 아래 상반기·하반기·합계
# ---------------------------------------------------------------------------

BRANCHES = [
    # 지점, 지역, 매출(상반기, 하반기), 비용(상반기, 하반기)
    ("서울점", "수도권", (4_200_000, 4_800_000), (2_500_000, 2_700_000)),
    ("강남점", "수도권", (3_900_000, 4_100_000), (2_300_000, 2_600_000)),
    ("인천점", "수도권", (2_100_000, 2_600_000), (1_500_000, 1_700_000)),
    ("부산점", "영남", (3_100_000, 3_300_000), (2_000_000, 2_100_000)),
    ("대구점", "영남", (2_500_000, 2_200_000), (1_600_000, 1_500_000)),
    ("울산점", "영남", (1_800_000, 2_000_000), (1_200_000, 1_300_000)),
    ("광주점", "호남", (1_900_000, 2_100_000), (1_300_000, 1_400_000)),
    ("전주점", "호남", (1_200_000, 1_500_000), (900_000, 1_000_000)),
]


def write_branch_sales(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "지점실적"
    ws["A1"], ws["B1"], ws["C1"], ws["F1"] = "지점", "지역", "매출", "비용"
    ws.merge_cells("A1:A2")
    ws.merge_cells("B1:B2")
    ws.merge_cells("C1:E1")
    ws.merge_cells("F1:H1")
    for col, name in zip("CDEFGH", ["상반기", "하반기", "합계"] * 2):
        ws[f"{col}2"] = name
    for i, (branch, region, sales, cost) in enumerate(BRANCHES, start=3):
        row = [branch, region, sales[0], sales[1], sum(sales), cost[0], cost[1], sum(cost)]
        for j, value in enumerate(row, start=1):
            ws.cell(i, j, value)
    wb.save(path)


def branch_cases() -> list[dict[str, Any]]:
    by_region: dict[str, list[tuple]] = defaultdict(list)
    for item in BRANCHES:
        by_region[item[1]].append(item)
    sales_total = {r: sum(sum(b[2]) for b in items) for r, items in by_region.items()}
    cost_total = {r: sum(sum(b[3]) for b in items) for r, items in by_region.items()}
    regions = list(by_region)
    picked = ("서울점", "부산점", "대구점")
    second_half = [b[2][1] for b in BRANCHES]
    return [
        _case("s1_region_sales_sum", "branch_sales_2h.xlsx", "지역별 매출 합계를 구해줘",
              pairs=sales_total),
        _case("s1_region_transpose", "branch_sales_2h.xlsx",
              "지역별 매출 합계와 비용 합계를 표로 만들고 행과 열을 바꿔줘",
              pairs=sales_total, cells=list(cost_total.values()), labels=regions),
        _case("s1_three_branch_total", "branch_sales_2h.xlsx",
              "서울점, 부산점, 대구점 세 지점의 매출을 모두 더한 값을 구해줘",
              cells=[sum(sum(b[2]) for b in BRANCHES if b[0] in picked)]),
        _case("s1_second_half_mean", "branch_sales_2h.xlsx", "전체 지점의 하반기 매출 평균을 구해줘",
              cells=[_r(sum(second_half) / len(second_half))]),
        _case("s1_branch_halves_add", "branch_sales_2h.xlsx", "지점별로 상반기 매출과 하반기 매출을 더해줘",
              pairs={b[0]: sum(b[2]) for b in BRANCHES}),
        _case("s1_region_cost_ratio", "branch_sales_2h.xlsx", "지역별 매출 합계 대비 비용 합계 비율을 구해줘",
              ratio_pairs={r: _r(cost_total[r] / sales_total[r]) for r in regions}),
        _case("s1_sales_grand_total", "branch_sales_2h.xlsx", "매출 전체 합계를 구해줘",
              cells=[sum(sales_total.values())]),
    ]


# ---------------------------------------------------------------------------
# S2. 부서별 소계 행 + 맨 아래 총계 행
# ---------------------------------------------------------------------------

STAFF = [
    # 부서, 이름, 직급, 기본급, 수당
    ("개발팀", "김민수", "과장", 4_200_000, 300_000),
    ("개발팀", "이서연", "대리", 3_600_000, 250_000),
    ("개발팀", "박준호", "사원", 3_000_000, 200_000),
    ("영업팀", "최지은", "부장", 5_100_000, 450_000),
    ("영업팀", "정우성", "대리", 3_500_000, 280_000),
    ("인사팀", "한예린", "과장", 4_000_000, 320_000),
    ("인사팀", "오세훈", "사원", 2_900_000, 180_000),
    ("인사팀", "윤하늘", "사원", 2_800_000, 170_000),
]


def _depts() -> list[str]:
    return list(dict.fromkeys(s[0] for s in STAFF))


def write_payroll(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "급여"
    ws.append(["부서", "이름", "직급", "기본급", "수당"])
    for dept in _depts():
        members = [s for s in STAFF if s[0] == dept]
        for m in members:
            ws.append(list(m))
        ws.append([dept, "소계", None, sum(m[3] for m in members), sum(m[4] for m in members)])
    ws.append(["총계", None, None, sum(s[3] for s in STAFF), sum(s[4] for s in STAFF)])
    wb.save(path)


def payroll_cases() -> list[dict[str, Any]]:
    depts = _depts()
    base = {d: sum(s[3] for s in STAFF if s[0] == d) for d in depts}
    allow = {d: sum(s[4] for s in STAFF if s[0] == d) for d in depts}
    heads = {d: sum(1 for s in STAFF if s[0] == d) for d in depts}
    total_base = sum(base.values())
    top = max(STAFF, key=lambda s: s[3])
    return [
        _case("s2_base_total", "dept_payroll_subtotal.xlsx", "기본급 전체 합계를 구해줘",
              cells=[total_base], absent_cells=[2 * total_base, 3 * total_base]),
        _case("s2_dept_base_sum", "dept_payroll_subtotal.xlsx", "부서별 기본급 합계를 구해줘",
              pairs=base, absent_labels=["총계"], absent_cells=[2 * v for v in base.values()]),
        _case("s2_allowance_mean", "dept_payroll_subtotal.xlsx", "직원 1인당 평균 수당을 구해줘",
              cells=[_r(sum(s[4] for s in STAFF) / len(STAFF))]),
        _case("s2_top_base_person", "dept_payroll_subtotal.xlsx", "기본급이 가장 높은 직원을 알려줘",
              labels=[top[1]], absent_labels=["소계", "총계"]),
        _case("s2_dept_headcount", "dept_payroll_subtotal.xlsx", "부서별 인원 수를 알려줘",
              pairs=heads, absent_labels=["총계"]),
        _case("s2_dept_transpose", "dept_payroll_subtotal.xlsx",
              "부서별 기본급과 수당 합계를 표로 보여주고 행과 열을 바꿔줘",
              pairs=base, cells=list(allow.values()), labels=depts, absent_labels=["총계"]),
    ]


# ---------------------------------------------------------------------------
# S3. 보고서 양식: 표 위 제목·작성일 줄, 표 아래 비고 줄
# ---------------------------------------------------------------------------

ITEMS = [
    # 품목코드, 품목명, 분류, 수량, 단가
    ("A-101", "볼펜", "문구", 520, 800),
    ("A-102", "노트", "문구", 310, 1_500),
    ("A-103", "형광펜", "문구", 85, 1_300),
    ("B-201", "A4용지", "소모품", 140, 4_500),
    ("B-202", "토너", "소모품", 12, 65_000),
    ("C-301", "USB메모리", "전산", 45, 12_000),
    ("C-302", "마우스", "전산", 58, 9_000),
    ("C-303", "키보드", "전산", 33, 24_000),
]


def write_inventory_form(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "재고현황"
    ws["A1"] = "2024년 3분기 재고 현황"
    ws.merge_cells("A1:F1")
    ws["A1"].alignment = Alignment(horizontal="center")
    ws["A2"] = "작성일: 2024-10-01 / 단위: 개, 원"
    ws.append([])
    ws.append(["품목코드", "품목명", "분류", "수량", "단가", "재고금액"])
    for code, name, cat, qty, price in ITEMS:
        ws.append([code, name, cat, qty, price, qty * price])
    ws.append([])
    ws.append(["※ 재고금액 = 수량 × 단가"])
    wb.save(path)


def inventory_cases() -> list[dict[str, Any]]:
    cats = list(dict.fromkeys(i[2] for i in ITEMS))
    value = {c: sum(i[3] * i[4] for i in ITEMS if i[2] == c) for c in cats}
    price = {c: [i[4] for i in ITEMS if i[2] == c] for c in cats}
    top = max(ITEMS, key=lambda i: i[3] * i[4])
    low = [i[1] for i in ITEMS if i[3] < 100]
    return [
        _case("s3_category_value_sum", "inventory_report_form.xlsx", "분류별 재고금액 합계를 구해줘",
              pairs=value),
        _case("s3_top_value_item", "inventory_report_form.xlsx", "재고금액이 가장 큰 품목을 알려줘",
              labels=[top[1]], max_rows=1),
        _case("s3_qty_total", "inventory_report_form.xlsx", "수량 합계를 구해줘",
              cells=[sum(i[3] for i in ITEMS)]),
        _case("s3_category_price_mean", "inventory_report_form.xlsx", "분류별 평균 단가를 구해줘",
              pairs={c: _r(sum(v) / len(v)) for c, v in price.items()}),
        _case("s3_low_qty_items", "inventory_report_form.xlsx", "수량이 100개 미만인 품목을 보여줘",
              labels=low, row_count=len(low)),
    ]


# ---------------------------------------------------------------------------
# S4. 코드·이름 병합 머리글 + 0~1로 저장한 비율(표시는 %)
# ---------------------------------------------------------------------------

PROJECTS = [
    # 코드, 과제명, 담당부서, 예산, 집행액
    ("P-01", "데이터플랫폼 고도화", "정보화팀", 50_000_000, 32_000_000),
    ("P-02", "보안관제 개선", "정보화팀", 30_000_000, 9_000_000),
    ("P-03", "신입 교육과정", "인사팀", 12_000_000, 10_800_000),
    ("P-04", "조직문화 진단", "인사팀", 8_000_000, 2_000_000),
    ("P-05", "고객만족도 조사", "기획팀", 15_000_000, 7_500_000),
    ("P-06", "중장기 전략 수립", "기획팀", 20_000_000, 4_000_000),
    ("P-07", "홍보영상 제작", "기획팀", 6_000_000, 6_000_000),
]


def write_projects(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "과제"
    ws.append(["과제", None, "담당부서", "예산", "집행액", "집행률"])
    ws.merge_cells("A1:B1")
    for code, name, dept, budget, spent in PROJECTS:
        ws.append([code, name, dept, budget, spent, spent / budget])
        ws.cell(ws.max_row, 6).number_format = "0.0%"
    wb.save(path)


def project_cases() -> list[dict[str, Any]]:
    depts = list(dict.fromkeys(p[2] for p in PROJECTS))
    budget = {d: sum(p[3] for p in PROJECTS if p[2] == d) for d in depts}
    spent = {d: sum(p[4] for p in PROJECTS if p[2] == d) for d in depts}
    over_half = [p[1] for p in PROJECTS if p[4] / p[3] >= 0.5]
    top3 = [p[1] for p in sorted(PROJECTS, key=lambda p: -p[3])[:3]]
    return [
        _case("s4_dept_spend_ratio", "project_code_name.xlsx", "담당부서별 예산 합계 대비 집행액 합계 비율을 구해줘",
              ratio_pairs={d: _r(spent[d] / budget[d]) for d in depts}),
        _case("s4_rate_over_half", "project_code_name.xlsx", "집행률이 50% 이상인 과제를 보여줘",
              labels=over_half, row_count=len(over_half)),
        _case("s4_unspent_by_project", "project_code_name.xlsx", "과제별 미집행액(예산 - 집행액)을 구해줘",
              pairs={p[1]: p[3] - p[4] for p in PROJECTS}),
        _case("s4_top3_budget", "project_code_name.xlsx", "예산이 가장 큰 과제 3개를 보여줘",
              labels=top3, row_count=3),
        _case("s4_dept_project_count", "project_code_name.xlsx", "담당부서별 과제 수를 알려줘",
              pairs={d: sum(1 for p in PROJECTS if p[2] == d) for d in depts}),
    ]


# ---------------------------------------------------------------------------
# S5. 영어 열 이름, 한국어 질문
# ---------------------------------------------------------------------------

STORES = [
    # Region, Store, Revenue, Target, Visitors
    ("North", "N1", 1_250_000, 1_200_000, 500),
    ("North", "N2", 980_000, 1_100_000, 410),
    ("North", "N3", 1_430_000, 1_300_000, 520),
    ("South", "S1", 870_000, 900_000, 300),
    ("South", "S2", 1_120_000, 1_000_000, 448),
    ("West", "W1", 760_000, 800_000, 380),
    ("West", "W2", 1_010_000, 950_000, 505),
]


def write_english_kpi(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "KPI"
    ws.append(["Region", "Store", "Revenue", "Target", "Visitors"])
    for row in STORES:
        ws.append(list(row))
    wb.save(path)


def english_cases() -> list[dict[str, Any]]:
    regions = list(dict.fromkeys(s[0] for s in STORES))
    rev = {r: sum(s[2] for s in STORES if s[0] == r) for r in regions}
    tgt = {r: sum(s[3] for s in STORES if s[0] == r) for r in regions}
    below = [s[1] for s in STORES if s[2] < s[3]]
    top3 = [s[1] for s in sorted(STORES, key=lambda s: -s[2])[:3]]
    return [
        _case("s5_region_revenue", "english_kpi.xlsx", "Region별 Revenue 합계를 구해줘",
              pairs=rev),
        _case("s5_below_target", "english_kpi.xlsx", "Revenue가 Target보다 낮은 Store를 보여줘",
              labels=below, row_count=len(below)),
        _case("s5_region_achievement", "english_kpi.xlsx", "Region별 Target 합계 대비 Revenue 합계 달성률을 구해줘",
              ratio_pairs={r: _r(rev[r] / tgt[r]) for r in regions}),
        _case("s5_revenue_per_visitor", "english_kpi.xlsx", "Store별 방문객 1인당 매출(Revenue ÷ Visitors)을 구해줘",
              pairs={s[1]: _r(s[2] / s[4]) for s in STORES}),
        _case("s5_visitors_mean", "english_kpi.xlsx", "Visitors 평균을 구해줘",
              cells=[_r(sum(s[4] for s in STORES) / len(STORES))]),
        _case("s5_top3_revenue", "english_kpi.xlsx", "Revenue 상위 3개 Store를 알려줘",
              labels=top3, row_count=3),
    ]


# ---------------------------------------------------------------------------

WRITERS: dict[str, Callable[[Path], None]] = {
    "branch_sales_2h.xlsx": write_branch_sales,
    "dept_payroll_subtotal.xlsx": write_payroll,
    "inventory_report_form.xlsx": write_inventory_form,
    "project_code_name.xlsx": write_projects,
    "english_kpi.xlsx": write_english_kpi,
}


def _case(case_id: str, dataset: str, question: str, **result: Any) -> dict[str, Any]:
    return {
        "id": f"structural_{case_id}",
        "domain": DOMAIN,
        "profile": "generic",
        "dataset": dataset,
        "question": question,
        "tags": ["structural"],
        "live_only": True,
        "expected": {"route": "analysis_plan", "expected_result": result},
    }


def build_cases() -> list[dict[str, Any]]:
    return branch_cases() + payroll_cases() + inventory_cases() + project_cases() + english_cases()


def render_cases_yaml() -> str:
    header = "# 생성 파일: python -m tests.benchmark.structural 로 다시 만든다. 손으로 고치지 않는다.\n"
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
