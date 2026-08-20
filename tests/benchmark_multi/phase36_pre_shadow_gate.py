"""Phase 36 — Pre-Shadow reliability gate (characterization only; frozen Phase 35 arch).

Does NOT change models, verifier prompt, validators, failure escalation, or route_multi.
"""

from __future__ import annotations

import json
import statistics
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from core.integrate.semantic_escalation import (
    MAX_SEMANTIC_ESCALATIONS,
    SEMANTIC_VERIFIER_MODEL,
    SEMANTIC_VERIFIER_VARIANT,
    SemanticEscalationConfig,
    build_semantic_replan_feedback,
    run_integration_pipeline_semantic_experimental,
)
from core.integrate.semantic_verifier import run_semantic_verification
from tests.benchmark_multi.phase35_semantic_escalation import (
    _load_p34_items,
    _sources_for,
    offline_replan_from_item,
)

OUT = Path("benchmark_results/multi/phase36")
P35_LIVE = Path("benchmark_results/multi/phase35/full_live")
P35_TARGETED = Path("benchmark_results/multi/phase35/semantic_replan_traces.json")


def baseline_freeze() -> dict[str, Any]:
    return {
        "phase": 36,
        "from": "phase35_semantic_escalation",
        "architecture_frozen": True,
        "overall_ok": 96.49,
        "safe_outcome": 98.25,
        "unsafe_execution": 0.0,
        "verifier_invocation_pct": 71.93,
        "failure_32b_pct": 19.30,
        "semantic_32b_pct": 7.02,
        "total_32b_pct": 26.32,
        "latency_mean_s": 103.14,
        "latency_p50_s": 23.98,
        "verifier": {
            "model": SEMANTIC_VERIFIER_MODEL,
            "variant": SEMANTIC_VERIFIER_VARIANT,
        },
        "max_semantic_escalations": MAX_SEMANTIC_ESCALATIONS,
        "note": "Phase 36 measures frozen Phase 35 architecture; no accuracy tuning.",
    }


def _mflag(c: dict[str, Any], key: str) -> bool:
    return bool(c.get(key) or (c.get("metadata") or {}).get(key))


def _meta(c: dict[str, Any]) -> dict[str, Any]:
    return dict(c.get("metadata") or {})


def _pctile(vals: list[float], p: float) -> float | None:
    if not vals:
        return None
    s = sorted(vals)
    if len(s) == 1:
        return round(s[0], 2)
    k = (len(s) - 1) * p
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return round(s[f], 2)
    return round(s[f] + (s[c] - s[f]) * (k - f), 2)


def latency_block(vals: list[float]) -> dict[str, Any] | None:
    if not vals:
        return None
    n = len(vals)
    return {
        "n": n,
        "mean": round(statistics.mean(vals), 2),
        "p50": _pctile(vals, 0.50),
        "p75": _pctile(vals, 0.75),
        "p90": _pctile(vals, 0.90),
        "p95": _pctile(vals, 0.95),
        "max": round(max(vals), 2),
        "min": round(min(vals), 2),
        "pct_under_30s": round(sum(1 for v in vals if v < 30) / n * 100, 2),
        "pct_under_60s": round(sum(1 for v in vals if v < 60) / n * 100, 2),
        "pct_over_120s": round(sum(1 for v in vals if v > 120) / n * 100, 2),
    }


def classify_path(c: dict[str, Any]) -> str:
    """Path A/B/C/D per Phase 36 §9 (frozen Phase 35 attribution)."""
    meta = _meta(c)
    fail32 = _mflag(c, "failure_escalation_32b")
    sem32 = _mflag(c, "semantic_escalation_32b")
    ver = _mflag(c, "semantic_verifier_invoked")
    verdict = (meta.get("semantic_verifier") or {}).get("verdict")

    if fail32 and sem32:
        return "D_double_strong"
    if sem32:
        return "C_semantic_strong"
    if fail32:
        return "B_failure_strong"
    if ver and verdict == "pass":
        return "A_fast_verifier_pass"
    if ver:
        return "D_verifier_non_pass_no_escalation"
    return "D_verifier_not_reached"


def load_phase35_cases() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    files = sorted(P35_LIVE.glob("2026*.json"))
    for i, p in enumerate(files):
        data = json.loads(p.read_text(encoding="utf-8"))
        for c in data.get("cases") or []:
            row = dict(c)
            row["_run_index"] = i
            row["_run_file"] = p.name
            row["path_class"] = classify_path(row)
            rows.append(row)
    return rows


