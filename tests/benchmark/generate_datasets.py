"""Generate reproducible synthetic Excel fixtures for Phase 6 benchmark.

Usage:
  python -m tests.benchmark.generate_datasets
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from tests.benchmark import DATASETS_DIR


def _rng(seed: int = 42) -> np.random.Generator:
    return np.random.default_rng(seed)


def generate_budget(path: Path) -> Path:
    """Budget-like sheet with detail + subtotal + footer (double-count trap)."""
    rows = [
        ("내부인건비", 101, "내부인건비", 10_000_000, 8_000_000, 1_000_000, False),
        ("내부인건비", 102, "계약직", 5_000_000, 4_500_000, 500_000, False),
        ("내부인건비", None, "소 계", 15_000_000, 12_500_000, 1_500_000, True),
        ("연구활동비", 201, "국내여비", 2_000_000, 1_200_000, 300_000, False),
        ("연구활동비", 202, "회의비", 1_500_000, 900_000, 200_000, False),
        ("연구활동비", 203, "사무용소모품비", 800_000, 0, 0, False),
        ("연구활동비", None, "소 계", 4_300_000, 2_100_000, 500_000, True),
        ("연구수당", 301, "연구수당", 3_000_000, 0, 0, False),
        ("연구수당", None, "소 계", 3_000_000, 0, 0, True),
        ("간접비", 401, "간접비", 4_000_000, 2_000_000, 400_000, False),
        ("간접비", None, "소 계", 4_000_000, 2_000_000, 400_000, True),
        ("", None, "내부흡수액", 26_300_000, 16_600_000, 2_400_000, True),
        ("", None, "외부유출액", 1_000_000, 500_000, 100_000, True),
    ]
    df = pd.DataFrame(
        rows,
        columns=[
            "비목분류",
            "비용명",
            "비용명_2",
            "실행예산_합계",
            "집행계_합계",
            "당년도집행",
            "_is_subtotal",
        ],
    )
    # derived remaining for ranking questions
    df["미집행금액"] = df["실행예산_합계"] - df["집행계_합계"]
    df = df.drop(columns=["_is_subtotal"])
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_excel(path, index=False)
    return path


def generate_sales(path: Path) -> Path:
    rows = [
        {"날짜": "2024-01-01", "지역": "서울", "영업사원": "김영업", "상품": "상품A", "수량": 10, "단가": 15000, "매출": 150000, "할인율": 0.0},
        {"날짜": "2024-01-02", "지역": "부산", "영업사원": "이영업", "상품": "상품B", "수량": 10, "단가": 13000, "매출": 130000, "할인율": 0.0},
        {"날짜": "2024-01-03", "지역": "서울", "영업사원": "김영업", "상품": "상품A", "수량": 5, "단가": 15000, "매출": 75000, "할인율": 0.0},
        {"날짜": "2024-01-04", "지역": "대구", "영업사원": "박영업", "상품": "상품C", "수량": 8, "단가": 20000, "매출": 160000, "할인율": 0.0},
        {"날짜": "2024-01-05", "지역": "인천", "영업사원": "최영업", "상품": "상품D", "수량": 4, "단가": 25000, "매출": 90000, "할인율": 0.1},
        {"날짜": "2024-01-06", "지역": "부산", "영업사원": "이영업", "상품": "상품E", "수량": 6, "단가": 10000, "매출": 57000, "할인율": 0.05},
        {"날짜": "2024-01-07", "지역": "서울", "영업사원": "박영업", "상품": "상품F", "수량": 3, "단가": 20000, "매출": 60000, "할인율": 0.0},
        {"날짜": "2024-01-08", "지역": "부산", "영업사원": "김영업", "상품": "상품B", "수량": 2, "단가": 13000, "매출": 26000, "할인율": 0.0},
        {"날짜": "2024-01-09", "지역": "대구", "영업사원": "최영업", "상품": "상품C", "수량": 1, "단가": 20000, "매출": 20000, "할인율": 0.0},
        {"날짜": "2024-01-10", "지역": "서울", "영업사원": "이영업", "상품": "상품D", "수량": 5, "단가": 25000, "매출": 125000, "할인율": 0.0},
        {"날짜": "2024-01-11", "지역": "인천", "영업사원": "박영업", "상품": "상품E", "수량": 10, "단가": 10000, "매출": 100000, "할인율": 0.0},
        {"날짜": "2024-01-12", "지역": "대구", "영업사원": "김영업", "상품": "상품F", "수량": 7, "단가": 20000, "매출": 140000, "할인율": 0.0},
    ]
    pd.DataFrame(rows).to_excel(path, index=False)
    return path


def generate_inventory(path: Path) -> Path:
    rows = [
        ("P001", "전자", 12, 20, 50, 38, 10000, "서울창고"),
        ("P002", "전자", 5, 15, 40, 35, 20000, "부산창고"),
        ("P003", "가구", 80, 30, 100, 20, 50000, "서울창고"),
        ("P004", "가구", 0, 10, 20, 20, 40000, "대구창고"),
        ("P005", "소모품", 200, 50, 300, 100, 1000, "부산창고"),
        ("P006", "소모품", 8, 25, 60, 52, 2000, "서울창고"),
        ("P007", "전자", 45, 40, 70, 25, 15000, "대구창고"),
        ("P008", "가구", 15, 20, 30, 15, 60000, "부산창고"),
    ]
    df = pd.DataFrame(
        rows,
        columns=[
            "상품코드",
            "카테고리",
            "재고수량",
            "안전재고",
            "입고량",
            "출고량",
            "단가",
            "창고",
        ],
    )
    df["재고금액"] = df["재고수량"] * df["단가"]
    df.to_excel(path, index=False)
    return path


def generate_hr(path: Path) -> Path:
    rows = [
        ("E01", "A부서", "사원", "2019-03-01", 4200, 78, 5),
        ("E02", "A부서", "대리", "2016-07-15", 5200, 88, 8),
        ("E03", "B부서", "사원", "2021-01-10", 4000, 72, 3),
        ("E04", "B부서", "과장", "2012-05-20", 6500, 91, 12),
        ("E05", "C부서", "대리", "2018-11-01", 5000, 85, 6),
        ("E06", "A부서", "과장", "2010-02-01", 7000, 93, 14),
        ("E07", "B부서", "사원", "2022-09-01", 3800, 70, 2),
        ("E08", "C부서", "사원", "2020-04-12", 4100, 80, 4),
    ]
    pd.DataFrame(
        rows,
        columns=["직원ID", "부서", "직급", "입사일", "연봉", "성과점수", "근속연수"],
    ).to_excel(path, index=False)
    return path


def generate_survey(path: Path) -> Path:
    rng = _rng(11)
    rows = []
    ages = ["20대", "30대", "40대", "50대"]
    for i in range(40):
        sat = int(rng.integers(1, 6))
        rec = int(rng.integers(1, 6))
        rows.append(
            {
                "응답ID": f"R{i+1:03d}",
                "연령대": ages[i % 4],
                "성별": "F" if i % 2 == 0 else "M",
                "만족도": sat,
                "추천의향": rec,
                "서비스평가": int(rng.integers(1, 6)),
                "가격평가": int(rng.integers(1, 6)),
            }
        )
    # anchors
    rows[0]["만족도"] = 5
    rows[1]["만족도"] = 4
    rows[2]["만족도"] = 3
    pd.DataFrame(rows).to_excel(path, index=False)
    return path


def generate_sensor(path: Path) -> Path:
    rng = _rng(13)
    rows = []
    for i in range(30):
        device = f"D{(i % 3) + 1}"
        temp = float(20 + rng.normal(0, 3))
        volt = float(12 + rng.normal(0, 0.5))
        curr = float(1.5 + 0.05 * volt + rng.normal(0, 0.1))
        rows.append(
            {
                "timestamp": f"2024-06-{(i % 28) + 1:02d}T{i % 24:02d}:00:00",
                "device_id": device,
                "temperature": round(temp, 2),
                "pressure": round(float(101 + rng.normal(0, 2)), 2),
                "voltage": round(volt, 2),
                "current": round(curr, 2),
            }
        )
    rows[5]["temperature"] = 45.0  # clear max
    rows[5]["device_id"] = "D2"
    pd.DataFrame(rows).to_excel(path, index=False)
    return path


def generate_orders(path: Path) -> Path:
    rows = [
        ("O1", "C1", "노트북", "전자", 1, 1000000, 0.0, "2024-01-05", "완료"),
        ("O2", "C2", "마우스", "전자", 2, 20000, 0.1, "2024-01-06", "완료"),
        ("O3", "C1", "책상", "가구", 1, 150000, 0.0, "2024-01-07", "취소"),
        ("O4", "C3", "의자", "가구", 2, 80000, 0.05, "2024-01-08", "완료"),
        ("O5", "C2", "노트북", "전자", 1, 1000000, 0.1, "2024-01-09", "완료"),
        ("O6", "C4", "모니터", "전자", 1, 300000, 0.0, "2024-01-10", "완료"),
        ("O7", "C3", "마우스", "전자", 5, 20000, 0.0, "2024-01-11", "완료"),
        ("O8", "C5", "책장", "가구", 1, 120000, 0.0, "2024-01-12", "취소"),
        ("O9", "C1", "모니터", "전자", 2, 300000, 0.0, "2024-01-13", "완료"),
        ("O10", "C2", "의자", "가구", 1, 80000, 0.0, "2024-01-14", "완료"),
    ]
    df = pd.DataFrame(
        rows,
        columns=[
            "order_id",
            "customer_id",
            "product",
            "category",
            "quantity",
            "unit_price",
            "discount",
            "order_date",
            "status",
        ],
    )
    df["order_amount"] = df["quantity"] * df["unit_price"] * (1 - df["discount"])
    df.to_excel(path, index=False)
    return path


def generate_dirty(path: Path) -> Path:
    """Messy Excel: spaced headers, string numbers, blanks, footer, dupes."""
    df = pd.DataFrame(
        {
            " 상품 명 ": ["연필", "노트", "연필", "지우개", "노트", "합계"],
            "카테고리": ["문구", "문구", "문구", "문구", "문구", ""],
            "수 량": ["1,000", "200", "500", "50", "100", "1,850"],
            "단가": ["10", "20", "10", "5", "20", ""],
            "매출액": ["10,000", "4,000", "5,000", "250", "2,000", "21,250"],
            "비고": ["", None, "", "", "중복가능", "footer"],
            "Unnamed: 6": [None, None, None, None, None, None],
        }
    )
    # duplicate row
    df = pd.concat([df.iloc[:5], df.iloc[[2]], df.iloc[5:]], ignore_index=True)
    df.to_excel(path, index=False)
    return path


def generate_ambiguous_sales(path: Path) -> Path:
    """Multiple revenue-like columns for semantic ambiguity."""
    df = pd.DataFrame(
        {
            "상품": ["A", "B", "C", "D"],
            "당년도매출": [100, 200, 150, 80],
            "누적매출": [500, 800, 600, 400],
            "목표매출": [120, 180, 160, 100],
            "지역": ["서울", "부산", "서울", "대구"],
        }
    )
    df.to_excel(path, index=False)
    return path


DATASETS = {
    "budget_basic.xlsx": generate_budget,
    "sales_basic.xlsx": generate_sales,
    "inventory_basic.xlsx": generate_inventory,
    "hr_basic.xlsx": generate_hr,
    "survey_basic.xlsx": generate_survey,
    "sensor_basic.xlsx": generate_sensor,
    "orders_basic.xlsx": generate_orders,
    "dirty_basic.xlsx": generate_dirty,
    "ambiguous_sales.xlsx": generate_ambiguous_sales,
}


def ensure_datasets(datasets_dir: Path | None = None, *, force: bool = False) -> dict[str, Path]:
    root = datasets_dir or DATASETS_DIR
    root.mkdir(parents=True, exist_ok=True)
    out: dict[str, Path] = {}
    for name, fn in DATASETS.items():
        path = root / name
        if force or not path.is_file():
            fn(path)
        out[name] = path
    return out


def main() -> None:
    paths = ensure_datasets(force=True)
    for name, path in paths.items():
        print(f"wrote {path} ({path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
