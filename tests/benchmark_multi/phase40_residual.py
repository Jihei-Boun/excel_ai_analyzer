"""Post-Phase-40 Step 2 — fresh residual bottleneck measurement (research only).

Calls the frozen Candidate path:
  build_cross_file_understanding → run_integration_pipeline_semantic_experimental

Does NOT modify core/, prompts, models, Shadow default, or wire contracts.
Fresh corpus: r40-* IDs. Not a clone of M1/M2/39S C-D / 40D-H / D01 fixtures.
"""

from __future__ import annotations

import argparse
import json
import os
import time
import traceback
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from core.integrate.relationship_infer import build_cross_file_understanding
from core.integrate.result_observation import observe_result_for_verifier
from core.integrate.semantic_escalation import (
    SEMANTIC_VERIFIER_MODEL,
    SEMANTIC_VERIFIER_VARIANT,
    SemanticEscalationConfig,
    run_integration_pipeline_semantic_experimental,
)
from core.shadow.config import load_shadow_config

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "benchmark_results" / "multi" / "phase40_residual"
RESULTS_JSONL = OUT / "case_results.jsonl"
SUMMARY_PATH = OUT / "phase40_residual_summary.json"

BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
HEAD_EXPECTED = "1911ed701c56d20593bb19bee752bc66ff1c4ed0"

FORBIDDEN_CLONE_TOKENS = (
    "P39S-C",
    "P39S-D",
    "D01-like",
    "campus",
    "desk_id",
    "reed_id",
    "quarry face",
    "marsh inlet",
)


def _case(
    *,
    case_id: str,
    category: str,
    prompt: str,
    files: dict[str, pd.DataFrame],
    expected: str,
    requirements: str,
    answerability: str,
    notes: str,
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "category": category,
        "request_id": case_id,
        "user_prompt": prompt,
        "files": files,
        "source_files": list(files.keys()),
        "manual_expected_outcome": expected,
        "manual_semantic_requirements": requirements,
        "answerability": answerability,
        "difficulty_notes": notes,
    }


