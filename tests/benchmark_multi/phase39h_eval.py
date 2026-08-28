"""Phase 39H: provenance independence — fake vs genuine dual-side ablation."""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from pathlib import Path
from typing import Any

from core.integrate.schema_lineage import build_schema_lineage
from core.integrate.semantic_verifier import run_semantic_verification

ROOT = Path(__file__).resolve().parents[2]
FIX_H = ROOT / "tests/benchmark_multi/fixtures/phase39h"
FIX_F = ROOT / "tests/benchmark_multi/fixtures/phase39f"
FIX_D = ROOT / "tests/benchmark_multi/fixtures/phase39d"
FIX_B = ROOT / "tests/benchmark_multi/fixtures/phase39b"
OUT = ROOT / "benchmark_results/multi/phase39h"
MODEL = "qwen2.5:7b"

MODES = {
    "V2": "final_schema_origins",
    "V2.1": "final_schema_expr",
    "V2.2": "final_schema_expr_partition",
}


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _dump(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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


def _ok(expect_pass: bool, verdict: str) -> bool:
    return (verdict == "pass") if expect_pass else (verdict != "pass")


def phase39h_cases() -> list[dict]:
    rows: list[dict] = []
    for c in _load(FIX_H / "fake_dual_family.json")["cases"]:
        rows.append(
            {
                "id": c["id"],
                "bucket": "fake_dual",
                "expect_pass": False,
                "prompt": c["prompt"],
                "plan": c["plan"],
                "source_schemas": c["source_schemas"],
            }
        )
    p11 = _load(FIX_H / "p39g11_canonical.json")
    rows.append(
        {
            "id": "FD8",
            "bucket": "fake_dual",
            "expect_pass": False,
            "prompt": p11["prompt"],
            "plan": p11["plan"],
            "source_schemas": p11["source_schemas"],
        }
    )
    for c in _load(FIX_H / "genuine_same_origin_dual.json")["cases"]:
        rows.append(
            {
                "id": c["id"],
                "bucket": "genuine_same_origin",
                "expect_pass": True,
                "prompt": c["prompt"],
                "plan": c["plan"],
                "source_schemas": c["source_schemas"],
            }
        )
    return rows


def regression_cases() -> list[dict]:
    rows: list[dict] = []
    p14 = _load(FIX_F / "p39e14_frozen.json")
    rows.append(
        {
            "id": "P39E-14",
            "bucket": "p39f_valid_dual",
            "expect_pass": True,
            "prompt": p14["prompt"],
            "plan": p14["plan"],
            "source_schemas": p14["source_schemas"],
        }
    )
    for fname, bucket, expect_pass in [
        ("dual_side_valid.json", "p39f_valid_dual", True),
        ("dual_side_wrong.json", "p39f_wrong", False),
        ("non_comparison_controls.json", "non_comparison", True),
    ]:
        for c in _load(FIX_F / fname)["cases"]:
            rows.append(
                {
                    "id": c["id"],
                    "bucket": bucket,
                    "expect_pass": expect_pass,
                    "prompt": c["prompt"],
                    "plan": c["plan"],
                    "source_schemas": c["source_schemas"],
                }
            )
    for fname, bucket, expect_pass in [
        ("live_fp_finance.json", "aspirational", False),
        ("live_ff_energy.json", "p39d_energy_ff", True),
    ]:
        path = FIX_D / fname
        if not path.exists():
            continue
        obj = _load(path)
        for c in obj.get("cases") or ([obj] if "plan" in obj else []):
            rows.append(
                {
                    "id": c.get("id") or path.stem,
                    "bucket": bucket,
                    "expect_pass": expect_pass,
                    "prompt": c["prompt"],
                    "plan": c["plan"],
                    "source_schemas": c.get("source_schemas"),
                }
            )
    c2_path = FIX_B / "c2_and_controls.json"
    if c2_path.exists():
        for c in _load(c2_path).get("cases") or []:
            exp = bool(c.get("expect_pass", False))
            if c.get("expected_verdict") == "pass":
                exp = True
            if c.get("expected_verdict_not") == "pass":
                exp = False
            rows.append(
                {
                    "id": c.get("id") or "C2",
                    "bucket": "c2_collapse",
                    "expect_pass": exp,
                    "prompt": c["prompt"],
                    "plan": c["plan"],
                    "source_schemas": c.get("source_schemas"),
                }
            )
    return rows


def structural_audit(cases: list[dict]) -> dict:
    out: dict[str, Any] = {}
    for c in cases:
        ev = build_schema_lineage(c["plan"], c["source_schemas"])
        out[c["id"]] = {
            "final_schema": ev.get("final_schema"),
            "equivalent_groups": [
                g.get("final_columns")
                for g in (ev.get("equivalent_evidence_signature_groups") or [])
            ],
            "shared_singleton_origin_groups": ev.get("shared_singleton_origin_groups"),
        }
    return out


def run_ablation(cases: list[dict], modes: list[str] | None = None) -> dict:
    modes = modes or list(MODES.keys())
    results: dict[str, Any] = {"modes": {}, "by_case": {}}
    for mlabel in modes:
        mode = MODES[mlabel]
        mode_rows = []
        for c in cases:
            r = _verify(c["prompt"], c["plan"], c["source_schemas"], mode)
            ok = _ok(c["expect_pass"], r["verdict"])
            row = {
                "id": c["id"],
                "bucket": c["bucket"],
                "expect_pass": c["expect_pass"],
                "ok": ok,
                **r,
            }
            mode_rows.append(row)
            results["by_case"].setdefault(c["id"], {})[mlabel] = row
            print(
                f"[{mlabel}] {c['id']} expect_pass={c['expect_pass']} "
                f"-> {r['verdict']} ok={ok} ({r['elapsed_s']}s)"
            )
        counts = Counter(x["verdict"] for x in mode_rows)
        results["modes"][mlabel] = {
            "materialization_mode": mode,
            "n": len(mode_rows),
            "ok": sum(1 for x in mode_rows if x["ok"]),
            "verdict_counts": dict(counts),
            "false_pass": [
                x["id"]
                for x in mode_rows
                if (not x["expect_pass"]) and x["verdict"] == "pass"
            ],
            "false_fail": [
                x["id"]
                for x in mode_rows
                if x["expect_pass"] and x["verdict"] != "pass"
            ],
            "rows": mode_rows,
        }
    return results


def run_stability(case_ids: list[str], n: int = 5, mode_label: str = "V2.2") -> dict:
    cases = {c["id"]: c for c in phase39h_cases() + regression_cases()}
    mode = MODES[mode_label]
    out: dict[str, Any] = {"n": n, "mode": mode, "cases": {}}
    for cid in case_ids:
        if cid not in cases:
            out["cases"][cid] = {"error": "missing_case"}
            continue
        c = cases[cid]
        runs = []
        for i in range(n):
            r = _verify(c["prompt"], c["plan"], c["source_schemas"], mode)
            runs.append(r)
            print(f"[stab {cid} {i + 1}/{n}] {r['verdict']} {r['elapsed_s']}s")
        dist = Counter(x["verdict"] for x in runs)
        stable_ok = all(_ok(c["expect_pass"], x["verdict"]) for x in runs)
        out["cases"][cid] = {
            "expect_pass": c["expect_pass"],
            "distribution": dict(dist),
            "stable_ok": stable_ok,
            "runs": runs,
        }
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ablation", action="store_true")
    ap.add_argument("--stability", action="store_true")
    ap.add_argument("--regression", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--n", type=int, default=5)
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    h_cases = phase39h_cases()
    _dump(OUT / "structural_signature_audit.json", structural_audit(h_cases))

    if args.all or args.ablation:
        _dump(OUT / "ablation.json", run_ablation(h_cases))

    if args.all or args.stability:
        _dump(
            OUT / "stability.json",
            run_stability(
                ["FD1", "FD2", "FD5", "FD8", "GS1", "GS2", "P39E-14"],
                n=args.n,
                mode_label="V2.2",
            ),
        )

    if args.all or args.regression:
        _dump(OUT / "regression.json", run_ablation(regression_cases(), modes=["V2.2"]))


if __name__ == "__main__":
    main()
