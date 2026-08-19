"""Phase 27: Architecture feasibility — residual cases expressible with current DSL.

Does NOT inject golden plans into production Planner.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from core.integrate.integration_pipeline import run_integration_pipeline
from core.integrate.relationship_infer import build_cross_file_understanding
from tests.benchmark_multi import DATASETS_DIR
from tests.benchmark_multi.generate_datasets import ensure_datasets
from tests.benchmark_multi.schema import load_all_cases


def _run_fixed(case_id: str):
    ensure_datasets(DATASETS_DIR, force=False)
    case = next(c for c in load_all_cases() if c.id == case_id)
    assert case.fixed_plan is not None
    sources = {
        Path(f).stem: pd.read_excel(DATASETS_DIR / f) for f in case.files
    }
    und = build_cross_file_understanding(
        list(sources.items()), infer_relationships=False
    ).to_dict()
    if case.fixed_relationships:
        und["relationships"] = list(case.fixed_relationships)

    def chat(prompt: str, **kwargs):  # noqa: ANN003
        del prompt, kwargs
        return dict(case.fixed_plan)

    return run_integration_pipeline(
        case.prompt,
        sources,
        und,
        max_retries=0,
        chat_json_fn=chat,
        model="feasibility-fixed",
    )


def test_feasibility_composite_join_only() -> None:
    pipe = _run_fixed("composite_key_join_001")
    assert pipe.status == "success"
    assert [s.op for s in pipe.plan.steps] == ["join"]
    assert pipe.final_output is not None
    for col in ("store_id", "product_id", "units", "unit_price"):
        assert col in pipe.final_output.columns


def test_feasibility_lookup_join_only() -> None:
    pipe = _run_fixed("lookup_join_001")
    assert pipe.status == "success"
    assert [s.op for s in pipe.plan.steps] == ["join"]
    assert pipe.final_output is not None
    assert "product_id" in pipe.final_output.columns
    assert "category_name" in pipe.final_output.columns


def test_feasibility_three_file_chain() -> None:
    pipe = _run_fixed("three_file_chain_001")
    assert pipe.status == "success"
    ops = [s.op for s in pipe.plan.steps]
    assert ops.count("join") >= 2
    assert "aggregate" in ops
    assert pipe.final_output is not None
    for col in ("customer_name", "category_name", "total_amount"):
        assert col in pipe.final_output.columns


def test_feasibility_dirty_rename_union() -> None:
    pipe = _run_fixed("dirty_multifile_001")
    assert pipe.status == "success"
    assert [s.op for s in pipe.plan.steps] == ["rename_columns", "union_rows"]
    assert pipe.final_output is not None
    assert len(pipe.final_output) == 4


def test_feasibility_ambiguous_cannot_plan() -> None:
    pipe = _run_fixed("ambiguous_keys_001")
    assert pipe.status == "cannot_plan"


def test_architecture_sufficiency_atomic_ops_only() -> None:
    """Correct finals use only existing atomic ops — no new DSL needed."""
    allowed = {
        "rename_columns",
        "filter_rows",
        "union_rows",
        "join",
        "aggregate",
        "select_columns",
    }
    for cid in (
        "composite_key_join_001",
        "lookup_join_001",
        "three_file_chain_001",
        "dirty_multifile_001",
    ):
        case = next(c for c in load_all_cases() if c.id == cid)
        ops = {s.get("op") for s in (case.fixed_plan or {}).get("steps") or []}
        assert ops <= allowed, (cid, ops)