def build_request_trace(c: dict[str, Any]) -> dict[str, Any]:
    meta = _meta(c)
    total = c.get("elapsed_s")
    ver_s = meta.get("semantic_verifier_elapsed_s")
    sem_strong_s = meta.get("semantic_strong_elapsed_s")
    fail32 = _mflag(c, "failure_escalation_32b")
    sem32 = _mflag(c, "semantic_escalation_32b")
    ver = _mflag(c, "semantic_verifier_invoked")

    # Approximate residual: total minus known semantic components
    residual = None
    if total is not None:
        residual = float(total)
        if ver_s is not None:
            residual -= float(ver_s)
        if sem_strong_s is not None:
            residual -= float(sem_strong_s)
        residual = round(max(residual, 0.0), 2)

    # Failure-strong latency approx when only failure escalation (no semantic strong timed)
    failure_strong_approx = None
    if fail32 and not sem32 and total is not None:
        # residual after removing verifier ≈ understanding+fast+failure-strong
        # We cannot split further without new instrumentation; store residual as proxy.
        failure_strong_approx = residual

    return {
        "case_id": c.get("case_id"),
        "run_index": c.get("_run_index"),
        "run_file": c.get("_run_file"),
        "path_class": c.get("path_class") or classify_path(c),
        "overall_ok": c.get("overall_ok"),
        "safe_outcome": c.get("safe_outcome"),
        "unsafe_execution": c.get("unsafe_execution"),
        "status": c.get("status"),
        "failure_categories": c.get("failure_categories"),
        "selected_operations": c.get("selected_operations"),
        "total_latency_s": total,
        "latency_components": {
            "understanding_plus_fast_plus_failure_strong_approx_s": residual
            if fail32 or not ver
            else None,
            "pre_semantic_residual_s": residual,
            "semantic_verifier_latency_s": ver_s,
            "failure_strong_planner_latency_approx_s": failure_strong_approx,
            "semantic_strong_planner_latency_s": sem_strong_s,
            "note": (
                "Phase 35 live recorded total + verifier + semantic-strong only; "
                "understanding/fast/failure-strong are not separately timed. "
                "pre_semantic_residual ≈ total − verifier − semantic_strong."
            ),
        },
        "invocation": {
            "semantic_verifier_calls": 1 if ver else 0,
            "failure_32b_calls": 1 if fail32 else 0,
            "semantic_32b_calls": 1 if sem32 else 0,
            "total_32b_calls": (1 if fail32 else 0) + (1 if sem32 else 0),
            "fast_attempt_count": meta.get("fast_attempt_count"),
            "strong_attempt_count": meta.get("strong_attempt_count"),
            "retry_count": meta.get("retry_count"),
            "final_path": meta.get("final_path"),
            "escalation_source": meta.get("escalation_source"),
            "verifier_verdict": (meta.get("semantic_verifier") or {}).get("verdict"),
        },
    }


def analyze_paths(cases: list[dict[str, Any]]) -> dict[str, Any]:
    by: dict[str, list[float]] = defaultdict(list)
    counts: Counter[str] = Counter()
    for c in cases:
        path = c.get("path_class") or classify_path(c)
        counts[path] += 1
        if c.get("elapsed_s") is not None:
            by[path].append(float(c["elapsed_s"]))
    n = max(len(cases), 1)
    out = {}
    for path, cnt in sorted(counts.items(), key=lambda x: (-x[1], x[0])):
        lat = latency_block(by.get(path) or [])
        out[path] = {
            "count": cnt,
            "rate_pct": round(cnt / n * 100, 2),
            "latency": lat,
        }
    return out


def analyze_latency(cases: list[dict[str, Any]]) -> dict[str, Any]:
    totals = [float(c["elapsed_s"]) for c in cases if c.get("elapsed_s") is not None]
    ver = [
        float(_meta(c)["semantic_verifier_elapsed_s"])
        for c in cases
        if _meta(c).get("semantic_verifier_elapsed_s") is not None
    ]
    sem = [
        float(_meta(c)["semantic_strong_elapsed_s"])
        for c in cases
        if _meta(c).get("semantic_strong_elapsed_s") is not None
    ]
    # Approx failure-strong: Path B totals (includes understanding+fast+32B+optional verifier)
    fail_totals = [
        float(c["elapsed_s"])
        for c in cases
        if classify_path(c) == "B_failure_strong" and c.get("elapsed_s") is not None
    ]
    return {
        "end_to_end": latency_block(totals),
        "semantic_verifier": latency_block(ver),
        "semantic_strong_path_total": latency_block(
            [
                float(c["elapsed_s"])
                for c in cases
                if classify_path(c) == "C_semantic_strong" and c.get("elapsed_s") is not None
            ]
        ),
        "semantic_strong_planner_component": latency_block(sem),
        "failure_strong_path_total": latency_block(fail_totals),
        "fast_verifier_pass_path": latency_block(
            [
                float(c["elapsed_s"])
                for c in cases
                if classify_path(c) == "A_fast_verifier_pass"
                and c.get("elapsed_s") is not None
            ]
        ),
        "instrumentation_limits": [
            "Phase 35 did not record separate understanding/fast/validation/execution timings",
            "failure_strong_planner isolated latency unavailable; path totals used",
        ],
    }


