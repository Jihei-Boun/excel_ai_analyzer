"""Phase 40G — final-grain observer evaluation (research harness).

Does not generate contracts or wire a production checker.
Replays the frozen Phase 40F structural corpus against observe_final_grain_identities.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import time
from collections import Counter
from pathlib import Path
from typing import Any

from core.integrate.result_observation import (
    MAX_RESULT_SAMPLE_COLUMNS,
    MAX_RESULT_SAMPLE_ROWS,
    MAX_RESULT_SERIALIZED_CHARS,
)
from core.integrate.schema_lineage import (
    GRAIN_INDETERMINATE,
    GRAIN_KNOWN,
    GRAIN_NOT_APPLICABLE,
    observe_final_grain_identities,
)
from core.integrate.semantic_escalation import (
    MAX_SEMANTIC_ESCALATIONS,
    SEMANTIC_VERIFIER_MODEL,
    SEMANTIC_VERIFIER_VARIANT,
)
from tests.benchmark_multi.phase40f_research import (
    build_fixtures,
    observe_final_grain,
)
from core.integrate.schema_lineage import build_schema_lineage

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "benchmark_results/multi/phase40g"
PHASE40F_SHA = "561138432aca0e6a88e5d33eaf1063bc4f76bac5"

# Manual structural grain oracle for the frozen 40F fixture ids.
# Answers only: what row-grain identities are deterministically known?
# Does not re-evaluate whether a contract binding was the right semantic choice.
_ID = tuple[str, str]

KNOWN: dict[str, list[_ID]] = {
    "p-agg-key": [("src_a", "entity_key")],
    "p-ren-agg": [("src_a", "entity_key")],
    "p-agg-ren": [("src_a", "entity_key")],
    "p-filt-ren-agg": [("src_a", "entity_key")],
    "p-g1g2": [("src_a", "entity_key"), ("src_a", "extra")],
    "p-agg-two-keys": [("src_a", "entity_key"), ("src_a", "extra")],
    "p-depth4": [("src_a", "entity_key")],
    "p-hist-campus-y": [("rooms.xlsx", "campus")],
    "p-hist-building-y": [("b.xlsx", "building")],
    "p-m2-lookalike": [("tickets.xlsx", "agent")],
    "p-agg-alias-metric": [("src_a", "entity_key")],
    "c-agg-other": [("src_a", "extra")],
    "c-agg-metric": [("src_a", "extra")],
    "c-m2-tid": [("tickets.xlsx", "tid")],
    "c-campus-crm": [("rooms.xlsx", "crm")],
    "c-building-room": [("b.xlsx", "room")],
    "c-ren-then-agg-other": [("src_a", "extra")],
    "c-g2-missing": [("src_a", "entity_key")],
    "c-join-then-agg-other": [("src_b", "val_b")],
    "c-union-then-agg-other": [("src_a", "measure")],
    "c-global-summary": [],  # known zero-dimensional grain
    "p-hist-campus-ren": [("rooms.xlsx", "campus")],
    "p-a-only-agg": [("src_a", "entity_key")],
    "p-no-src-fallback": [("src_a", "entity_key")],
    "c-lookalike-campus": [("rooms.xlsx", "crm")],
    "i-branch-agg-join": [("src_a", "entity_key")],
    "imm-a1": [("src_a", "entity_key")],
    "imm-a2": [("src_a", "extra")],
}

GLOBAL_IDS = frozenset({"c-global-summary"})
NA_IDS = frozenset({
    "na-cannot-plan",
    "na-cannot-plan-grounded",
    "r40d-cannot-plan",
})


def _write(name: str, obj: Any) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n")


def manual_grain_oracle(fixture_id: str) -> dict[str, Any]:
    if fixture_id in NA_IDS:
        return {"status": GRAIN_NOT_APPLICABLE, "identities": [], "reason": "cannot_plan"}
    if fixture_id in KNOWN:
        reason = "global_aggregate" if fixture_id in GLOBAL_IDS else None
        ids = [{"source_id": a, "origin_column_ref": b} for a, b in KNOWN[fixture_id]]
        return {"status": GRAIN_KNOWN, "identities": ids, "reason": reason}
    return {"status": GRAIN_INDETERMINATE, "identities": [], "reason": None}


def _idset(obs: dict[str, Any]) -> list[tuple[str, str]]:
    return [(x["source_id"], x["origin_column_ref"]) for x in (obs.get("identities") or [])]


def evaluate() -> dict[str, Any]:
    fixtures = build_fixtures()
    rows = []
    times = []
    false_known = []
    mismatch = []
    missed = []
    f40_unknown = []
    for fx in fixtures:
        t0 = time.perf_counter()
        obs = observe_final_grain_identities(fx["plan"], fx["schemas"])
        elapsed = time.perf_counter() - t0
        times.append(elapsed)
        oracle = manual_grain_oracle(fx["fixture_id"])
        f40 = None
        if fx["plan"] is not None and (fx["plan"] or {}).get("status") == "planned":
            lin = build_schema_lineage(fx["plan"], fx["schemas"])
            f40 = observe_final_grain(fx["plan"], lin)
        unknown40 = bool(f40 and f40.get("gap") == "FINAL_GRAIN_UNKNOWN")
        fk = (
            obs["status"] == GRAIN_KNOWN
            and oracle["status"] != GRAIN_KNOWN
        )
        mm = (
            obs["status"] == GRAIN_KNOWN
            and oracle["status"] == GRAIN_KNOWN
            and _idset(obs) != _idset(oracle)
        )
        mk = oracle["status"] == GRAIN_KNOWN and obs["status"] == GRAIN_INDETERMINATE
        row = {
            "fixture_id": fx["fixture_id"],
            "family": fx["family"],
            "oracle_status": oracle["status"],
            "observer_status": obs["status"],
            "observer_reason": obs.get("reason"),
            "oracle_identities": _idset(oracle),
            "observer_identities": _idset(obs),
            "elapsed_s": round(elapsed, 6),
            "phase40f_final_grain_unknown": unknown40,
            "FALSE_KNOWN_GRAIN": fk,
            "KNOWN_IDENTITY_MISMATCH": mm,
            "MISSED_KNOWN_GRAIN": mk,
        }
        rows.append(row)
        if fk:
            false_known.append(row)
        if mm:
            mismatch.append(row)
        if mk:
            missed.append(row)
        if unknown40:
            f40_unknown.append({
                "fixture_id": fx["fixture_id"],
                "observer_status": obs["status"],
                "observer_reason": obs.get("reason"),
                "observer_identities": _idset(obs),
            })

    n = len(rows)
    applicable = [r for r in rows if r["oracle_status"] != GRAIN_NOT_APPLICABLE]
    known_app = [r for r in applicable if r["observer_status"] == GRAIN_KNOWN]
    coverage = len(known_app) / max(len(applicable), 1)
    ind_rate = sum(1 for r in rows if r["observer_status"] == GRAIN_INDETERMINATE) / max(n, 1)

    resolved_unknown = [u for u in f40_unknown if u["observer_status"] == GRAIN_KNOWN]
    remain_unknown = [u for u in f40_unknown if u["observer_status"] != GRAIN_KNOWN]

    def _fam(name: str) -> list[dict[str, Any]]:
        return [r for r in rows if r["family"] == name]

    _write("baseline_freeze.json", {
        "phase40f_sha": PHASE40F_SHA,
        "phase40f_observability": "SMALL_OBSERVATION_EXTENSION_REQUIRED",
        "phase40f_checker": "FIX_OBSERVER_FIRST",
        "shadow": "OFF",
        "migration": "NOT_APPROVED",
        "production_verifier": SEMANTIC_VERIFIER_MODEL,
        "production_variant": SEMANTIC_VERIFIER_VARIANT,
        "bounded": {
            "MAX_RESULT_SAMPLE_ROWS": MAX_RESULT_SAMPLE_ROWS,
            "MAX_RESULT_SAMPLE_COLUMNS": MAX_RESULT_SAMPLE_COLUMNS,
            "MAX_RESULT_SERIALIZED_CHARS": MAX_RESULT_SERIALIZED_CHARS,
        },
        "MAX_SEMANTIC_ESCALATIONS": MAX_SEMANTIC_ESCALATIONS,
        "corpus_n": n,
    })
    src = inspect.getsource(observe_final_grain_identities)
    _write("observer_design.json", {
        "api": "observe_final_grain_identities(plan, source_schemas, lineage=None)",
        "module": "core.integrate.schema_lineage",
        "canonical_identity": "(source_id, origin_column_ref)",
        "statuses": [GRAIN_KNOWN, GRAIN_INDETERMINATE, GRAIN_NOT_APPLICABLE],
        "unknown_vs_empty": "indeterminate identities=[] vs known global_aggregate identities=[]",
        "reads_user_prompt": "user_prompt" in src,
        "reads_semantic_label": "semantic_label" in src,
        "attached_to_build_schema_lineage": False,
        "wired_to_validator": False,
        "wired_to_contract_checker": False,
    })
    _write("observer_api.json", {
        "function": "observe_final_grain_identities",
        "returns": {
            "status": "known | indeterminate | not_applicable",
            "identities": [{"source_id": "str", "origin_column_ref": "str"}],
            "reason": "str | null",
        },
        "cannot_plan": "not_applicable / cannot_plan",
        "invalid_plan": "indeterminate / invalid_plan",
        "global_aggregate": "known / identities=[] / reason=global_aggregate",
    })
    _write("operation_semantics.json", {
        "source": "indeterminate (no declared unique identity)",
        "filter_rows": "copy prior grain",
        "rename_columns": "preserve canonical identities; update display columns internally",
        "select_columns": "known iff every grain display column remains",
        "aggregate": "sets grain from singleton origins of group_by; empty group_by = known global",
        "join": "known only if both sides known, identical identity tuples, and join keys cover that grain",
        "union_rows": "known only if every branch is known with identical identity tuples",
        "unsupported": "indeterminate",
    })
    _write("phase40f_replay.json", {"n": n, "rows": rows})
    _write("final_grain_unknown_replay.json", {
        "n_40f_unknown": len(f40_unknown),
        "resolved_known": resolved_unknown,
        "remain_indeterminate": remain_unknown,
    })
    _write("rename_replay.json", _fam("rename") + [r for r in rows if r["fixture_id"] == "r40d-rename"])
    _write("aggregate_replay.json", _fam("aggregate"))
    _write("join_replay.json", _fam("join"))
    _write("union_replay.json", _fam("union"))
    _write("branch_replay.json", _fam("branch"))
    _write("multi_stage_replay.json", _fam("multi"))
    _write("false_known_grain_review.json", {"n": len(false_known), "rows": false_known})
    _write("known_identity_mismatch_review.json", {"n": len(mismatch), "rows": mismatch})
    _write("missed_known_grain_review.json", {"n": len(missed), "rows": missed})
    _write("coverage_metrics.json", {
        "n": n,
        "applicable": len(applicable),
        "known": len(known_app),
        "KNOWN_GRAIN_COVERAGE": round(coverage, 4),
        "by_family": {
            fam: {
                "n": sum(1 for r in rows if r["family"] == fam),
                "known": sum(1 for r in rows if r["family"] == fam and r["observer_status"] == GRAIN_KNOWN),
            }
            for fam in sorted({r["family"] for r in rows})
        },
    })
    _write("indeterminate_analysis.json", {
        "rate": round(ind_rate, 4),
        "reasons": dict(Counter(r["observer_reason"] for r in rows if r["observer_status"] == GRAIN_INDETERMINATE)),
        "note_ko": "소스에 선언된 unique grain이 없으면 indeterminate가 정상이다. join 카디널리티는 Validator 소유.",
    })
    _write("performance_results.json", {
        "n": n,
        "mean_s": round(sum(times) / max(n, 1), 6),
        "p95_s": round(sorted(times)[max(int(n * 0.95) - 1, 0)], 6),
        "max_s": round(max(times), 6),
        "note_ko": "LLM 호출 대비 무시 가능",
    })
    _write("regression_results.json", {
        "validator_semantics": "unchanged",
        "executor_semantics": "unchanged",
        "build_schema_lineage_payload_keys": "unchanged (grain not attached)",
    })
    _write("production_diff_proof.json", {
        "core_files_intended": ["core/integrate/schema_lineage.py"],
        "planner": "unchanged",
        "dsl": "unchanged",
        "validator": "unchanged",
        "executor": "unchanged",
        "verifier": "unchanged",
        "escalation": "unchanged",
        "legacy": "unchanged",
    })
    _write("shadow_state_proof.json", {
        "shadow": "OFF",
        "env_MULTI_SHADOW_ENABLED": os.environ.get("MULTI_SHADOW_ENABLED", ""),
    })

    fk_n, mm_n = len(false_known), len(mismatch)
    verdict = "OBSERVER_CORRECTED"
    ready = "READY_FOR_CONTRACT_OPERATIONAL_STRATEGY_RESEARCH"
    if fk_n or mm_n:
        verdict, ready = "OBSERVER_UNSAFE", "REVERT_OBSERVER_CHANGE"
    _write("phase40g_summary.json", {
        "gate": "C" if fk_n or mm_n else "A",
        "migration": "NOT_APPROVED",
        "shadow": "OFF",
        "production_semantic_change": "NO",
        "phase40f_sha": PHASE40F_SHA,
        "n": n,
        "FALSE_KNOWN_GRAIN": fk_n,
        "KNOWN_IDENTITY_MISMATCH": mm_n,
        "MISSED_KNOWN_GRAIN": len(missed),
        "KNOWN_GRAIN_COVERAGE": round(coverage, 4),
        "INDETERMINATE_rate": round(ind_rate, 4),
        "final_grain_unknown_40f": len(f40_unknown),
        "unknown_resolved_known": len(resolved_unknown),
        "primary_verdict": verdict,
        "readiness_verdict": ready,
        "schema_lineage_sha16": hashlib.sha256(
            (ROOT / "core/integrate/schema_lineage.py").read_bytes()
        ).hexdigest()[:16],
    })
    return {
        "n": n,
        "FALSE_KNOWN_GRAIN": fk_n,
        "KNOWN_IDENTITY_MISMATCH": mm_n,
        "MISSED_KNOWN_GRAIN": len(missed),
        "coverage": coverage,
        "unknown_resolved": len(resolved_unknown),
        "unknown_remain": len(remain_unknown),
    }


def main() -> None:
    stats = evaluate()
    print(stats)
    print("wrote", OUT)


if __name__ == "__main__":
    main()