def build_fresh_corpus() -> list[dict[str, Any]]:
    """30 fresh cases. Neutral spreadsheet schemas. Not prior exact clones."""
    c: list[dict[str, Any]] = []

    # ----- A straightforward valid -----
    c.append(_case(
        case_id="r40-A01",
        category="straightforward_valid",
        prompt="For each sku, show the product name and units sold.",
        files={
            "catalog.xlsx": pd.DataFrame({
                "sku": ["SK10", "SK11", "SK12"],
                "product_name": ["Nimbus mug", "Cedar tray", "Oak spoon"],
            }),
            "weekly_units.xlsx": pd.DataFrame({
                "sku": ["SK10", "SK11", "SK12"],
                "units": [12, 4, 9],
            }),
        },
        expected="YES",
        requirements="Join on sku. One row per sku with product_name and units.",
        answerability="answerable",
        notes="Simple named-key join.",
    ))
    c.append(_case(
        case_id="r40-A02",
        category="straightforward_valid",
        prompt="Stack both warehouse inventory tables into one table of item rows.",
        files={
            "warehouse_north.xlsx": pd.DataFrame({
                "item_id": ["IT1", "IT2"],
                "qty": [30, 8],
            }),
            "warehouse_south.xlsx": pd.DataFrame({
                "item_id": ["IT3", "IT4"],
                "qty": [5, 16],
            }),
        },
        expected="YES",
        requirements="Union compatible schemas. Do not join north to south.",
        answerability="answerable",
        notes="Same-schema append.",
    ))
    c.append(_case(
        case_id="r40-A03",
        category="straightforward_valid",
        prompt="Total invoice amount per plan, including the plan name.",
        files={
            "plans.xlsx": pd.DataFrame({
                "plan_id": ["PL1", "PL2"],
                "plan_name": ["Starter", "Plus"],
            }),
            "invoices.xlsx": pd.DataFrame({
                "plan_id": ["PL1", "PL1", "PL2"],
                "amount": [20.0, 15.0, 40.0],
            }),
        },
        expected="YES",
        requirements="Join invoices to plans on plan_id; sum amount grouped by plan.",
        answerability="answerable",
        notes="Join then aggregate.",
    ))
    c.append(_case(
        case_id="r40-A04",
        category="straightforward_valid",
        prompt="Show kilograms and delivery zone for each package.",
        files={
            "packages.xlsx": pd.DataFrame({
                "pkg_id": ["P9", "P10"],
                "kg": [2.4, 1.1],
            }),
            "labels.xlsx": pd.DataFrame({
                "pkg_id": ["P9", "P10"],
                "zone": ["Z2", "Z5"],
            }),
        },
        expected="YES",
        requirements="Join on pkg_id. Keep kg and zone.",
        answerability="answerable",
        notes="Two-column join.",
    ))
    c.append(_case(
        case_id="r40-A05",
        category="straightforward_valid",
        prompt="Total kwh for each site_name.",
        files={
            "meters.xlsx": pd.DataFrame({
                "meter_id": ["M1", "M2", "M1"],
                "kwh": [100, 40, 25],
            }),
            "sites.xlsx": pd.DataFrame({
                "meter_id": ["M1", "M2"],
                "site_name": ["Ridge", "Harbor"],
            }),
        },
        expected="YES",
        requirements="Join on meter_id; sum kwh per site_name.",
        answerability="answerable",
        notes="Energy domain join+sum.",
    ))
    c.append(_case(
        case_id="r40-A06",
        category="straightforward_valid",
        prompt="Total handling minutes per agent.",
        files={
            "tickets.xlsx": pd.DataFrame({
                "ticket_id": ["T1", "T2", "T3"],
                "minutes": [12, 8, 20],
            }),
            "agents.xlsx": pd.DataFrame({
                "ticket_id": ["T1", "T2", "T3"],
                "agent": ["Lina", "Omar", "Lina"],
            }),
        },
        expected="YES",
        requirements="Join on ticket_id; sum minutes grouped by agent.",
        answerability="answerable",
        notes="Support domain.",
    ))
    c.append(_case(
        case_id="r40-A07",
        category="straightforward_valid",
        prompt="Total machine hours per plant.",
        files={
            "machines.xlsx": pd.DataFrame({
                "machine_id": ["MC1", "MC2", "MC3"],
                "hours": [6.0, 3.5, 4.0],
            }),
            "plants.xlsx": pd.DataFrame({
                "machine_id": ["MC1", "MC2", "MC3"],
                "plant": ["East", "East", "West"],
            }),
        },
        expected="YES",
        requirements="Join on machine_id; sum hours per plant.",
        answerability="answerable",
        notes="Operations domain.",
    ))

    # ----- B grain-sensitive -----
    c.append(_case(
        case_id="r40-B01",
        category="grain_sensitive",
        prompt="Total order amount per customer_id, not per store.",
        files={
            "orders.xlsx": pd.DataFrame({
                "order_id": ["O1", "O2", "O3", "O4"],
                "customer_id": ["C1", "C1", "C2", "C2"],
                "amount": [10, 7, 9, 3],
            }),
            "store_map.xlsx": pd.DataFrame({
                "order_id": ["O1", "O2", "O3", "O4"],
                "store_id": ["S1", "S2", "S1", "S1"],
            }),
        },
        expected="YES",
        requirements="Final grain is customer_id. Store-level totals are wrong.",
        answerability="answerable",
        notes="Coarse store grain is executable but wrong.",
    ))
    c.append(_case(
        case_id="r40-B02",
        category="grain_sensitive",
        prompt="Total shift minutes per employee_id. Do not roll up to team.",
        files={
            "shifts.xlsx": pd.DataFrame({
                "shift_id": ["H1", "H2", "H3", "H4"],
                "employee_id": ["E1", "E2", "E1", "E3"],
                "minutes": [40, 35, 20, 50],
            }),
            "teams.xlsx": pd.DataFrame({
                "employee_id": ["E1", "E2", "E3"],
                "team": ["Alpha", "Beta", "Alpha"],
            }),
        },
        expected="YES",
        requirements="Grain is employee_id. Team totals are too coarse.",
        answerability="answerable",
        notes="HR grain vs team rollup.",
    ))
    c.append(_case(
        case_id="r40-B03",
        category="grain_sensitive",
        prompt="Total visit fee per patient_id.",
        files={
            "visits.xlsx": pd.DataFrame({
                "visit_id": ["V1", "V2", "V3", "V4"],
                "patient_id": ["PT1", "PT1", "PT2", "PT3"],
                "fee": [50, 20, 30, 40],
            }),
            "calendar.xlsx": pd.DataFrame({
                "visit_id": ["V1", "V2", "V3", "V4"],
                "month": ["Jan", "Feb", "Jan", "Jan"],
            }),
        },
        expected="YES",
        requirements="Grain is patient_id. Month-level totals are wrong.",
        answerability="answerable",
        notes="Clinic domain; month is a distractor.",
    ))
    c.append(_case(
        case_id="r40-B04",
        category="grain_sensitive",
        prompt="Total nights occupied per room_id, not per floor.",
        files={
            "stays.xlsx": pd.DataFrame({
                "stay_id": ["Y1", "Y2", "Y3"],
                "room_id": ["R1", "R1", "R2"],
                "nights": [2, 1, 3],
            }),
            "floors.xlsx": pd.DataFrame({
                "room_id": ["R1", "R2"],
                "floor": [3, 5],
            }),
        },
        expected="YES",
        requirements="Grain is room_id. Floor totals collapse rooms.",
        answerability="answerable",
        notes="Hospitality grain.",
    ))
    c.append(_case(
        case_id="r40-B05",
        category="grain_sensitive",
        prompt="Total quantity per product_id. Do not group only by category.",
        files={
            "lines.xlsx": pd.DataFrame({
                "line_id": ["L1", "L2", "L3", "L4"],
                "product_id": ["PR1", "PR2", "PR1", "PR3"],
                "qty": [2, 5, 1, 4],
            }),
            "taxonomy.xlsx": pd.DataFrame({
                "product_id": ["PR1", "PR2", "PR3"],
                "category": ["paper", "ink", "paper"],
            }),
        },
        expected="YES",
        requirements="Grain is product_id. Category-only aggregate is wrong.",
        answerability="answerable",
        notes="Retail grain vs category.",
    ))

    # ----- C join vs union -----
    c.append(_case(
        case_id="r40-C01",
        category="join_vs_union",
        prompt="Combine January and February ledger rows into one table.",
        files={
            "ledger_jan.xlsx": pd.DataFrame({
                "account": ["A1", "A2"],
                "balance": [100, 40],
            }),
            "ledger_feb.xlsx": pd.DataFrame({
                "account": ["A1", "A3"],
                "balance": [110, 15],
            }),
        },
        expected="YES",
        requirements="Union/stack rows. Joining Jan to Feb on account drops or duplicates wrongly.",
        answerability="answerable",
        notes="Same schema monthly append; overlapping account ids.",
    ))
    c.append(_case(
        case_id="r40-C02",
        category="join_vs_union",
        prompt="For each employee, show department and salary together.",
        files={
            "employees.xlsx": pd.DataFrame({
                "emp_id": ["U1", "U2", "U3"],
                "dept": ["ops", "ops", "legal"],
            }),
            "salaries.xlsx": pd.DataFrame({
                "emp_id": ["U1", "U2", "U3"],
                "salary": [50, 55, 70],
            }),
        },
        expected="YES",
        requirements="Join on emp_id. Union is schema-incompatible / semantically wrong.",
        answerability="answerable",
        notes="Different schemas; join is required.",
    ))
    c.append(_case(
        case_id="r40-C03",
        category="join_vs_union",
        prompt="Put all north and south call rows into a single call list.",
        files={
            "north_calls.xlsx": pd.DataFrame({
                "call_id": ["N1", "N2"],
                "duration": [30, 18],
            }),
            "south_calls.xlsx": pd.DataFrame({
                "call_id": ["S1", "S2"],
                "duration": [22, 9],
            }),
        },
        expected="YES",
        requirements="Union. There is no shared call identity to join.",
        answerability="answerable",
        notes="Disjoint ids, same schema.",
    ))
    c.append(_case(
        case_id="r40-C04",
        category="join_vs_union",
        prompt="Append both survey waves into one respondent-score table.",
        files={
            "wave_alpha.xlsx": pd.DataFrame({
                "respondent": ["R1", "R2"],
                "score": [8, 6],
            }),
            "wave_beta.xlsx": pd.DataFrame({
                "respondent": ["R1", "R3"],
                "score": [7, 9],
            }),
        },
        expected="YES",
        requirements="Union both waves. Inner join on respondent drops R2/R3 and is not an append.",
        answerability="answerable",
        notes="Overlapping respondent is a join trap.",
    ))

    # ----- D independent evidence -----
    c.append(_case(
        case_id="r40-D01",
        category="independent_evidence",
        prompt=(
            "For each sku, compare 2023 revenue versus 2024 revenue and keep "
            "both year amounts visible."
        ),
        files={
            "rev_2023.xlsx": pd.DataFrame({
                "sku": ["Q1", "Q2"],
                "revenue": [80, 25],
                "year": [2023, 2023],
            }),
            "rev_2024.xlsx": pd.DataFrame({
                "sku": ["Q1", "Q2"],
                "revenue": [90, 30],
                "year": [2024, 2024],
            }),
        },
        expected="YES",
        requirements=(
            "Two independently sourced year amounts must survive. "
            "A single combined revenue total is wrong."
        ),
        answerability="answerable",
        notes="Genuine dual files, not alias of one expression.",
    ))
    c.append(_case(
        case_id="r40-D02",
        category="independent_evidence",
        prompt="For each store_id, show east visits and west visits as separate values.",
        files={
            "east_visits.xlsx": pd.DataFrame({
                "store_id": ["ST1", "ST2"],
                "visits": [40, 12],
            }),
            "west_visits.xlsx": pd.DataFrame({
                "store_id": ["ST1", "ST2"],
                "visits": [22, 18],
            }),
        },
        expected="YES",
        requirements="Independent east/west visit columns. One stacked total is wrong.",
        answerability="answerable",
        notes="Region files with same metric name.",
    ))
    c.append(_case(
        case_id="r40-D03",
        category="independent_evidence",
        prompt="For each line_id, show actual amount and forecast amount as two columns.",
        files={
            "budget_lines.xlsx": pd.DataFrame({
                "line_id": ["B1", "B1", "B2", "B2"],
                "scenario": ["actual", "forecast", "actual", "forecast"],
                "amount": [10, 12, 8, 7],
            }),
            "line_names.xlsx": pd.DataFrame({
                "line_id": ["B1", "B2"],
                "title": ["fuel", "parts"],
            }),
        },
        expected="YES",
        requirements="Partition scenario into two amount columns per line_id.",
        answerability="answerable",
        notes="Scenario partition; names file is optional context.",
    ))
    c.append(_case(
        case_id="r40-D04",
        category="independent_evidence",
        prompt="For each sensor_id, compare day kwh versus night kwh and keep both.",
        files={
            "readings.xlsx": pd.DataFrame({
                "sensor_id": ["SN1", "SN1", "SN2", "SN2"],
                "shift": ["day", "night", "day", "night"],
                "kwh": [15, 6, 11, 4],
            }),
            "locations.xlsx": pd.DataFrame({
                "sensor_id": ["SN1", "SN2"],
                "yard": ["north", "dock"],
            }),
        },
        expected="YES",
        requirements="Filter/partition shift then keep both kwh sides per sensor.",
        answerability="answerable",
        notes="Shift partition; not 39U entity/part fixture.",
    ))

    # ----- E cannot-plan -----
    c.append(_case(
        case_id="r40-E01",
        category="cannot_plan",
        prompt="Compare inbound delay versus outbound delay for each flight_id.",
        files={
            "flights.xlsx": pd.DataFrame({
                "flight_id": ["F1", "F2"],
                "delay": [4, 11],
            }),
            "airports.xlsx": pd.DataFrame({
                "flight_id": ["F1", "F2"],
                "airport": ["HEL", "OSL"],
            }),
        },
        expected="CORRECT_CANNOT_PLAN",
        requirements="No inbound/outbound column or partition exists. Dual aliases of delay are wrong.",
        answerability="not_answerable",
        notes="Missing requested distinction.",
    ))
    c.append(_case(
        case_id="r40-E02",
        category="cannot_plan",
        prompt="Join customers to orders so each order shows the customer name.",
        files={
            "customers.xlsx": pd.DataFrame({
                "name": ["Ada", "Bo"],
                "city": ["Bergen", "Tromso"],
            }),
            "orders.xlsx": pd.DataFrame({
                "order_id": ["Z1", "Z2"],
                "amount": [12, 9],
            }),
        },
        expected="CORRECT_CANNOT_PLAN",
        requirements="No join identity between the files.",
        answerability="not_answerable",
        notes="No shared key.",
    ))
    c.append(_case(
        case_id="r40-E03",
        category="cannot_plan",
        prompt="Compare paid versus complimentary subscriber revenue for each plan_id.",
        files={
            "revenue.xlsx": pd.DataFrame({
                "plan_id": ["PL9", "PL8"],
                "revenue": [100, 60],
            }),
            "plan_meta.xlsx": pd.DataFrame({
                "plan_id": ["PL9", "PL8"],
                "owner": ["core", "labs"],
            }),
        },
        expected="CORRECT_CANNOT_PLAN",
        requirements="No paid/free flag. Relabeling one revenue twice is wrong.",
        answerability="not_answerable",
        notes="Missing side discriminator.",
    ))
    c.append(_case(
        case_id="r40-E04",
        category="cannot_plan",
        prompt="Match inventory items between the two catalogs by a shared item identity.",
        files={
            "catalog_left.xlsx": pd.DataFrame({
                "color": ["red", "blue"],
                "width": [3, 5],
            }),
            "catalog_right.xlsx": pd.DataFrame({
                "material": ["wool", "linen"],
                "length": [10, 12],
            }),
        },
        expected="CORRECT_CANNOT_PLAN",
        requirements="No overlapping identity column.",
        answerability="not_answerable",
        notes="Unrelated schemas.",
    ))

    # ----- F filter-sensitive join -----
    c.append(_case(
        case_id="r40-F01",
        category="filter_sensitive_join",
        prompt="For each car_id, show front occupancy and rear occupancy as two values.",
        files={
            "seats.xlsx": pd.DataFrame({
                "car_id": ["CR1", "CR1", "CR2", "CR2"],
                "cabin": ["front", "rear", "front", "rear"],
                "occupancy": [2, 3, 1, 4],
            }),
            "routes.xlsx": pd.DataFrame({
                "car_id": ["CR1", "CR2"],
                "route": ["coast", "inland"],
            }),
        },
        expected="YES",
        requirements=(
            "Filter cabin then join/compare. Pre-filter car_id is not unique; "
            "post-filter it is. Many-to-many on the raw table is not the join state."
        ),
        answerability="answerable",
        notes="Transit domain analog of filter-then-join; not 39U part/entity clone.",
    ))
    c.append(_case(
        case_id="r40-F02",
        category="filter_sensitive_join",
        prompt="For each batch_id, show replicate 1 score and replicate 2 score.",
        files={
            "lab.xlsx": pd.DataFrame({
                "batch_id": ["BT1", "BT1", "BT2", "BT2"],
                "replicate": [1, 2, 1, 2],
                "score": [0.8, 0.7, 0.9, 0.6],
            }),
            "labsites.xlsx": pd.DataFrame({
                "batch_id": ["BT1", "BT2"],
                "lab": ["north", "south"],
            }),
        },
        expected="YES",
        requirements="Filter replicate then keep both scores per batch.",
        answerability="answerable",
        notes="Lab replicates; uniqueness after filter.",
    ))
    c.append(_case(
        case_id="r40-F03",
        category="filter_sensitive_join",
        prompt="For each host, compare prod cpu versus test cpu.",
        files={
            "logs.xlsx": pd.DataFrame({
                "host": ["H1", "H1", "H2", "H2"],
                "env": ["prod", "test", "prod", "test"],
                "cpu": [0.4, 0.1, 0.6, 0.2],
            }),
            "racks.xlsx": pd.DataFrame({
                "host": ["H1", "H2"],
                "rack": ["R4", "R7"],
            }),
        },
        expected="YES",
        requirements="Partition env then compare cpu per host.",
        answerability="answerable",
        notes="Ops env partition.",
    ))

    # ----- G multi-step -----
    c.append(_case(
        case_id="r40-G01",
        category="multi_step",
        prompt=(
            "Using only the west region rows, total sold_qty per vendor_name. "
            "The sold column is currently named q_sold."
        ),
        files={
            "vendor_sales.xlsx": pd.DataFrame({
                "vendor_id": ["VD1", "VD2", "VD1", "VD3"],
                "region": ["west", "east", "west", "west"],
                "q_sold": [4, 9, 6, 2],
            }),
            "vendors.xlsx": pd.DataFrame({
                "vendor_id": ["VD1", "VD2", "VD3"],
                "vendor_name": ["Pike", "Harbor", "Lane"],
            }),
        },
        expected="YES",
        requirements="Filter region=west, rename q_sold, join vendors, sum per vendor_name.",
        answerability="answerable",
        notes="Rename + filter + join + aggregate.",
    ))
    c.append(_case(
        case_id="r40-G02",
        category="multi_step",
        prompt="Total line quantity per product_name.",
        files={
            "orders.xlsx": pd.DataFrame({
                "order_id": ["OR1", "OR2"],
                "channel": ["web", "store"],
            }),
            "line_items.xlsx": pd.DataFrame({
                "order_id": ["OR1", "OR1", "OR2"],
                "product_id": ["X1", "X2", "X1"],
                "qty": [2, 1, 3],
            }),
            "products.xlsx": pd.DataFrame({
                "product_id": ["X1", "X2"],
                "product_name": ["Bolt", "Washer"],
            }),
        },
        expected="YES",
        requirements="Join line_items to products; sum qty per product_name.",
        answerability="answerable",
        notes="Three-file chain; orders is optional context.",
    ))
    c.append(_case(
        case_id="r40-G03",
        category="multi_step",
        prompt="Total revenue per store_id and month together.",
        files={
            "sales.xlsx": pd.DataFrame({
                "sale_id": ["S1", "S2", "S3", "S4"],
                "store_id": ["W1", "W1", "W2", "W2"],
                "revenue": [5, 8, 3, 6],
            }),
            "sale_dates.xlsx": pd.DataFrame({
                "sale_id": ["S1", "S2", "S3", "S4"],
                "month": ["Mar", "Apr", "Mar", "Mar"],
            }),
        },
        expected="YES",
        requirements="Join on sale_id; aggregate grain is (store_id, month).",
        answerability="answerable",
        notes="Composite grain.",
    ))

    return c