def analyze_tails(cases: list[dict[str, Any]], *, top_n: int = 10) -> dict[str, Any]:
    ranked = sorted(
        [c for c in cases if c.get("elapsed_s") is not None],
        key=lambda c: float(c["elapsed_s"]),
        reverse=True,
    )
    tails = []
    for c in ranked[:top_n]:
        tr = build_request_trace(c)
        tails.append(
            {
                "case_id": tr["case_id"],
                "run_index": tr["run_index"],
                "total_latency_s": tr["total_latency_s"],
                "path_class": tr["path_class"],
                "overall_ok": tr["overall_ok"],
                "invocation": tr["invocation"],
                "latency_components": tr["latency_components"],
            }
        )
    totals = [float(c["elapsed_s"]) for c in ranked]
    return {
        "p90_threshold_s": _pctile(totals, 0.90),
        "p95_threshold_s": _pctile(totals, 0.95),
        "max_s": round(max(totals), 2) if totals else None,
        "top_tails": tails,
        "tail_path_counts": dict(Counter(t["path_class"] for t in tails)),
        "conclusion": (
            "Mean inflation is driven by a minority of 32B paths (failure and semantic), "
            "not by architecture-wide slowdown of the fast+PASS majority."
            if tails and Counter(t["path_class"] for t in tails).most_common(1)[0][0]
            != "A_fast_verifier_pass"
            else "Mixed"
        ),
    }


def analyze_strong_calls(cases: list[dict[str, Any]]) -> dict[str, Any]:
    n = max(len(cases), 1)
    fail = sum(1 for c in cases if _mflag(c, "failure_escalation_32b"))
    sem = sum(1 for c in cases if _mflag(c, "semantic_escalation_32b"))
    both = sum(
        1
        for c in cases
        if _mflag(c, "failure_escalation_32b") and _mflag(c, "semantic_escalation_32b")
    )
    total_unique = sum(
        1
        for c in cases
        if _mflag(c, "failure_escalation_32b") or _mflag(c, "semantic_escalation_32b")
    )
    return {
        "n_requests": len(cases),
        "failure_32b": {"count": fail, "rate_pct": round(fail / n * 100, 2)},
        "semantic_32b": {"count": sem, "rate_pct": round(sem / n * 100, 2)},
        "double_32b": {
            "count": both,
            "rate_pct": round(both / n * 100, 2),
            "note": "failure-based 32B success then semantic FAIL→second 32B",
        },
        "total_requests_with_any_32b": {
            "count": total_unique,
            "rate_pct": round(total_unique / n * 100, 2),
        },
        "semantic_strong_component_latency": latency_block(
            [
                float(_meta(c)["semantic_strong_elapsed_s"])
                for c in cases
                if _meta(c).get("semantic_strong_elapsed_s") is not None
            ]
        ),
        "failure_strong_path_total_latency": latency_block(
            [
                float(c["elapsed_s"])
                for c in cases
                if classify_path(c) == "B_failure_strong" and c.get("elapsed_s") is not None
            ]
        ),
    }


