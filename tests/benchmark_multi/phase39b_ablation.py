"""Phase 39B offline ablation: independent verifier on frozen C2 / valid plans."""

from __future__ import annotations

import json
import time
from pathlib import Path

from core.integrate.semantic_verifier import run_semantic_verification

FIX = Path("tests/benchmark_multi/fixtures/phase39b/c2_and_controls.json")
OUT = Path("benchmark_results/multi/phase39b")


def _run_case(case: dict, *, independent: bool) -> dict:
    t0 = time.time()
    result = run_semantic_verification(
        user_prompt=case["prompt"],
        plan=case["plan"],
        variant="V1",
        model="qwen2.5:7b",
        independent=independent,
    )
    elapsed = round(time.time() - t0, 3)
    return {
        "id": case["id"],
        "family": case["family"],
        "independent": independent,
        "verdict": result.verdict,
        "reason_code": result.reason_code,
        "evidence": result.evidence,
        "elapsed_s": elapsed,
        "expected_verdict": case.get("expected_verdict"),
        "expected_verdict_not": case.get("expected_verdict_not"),
    }


def _score(rows: list[dict]) -> dict:
    c2 = [r for r in rows if r["family"] == "c2_wrong"]
    vc = [r for r in rows if r["family"] == "valid_control"]
    c2_recall = (
        sum(1 for r in c2 if r["verdict"] != "pass") / len(c2) if c2 else None
    )
    vc_fp = (
        sum(1 for r in vc if r["verdict"] == "fail") / len(vc) if vc else None
    )
    # treat uncertain on valid as soft FP for reporting
    vc_non_pass = (
        sum(1 for r in vc if r["verdict"] != "pass") / len(vc) if vc else None
    )
    return {
        "n_c2": len(c2),
        "n_valid": len(vc),
        "c2_wrong_recall_non_pass": None if c2_recall is None else round(c2_recall, 3),
        "valid_control_fail_fp": None if vc_fp is None else round(vc_fp, 3),
        "valid_control_non_pass_rate": None
        if vc_non_pass is None
        else round(vc_non_pass, 3),
        "mean_elapsed_s": round(sum(r["elapsed_s"] for r in rows) / max(len(rows), 1), 3),
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    data = json.loads(FIX.read_text(encoding="utf-8"))
    cases = data["cases"]

    # Variant B: independent verifier only (frozen plans)
    rows_b = [_run_case(c, independent=True) for c in cases]
    # Ablation: legacy mixed payload (independent=False)
    rows_legacy = [_run_case(c, independent=False) for c in cases]

    report = {
        "baseline_git": "1bd720497a82462a9a434c267f0975333ae46515",
        "note": (
            "Variant A (planner roles only) is not separately runnable offline "
            "without live planner calls; Variant B/C share the independent "
            "verifier path on frozen plans. Legacy=independent=False ablation."
        ),
        "variant_B_independent": {
            "rows": rows_b,
            "metrics": _score(rows_b),
        },
        "ablation_legacy_mixed_payload": {
            "rows": rows_legacy,
            "metrics": _score(rows_legacy),
        },
    }
    (OUT / "phase39b_ablation.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report["variant_B_independent"]["metrics"], indent=2))
    print(json.dumps(report["ablation_legacy_mixed_payload"]["metrics"], indent=2))
    for r in rows_b:
        print(r["id"], r["verdict"], r.get("expected_verdict") or f"not:{r.get('expected_verdict_not')}")


if __name__ == "__main__":
    main()
