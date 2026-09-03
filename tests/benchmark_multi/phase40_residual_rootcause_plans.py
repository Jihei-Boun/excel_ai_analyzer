"""Post-Phase-40 Step 3 — diagnostic reference IntegrationPlan witnesses.

Research only. Current IntegrationPlan DSL. Not production oracles.
Never feed these plans to the Planner as 'make it like this'.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from tests.benchmark_multi.phase40_residual import _case, build_fresh_corpus


TARGET_IDS = (
    "r40-B02",
    "r40-D03",
    "r40-D04",
    "r40-D01",
    "r40-F01",
    "r40-F03",
    "r40-G01",
    "r40-G03",
    "r40-F02",
)


def _step(
    sid: str,
    op: str,
    inputs: list[str],
    output: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    return {"id": sid, "op": op, "inputs": inputs, "output": output, "params": params}


def _filter(sid: str, src: str, out: str, column: str, value: Any) -> dict[str, Any]:
    return _step(
        sid,
        "filter_rows",
        [src],
        out,
        {"conditions": [{"column": column, "operator": "eq", "value": value}]},
    )


def _rename(sid: str, src: str, out: str, mapping: dict[str, str]) -> dict[str, Any]:
    return _step(sid, "rename_columns", [src], out, {"mapping": mapping})


def _join(
    sid: str,
    left: str,
    right: str,
    out: str,
    keys: list[str],
    how: str = "inner",
) -> dict[str, Any]:
    return _step(
        sid,
        "join",
        [left, right],
        out,
        {"left_keys": list(keys), "right_keys": list(keys), "how": how},
    )


def _agg(
    sid: str,
    src: str,
    out: str,
    group_by: list[str],
    column: str,
    alias: str,
) -> dict[str, Any]:
    return _step(
        sid,
        "aggregate",
        [src],
        out,
        {
            "group_by": list(group_by),
            "metrics": [{"column": column, "function": "sum", "alias": alias}],
        },
    )


def _plan(
    *,
    final: str,
    steps: list[dict[str, Any]],
    grain: str,
    required: list[str],
    one_row: str,
) -> dict[str, Any]:
    return {
        "status": "planned",
        "reason": "step3_reference_witness",
        "final_output": final,
        "steps": steps,
        "final_output_requirements": {
            "grain": grain,
            "required_columns": list(required),
            "one_row_represents": one_row,
        },
    }


def _partition_join_plan(
    *,
    src: str,
    part_col: str,
    val_a: Any,
    val_b: Any,
    metric: str,
    alias_a: str,
    alias_b: str,
    key: str,
) -> dict[str, Any]:
    steps = [
        _filter("f_a", src, "side_a", part_col, val_a),
        _rename("r_a", "side_a", "side_a_r", {metric: alias_a}),
        _filter("f_b", src, "side_b", part_col, val_b),
        _rename("r_b", "side_b", "side_b_r", {metric: alias_b}),
        _join("j_ab", "side_a_r", "side_b_r", "final_cmp", [key]),
    ]
    return _plan(
        final="final_cmp",
        steps=steps,
        grain="entity",
        required=[key, alias_a, alias_b],
        one_row=f"one {key} with both partition sides",
    )


def _two_file_metric_join(
    *,
    left: str,
    right: str,
    key: str,
    metric: str,
    alias_a: str,
    alias_b: str,
) -> dict[str, Any]:
    steps = [
        _rename("r_l", left, "left_r", {metric: alias_a}),
        _rename("r_r", right, "right_r", {metric: alias_b}),
        _join("j", "left_r", "right_r", "final_cmp", [key]),
    ]
    return _plan(
        final="final_cmp",
        steps=steps,
        grain="entity",
        required=[key, alias_a, alias_b],
        one_row=f"one {key} with both independently sourced amounts",
    )


def reference_plans() -> dict[str, dict[str, Any]]:
    """Minimal valid DSL witnesses keyed by case_id (targets + controls)."""
    return {
        "r40-B02": _plan(
            final="by_employee",
            steps=[
                _agg(
                    "agg_emp",
                    "shifts.xlsx",
                    "by_employee",
                    ["employee_id"],
                    "minutes",
                    "total_minutes",
                )
            ],
            grain="group",
            required=["employee_id", "total_minutes"],
            one_row="one employee_id with total shift minutes",
        ),
        "r40-D03": _partition_join_plan(
            src="budget_lines.xlsx",
            part_col="scenario",
            val_a="actual",
            val_b="forecast",
            metric="amount",
            alias_a="actual_amount",
            alias_b="forecast_amount",
            key="line_id",
        ),
        "r40-D04": _partition_join_plan(
            src="readings.xlsx",
            part_col="shift",
            val_a="day",
            val_b="night",
            metric="kwh",
            alias_a="day_kwh",
            alias_b="night_kwh",
            key="sensor_id",
        ),
        "r40-D01": _two_file_metric_join(
            left="rev_2023.xlsx",
            right="rev_2024.xlsx",
            key="sku",
            metric="revenue",
            alias_a="revenue_2023",
            alias_b="revenue_2024",
        ),
        "r40-F01": _partition_join_plan(
            src="seats.xlsx",
            part_col="cabin",
            val_a="front",
            val_b="rear",
            metric="occupancy",
            alias_a="front_occupancy",
            alias_b="rear_occupancy",
            key="car_id",
        ),
        "r40-F02": _partition_join_plan(
            src="lab.xlsx",
            part_col="replicate",
            val_a=1,
            val_b=2,
            metric="score",
            alias_a="score_replicate_1",
            alias_b="score_replicate_2",
            key="batch_id",
        ),
        "r40-F03": _partition_join_plan(
            src="logs.xlsx",
            part_col="env",
            val_a="prod",
            val_b="test",
            metric="cpu",
            alias_a="prod_cpu",
            alias_b="test_cpu",
            key="host",
        ),
        "r40-G01": _plan(
            final="west_by_vendor",
            steps=[
                _filter("f_west", "vendor_sales.xlsx", "west_rows", "region", "west"),
                _rename("r_qty", "west_rows", "west_renamed", {"q_sold": "sold_qty"}),
                _join(
                    "j_vendors",
                    "west_renamed",
                    "vendors.xlsx",
                    "west_named",
                    ["vendor_id"],
                ),
                _agg(
                    "agg_v",
                    "west_named",
                    "west_by_vendor",
                    ["vendor_name"],
                    "sold_qty",
                    "total_sold_qty",
                ),
            ],
            grain="group",
            required=["vendor_name", "total_sold_qty"],
            one_row="one vendor_name with west-region sold total",
        ),
        "r40-G03": _plan(
            final="by_store_month",
            steps=[
                _join(
                    "j_dates",
                    "sales.xlsx",
                    "sale_dates.xlsx",
                    "with_month",
                    ["sale_id"],
                ),
                _agg(
                    "agg_sm",
                    "with_month",
                    "by_store_month",
                    ["store_id", "month"],
                    "revenue",
                    "total_revenue",
                ),
            ],
            grain="group",
            required=["store_id", "month", "total_revenue"],
            one_row="one (store_id, month) with total revenue",
        ),
        "CTRL-D03-SPLIT": _two_file_metric_join(
            left="actual_lines.xlsx",
            right="forecast_lines.xlsx",
            key="line_id",
            metric="amount",
            alias_a="actual_amount",
            alias_b="forecast_amount",
        ),
        "CTRL-D04-SPLIT": _two_file_metric_join(
            left="day_readings.xlsx",
            right="night_readings.xlsx",
            key="sensor_id",
            metric="kwh",
            alias_a="day_kwh",
            alias_b="night_kwh",
        ),
        "CTRL-B02-SINGLE": _plan(
            final="by_employee",
            steps=[
                _agg(
                    "agg_emp",
                    "shifts.xlsx",
                    "by_employee",
                    ["employee_id"],
                    "minutes",
                    "total_minutes",
                )
            ],
            grain="group",
            required=["employee_id", "total_minutes"],
            one_row="one employee_id with total shift minutes",
        ),
        "CTRL-B02-SIMPLE": _plan(
            final="by_employee",
            steps=[
                _agg(
                    "agg_emp",
                    "shifts.xlsx",
                    "by_employee",
                    ["employee_id"],
                    "minutes",
                    "total_minutes",
                )
            ],
            grain="group",
            required=["employee_id", "total_minutes"],
            one_row="one employee_id with total shift minutes",
        ),
        "CTRL-D03-NEUTRAL": _partition_join_plan(
            src="budget_lines.xlsx",
            part_col="scenario",
            val_a="alpha",
            val_b="beta",
            metric="amount",
            alias_a="alpha_amount",
            alias_b="beta_amount",
            key="line_id",
        ),
        "CTRL-D03-METRIC": _partition_join_plan(
            src="budget_lines.xlsx",
            part_col="scenario",
            val_a="actual",
            val_b="forecast",
            metric="qty",
            alias_a="actual_qty",
            alias_b="forecast_qty",
            key="line_id",
        ),
        "CTRL-G03-WRONGJOIN": _plan(
            final="by_store_month",
            steps=[
                _join(
                    "j_wrong",
                    "sales.xlsx",
                    "sale_dates.xlsx",
                    "with_month",
                    ["sale_id"],
                ),
                _agg(
                    "agg_sm",
                    "with_month",
                    "by_store_month",
                    ["store_id", "month"],
                    "revenue",
                    "total_revenue",
                ),
            ],
            grain="group",
            required=["store_id", "month", "total_revenue"],
            one_row="one (store_id, month) with total revenue",
        ),
    }


def build_control_cases() -> list[dict[str, Any]]:
    """Hypothesis-discrimination controls. Not a new 30-case benchmark."""
    return [
        _case(
            case_id="CTRL-D03-SPLIT",
            category="control_partition_location",
            prompt="For each line_id, show actual amount and forecast amount as two columns.",
            files={
                "actual_lines.xlsx": pd.DataFrame({
                    "line_id": ["B1", "B2"],
                    "amount": [10, 8],
                }),
                "forecast_lines.xlsx": pd.DataFrame({
                    "line_id": ["B1", "B2"],
                    "amount": [12, 7],
                }),
            },
            expected="YES",
            requirements="Same semantics as D03, partitions split across two files.",
            answerability="answerable",
            notes="Ablation: file separation vs in-file scenario partition.",
        ),
        _case(
            case_id="CTRL-D04-SPLIT",
            category="control_partition_location",
            prompt="For each sensor_id, compare day kwh versus night kwh and keep both.",
            files={
                "day_readings.xlsx": pd.DataFrame({
                    "sensor_id": ["SN1", "SN2"],
                    "kwh": [15, 11],
                }),
                "night_readings.xlsx": pd.DataFrame({
                    "sensor_id": ["SN1", "SN2"],
                    "kwh": [6, 4],
                }),
            },
            expected="YES",
            requirements="Same semantics as D04, partitions split across two files.",
            answerability="answerable",
            notes="Ablation: file separation vs in-file shift partition.",
        ),
        _case(
            case_id="CTRL-B02-SINGLE",
            category="control_grain_complexity",
            prompt="Total shift minutes per employee_id. Do not roll up to team.",
            files={
                "shifts.xlsx": pd.DataFrame({
                    "shift_id": ["H1", "H2", "H3", "H4"],
                    "employee_id": ["E1", "E2", "E1", "E3"],
                    "minutes": [40, 35, 20, 50],
                }),
            },
            expected="YES",
            requirements="Grain is employee_id. No team file distractor.",
            answerability="answerable",
            notes="Ablation: remove teams.xlsx distractor.",
        ),
        _case(
            case_id="CTRL-B02-SIMPLE",
            category="control_grain_complexity",
            prompt="Total shift minutes per employee_id.",
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
            requirements="Same schema as B02; prompt omits the team-negative instruction.",
            answerability="answerable",
            notes="Ablation: grain instruction complexity, not keyword routing.",
        ),
        _case(
            case_id="CTRL-D03-NEUTRAL",
            category="control_partition_label",
            prompt="For each line_id, show alpha amount and beta amount as two columns.",
            files={
                "budget_lines.xlsx": pd.DataFrame({
                    "line_id": ["B1", "B1", "B2", "B2"],
                    "scenario": ["alpha", "beta", "alpha", "beta"],
                    "amount": [10, 12, 8, 7],
                }),
                "line_names.xlsx": pd.DataFrame({
                    "line_id": ["B1", "B2"],
                    "title": ["fuel", "parts"],
                }),
            },
            expected="YES",
            requirements="Same partition structure as D03 with neutral labels.",
            answerability="answerable",
            notes="Ablation: actual/forecast naming vs generic labels.",
        ),
        _case(
            case_id="CTRL-D03-METRIC",
            category="control_metric_name",
            prompt="For each line_id, show actual qty and forecast qty as two columns.",
            files={
                "budget_lines.xlsx": pd.DataFrame({
                    "line_id": ["B1", "B1", "B2", "B2"],
                    "scenario": ["actual", "forecast", "actual", "forecast"],
                    "qty": [10, 12, 8, 7],
                }),
                "line_names.xlsx": pd.DataFrame({
                    "line_id": ["B1", "B2"],
                    "title": ["fuel", "parts"],
                }),
            },
            expected="YES",
            requirements="Same D03 partition; metric column renamed amount→qty.",
            answerability="answerable",
            notes="Ablation: metric naming sensitivity.",
        ),
    ]


def all_diagnostic_cases() -> list[dict[str, Any]]:
    by_id = {c["case_id"]: c for c in build_fresh_corpus()}
    out = [by_id[i] for i in TARGET_IDS]
    out.extend(build_control_cases())
    return out


def case_by_id(case_id: str) -> dict[str, Any]:
    for c in all_diagnostic_cases():
        if c["case_id"] == case_id:
            return c
    raise KeyError(case_id)
