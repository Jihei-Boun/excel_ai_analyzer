"""Fifth held-out benchmark: tables made by another AI, questions written by the user.

Nothing here was shaped by the app. The user asked a separate assistant for five
office tables (budget, sales ledger, staff list, stock, schedule), then wrote one
question per table in their own words, with the method and the expected table.
The expected values below are recomputed from the workbook in code, and the
tests check them against the tables the user wrote by hand.

Each question asks for a new table built in several steps (reallocation, date
ranges, weekday counts, ordered allocation, a transpose). The current engine has
none of these, so the first thing this set shows is whether the app says so or
returns a plausible wrong table.

Do not run the app on this set while developing a fix. Run it only at the end.
Nothing in the app may be tuned to these files or questions.

  python -m tests.benchmark.heldout5                          # rewrite xlsx + cases/heldout5.yaml
  python -m tests.benchmark.runner --live --domain heldout5   # run only at the end
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

import openpyxl
import yaml

from tests.benchmark import CASES_DIR, DATASETS_DIR

BENCHMARK_ROOT = Path(__file__).resolve().parent
SOURCE = BENCHMARK_ROOT / "sources" / "heldout5_office_tables.xlsx"
CASES_FILE = CASES_DIR / "heldout5.yaml"
DOMAIN = "heldout5"
BASE_DATE = dt.date(2026, 11, 20)

SHEETS = {
    "예산": "office_budget.xlsx",
    "매출": "office_sales.xlsx",
    "인사": "office_staff.xlsx",
    "재고": "office_stock.xlsx",
    "일정": "office_schedule.xlsx",
}


def _sheet_rows(sheet: str) -> list[tuple[Any, ...]]:
    wb = openpyxl.load_workbook(SOURCE, data_only=True)
    return list(wb[sheet].iter_rows(values_only=True))


def _writer(sheet: str) -> Callable[[Path], None]:
    def write(path: Path) -> None:
        wb = openpyxl.load_workbook(SOURCE)
        for name in list(wb.sheetnames):
            if name != sheet:
                del wb[name]
        wb.save(path)

    return write


WRITERS: dict[str, Callable[[Path], None]] = {file: _writer(sheet) for sheet, file in SHEETS.items()}


def _ratio(value: float) -> dict[str, float]:
    return {"ratio": round(value, 9)}


def _date(value: dt.date) -> dict[str, str]:
    return {"date": value.isoformat()}


# ---------------------------------------------------------------------------
# 예산: one team's items as columns, metrics as rows, with a reallocation
# ---------------------------------------------------------------------------

BUDGET_QUESTION = (
    "연구개발팀의 지출 항목과 팀 합계를 열에, 편성 예산·집행액·잔액·집행률·재배분액·재배분 후 잔액을 행에 놓아줘. "
    "예산을 초과한 항목이 있으면 같은 팀에서 잔액이 가장 큰 항목에서 초과분만큼 예산을 옮겨줘. "
    "지원받는 항목의 재배분액은 양수, 지원하는 항목은 음수로 표시해. "
    "기존 소계 행은 집계에서 제외하고, 부서별 팀의 집행률은 팀 집행액 합계 ÷ 팀 편성 예산 합계로 다시 계산해. "
    "금액 단위는 천원으로 표시하고, 재배분 전후 팀 전체 편성 예산이 같은지도 확인해줘"
)


def budget_case() -> dict[str, Any]:
    items = [r for r in _sheet_rows("예산") if r[0] == "연구개발팀" and r[1] and isinstance(r[2], (int, float))]
    others = [r[1] for r in _sheet_rows("예산") if r[0] not in (None, "연구개발팀") and r[1]
              and isinstance(r[2], (int, float))]
    balance = {r[1]: r[2] - r[3] for r in items}
    moves = {r[1]: 0 for r in items}
    for over in [r for r in items if r[3] > r[2]]:
        need = over[3] - over[2]
        donor = max((r for r in items if r[3] <= r[2]), key=lambda r: balance[r[1]])
        moves[over[1]] += need
        moves[donor[1]] -= need
    rows: dict[str, list[Any]] = {}
    for name, budget, spent, *_ in [(r[1], r[2], r[3]) for r in items]:
        rows[name] = [budget, spent, budget - spent, _ratio(spent / budget), moves[name],
                      budget + moves[name] - spent]
    budget_total = sum(r[2] for r in items)
    spent_total = sum(r[3] for r in items)
    rows["팀 합계|합계|전체|연구개발팀 합계|연구개발팀"] = [
        budget_total, spent_total, budget_total - spent_total, _ratio(spent_total / budget_total),
        0, budget_total - spent_total,
    ]
    return _case("budget_reallocation", "office_budget.xlsx", BUDGET_QUESTION,
                 rows=rows, absent_labels=others)


# ---------------------------------------------------------------------------
# 매출: two periods by channel, top item per channel, total row
# ---------------------------------------------------------------------------

SALES_QUESTION = (
    "2026년 11월 2~10일을 전반기, 11~19일을 후반기로 나눠 판매 채널별 비교표를 만들어줘. "
    "채널별로 전반기 순매출, 후반기 순매출, 증감액, 증감률, 후반기 순매출 비중, 후반기 할인율, "
    "후반기 순매출 1위 품목과 그 금액을 보여줘. 순매출 비중의 분모는 모든 채널의 후반기 순매출 합계로, "
    "할인율은 후반기 할인액 합계 ÷ 후반기 공급가액 합계로 계산해. 1위 품목은 거래 한 건의 금액이 아니라 "
    "품목별 순매출 합계로 판단하고, 표는 채널별 증감률이 높은 순서로 정렬해줘. "
    "마지막에 전체 행을 추가하고 전체 증감률과 할인율은 합계 금액으로 다시 계산해줘."
)


def sales_case() -> dict[str, Any]:
    deals = [r for r in _sheet_rows("매출") if isinstance(r[0], dt.datetime)]
    first: dict[str, int] = defaultdict(int)
    second: dict[str, int] = defaultdict(int)
    disc: dict[str, int] = defaultdict(int)
    supply: dict[str, int] = defaultdict(int)
    items: dict[tuple[str, str], int] = defaultdict(int)
    for day, _, channel, item, _, _, sup, discount, net in deals:
        if day.day <= 10:
            first[channel] += net
        else:
            second[channel] += net
            disc[channel] += discount
            supply[channel] += sup
            items[(channel, item)] += net
    second_total = sum(second.values())
    rows: dict[str, list[Any]] = {}
    growth: dict[str, float] = {}
    for channel in first:
        top_item, top_value = max(((i, v) for (c, i), v in items.items() if c == channel), key=lambda x: x[1])
        growth[channel] = (second[channel] - first[channel]) / first[channel]
        rows[channel] = [
            first[channel], second[channel], second[channel] - first[channel], _ratio(growth[channel]),
            _ratio(second[channel] / second_total), _ratio(disc[channel] / supply[channel]),
            top_item, top_value,
        ]
    a, b = sum(first.values()), second_total
    rows["전체|합계|총계"] = [a, b, b - a, _ratio((b - a) / a), _ratio(sum(disc.values()) / sum(supply.values()))]
    order = sorted(growth, key=lambda c: -growth[c])
    return _case("sales_channel_halves", "office_sales.xlsx", SALES_QUESTION, rows=rows, row_order=order)


# ---------------------------------------------------------------------------
# 인사: headcount by department, tenure in whole months, mentor pick
# ---------------------------------------------------------------------------

STAFF_QUESTION = (
    "2026년 11월 20일을 기준으로 부서별 등록 인원, 재직 정규직, 재직 계약직, 휴직 인원, "
    "재직자 중 근속 1년 미만 인원, 재직자 평균 근속 개월 수를 집계해줘. "
    "근속 개월 수는 입사일부터 기준일까지 완전히 지난 개월 수로 계산하고, 평균은 소수점 첫째 자리까지 표시해. "
    "부서마다 재직 중인 정규직 중 근속 기간이 36개월 이상인 사람을 멘토 후보로 보고, "
    "가장 오래 근무한 한 명의 이름과 근속 개월 수를 넣어줘. 입사일이 같으면 사번이 앞선 사람을 선택하고, "
    "후보가 없으면 해당 없음으로 표시해. 마지막에 전체 재직자 평균 근속 개월 수도 개인별 근속 개월 수를 기준으로 계산해줘."
)


def _months(start: dt.date, end: dt.date) -> int:
    months = (end.year - start.year) * 12 + end.month - start.month
    return months - 1 if end.day < start.day else months


def staff_case() -> dict[str, Any]:
    people = [r for r in _sheet_rows("인사") if isinstance(r[5], dt.datetime)]
    by_dept: dict[str, list[tuple[Any, ...]]] = defaultdict(list)
    for p in people:
        by_dept[p[2]].append(p)
    rows: dict[str, list[Any]] = {}
    all_months: list[int] = []
    for dept, members in by_dept.items():
        active = [p for p in members if p[6] == "재직"]
        months = [_months(p[5].date(), BASE_DATE) for p in active]
        all_months += months
        mentors = sorted(
            (p for p in active if p[4] == "정규직" and _months(p[5].date(), BASE_DATE) >= 36),
            key=lambda p: (p[5], p[0]),
        )
        mentor: list[Any] = (
            [mentors[0][1], _months(mentors[0][5].date(), BASE_DATE)] if mentors else ["해당 없음"]
        )
        rows[dept] = [
            len(members),
            sum(p[4] == "정규직" for p in active),
            sum(p[4] == "계약직" for p in active),
            sum(p[6] == "휴직" for p in members),
            sum(m < 12 for m in months),
            {"value": round(sum(months) / len(months), 6), "tol": 0.05},
            *mentor,
        ]
    rows["전체|합계|총계"] = [
        len(people),
        sum(p[6] == "재직" and p[4] == "정규직" for p in people),
        sum(p[6] == "재직" and p[4] == "계약직" for p in people),
        sum(p[6] == "휴직" for p in people),
        sum(m < 12 for m in all_months),
        {"value": round(sum(all_months) / len(all_months), 6), "tol": 0.05},
    ]
    return _case("staff_tenure_mentor", "office_staff.xlsx", STAFF_QUESTION, rows=rows)


# ---------------------------------------------------------------------------
# 재고: ordered allocation of a fixed restock to short items
# ---------------------------------------------------------------------------

STOCK_QUESTION = (
    "기말 재고가 안전 재고보다 적은 품목만 골라, 다음 입고 물량 최대 40개를 배정해줘. "
    "품목별 부족 수량은 안전 재고 - 기말 재고, 부족률은 부족 수량 ÷ 안전 재고로 계산해. "
    "부족률이 높은 품목부터 부족 수량만큼 배정하되, 남은 물량이 부족하면 가능한 수량까지만 배정해. "
    "부족률이 같으면 출고량이 많은 품목을 먼저, 출고량도 같으면 품목 코드 오름차순으로 처리해. "
    "결과표에는 배정 순위, 품목명, 보관 위치, 기말 재고, 안전 재고, 부족률, 배정 수량, 배정 후 예상 재고, "
    "남은 부족 수량을 표시해. 마지막에 보관 위치별 배정 소계와 전체 배정량을 보여줘."
)
RESTOCK = 40


def stock_case() -> dict[str, Any]:
    items = [r for r in _sheet_rows("재고") if isinstance(r[0], str) and r[0].startswith("자재")]
    short = sorted(
        (r for r in items if r[6] < r[7]),
        key=lambda r: (-(r[7] - r[6]) / r[7], -r[5], r[0]),
    )
    left = RESTOCK
    rows: dict[str, list[Any]] = {}
    by_place: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for rank, r in enumerate(short, 1):
        need = r[7] - r[6]
        give = min(need, left)
        left -= give
        rows[r[1]] = [rank, r[2], r[6], r[7], _ratio(need / r[7]), give, r[6] + give, need - give]
        by_place[r[2]][0] += give
        by_place[r[2]][1] += need - give
    for place, (given, missing) in sorted(by_place.items()):
        rows.setdefault(place, [given, missing])
    rows["전체|합계|총계"] = [RESTOCK - left, sum(m for _, m in by_place.values())]
    not_short = [r[1] for r in items if r[6] >= r[7]]
    return _case("stock_restock_allocation", "office_stock.xlsx", STOCK_QUESTION, rows=rows,
                 row_order=[r[1] for r in short], absent_labels=not_short)


# ---------------------------------------------------------------------------
# 일정: open tasks overlapping a week, weekday counts, clashes per person
# ---------------------------------------------------------------------------

SCHEDULE_QUESTION = (
    "2026년 11월 20일 기준으로 후속 작업표를 만들어줘. 세로로 병합된 단계명은 해당 작업 행마다 채우고, "
    "구분용 빈 행과 맨 아래 집계 행은 제외해. 실제 완료일이 비어 있고 예정 기간이 11월 23~27일과 하루라도 겹치는 "
    "작업만 추출한 뒤, 해당 주에 걸치는 시작일·종료일과 작업일 수를 계산해. 작업일 수는 시작일과 종료일을 모두 "
    "포함하고 월~금만 세어줘. 담당자가 같은 두 작업이 같은 평일에 겹치면 겹치는 날짜와 상대 작업명을 비고에 표시해. "
    "마지막에 담당자별 작업별 일수 합계와 중복 날짜를 한 번만 센 실제 일정일 수를 나란히 보여줘."
)
WEEK = (dt.date(2026, 11, 23), dt.date(2026, 11, 27))


def schedule_case() -> dict[str, Any]:
    stage = None
    tasks = []
    for r in _sheet_rows("일정")[2:]:
        if r[0]:
            stage = r[0]
        if r[1] and isinstance(r[3], dt.datetime):
            tasks.append((stage, *r[1:]))
    rows: dict[str, list[Any]] = {}
    days_by_person: dict[str, list[set[dt.date]]] = defaultdict(list)
    done = []
    for stage_name, task, person, start, end, finished, *_ in tasks:
        s, e = start.date(), end.date()
        if finished is not None or s > WEEK[1] or e < WEEK[0]:
            done.append(task)
            continue
        a, b = max(s, WEEK[0]), min(e, WEEK[1])
        days = {a + dt.timedelta(k) for k in range((b - a).days + 1) if (a + dt.timedelta(k)).weekday() < 5}
        days_by_person[person].append(days)
        rows[task] = [stage_name, person, _date(a), _date(b), len(days)]
    for person, sets in days_by_person.items():
        rows[person] = [sum(len(d) for d in sets), len(set().union(*sets))]
    return _case("schedule_week_clashes", "office_schedule.xlsx", SCHEDULE_QUESTION, rows=rows,
                 absent_labels=done)


# ---------------------------------------------------------------------------


def _case(case_id: str, dataset: str, question: str, **result: Any) -> dict[str, Any]:
    return {
        "id": f"heldout5_{case_id}",
        "domain": DOMAIN,
        "profile": "generic",
        "dataset": dataset,
        "question": question,
        "tags": ["heldout5", "user_written"],
        "live_only": True,
        "expected": {"expected_result": result},
    }


def build_cases() -> list[dict[str, Any]]:
    return [budget_case(), sales_case(), staff_case(), stock_case(), schedule_case()]


def render_cases_yaml() -> str:
    header = "# Generated file: rebuild with python -m tests.benchmark.heldout5. Do not edit by hand.\n"
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
