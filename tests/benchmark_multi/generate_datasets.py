"""Generate reproducible synthetic multi-file Excel fixtures (Phase 19).

Usage:
  python -m tests.benchmark_multi.generate_datasets
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from tests.benchmark_multi import DATASETS_DIR

SEED = 19


def _rng(seed: int = SEED) -> np.random.Generator:
    return np.random.default_rng(seed)


def _write(df: pd.DataFrame, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_excel(path, index=False)
    return path


def generate_all(datasets_dir: Path | None = None, *, force: bool = True) -> dict[str, Path]:
    root = datasets_dir or DATASETS_DIR
    root.mkdir(parents=True, exist_ok=True)
    out: dict[str, Path] = {}

    # 1–2 same-schema / compatible sales
    out["sales_jan.xlsx"] = _write(
        pd.DataFrame(
            {
                "product_id": ["P1", "P2", "P3"],
                "qty": [10, 5, 2],
                "amount": [100.0, 50.0, 20.0],
            }
        ),
        root / "sales_jan.xlsx",
    )
    out["sales_feb.xlsx"] = _write(
        pd.DataFrame(
            {
                "product_id": ["P1", "P2", "P4"],
                "qty": [3, 7, 4],
                "amount": [30.0, 70.0, 40.0],
            }
        ),
        root / "sales_feb.xlsx",
    )
    out["sales_a.xlsx"] = _write(
        pd.DataFrame(
            {
                "product_id": ["P1", "P2"],
                "qty": [1, 2],
                "amount": [10.0, 20.0],
            }
        ),
        root / "sales_a.xlsx",
    )
    out["sales_b.xlsx"] = _write(
        pd.DataFrame(
            {
                "product_id": ["P3"],
                "qty": [3],
                "amount": [30.0],
                "region": ["Seoul"],
            }
        ),
        root / "sales_b.xlsx",
    )

    # 3 warehouse union→aggregate
    out["warehouse_a.xlsx"] = _write(
        pd.DataFrame({"product_id": ["P001", "P002"], "stock": [40, 10]}),
        root / "warehouse_a.xlsx",
    )
    out["warehouse_b.xlsx"] = _write(
        pd.DataFrame({"product_id": ["P001", "P003"], "stock": [80, 5]}),
        root / "warehouse_b.xlsx",
    )

    # 4–6 customers / orders / products
    out["customers.xlsx"] = _write(
        pd.DataFrame(
            {
                "customer_id": [1, 2, 3],
                "customer_name": ["Alice", "Bob", "Carol"],
                "region": ["Seoul", "Busan", "Seoul"],
            }
        ),
        root / "customers.xlsx",
    )
    out["orders.xlsx"] = _write(
        pd.DataFrame(
            {
                "order_id": [10, 11, 12, 13],
                "customer_id": [1, 1, 2, 3],
                "product_id": ["X", "Y", "X", "Z"],
                "order_amount": [100.0, 50.0, 20.0, 30.0],
            }
        ),
        root / "orders.xlsx",
    )
    out["products.xlsx"] = _write(
        pd.DataFrame(
            {
                "product_id": ["X", "Y", "Z"],
                "category_name": ["Electronics", "Home", "Electronics"],
            }
        ),
        root / "products.xlsx",
    )
    out["orders_lookup.xlsx"] = _write(
        pd.DataFrame(
            {
                "order_id": [1, 2, 3],
                "product_id": ["X", "X", "Y"],
                "qty": [2, 1, 4],
                "amount": [20.0, 10.0, 40.0],
            }
        ),
        root / "orders_lookup.xlsx",
    )

    # 7 rename join
    out["customers_cid.xlsx"] = _write(
        pd.DataFrame({"customer_id": [1, 2], "name": ["A", "B"]}),
        root / "customers_cid.xlsx",
    )
    out["orders_cust_id.xlsx"] = _write(
        pd.DataFrame({"cust_id": [1, 1, 2], "amount": [10.0, 5.0, 7.0]}),
        root / "orders_cust_id.xlsx",
    )

    # 8 filter→union→aggregate
    out["inv_east.xlsx"] = _write(
        pd.DataFrame({"sku": ["S1", "S2", "S3"], "qty": [1, 8, 3]}),
        root / "inv_east.xlsx",
    )
    out["inv_west.xlsx"] = _write(
        pd.DataFrame({"sku": ["S1", "S4"], "qty": [2, 9]}),
        root / "inv_west.xlsx",
    )

    # 9 composite key — each single key unique enough to avoid false many-to-many
    # on per-column uniqueness checks; composite keys still required in plan.
    out["sales_store.xlsx"] = _write(
        pd.DataFrame(
            {
                "store_id": [1, 2, 3],
                "product_id": ["A", "B", "C"],
                "units": [5, 3, 2],
            }
        ),
        root / "sales_store.xlsx",
    )
    out["price_store.xlsx"] = _write(
        pd.DataFrame(
            {
                "store_id": [1, 2, 3],
                "product_id": ["A", "B", "C"],
                "unit_price": [10.0, 20.0, 15.0],
            }
        ),
        root / "price_store.xlsx",
    )

    # 10 partial overlap
    out["partial_left.xlsx"] = _write(
        pd.DataFrame({"id": list(range(1, 21)), "v": list(range(20))}),
        root / "partial_left.xlsx",
    )
    out["partial_right.xlsx"] = _write(
        pd.DataFrame({"id": [1, 2, 99, 100], "w": [10, 20, 30, 40]}),
        root / "partial_right.xlsx",
    )

    # 11 ambiguous keys
    out["ambiguous_a.xlsx"] = _write(
        pd.DataFrame(
            {
                "customer_id": [1, 2, 3, 4],
                "account_id": [10, 20, 30, 40],
                "name": ["a", "b", "c", "d"],
            }
        ),
        root / "ambiguous_a.xlsx",
    )
    out["ambiguous_b.xlsx"] = _write(
        pd.DataFrame(
            {
                "customer_id": [1, 2, 3, 4],
                "account_id": [10, 20, 30, 40],
                "balance": [100.0, 200.0, 300.0, 400.0],
            }
        ),
        root / "ambiguous_b.xlsx",
    )

    # 12 unrelated
    out["employees.xlsx"] = _write(
        pd.DataFrame(
            {
                "employee_id": [1, 2, 3],
                "dept": ["HR", "Eng", "Sales"],
                "salary": [50, 80, 60],
            }
        ),
        root / "employees.xlsx",
    )
    out["sensor_readings.xlsx"] = _write(
        pd.DataFrame(
            {
                "sensor_id": ["S1", "S2", "S3"],
                "temp_c": [21.0, 22.5, 19.0],
                "humidity": [40, 55, 60],
            }
        ),
        root / "sensor_readings.xlsx",
    )

    # 13 many-to-many (n=10 → amp=10x; small enough for memory, triggers result ERROR)
    out["campaign_events.xlsx"] = _write(
        pd.DataFrame(
            {
                "category": ["promo"] * 10,
                "event_id": list(range(1, 11)),
            }
        ),
        root / "campaign_events.xlsx",
    )
    out["campaign_orders.xlsx"] = _write(
        pd.DataFrame(
            {
                "category": ["promo"] * 10,
                "order_id": list(range(100, 110)),
                "amount": [float(i) for i in range(1, 11)],
            }
        ),
        root / "campaign_orders.xlsx",
    )

    # 14 dirty (no leading/trailing spaces — parser strips rename keys)
    out["dirty_a.xlsx"] = _write(
        pd.DataFrame(
            {
                "Product ID": ["p1", "p2"],
                "QTY": ["10", "20"],
                "Amount": [100, 200],
            }
        ),
        root / "dirty_a.xlsx",
    )
    out["dirty_b.xlsx"] = _write(
        pd.DataFrame(
            {
                "product_id": ["p1", "p3"],
                "qty": [5, 7],
                "amount": [50.0, 70.0],
            }
        ),
        root / "dirty_b.xlsx",
    )

    # 15 budget multi-file
    out["budget_a.xlsx"] = _write(
        pd.DataFrame(
            {
                "비용코드": ["C01", "C02"],
                "비목": ["인건비", "여비"],
                "실행예산": [1000.0, 500.0],
                "집행액": [800.0, 200.0],
            }
        ),
        root / "budget_a.xlsx",
    )
    out["budget_b.xlsx"] = _write(
        pd.DataFrame(
            {
                "비용코드": ["C01", "C03"],
                "비목": ["인건비", "재료비"],
                "실행예산": [300.0, 400.0],
                "집행액": [100.0, 50.0],
            }
        ),
        root / "budget_b.xlsx",
    )

    # 16 incompatible union
    out["schema_left.xlsx"] = _write(
        pd.DataFrame({"customer_id": [1], "amount": [10.0]}),
        root / "schema_left.xlsx",
    )
    out["schema_right.xlsx"] = _write(
        pd.DataFrame({"customer_id": [2], "name": ["x"]}),
        root / "schema_right.xlsx",
    )

    # 17 impossible aggregate source (string metric)
    out["agg_bad.xlsx"] = _write(
        pd.DataFrame({"g": ["a", "a"], "label": ["x", "y"]}),
        root / "agg_bad.xlsx",
    )
    out["agg_bad_b.xlsx"] = _write(
        pd.DataFrame({"g": ["b"], "label": ["z"]}),
        root / "agg_bad_b.xlsx",
    )

    _ = force  # always rewrite for reproducibility
    (root / ".gitkeep").touch(exist_ok=True)
    return out


def ensure_datasets(datasets_dir: Path | None = None, *, force: bool = False) -> dict[str, Path]:
    root = datasets_dir or DATASETS_DIR
    needed = [
        "sales_jan.xlsx",
        "customers.xlsx",
        "campaign_events.xlsx",
        "budget_a.xlsx",
        "three_marker.txt",
    ]
    # regenerate if any core missing or force
    if force or not (root / "sales_jan.xlsx").is_file():
        return generate_all(root, force=True)
    # light check
    paths = {p.name: p for p in root.glob("*.xlsx")}
    if len(paths) < 20:
        return generate_all(root, force=True)
    return paths


def main() -> None:
    paths = generate_all(force=True)
    print(f"Generated {len(paths)} datasets under {DATASETS_DIR}")


if __name__ == "__main__":
    main()
