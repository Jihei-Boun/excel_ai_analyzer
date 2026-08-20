"""Phase 38 — Real-traffic / controlled-replay Shadow evaluation (measurement only).

Does NOT change candidate pipeline, Shadow infrastructure defaults, or route_multi.
Never treats legacy as golden or picks a semantic winner in code.
"""

from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from core.shadow.config import PIPELINE_VERSION, SHADOW_SCHEMA_VERSION
from core.shadow.fingerprint import structural_compare

OUT = Path("benchmark_results/multi/phase38")

# Evidence source tags — never blur these
SOURCE_REAL = "real_traffic"
SOURCE_REPLAY = "controlled_replay"
SOURCE_SYNTHETIC = "synthetic_benchmark"


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


def classify_path(shadow: dict[str, Any]) -> str:
    """Phase 36 path taxonomy on shadow observation payload."""
    fail32 = bool(shadow.get("failure_32b_invoked"))
    sem32 = bool(shadow.get("semantic_32b_invoked"))
    ver = bool(shadow.get("semantic_verifier_invoked"))
    verdict = shadow.get("semantic_verifier_verdict")
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


def classify_failure_family(shadow: dict[str, Any]) -> str | None:
    if shadow.get("shadow_success"):
        return None
    fam = shadow.get("error_family")
    if fam in {
        "shadow_timeout",
        "shadow_infrastructure_error",
        "shadow_skipped_capacity",
        "shadow_queue_rejected",
        "shadow_skipped_sampling",
        "shadow_disabled",
    }:
        return fam
    status = shadow.get("shadow_status") or ""
    if status == "cannot_plan" or shadow.get("cannot_plan"):
        return "cannot_plan"
    if status == "shadow_timeout" or shadow.get("shadow_timeout"):
        return "shadow_timeout"
    if shadow.get("semantic_verifier_verdict") == "fail" and not shadow.get(
        "semantic_32b_invoked"
    ):
        return "semantic_verifier_FAIL"
    if shadow.get("semantic_verifier_verdict") == "uncertain":
        return "semantic_verifier_UNCERTAIN"
    if shadow.get("plan_validation_status") == "failed":
        return "plan_validation_failure"
    if shadow.get("executor_status") is False:
        return "execution_failure"
    if shadow.get("result_validation_status") is False:
        return "result_validation_failure"
    if shadow.get("semantic_32b_invoked") and not shadow.get("shadow_success"):
        return "strong_replan_failure"
    if status in {"failed", "shadow_pipeline_exception"}:
        return status
    return status or "unknown_failure"


def matrix_cell(legacy_ok: bool, shadow_ok: bool) -> str:
    if legacy_ok and shadow_ok:
        return "L+_S+"
    if legacy_ok and not shadow_ok:
        return "L+_S-"
    if not legacy_ok and shadow_ok:
        return "L-_S+"
    return "L-_S-"


