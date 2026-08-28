"""Phase 39D ablation harness: verifier materialization grounding variants."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from core.integrate.schema_lineage import build_schema_lineage
from core.integrate.semantic_verifier import run_semantic_verification

ROOT = Path(__file__).resolve().parents[2]
FIX39B = ROOT / "tests/benchmark_multi/fixtures/phase39b/c2_and_controls.json"
FIX39C = ROOT / "tests/benchmark_multi/fixtures/phase39c/offline_generalization.json"
FIX_FP = ROOT / "tests/benchmark_multi/fixtures/phase39d/live_fp_finance.json"
FIX_FF = ROOT / "tests/benchmark_multi/fixtures/phase39d/live_ff_energy.json"
FIX_CONS = ROOT / "tests/benchmark_multi/fixtures/phase39d/live_consistency_set.json"
OUT = ROOT / "benchmark_results/multi/phase39d"

VERIFIER_MODEL = "qwen2.5:7b"

MODES = {
    "V0": "none",
    "V1": "final_schema",
    "V2": "lineage",
    "V3": "lineage_claims_separated",
}


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _verify(
    prompt: str,
    plan: dict[str, Any],
    *,
    mode: str,
    source_schemas: dict[str, list[str]] | None,
) -> dict[str, Any]:
    t0 = time.time()
    r = run_semantic_verification(
        user_prompt=prompt,
        plan=plan,
        variant="V1",
        model=VERIFIER_MODEL,
        independent=True,
        source_schemas=source_schemas,
        materialization_mode=mode,
    )
    return {
        "verdict": r.verdict,
        "reason_code": r.reason_code,
        "evidence": list(r.evidence or []),
        "elapsed_s": round(time.time() - t0, 3),
        "parse_ok": r.parse_ok,
        "error": r.error,
    }


def run_ablation(variants: list[str] | None = None) -> dict[str, Any]:
    variants = variants or list(MODES)
    OUT.mkdir(parents=True, exist_ok=True)

    fp = _load(FIX_FP)
    ff = _load(FIX_FF)
    consistency = _load(FIX_CONS)["cases"]

    offline: list[dict[str, Any]] = []
    for c in _load(FIX39B)["cases"]:
        offline.append(
            {
                "id": f"P39B-{c['id']}",
                "family": c["family"],
                "prompt": c["prompt"],
                "plan": c["plan"],
                "source_schemas": c.get("source_schemas"),
            }
        )
    if FIX39C.exists():
        for c in _load(FIX39C)["cases"]:
            offline.append(
                {
                    "id": c["id"],
                    "family": c["family"],
                    "prompt": c["prompt"],
                    "plan": c["plan"],
                    "source_schemas": c.get("source_schemas"),
                }
            )

    report: dict[str, Any] = {
        "phase": "39D",
        "model": VERIFIER_MODEL,
        "variants": {},
        "lineage_smoke": {
            "fp": build_schema_lineage(fp["plan"], fp["source_schemas"]),
            "ff": build_schema_lineage(ff["plan"], ff["source_schemas"]),
        },
    }

    for vname in variants:
        mode = MODES[vname]
        rows: list[dict[str, Any]] = []

        for tag, fx, expect_pass in [
            ("LIVE_FP_finance", fp, False),
            ("LIVE_FF_energy", ff, True),
        ]:
            ver = _verify(
                fx["prompt"],
                fx["plan"],
                mode=mode,
                source_schemas=fx["source_schemas"],
            )
            ok = (ver["verdict"] == "pass") if expect_pass else (ver["verdict"] != "pass")
            rows.append(
                {
                    "id": tag,
                    "bucket": "live_anchor",
                    "expect_pass": expect_pass,
                    "ok": ok,
                    **ver,
                }
            )

        for c in consistency:
            ver = _verify(
                c["prompt"],
                c["plan"],
                mode=mode,
                source_schemas=c.get("source_schemas"),
            )
            if "expected_verdict" in c:
                ok = ver["verdict"] == c["expected_verdict"]
            else:
                ok = ver["verdict"] != c.get("expected_verdict_not", "pass")
            rows.append({"id": c["id"], "bucket": c["family"], "ok": ok, **ver})

        for c in offline:
            ver = _verify(
                c["prompt"],
                c["plan"],
                mode=mode,
                source_schemas=c.get("source_schemas"),
            )
            if c["family"] == "c2_wrong":
                ok = ver["verdict"] != "pass"
            elif c["family"] == "valid_control":
                ok = ver["verdict"] != "fail"
            else:
                ok = ver["verdict"] != "pass" if "wrong" in c["family"] else ver["verdict"] != "fail"
            rows.append(
                {
                    "id": c["id"],
                    "bucket": f"offline_{c['family']}",
                    "ok": ok,
                    **ver,
                }
            )

        def _summ(pred) -> dict[str, Any]:
            subset = [r for r in rows if pred(r)]
            return {
                "n": len(subset),
                "ok": sum(1 for r in subset if r["ok"]),
                "pass": sum(1 for r in subset if r["verdict"] == "pass"),
                "fail": sum(1 for r in subset if r["verdict"] == "fail"),
                "uncertain": sum(1 for r in subset if r["verdict"] == "uncertain"),
                "mean_s": round(
                    sum(float(r["elapsed_s"]) for r in subset) / max(len(subset), 1),
                    3,
                ),
            }

        fp_row = next(r for r in rows if r["id"] == "LIVE_FP_finance")
        ff_row = next(r for r in rows if r["id"] == "LIVE_FF_energy")
        summary = {
            "live_fp_non_pass": fp_row["verdict"] != "pass",
            "live_ff_pass": ff_row["verdict"] == "pass",
            "dual_side": _summ(lambda r: r["bucket"] == "dual_side_correct"),
            "aspirational": _summ(lambda r: r["bucket"] == "aspirational_ungrounded"),
            "valid_non_comparison": _summ(
                lambda r: r["bucket"] == "valid_non_comparison"
            ),
            "roles_absent_valid": _summ(lambda r: r["bucket"] == "roles_absent_valid"),
            "offline_c2": _summ(lambda r: r["bucket"] == "offline_c2_wrong"),
            "offline_valid": _summ(lambda r: r["bucket"] == "offline_valid_control"),
            "all_mean_s": round(
                sum(float(r["elapsed_s"]) for r in rows) / max(len(rows), 1), 3
            ),
        }
        report["variants"][vname] = {
            "materialization_mode": mode,
            "summary": summary,
            "rows": rows,
        }
        print(vname, json.dumps(summary, ensure_ascii=False))

    out_path = OUT / "ablation_v0_v3.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote", out_path)
    return report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variants", nargs="*", default=list(MODES))
    args = ap.parse_args()
    run_ablation(args.variants)


if __name__ == "__main__":
    main()
