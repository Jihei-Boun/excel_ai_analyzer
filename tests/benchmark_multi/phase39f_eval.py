"""Phase 39F: dual-side false-fail diagnosis + evidence ablation."""

from __future__ import annotations

import argparse
import json
import statistics
import time
from collections import Counter
from pathlib import Path
from typing import Any

from core.integrate.schema_lineage import build_schema_lineage
from core.integrate.semantic_verifier import run_semantic_verification

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "tests/benchmark_multi/fixtures/phase39f"
OUT = ROOT / "benchmark_results/multi/phase39f"
MODEL = "qwen2.5:7b"

MODES = {
    "V1": "final_schema",
    "V2": "final_schema_origins",
    "V3": "lineage_origins",
    "V4": "full_lineage",
}


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _verify(prompt: str, plan: dict, schemas: dict | None, mode: str) -> dict:
    t0 = time.time()
    r = run_semantic_verification(
        user_prompt=prompt,
        plan=plan,
        variant="V1",
        model=MODEL,
        independent=True,
        source_schemas=schemas,
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


def _cases() -> list[dict]:
    rows: list[dict] = []
    p14 = _load(FIX / "p39e14_frozen.json")
    rows.append(
        {
            "id": "P39E-14",
            "bucket": "live_false_fail",
            "expect_pass": True,
            "prompt": p14["prompt"],
            "plan": p14["plan"],
            "source_schemas": p14["source_schemas"],
        }
    )
    for c in _load(FIX / "dual_side_valid.json")["cases"]:
        rows.append(
            {
                "id": c["id"],
                "bucket": "valid_dual_side",
                "expect_pass": True,
                "prompt": c["prompt"],
                "plan": c["plan"],
                "source_schemas": c["source_schemas"],
            }
        )
    for c in _load(FIX / "dual_side_wrong.json")["cases"]:
        rows.append(
            {
                "id": c["id"],
                "bucket": "wrong_collapse_or_aspirational",
                "expect_pass": False,
                "prompt": c["prompt"],
                "plan": c["plan"],
                "source_schemas": c["source_schemas"],
            }
        )
    for c in _load(FIX / "non_comparison_controls.json")["cases"]:
        rows.append(
            {
                "id": c["id"],
                "bucket": "valid_non_comparison",
                "expect_pass": True,
                "prompt": c["prompt"],
                "plan": c["plan"],
                "source_schemas": c["source_schemas"],
            }
        )
    return rows


def _ok(expect_pass: bool, verdict: str) -> bool:
    if expect_pass:
        return verdict == "pass"
    return verdict != "pass"


def run_ablation(variants: list[str] | None = None) -> dict:
    variants = variants or list(MODES)
    OUT.mkdir(parents=True, exist_ok=True)
    cases = _cases()
    report: dict[str, Any] = {"phase": "39F", "model": MODEL, "variants": {}}

    for vname in variants:
        mode = MODES[vname]
        rows = []
        for c in cases:
            ver = _verify(c["prompt"], c["plan"], c["source_schemas"], mode)
            rows.append(
                {
                    "id": c["id"],
                    "bucket": c["bucket"],
                    "expect_pass": c["expect_pass"],
                    "ok": _ok(c["expect_pass"], ver["verdict"]),
                    **ver,
                }
            )
            print(
                f"{vname} {c['id']}: {ver['verdict']} "
                f"({'ok' if rows[-1]['ok'] else 'BAD'}) {ver['elapsed_s']}s"
            )
        by_bucket: dict[str, dict] = {}
        for b in sorted({r["bucket"] for r in rows}):
            subset = [r for r in rows if r["bucket"] == b]
            by_bucket[b] = {
                "n": len(subset),
                "ok": sum(1 for r in subset if r["ok"]),
                "verdicts": dict(Counter(r["verdict"] for r in subset)),
                "false_fail": sum(
                    1
                    for r in subset
                    if r["expect_pass"] and r["verdict"] != "pass"
                ),
                "silent_wrong": sum(
                    1
                    for r in subset
                    if (not r["expect_pass"]) and r["verdict"] == "pass"
                ),
            }
        report["variants"][vname] = {
            "mode": mode,
            "rows": rows,
            "by_bucket": by_bucket,
            "overall_ok": sum(1 for r in rows if r["ok"]),
            "overall_n": len(rows),
            "false_fail_total": sum(
                1 for r in rows if r["expect_pass"] and r["verdict"] != "pass"
            ),
            "silent_wrong_total": sum(
                1 for r in rows if (not r["expect_pass"]) and r["verdict"] == "pass"
            ),
            "mean_latency_s": round(
                statistics.mean(r["elapsed_s"] for r in rows), 3
            ),
        }
        print(
            vname,
            "ok",
            report["variants"][vname]["overall_ok"],
            "/",
            report["variants"][vname]["overall_n"],
            "FF",
            report["variants"][vname]["false_fail_total"],
            "SW",
            report["variants"][vname]["silent_wrong_total"],
        )

    (OUT / "ablation_v1_v4.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return report


def run_stability(n: int = 5, modes: list[str] | None = None) -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    cases = _cases()
    by_id = {c["id"]: c for c in cases}
    valids = [c for c in cases if c["bucket"] == "valid_dual_side"][:3]
    wrongs = [c for c in cases if c["bucket"] == "wrong_collapse_or_aspirational"][:3]
    selected = [by_id["P39E-14"]] + valids + wrongs

    modes = modes or ["final_schema", "final_schema_origins"]
    out: dict[str, Any] = {"n_repeats": n, "modes": {}}
    for mode in modes:
        mode_rows: dict[str, Any] = {}
        for c in selected:
            runs = []
            for i in range(n):
                ver = _verify(c["prompt"], c["plan"], c["source_schemas"], mode)
                runs.append(ver)
                print(mode, c["id"], i + 1, ver["verdict"], ver["reason_code"])
            counts = dict(Counter(r["verdict"] for r in runs))
            mode_rows[c["id"]] = {
                "expect_pass": c["expect_pass"],
                "bucket": c["bucket"],
                "counts": counts,
                "stable_correct": (
                    counts.get("pass", 0) == n
                    if c["expect_pass"]
                    else counts.get("pass", 0) == 0
                ),
                "runs": runs,
            }
        out["modes"][mode] = mode_rows
    (OUT / "stability.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return out


def evidence_audit_p39e14() -> dict:
    fx = _load(FIX / "p39e14_frozen.json")
    lin = build_schema_lineage(
        fx["plan"], fx["source_schemas"], include_intermediates=True
    )
    origins = lin.get("final_column_origins") or {}
    audit = {
        "prompt": fx["prompt"],
        "ops": [s.get("op") for s in fx["plan"].get("steps") or []],
        "final_schema": lin.get("final_schema"),
        "final_column_origins": origins,
        "source_files_represented_in_final": lin.get(
            "source_files_represented_in_final"
        ),
        "claimed_absent": lin.get("claimed_columns_absent_from_final"),
        "unresolved": lin.get("unresolved_column_refs"),
        "structural_events": lin.get("structural_events"),
        "both_sides_objectively_present": set(lin.get("final_schema") or [])
        >= {"site_id", "kwh_p1", "kwh_p2"},
        "distinct_source_files_for_metrics": sorted(
            {
                (origins.get("kwh_p1") or [{}])[0].get("source"),
                (origins.get("kwh_p2") or [{}])[0].get("source"),
            }
        ),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "p39e14_evidence_audit.json").write_text(
        json.dumps(audit, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return audit


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ablation", action="store_true")
    ap.add_argument("--stability", action="store_true")
    ap.add_argument("--audit", action="store_true")
    ap.add_argument("--variants", nargs="*", default=None)
    ap.add_argument("--n", type=int, default=5)
    ap.add_argument("--stability-modes", nargs="*", default=None)
    args = ap.parse_args()
    if args.audit:
        print(json.dumps(evidence_audit_p39e14(), indent=2, ensure_ascii=False))
    if args.ablation:
        run_ablation(args.variants)
    if args.stability:
        run_stability(args.n, args.stability_modes)
    if not (args.audit or args.ablation or args.stability):
        evidence_audit_p39e14()
        run_ablation(args.variants)
        run_stability(args.n, args.stability_modes)


if __name__ == "__main__":
    main()