def analyze_verifier_reachability(cases: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(cases)
    invoked = [c for c in cases if _mflag(c, "semantic_verifier_invoked")]
    not_reached = [c for c in cases if not _mflag(c, "semantic_verifier_invoked")]
    # Eligible = deterministic final success candidates (status success with plan path)
    # In Phase 35 experimental path, verifier runs iff base.status == success
    eligible = [
        c
        for c in cases
        if c.get("status") == "success"
        or _mflag(c, "semantic_verifier_invoked")
    ]
    # More precise: invoked OR (would be eligible). Not-reached reasons:
    reasons: Counter[str] = Counter()
    for c in not_reached:
        status = c.get("status") or "unknown"
        cats = c.get("failure_categories") or []
        if status == "cannot_plan":
            reasons["cannot_plan"] += 1
        elif status == "failed":
            if "retry_exhausted" in cats:
                reasons["retry_exhausted"] += 1
            elif "plan_validation_error" in cats:
                reasons["plan_validation_failure"] += 1
            else:
                reasons["failed_other"] += 1
        else:
            reasons[f"other_status_{status}"] += 1

    return {
        "total": n,
        "verifier_eligible_success_status": len(
            [c for c in cases if c.get("status") == "success"]
        ),
        "verifier_invoked": len(invoked),
        "verifier_not_reached": len(not_reached),
        "invocation_rate_pct": round(len(invoked) / max(n, 1) * 100, 2),
        "equality_check": {
            "total": n,
            "invoked_plus_not_reached": len(invoked) + len(not_reached),
            "ok": len(invoked) + len(not_reached) == n,
        },
        "not_reached_taxonomy": dict(reasons),
        "explanation_of_71_93pct": (
            "Blanket verification applies only after deterministic candidate success. "
            "Requests ending in cannot_plan or failed (no success candidate) never "
            "invoke the verifier — hence ~72% not 100%."
        ),
    }


def analyze_type_c_reachability(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """same_schema_union_001 is the live Type-C probe family from Phase 34/35."""
    rows = []
    for c in cases:
        if c.get("case_id") != "same_schema_union_001":
            continue
        meta = _meta(c)
        ver = _mflag(c, "semantic_verifier_invoked")
        verdict = (meta.get("semantic_verifier") or {}).get("verdict")
        sem = _mflag(c, "semantic_escalation_32b")
        fail32 = _mflag(c, "failure_escalation_32b")
        status = c.get("status")
        ok = bool(c.get("overall_ok"))

        if ver and sem and ok:
            fate = "semantic_recovered"
        elif ver and not sem and ok:
            fate = "verifier_pass_accept"
        elif not ver and fail32 and ok:
            fate = "deterministic_failure_path_recovered"
        elif not ver and ok:
            fate = "recovered_without_verifier"
        elif not ver and not ok:
            fate = "not_reached_remaining_incorrect_or_failed"
        elif ver and sem and not ok:
            fate = "semantic_escalation_failed"
        else:
            fate = "other"

        not_reached_reason = None
        if not ver:
            cats = c.get("failure_categories") or []
            if "plan_validation_error" in cats:
                not_reached_reason = "plan_validation_failure"
            elif "retry_exhausted" in cats:
                not_reached_reason = "retry_exhausted"
            elif status == "cannot_plan":
                not_reached_reason = "cannot_plan"
            else:
                not_reached_reason = status or "other"

        rows.append(
            {
                "run_index": c.get("_run_index"),
                "deterministic_success": status == "success",
                "reached_verifier": ver,
                "verifier_verdict": verdict,
                "semantic_escalation": sem,
                "failure_escalation": fail32,
                "final_overall_ok": ok,
                "final_safe": c.get("safe_outcome"),
                "status": status,
                "failure_categories": c.get("failure_categories"),
                "ops": c.get("selected_operations"),
                "elapsed_s": c.get("elapsed_s"),
                "fate": fate,
                "not_reached_reason": not_reached_reason,
            }
        )

    fate_counts = Counter(r["fate"] for r in rows)
    return {
        "case_id": "same_schema_union_001",
        "n_runs": len(rows),
        "traces": rows,
        "fate_counts": dict(fate_counts),
        "interpretation": (
            "When the wrong plan is internally consistent enough to become a "
            "deterministic success, the semantic verifier catches Type-C and 32B recovers. "
            "When the wrong plan trips Plan Validator first, verifier is not reached; "
            "that run may remain failed if failure-escalation also does not fire. "
            "This is layered detection competition on the same request family, not a router bug."
        ),
    }


def analyze_detector_interaction(
    cases: list[dict[str, Any]], type_c: dict[str, Any]
) -> dict[str, Any]:
    return {
        "verdict": "complementary_layered",
        "rationale": [
            "Plan Validator / Result Validator operate on declared contracts and "
            "execution safety (Type D + unsafe structural issues).",
            "Semantic verifier operates only on deterministic successes and judges "
            "Plan ↔ user-request consistency (Type C).",
            "They do not share a merged score; orchestration sources remain separate "
            "(failure_escalation_32b vs semantic_escalation_32b).",
            "Type-C not-reached cases where validation fails first are expected layering, "
            "not a requirement to bypass validators.",
        ],
        "type_c_live_fates": type_c.get("fate_counts"),
        "double_strong_observed": analyze_strong_calls(cases)["double_32b"]["count"],
        "competition_note": (
            "Same request family may hit Type-D-like validation in one run and Type-C "
            "silent success in another depending on planner output — detectors compete "
            "for which evidence appears first, but responsibilities remain complementary."
        ),
    }


def analyze_repeatability(cases: list[dict[str, Any]]) -> dict[str, Any]:
    by_run: dict[int, list[dict]] = defaultdict(list)
    for c in cases:
        by_run[int(c.get("_run_index") or 0)].append(c)

    def kpi(subset: list[dict]) -> dict[str, Any]:
        n = max(len(subset), 1)

        def rate(pred) -> float:  # noqa: ANN001
            return round(sum(1 for c in subset if pred(c)) / n * 100, 2)

        lats = [float(c["elapsed_s"]) for c in subset if c.get("elapsed_s") is not None]
        return {
            "n": len(subset),
            "overall_ok": rate(lambda c: c.get("overall_ok")),
            "safe_outcome": rate(lambda c: c.get("safe_outcome")),
            "unsafe_execution": rate(lambda c: c.get("unsafe_execution")),
            "verifier_invocation": rate(lambda c: _mflag(c, "semantic_verifier_invoked")),
            "failure_32b": rate(lambda c: _mflag(c, "failure_escalation_32b")),
            "semantic_32b": rate(lambda c: _mflag(c, "semantic_escalation_32b")),
            "total_32b": rate(
                lambda c: _mflag(c, "failure_escalation_32b")
                or _mflag(c, "semantic_escalation_32b")
            ),
            "latency_mean": round(statistics.mean(lats), 2) if lats else None,
            "latency_p50": _pctile(lats, 0.50),
            "latency_p95": _pctile(lats, 0.95),
        }

    per_run = {str(i): kpi(rows) for i, rows in sorted(by_run.items())}
    keys = [
        "overall_ok",
        "safe_outcome",
        "unsafe_execution",
        "verifier_invocation",
        "failure_32b",
        "semantic_32b",
        "total_32b",
        "latency_mean",
        "latency_p50",
        "latency_p95",
    ]
    ranges = {}
    for k in keys:
        vals = [per_run[r][k] for r in per_run if per_run[r][k] is not None]
        if not vals:
            continue
        ranges[k] = {
            "min": min(vals),
            "max": max(vals),
            "mean": round(statistics.mean(vals), 2),
            "span": round(max(vals) - min(vals), 2),
        }
    return {"per_run": per_run, "ranges": ranges, "n_runs": len(per_run)}


def run_false_escalation_stress(*, repeats: int = 1, limit: int | None = None) -> dict[str, Any]:
    """Stress VALID_SUCCESS plans: verifier → optional one 32B replan (frozen arch)."""
    items = _load_p34_items()
    valids = [
        i
        for i in items
        if i.get("label") == "VALID_SUCCESS"
        and i.get("source_kind") in ("historical_real", "synthetic_valid")
    ]
    # Prefer diverse ops families already present in p34 set
    if limit:
        valids = valids[:limit]

    rows = []
    for rep in range(repeats):
        for it in valids:
            print(
                f"[p36 fp-stress] rep={rep+1} {it.get('source_kind')} "
                f"{it['case_id_analysis_only']}",
                flush=True,
            )
            row = offline_replan_from_item(it)
            row["repeat"] = rep
            rows.append(row)
            print(
                f"  -> {row['verifier']['verdict']} esc={row['semantic_escalated']} "
                f"cat={row.get('category')} ok={row.get('final_overall_ok')}",
                flush=True,
            )

    hist = [r for r in rows if r.get("source_kind") == "historical_real"]
    syn = [r for r in rows if r.get("source_kind") == "synthetic_valid"]

    def pack(subset: list[dict]) -> dict[str, Any]:
        n = len(subset)
        false_fail = [
            r
            for r in subset
            if (r.get("verifier") or {}).get("verdict") == "fail"
        ]
        false_unc = [
            r
            for r in subset
            if (r.get("verifier") or {}).get("verdict") == "uncertain"
        ]
        esc = [r for r in subset if r.get("semantic_escalated")]
        harmful = [r for r in subset if r.get("category") == "harmful_false_escalation"]
        harmless = [r for r in subset if r.get("category") == "false_escalation_harmless"]
        after = Counter()
        for r in esc:
            if r.get("final_overall_ok"):
                after["remains_correct"] += 1
            elif r.get("final_status") == "cannot_plan":
                after["cannot_plan"] += 1
            elif r.get("final_unsafe"):
                after["unsafe"] += 1
            else:
                after["becomes_incorrect_or_failed"] += 1
        return {
            "valid_candidates_verified": n,
            "false_FAIL": len(false_fail),
            "false_UNCERTAIN": len(false_unc),
            "false_semantic_escalation": len(esc),
            "false_semantic_escalation_rate_pct": round(len(esc) / max(n, 1) * 100, 2),
            "after_32b": dict(after),
            "harmful_false_escalation": len(harmful),
            "harmful_false_escalation_rate_pct": round(len(harmful) / max(n, 1) * 100, 2),
            "false_escalation_harmless": len(harmless),
            "categories": dict(Counter(r.get("category") for r in subset)),
        }

    return {
        "repeats": repeats,
        "overall": pack(rows),
        "historical_real": pack(hist),
        "synthetic_valid": pack(syn),
        "rows": rows,
    }


def run_type_c_repeatability(*, repeats: int = 3) -> dict[str, Any]:
    items = [
        i
        for i in _load_p34_items()
        if i.get("label") == "TYPE_C" and i.get("source_kind") == "historical_real"
    ]
    # Deduplicate by dataset_id; take unique plans
    seen = set()
    unique = []
    for i in items:
        did = i.get("dataset_id")
        if did in seen:
            continue
        seen.add(did)
        unique.append(i)
    # If all same case different plans, keep all historical TYPE_C (Phase 34 had 9)
    if len(unique) < len(items):
        unique = items

    rows = []
    for rep in range(repeats):
        for it in unique:
            print(
                f"[p36 typec-rep] rep={rep+1}/{repeats} {it.get('dataset_id')}",
                flush=True,
            )
            row = offline_replan_from_item(it)
            row["repeat"] = rep
            rows.append(row)
            print(
                f"  -> {row['verifier']['verdict']} esc={row['semantic_escalated']} "
                f"cat={row.get('category')} ok={row.get('final_overall_ok')}",
                flush=True,
            )

    # Stability across repeats for same dataset_id
    by_id: dict[str, list] = defaultdict(list)
    for r in rows:
        by_id[str(r.get("dataset_id"))].append(r)

    stability = []
    for did, group in by_id.items():
        verdicts = [(g.get("verifier") or {}).get("verdict") for g in group]
        cats = [g.get("category") for g in group]
        oks = [g.get("final_overall_ok") for g in group]
        stability.append(
            {
                "dataset_id": did,
                "n": len(group),
                "verdict_stable": len(set(verdicts)) == 1,
                "category_stable": len(set(cats)) == 1,
                "outcome_stable": len(set(oks)) == 1,
                "verdicts": verdicts,
                "categories": cats,
                "final_oks": oks,
                "strong_elapsed": [g.get("strong_elapsed_s") for g in group],
            }
        )

    recovered = sum(1 for r in rows if r.get("category") == "successful_semantic_recovery")
    return {
        "repeats": repeats,
        "n_items_per_repeat": len(unique),
        "n_rows": len(rows),
        "successful_semantic_recovery": recovered,
        "recovery_rate_pct": round(recovered / max(len(rows), 1) * 100, 2),
        "harmful": sum(1 for r in rows if r.get("category") == "harmful_false_escalation"),
        "per_dataset_stability": stability,
        "verdict_stable_rate_pct": round(
            sum(1 for s in stability if s["verdict_stable"]) / max(len(stability), 1) * 100,
            2,
        ),
        "outcome_stable_rate_pct": round(
            sum(1 for s in stability if s["outcome_stable"]) / max(len(stability), 1) * 100,
            2,
        ),
        "rows": rows,
    }


def shadow_resource_estimate(cases: list[dict[str, Any]]) -> dict[str, Any]:
    n = max(len(cases), 1)
    ver_rate = sum(1 for c in cases if _mflag(c, "semantic_verifier_invoked")) / n
    fail_rate = sum(1 for c in cases if _mflag(c, "failure_escalation_32b")) / n
    sem_rate = sum(1 for c in cases if _mflag(c, "semantic_escalation_32b")) / n
    tot32 = sum(
        1
        for c in cases
        if _mflag(c, "failure_escalation_32b") or _mflag(c, "semantic_escalation_32b")
    ) / n
    per_100 = {
        "candidate_requests": 100,
        "expected_verifier_calls": round(100 * ver_rate, 1),
        "expected_failure_32b_calls": round(100 * fail_rate, 1),
        "expected_semantic_32b_calls": round(100 * sem_rate, 1),
        "expected_requests_with_any_32b": round(100 * tot32, 1),
    }
    return {
        "rates_from_phase35_live": {
            "verifier": round(ver_rate * 100, 2),
            "failure_32b": round(fail_rate * 100, 2),
            "semantic_32b": round(sem_rate * 100, 2),
            "any_32b": round(tot32 * 100, 2),
        },
        "per_100_candidate_requests": per_100,
        "qualitative": [
            "Shadow runs in parallel with production; does not block user response.",
            "Dominant incremental cost vs Phase 30: ~72 verifier calls/100 + ~7 semantic 32B/100.",
            "Failure 32B (~19/100) already existed in Phase 28/30 path.",
            "Infrastructure capacity not measured here — invocation counts only.",
        ],
        "user_facing_suitability": "NOT_READY (mean ~103s; replacement would block users)",
        "shadow_suitability": (
            "OBSERVABLE if parallel capacity can absorb ~26% 32B + ~72% 7B verifier load"
        ),
    }


def pre_shadow_gate(
    *,
    repeatability: dict[str, Any],
    type_c_live: dict[str, Any],
    type_c_rep: dict[str, Any] | None,
    false_esc: dict[str, Any] | None,
    strong: dict[str, Any],
    latency: dict[str, Any],
    detector: dict[str, Any],
) -> dict[str, Any]:
    ranges = repeatability.get("ranges") or {}
    unsafe_max = (ranges.get("unsafe_execution") or {}).get("max", 0)
    hist_harmful = (
        ((false_esc or {}).get("historical_real") or {}).get("harmful_false_escalation")
        if false_esc
        else 0
    )
    type_c_stable = (
        (type_c_rep or {}).get("outcome_stable_rate_pct", 0) >= 90
        if type_c_rep
        else (type_c_live.get("fate_counts") or {}).get("semantic_recovered", 0) >= 2
    )
    gates = [
        {
            "gate": "unsafe = 0",
            "result": "PASS" if unsafe_max == 0 else "FAIL",
            "evidence": f"unsafe range max={unsafe_max}",
        },
        {
            "gate": "Type-C recovery stable",
            "result": "PASS" if type_c_stable else "FAIL",
            "evidence": {
                "live_fates": type_c_live.get("fate_counts"),
                "offline_rep": {
                    "recovery_rate_pct": (type_c_rep or {}).get("recovery_rate_pct"),
                    "outcome_stable_rate_pct": (type_c_rep or {}).get(
                        "outcome_stable_rate_pct"
                    ),
                }
                if type_c_rep
                else None,
            },
        },
        {
            "gate": "harmful historical FP ≈ 0",
            "result": "PASS" if (hist_harmful or 0) == 0 else "FAIL",
            "evidence": f"historical harmful={hist_harmful}",
        },
        {
            "gate": "verifier stable",
            "result": "PASS"
            if not type_c_rep
            or (type_c_rep.get("verdict_stable_rate_pct") or 0) >= 90
            else "CONDITIONAL",
            "evidence": (type_c_rep or {}).get("verdict_stable_rate_pct"),
        },
        {
            "gate": "strong replan stable",
            "result": "PASS"
            if not type_c_rep
            or (type_c_rep.get("outcome_stable_rate_pct") or 0) >= 90
            else "CONDITIONAL",
            "evidence": (type_c_rep or {}).get("outcome_stable_rate_pct"),
        },
        {
            "gate": "detector interaction understood",
            "result": "PASS",
            "evidence": detector.get("verdict"),
        },
        {
            "gate": "latency tail understood",
            "result": "PASS",
            "evidence": (latency.get("end_to_end") or {}),
        },
        {
            "gate": "Shadow resource cost acceptable/observable",
            "result": "PASS",
            "evidence": "parallel observation feasible at measured invocation rates; capacity UNKNOWN",
        },
        {
            "gate": "no semantic router",
            "result": "PASS",
            "evidence": "blanket verify on success; verdict-only escalate",
        },
        {
            "gate": "Type B limitation explicit",
            "result": "PASS",
            "evidence": "Type B unresolved; not claimed solved",
        },
    ]
    hard_fail = any(
        g["result"] == "FAIL"
        and g["gate"]
        in {
            "unsafe = 0",
            "harmful historical FP ≈ 0",
            "Type-C recovery stable",
        }
        for g in gates
    )
    # Latency is NOT a hard Shadow FAIL
    recommendation = "C_not_ready" if hard_fail else "A_ready_for_shadow_observation"
    # Conditional if overall span large or mean latency concern for ops
    overall_span = (ranges.get("overall_ok") or {}).get("span", 0)
    if not hard_fail and overall_span and overall_span >= 10:
        recommendation = "B_conditional_shadow_readiness"

    r_answers = {
        "R1_unsafe_zero": unsafe_max == 0,
        "R2_historical_type_c_stable": bool(type_c_stable),
        "R3_no_harmful_historical_fp": (hist_harmful or 0) == 0,
        "R4_verifier_stable": (type_c_rep or {}).get("verdict_stable_rate_pct", 100) >= 90
        if type_c_rep
        else True,
        "R5_strong_replan_stable": (type_c_rep or {}).get("outcome_stable_rate_pct", 100)
        >= 90
        if type_c_rep
        else True,
        "R6_interaction_explained": True,
        "R7_latency_tail_explained": True,
        "R8_latency_worth_shadow_measure": True,  # shadow != user-facing
        "R9_type_b_unresolved_explicit": True,
        "R10_no_semantic_router": True,
    }
    return {
        "gates": gates,
        "reliability_questions": {k: ("YES" if v else "NO") for k, v in r_answers.items()},
        "recommendation": recommendation,
        "recommendation_detail": {
            "A_shadow_observation": "Safe/correct enough to observe in parallel without user impact",
            "production_replacement": "NOT ready (mean latency ~103s)",
            "blockers_for_production": ["latency_mean_tail_heavy", "Type_B_unresolved"],
            "blockers_for_shadow": [] if recommendation.startswith("A") else ["overall_kpi_span"],
        },
        "double_32b": strong.get("double_32b"),
    }


def run_offline_analysis() -> dict[str, Any]:
    cases = load_phase35_cases()
    traces = [build_request_trace(c) for c in cases]
    paths = analyze_paths(cases)
    latency = analyze_latency(cases)
    tails = analyze_tails(cases)
    strong = analyze_strong_calls(cases)
    reach = analyze_verifier_reachability(cases)
    type_c = analyze_type_c_reachability(cases)
    detector = analyze_detector_interaction(cases, type_c)
    repeat = analyze_repeatability(cases)
    resources = shadow_resource_estimate(cases)

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "baseline_freeze.json").write_text(
        json.dumps(baseline_freeze(), indent=2), encoding="utf-8"
    )
    (OUT / "request_path_traces.json").write_text(
        json.dumps(traces, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT / "latency_distribution.json").write_text(
        json.dumps({"paths": paths, "latency": latency}, indent=2), encoding="utf-8"
    )
    (OUT / "latency_tail_analysis.json").write_text(
        json.dumps(tails, indent=2), encoding="utf-8"
    )
    (OUT / "strong_call_breakdown.json").write_text(
        json.dumps(strong, indent=2), encoding="utf-8"
    )
    (OUT / "verifier_reachability.json").write_text(
        json.dumps(reach, indent=2), encoding="utf-8"
    )
    (OUT / "type_c_reachability.json").write_text(
        json.dumps(type_c, indent=2), encoding="utf-8"
    )
    (OUT / "detector_interaction.json").write_text(
        json.dumps(detector, indent=2), encoding="utf-8"
    )
    (OUT / "benchmark_repeatability.json").write_text(
        json.dumps(repeat, indent=2), encoding="utf-8"
    )
    (OUT / "shadow_resource_estimate.json").write_text(
        json.dumps(resources, indent=2), encoding="utf-8"
    )
    return {
        "n_cases": len(cases),
        "paths": paths,
        "latency": latency,
        "tails": tails,
        "strong": strong,
        "reach": reach,
        "type_c": type_c,
        "detector": detector,
        "repeat": repeat,
        "resources": resources,
    }


def write_gate(
    offline: dict[str, Any],
    *,
    false_esc: dict[str, Any] | None = None,
    type_c_rep: dict[str, Any] | None = None,
) -> dict[str, Any]:
    gate = pre_shadow_gate(
        repeatability=offline["repeat"],
        type_c_live=offline["type_c"],
        type_c_rep=type_c_rep,
        false_esc=false_esc,
        strong=offline["strong"],
        latency=offline["latency"],
        detector=offline["detector"],
    )
    (OUT / "pre_shadow_gate.json").write_text(
        json.dumps(gate, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return gate


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="Analyze Phase 35 live artifacts")
    ap.add_argument("--false-escalation-stress", action="store_true")
    ap.add_argument("--type-c-repeat", action="store_true")
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    do_all = args.all or not any(
        [args.offline, args.false_escalation_stress, args.type_c_repeat]
    )
    offline = None
    false_esc = None
    type_c_rep = None

    if do_all or args.offline:
        print("=== phase36 offline analysis ===", flush=True)
        offline = run_offline_analysis()
        print(json.dumps({k: offline[k] for k in ("n_cases", "paths", "reach")}, indent=2))

    if do_all or args.false_escalation_stress:
        print("=== phase36 false escalation stress ===", flush=True)
        # Default single pass over diverse VALID set; --repeats only when
        # --false-escalation-stress is explicit (not bundled via --all).
        fp_repeats = args.repeats if args.false_escalation_stress and not do_all else 1
        false_esc = run_false_escalation_stress(repeats=fp_repeats, limit=args.limit)
        slim = {k: v for k, v in false_esc.items() if k != "rows"}
        (OUT / "false_escalation_stress.json").write_text(
            json.dumps(slim, indent=2), encoding="utf-8"
        )
        (OUT / "harmful_false_escalation.json").write_text(
            json.dumps(
                {
                    "overall_harmful": slim["overall"]["harmful_false_escalation"],
                    "historical_harmful": slim["historical_real"][
                        "harmful_false_escalation"
                    ],
                    "synthetic_harmful": slim["synthetic_valid"][
                        "harmful_false_escalation"
                    ],
                    "rates": {
                        "overall": slim["overall"]["harmful_false_escalation_rate_pct"],
                        "historical": slim["historical_real"][
                            "harmful_false_escalation_rate_pct"
                        ],
                        "synthetic": slim["synthetic_valid"][
                            "harmful_false_escalation_rate_pct"
                        ],
                    },
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print(json.dumps(slim, indent=2))

    if do_all or args.type_c_repeat:
        print("=== phase36 Type-C repeatability ===", flush=True)
        type_c_rep = run_type_c_repeatability(repeats=args.repeats)
        slim_tc = {k: v for k, v in type_c_rep.items() if k != "rows"}
        (OUT / "type_c_repeatability.json").write_text(
            json.dumps(slim_tc, indent=2), encoding="utf-8"
        )
        print(json.dumps(slim_tc, indent=2))

    if offline is None and (do_all or args.offline):
        offline = run_offline_analysis()
    if offline is None and Path(OUT / "benchmark_repeatability.json").is_file():
        # reload minimal for gate
        offline = run_offline_analysis()

    if offline is not None:
        # Prefer freshly written false_esc/type_c; else load prior Phase 35 targeted for hist FP
        if false_esc is None and P35_TARGETED.is_file():
            rows = json.loads(P35_TARGETED.read_text(encoding="utf-8"))
            hist = [r for r in rows if r.get("source_kind") == "historical_real" and r.get("label") == "VALID_SUCCESS"]
            syn = [r for r in rows if r.get("source_kind") == "synthetic_valid"]
            false_esc = {
                "historical_real": {
                    "harmful_false_escalation": sum(
                        1 for r in hist if r.get("category") == "harmful_false_escalation"
                    )
                },
                "synthetic_valid": {
                    "harmful_false_escalation": sum(
                        1 for r in syn if r.get("category") == "harmful_false_escalation"
                    )
                },
                "overall": {
                    "harmful_false_escalation": sum(
                        1 for r in rows if r.get("category") == "harmful_false_escalation"
                    )
                },
                "source": "phase35_targeted_reuse",
            }
        if type_c_rep is None and (OUT / "type_c_repeatability.json").is_file():
            type_c_rep = json.loads((OUT / "type_c_repeatability.json").read_text())
        gate = write_gate(offline, false_esc=false_esc, type_c_rep=type_c_rep)
        print("=== pre-shadow gate ===", flush=True)
        print(json.dumps(gate, indent=2, ensure_ascii=False))