def corpus_manifest() -> list[dict[str, Any]]:
    rows = []
    for case in build_fresh_corpus():
        rows.append({
            "case_id": case["case_id"],
            "category": case["category"],
            "source_files": case["source_files"],
            "user_prompt": case["user_prompt"],
            "manual_expected_outcome": case["manual_expected_outcome"],
            "manual_semantic_requirements": case["manual_semantic_requirements"],
            "answerability": case["answerability"],
            "difficulty_notes": case["difficulty_notes"],
        })
    return rows


def _compact_plan(plan: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(plan, dict):
        return None
    steps = []
    for s in plan.get("steps") or []:
        if not isinstance(s, dict):
            continue
        steps.append({
            "id": s.get("id"),
            "op": s.get("op"),
            "inputs": list(s.get("inputs") or []),
            "output": s.get("output"),
            "params": s.get("params") or {},
        })
    return {
        "status": plan.get("status"),
        "steps": steps,
        "final_output": plan.get("final_output"),
        "final_output_requirements": plan.get("final_output_requirements"),
        "reason": plan.get("reason"),
    }


def _issue_codes(obj: Any) -> list[str]:
    if obj is None:
        return []
    if hasattr(obj, "errors"):
        return [getattr(e, "code", str(e)) for e in (obj.errors or [])]
    if isinstance(obj, dict):
        return [str(e.get("code")) for e in (obj.get("errors") or []) if isinstance(e, dict)]
    return []


def _ops(plan: dict[str, Any] | None) -> list[str]:
    if not plan:
        return []
    return [str(s.get("op")) for s in (plan.get("steps") or []) if isinstance(s, dict)]


def extract_row(result: Any) -> dict[str, Any]:
    meta = dict(getattr(result, "metadata", None) or {}) if result else {}
    plan = result.plan.to_dict() if result and result.plan else None
    plan_val = getattr(result, "plan_validation", None) if result else None
    execution = getattr(result, "execution", None) if result else None
    res_val = getattr(result, "result_validation", None) if result else None
    fo = getattr(result, "final_output", None) if result else None
    trace = meta.get("semantic_escalation") or {}
    verifier = meta.get("semantic_verifier") if isinstance(meta.get("semantic_verifier"), dict) else (trace.get("verifier") or {})
    lineage = meta.get("attempt_lineage") if isinstance(meta.get("attempt_lineage"), dict) else {}
    original_plan = trace.get("original_plan") if isinstance(trace, dict) else None
    strong_plan = trace.get("strong_plan") if isinstance(trace, dict) else None
    return {
        "pipeline_status": getattr(result, "status", None) if result else None,
        "final_plan": _compact_plan(plan),
        "final_ops": _ops(plan),
        "original_plan": _compact_plan(original_plan if isinstance(original_plan, dict) else None),
        "original_ops": _ops(original_plan if isinstance(original_plan, dict) else None),
        "strong_plan": _compact_plan(strong_plan if isinstance(strong_plan, dict) else None),
        "strong_ops": _ops(strong_plan if isinstance(strong_plan, dict) else None),
        "plan_validation_valid": bool(getattr(plan_val, "valid", None)) if plan_val is not None else None,
        "plan_validation_codes": _issue_codes(plan_val),
        "executor_success": bool(getattr(execution, "success", None)) if execution is not None else None,
        "executor_error": (
            getattr(getattr(execution, "error", None), "code", None) if execution is not None else None
        ),
        "result_validation_valid": bool(getattr(res_val, "valid", None)) if res_val is not None else None,
        "result_validation_codes": _issue_codes(res_val),
        "result_obs": observe_result_for_verifier(fo) if fo is not None else None,
        "semantic_verifier_invoked": bool(meta.get("semantic_verifier_invoked")),
        "verifier_verdict": verifier.get("verdict") if isinstance(verifier, dict) else None,
        "verifier_reason_code": verifier.get("reason_code") if isinstance(verifier, dict) else None,
        "verifier_evidence": list(verifier.get("evidence") or [])[:8] if isinstance(verifier, dict) else [],
        "verifier_invocation_id": verifier.get("verifier_invocation_id") if isinstance(verifier, dict) else None,
        "failure_escalation_32b": bool(meta.get("failure_escalation_32b")),
        "semantic_escalation_32b": bool(meta.get("semantic_escalation_32b")),
        "semantic_escalated": bool(trace.get("semantic_escalated")) if isinstance(trace, dict) else False,
        "semantic_escalation_reason": trace.get("semantic_escalation_reason") if isinstance(trace, dict) else None,
        "final_path": meta.get("final_path"),
        "escalation_source": meta.get("escalation_source"),
        "attempt_lineage": lineage,
        "verified_attempt_id": meta.get("verified_attempt_id"),
        "final_attempt_id": meta.get("final_attempt_id"),
        "verified_plan_fingerprint": meta.get("verified_plan_fingerprint"),
        "final_plan_fingerprint": meta.get("final_plan_fingerprint"),
        "semantic_verifier_elapsed_s": meta.get("semantic_verifier_elapsed_s"),
        "semantic_strong_elapsed_s": meta.get("semantic_strong_elapsed_s"),
        "retry_log": list(getattr(result, "retry_log", None) or []) if result else [],
        "fast_attempt_count": meta.get("fast_attempt_count"),
        "strong_attempt_count": meta.get("strong_attempt_count"),
        "initial_model": meta.get("initial_model"),
        "final_model": meta.get("final_model"),
    }


def production_config() -> SemanticEscalationConfig:
    return SemanticEscalationConfig(
        enable_failure_escalation=True,
        enable_semantic_escalation=True,
        uncertain_policy="escalate",
        verifier_model=SEMANTIC_VERIFIER_MODEL,
        strong_model="qwen3:32b",
        strong_max_retries=2,
        reverify_strong=False,
    )


def run_one(case: dict[str, Any]) -> dict[str, Any]:
    sources = {str(k): v.copy() for k, v in case["files"].items()}
    t0 = time.time()
    row: dict[str, Any] = {
        "case_id": case["case_id"],
        "request_id": case["request_id"],
        "category": case["category"],
        "user_prompt": case["user_prompt"],
        "source_files": case["source_files"],
        "manual_expected_outcome": case["manual_expected_outcome"],
        "manual_semantic_requirements": case["manual_semantic_requirements"],
        "answerability": case["answerability"],
        "captured_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "verifier_model": SEMANTIC_VERIFIER_MODEL,
        "verifier_variant": SEMANTIC_VERIFIER_VARIANT,
        "shadow_enabled_at_run": bool(load_shadow_config().enabled),
        "error": None,
        "error_family": None,
        "understanding_elapsed_s": None,
        "pipeline_elapsed_s": None,
        "total_elapsed_s": None,
    }
    try:
        t_und = time.time()
        understanding = build_cross_file_understanding(
            list(sources.items()),
            base_url=BASE_URL,
            model="qwen2.5:7b",
            infer_relationships=True,
        )
        row["understanding_elapsed_s"] = round(time.time() - t_und, 3)
        t_pipe = time.time()
        result = run_integration_pipeline_semantic_experimental(
            case["user_prompt"],
            sources,
            understanding,
            config=production_config(),
            base_url=BASE_URL,
            request_id=case["request_id"],
            case_id=case["case_id"],
        )
        row["pipeline_elapsed_s"] = round(time.time() - t_pipe, 3)
        row.update(extract_row(result))
    except Exception as exc:  # noqa: BLE001
        row["error"] = f"{type(exc).__name__}: {exc}"
        row["error_family"] = type(exc).__name__
        row["error_traceback_tail"] = traceback.format_exc()[-1500:]
        if "Timeout" in type(exc).__name__ or "timeout" in str(exc).lower():
            row["error_family"] = "timeout"
    row["total_elapsed_s"] = round(time.time() - t0, 3)
    return row


def _load_done_ids(path: Path) -> set[str]:
    done: set[str] = set()
    if not path.is_file():
        return done
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        cid = rec.get("case_id")
        if isinstance(cid, str):
            done.add(cid)
    return done


def run_corpus(*, only: list[str] | None = None, resume: bool = True) -> list[dict[str, Any]]:
    OUT.mkdir(parents=True, exist_ok=True)
    cases = build_fresh_corpus()
    if only:
        want = set(only)
        cases = [c for c in cases if c["case_id"] in want]
    done = _load_done_ids(RESULTS_JSONL) if resume else set()
    rows: list[dict[str, Any]] = []
    if RESULTS_JSONL.is_file() and resume:
        for line in RESULTS_JSONL.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                if not only or rec.get("case_id") in (only or []):
                    rows.append(rec)
    for case in cases:
        if case["case_id"] in done:
            continue
        print(f"[r40] start {case['case_id']} {case['category']}", flush=True)
        rec = run_one(case)
        with RESULTS_JSONL.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        rows.append(rec)
        print(
            f"[r40] done {case['case_id']} status={rec.get('pipeline_status')} "
            f"verdict={rec.get('verifier_verdict')} path={rec.get('final_path')} "
            f"t={rec.get('total_elapsed_s')}",
            flush=True,
        )
    return rows


def load_results() -> list[dict[str, Any]]:
    if not RESULTS_JSONL.is_file():
        return []
    return [json.loads(line) for line in RESULTS_JSONL.read_text(encoding="utf-8").splitlines() if line.strip()]


# --- research-only structural hints for manual review (not production rules) ---

def _group_bys(plan: dict[str, Any] | None) -> list[list[str]]:
    out: list[list[str]] = []
    if not plan:
        return out
    for s in plan.get("steps") or []:
        if isinstance(s, dict) and s.get("op") == "aggregate":
            out.append([str(x) for x in ((s.get("params") or {}).get("group_by") or [])])
    return out


def inspect_attempt(plan: dict[str, Any] | None, case: dict[str, Any]) -> dict[str, Any]:
    """Research notes to help the human evaluator. Not a production decision."""
    ops = _ops(plan)
    notes: list[str] = []
    if plan is None:
        return {"ops": [], "group_bys": [], "notes": ["no_plan"]}
    if plan.get("status") == "cannot_plan":
        notes.append("plan_status_cannot_plan")
    if "union_rows" in ops:
        notes.append("has_union")
    if "join" in ops:
        notes.append("has_join")
    gb = _group_bys(plan)
    if gb:
        notes.append("group_by=" + json.dumps(gb, ensure_ascii=False))
    return {"ops": ops, "group_bys": gb, "notes": notes}


def freeze_snapshot() -> dict[str, Any]:
    return {
        "head_expected": HEAD_EXPECTED,
        "verifier_model": SEMANTIC_VERIFIER_MODEL,
        "verifier_variant": SEMANTIC_VERIFIER_VARIANT,
        "shadow_enabled": bool(load_shadow_config().enabled),
        "corpus_n": len(build_fresh_corpus()),
        "categories": dict(Counter(c["category"] for c in build_fresh_corpus())),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", action="append", default=None)
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument("--manifest-only", action="store_true")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "corpus_manifest.json").write_text(
        json.dumps({"cases": corpus_manifest(), "freeze": freeze_snapshot()}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (OUT / "baseline_freeze.json").write_text(
        json.dumps(freeze_snapshot(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    if args.manifest_only:
        print("wrote manifest", len(corpus_manifest()))
        return
    if load_shadow_config().enabled:
        raise SystemExit("Shadow must stay OFF for this research run")
    run_corpus(only=args.case, resume=not args.no_resume)


if __name__ == "__main__":
    main()