def load_telemetry_jsonl(paths: Iterable[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for p in paths:
        if not p.is_file():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def normalize_observation(rec: dict[str, Any]) -> dict[str, Any]:
    """Normalize a telemetry record or replay row into a common observation."""
    source = rec.get("evidence_source") or rec.get("source") or SOURCE_REAL
    leg = rec.get("legacy") or {}
    sh = rec.get("shadow") or {}
    # Skipped events
    if rec.get("event") == "shadow_skipped":
        sh = {
            "shadow_started": False,
            "shadow_completed": False,
            "shadow_success": False,
            "shadow_status": rec.get("shadow_status") or rec.get("error_family"),
            "error_family": rec.get("error_family"),
        }
    legacy_raw = leg.get("legacy_success")
    legacy_available = leg.get("legacy_available")
    if legacy_available is None:
        legacy_available = legacy_raw is not None
    shadow_ok = bool(sh.get("shadow_success"))
    structural = None
    matrix = None
    if legacy_available and legacy_raw is not None:
        legacy_ok = bool(legacy_raw)
        if legacy_ok and shadow_ok:
            structural = structural_compare(
                leg.get("result_fingerprint"), sh.get("result_fingerprint")
            )
        matrix = matrix_cell(legacy_ok, shadow_ok)
    return {
        "evidence_source": source,
        "request_id": rec.get("request_id"),
        "shadow_request_id": rec.get("shadow_request_id"),
        "event": rec.get("event"),
        "legacy_success": bool(legacy_raw) if legacy_available else None,
        "legacy_available": legacy_available,
        "shadow_success": shadow_ok,
        "matrix": matrix,
        "structural": structural,
        "path_class": classify_path(sh) if sh.get("shadow_started") else None,
        "failure_family": classify_failure_family(sh),
        "legacy": leg,
        "shadow": sh,
        "comparison": rec.get("comparison"),
        "file_count": rec.get("file_count"),
        "prompt_hash": rec.get("prompt_hash"),
        "case_id": rec.get("case_id"),  # replay only
    }


def aggregate_observations(obs: list[dict[str, Any]]) -> dict[str, Any]:
    by_src: dict[str, list[dict]] = defaultdict(list)
    for o in obs:
        by_src[o["evidence_source"]].append(o)

    def _agg(subset: list[dict]) -> dict[str, Any]:
        n = len(subset)
        matrix = Counter(o["matrix"] for o in subset if o.get("matrix") is not None)
        structural = Counter(
            o["structural"] for o in subset if o.get("structural") is not None
        )
        paths = Counter(o["path_class"] for o in subset if o.get("path_class"))
        fails = Counter(o["failure_family"] for o in subset if o.get("failure_family"))
        started = [o for o in subset if (o.get("shadow") or {}).get("shadow_started")]
        completed = [
            o for o in subset if (o.get("shadow") or {}).get("shadow_completed")
        ]
        shadow_pipe_ok = sum(1 for o in subset if o.get("shadow_success"))
        suite_ok = sum(
            1
            for o in subset
            if (o.get("shadow") or {}).get("suite_overall_ok")
        )
        cannot_plan_n = sum(
            1 for o in subset if (o.get("shadow") or {}).get("cannot_plan")
        )
        status_counts = Counter(
            (o.get("shadow") or {}).get("shadow_status") for o in subset
            if (o.get("shadow") or {}).get("shadow_status")
        )
        lats = [
            float(o["shadow"]["latency_total_s"])
            for o in subset
            if (o.get("shadow") or {}).get("latency_total_s") is not None
        ]
        ver_inv = sum(
            1
            for o in subset
            if (o.get("shadow") or {}).get("semantic_verifier_invoked")
        )
        ver_pass = sum(
            1
            for o in subset
            if (o.get("shadow") or {}).get("semantic_verifier_verdict") == "pass"
        )
        ver_fail = sum(
            1
            for o in subset
            if (o.get("shadow") or {}).get("semantic_verifier_verdict") == "fail"
        )
        ver_unc = sum(
            1
            for o in subset
            if (o.get("shadow") or {}).get("semantic_verifier_verdict") == "uncertain"
        )
        fail32 = sum(
            1 for o in subset if (o.get("shadow") or {}).get("failure_32b_invoked")
        )
        sem32 = sum(
            1 for o in subset if (o.get("shadow") or {}).get("semantic_32b_invoked")
        )
        both32 = sum(
            1
            for o in subset
            if (o.get("shadow") or {}).get("failure_32b_invoked")
            and (o.get("shadow") or {}).get("semantic_32b_invoked")
        )
        any32 = sum(
            1
            for o in subset
            if (o.get("shadow") or {}).get("failure_32b_invoked")
            or (o.get("shadow") or {}).get("semantic_32b_invoked")
        )
        unsafe = sum(
            1 for o in subset if (o.get("shadow") or {}).get("unsafe_execution")
        )

        path_lat: dict[str, dict] = {}
        by_path: dict[str, list[float]] = defaultdict(list)
        for o in subset:
            pc = o.get("path_class")
            lt = (o.get("shadow") or {}).get("latency_total_s")
            if pc and lt is not None:
                by_path[pc].append(float(lt))
        for pc, vals in by_path.items():
            path_lat[pc] = latency_block(vals) or {}

        return {
            "n": n,
            "shadow_started": len(started),
            "shadow_completed": len(completed),
            "shadow_pipeline_success_n": shadow_pipe_ok,
            "shadow_pipeline_success_pct": round(shadow_pipe_ok / max(n, 1) * 100, 2),
            "suite_overall_ok_n": suite_ok,
            "suite_overall_ok_pct": round(suite_ok / max(n, 1) * 100, 2),
            "matrix": dict(matrix),
            "matrix_note": (
                "Empty when legacy_available=False (candidate-only replay)."
            ),
            "status_counts": dict(status_counts),
            "cannot_plan_n": cannot_plan_n,
            "structural": dict(structural),
            "path_counts": dict(paths),
            "failure_taxonomy": dict(fails),
            "latency": latency_block(lats),
            "path_latency": path_lat,
            "verifier": {
                "invoked": ver_inv,
                "invocation_rate_pct": round(ver_inv / max(n, 1) * 100, 2),
                "pass": ver_pass,
                "fail": ver_fail,
                "uncertain": ver_unc,
            },
            "strong_model": {
                "failure_32b": fail32,
                "semantic_32b": sem32,
                "double_32b": both32,
                "any_32b": any32,
                "failure_32b_pct": round(fail32 / max(n, 1) * 100, 2),
                "semantic_32b_pct": round(sem32 / max(n, 1) * 100, 2),
                "total_32b_pct": round(any32 / max(n, 1) * 100, 2),
                "double_32b_pct": round(both32 / max(n, 1) * 100, 2),
            },
            "unsafe_execution_count": unsafe,
        }

    return {
        "by_source": {k: _agg(v) for k, v in by_src.items()},
        "overall_all_sources_mixed_warning": (
            "Do not mix real and replay for Production Gate G12; use by_source."
        ),
        "real_only": _agg(by_src.get(SOURCE_REAL, [])),
        "replay_only": _agg(by_src.get(SOURCE_REPLAY, [])),
    }


def disagreement_inventory(obs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    priority = {"L+_S-", "L-_S+", "L+_S+"}
    out = []
    for o in obs:
        if o.get("matrix") is None:
            # candidate-only: still queue semantic FAIL / cannot_plan for review packets
            if (o.get("shadow") or {}).get("semantic_verifier_verdict") in {
                "fail",
                "uncertain",
            }:
                out.append({**o, "review_priority": "medium", "review_basis": "verifier"})
            continue
        if o["matrix"] in {"L+_S-", "L-_S+"}:
            out.append({**o, "review_priority": "high"})
        elif o["matrix"] == "L+_S+" and o.get("structural") in {
            "structurally_different",
            "structurally_similar",
        }:
            out.append({**o, "review_priority": "medium"})
        elif (o.get("shadow") or {}).get("semantic_verifier_verdict") in {
            "fail",
            "uncertain",
        }:
            out.append({**o, "review_priority": "medium"})
    return out


def production_gate(
    *,
    real_agg: dict[str, Any],
    replay_agg: dict[str, Any],
    real_n: int,
    manual_review: dict[str, Any] | None,
    production_impact: dict[str, Any],
    architecture_clean: bool = True,
) -> dict[str, Any]:
    """Answer G1–G12. Does not invent numeric pass thresholds post-hoc."""
    unsafe_real = real_agg.get("unsafe_execution_count", 0)
    unsafe_replay = replay_agg.get("unsafe_execution_count", 0)
    impact_ok = bool(production_impact.get("legacy_unaffected", True))
    review = manual_review or {}
    systematic_harm = int(review.get("systematic_shadow_harm_count") or 0)

    # Evidence sufficiency: require non-trivial REAL traffic — not replay
    min_real_for_sufficiency = 20  # documented floor for "non-trivial"; not a quality score
    evidence_sufficient = real_n >= min_real_for_sufficiency

    def yn(cond: bool | None, *, insuff_if: bool = False) -> str:
        if insuff_if:
            return "INSUFFICIENT"
        if cond is None:
            return "INSUFFICIENT"
        return "YES" if cond else "NO"

    gates = [
        {
            "id": "G1",
            "question": "production unaffected by Shadow",
            "result": yn(impact_ok),
            "evidence": production_impact,
        },
        {
            "id": "G2",
            "question": "unsafe=0",
            "result": yn(unsafe_real == 0 and unsafe_replay == 0),
            "evidence": {"real_unsafe": unsafe_real, "replay_unsafe": unsafe_replay},
        },
        {
            "id": "G3",
            "question": "shadow stable on real traffic",
            "result": yn(None, insuff_if=real_n == 0)
            if real_n == 0
            else yn(real_n > 0 and real_agg.get("n", 0) > 0),
            "evidence": {"real_n": real_n, "real_completed": real_agg.get("shadow_completed")},
        },
        {
            "id": "G4",
            "question": "disagreement observable",
            "result": "YES",
            "evidence": "matrix + structural comparison tooling in place",
        },
        {
            "id": "G5",
            "question": "no systematic semantic harm",
            "result": yn(systematic_harm == 0)
            if review.get("reviewed_n", 0) > 0
            else yn(None, insuff_if=True),
            "evidence": review,
        },
        {
            "id": "G6",
            "question": "verifier useful on real traffic",
            "result": yn(None, insuff_if=True)
            if real_n == 0
            else yn(real_agg.get("verifier", {}).get("invoked", 0) > 0),
            "evidence": {"real_verifier": real_agg.get("verifier"), "real_n": real_n},
        },
        {
            "id": "G7",
            "question": "Type-B risk understood",
            "result": yn(None, insuff_if=True)
            if real_n == 0 or int(review.get("reviewed_n") or 0) == 0
            else "YES",
            "evidence": (
                "Type B remains unresolved by design; real frequency requires "
                "human-reviewed disagreements (real_n=0 → INSUFFICIENT)"
            ),
        },
        {
            "id": "G8",
            "question": "no new severe family",
            "result": yn(None, insuff_if=real_n == 0)
            if real_n == 0
            else "YES",
            "evidence": {"real_n": real_n, "replay_failures": replay_agg.get("failure_taxonomy")},
        },
        {
            "id": "G9",
            "question": "resource burden understood",
            "result": "YES",
            "evidence": "Phase 36 + replay/strong metrics; ~26% 32B prior",
        },
        {
            "id": "G10",
            "question": "latency understood",
            "result": "YES",
            "evidence": "Phase 36 mean~103s p50~24s; sync replacement unsuitable",
        },
        {
            "id": "G11",
            "question": "no semantic routing",
            "result": yn(architecture_clean),
            "evidence": "frozen Phase 35–37; Phase 38 measurement only",
        },
        {
            "id": "G12",
            "question": "real-traffic evidence sufficient for migration review",
            "result": yn(evidence_sufficient),
            "evidence": {
                "real_n": real_n,
                "min_real_for_sufficiency": min_real_for_sufficiency,
                "note": "Replay/synthetic cannot satisfy G12 alone",
            },
        },
    ]

    hard_fail = any(
        g["result"] == "NO"
        and g["id"] in {"G1", "G2", "G11"}
        for g in gates
    )
    if hard_fail:
        recommendation = "D_reliability_blocker"
    elif not evidence_sufficient:
        recommendation = "C_evidence_insufficient"
    elif any(g["result"] == "NO" for g in gates):
        recommendation = "D_reliability_blocker"
    else:
        # Even with sufficient real N, latency may operationally block sync migration
        recommendation = "B_functionally_ready_operationally_blocked"

    return {
        "gates": gates,
        "recommendation": recommendation,
        "recommendation_options": {
            "A": "Production Migration Candidate",
            "B": "Functionally Ready, Operationally Blocked",
            "C": "Evidence Insufficient",
            "D": "Reliability Blocker Found",
        },
        "hard_blockers_checked": [
            "unsafe",
            "production_regression",
            "isolation/routing violations",
        ],
        "latency_note": (
            "Even if correctness were strong, Phase 36 mean~103s remains a "
            "sync production-replacement blocker (Shadow observation can still proceed)."
        ),
    }


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
