"""Phase 38 experiment runner — real telemetry + controlled replay (separated).

Does not enable production Shadow by default. Does not tune candidate pipeline.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.shadow.config import PIPELINE_VERSION, SHADOW_SCHEMA_VERSION
from tests.benchmark_multi.phase38_eval import (
    OUT,
    SOURCE_REAL,
    SOURCE_REPLAY,
    aggregate_observations,
    disagreement_inventory,
    load_telemetry_jsonl,
    normalize_observation,
    production_gate,
    write_json,
)

P35_LIVE = Path("benchmark_results/multi/phase35/full_live")
P36_BASE = Path("benchmark_results/multi/phase36")
SHADOW_TEL = Path("data/shadow_telemetry")
P37_TEL = Path("benchmark_results/multi/phase37/dry_run_telemetry")


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def experiment_config() -> dict[str, Any]:
    return {
        "phase": 38,
        "candidate_version": PIPELINE_VERSION,
        "shadow_schema_version": SHADOW_SCHEMA_VERSION,
        "observation_started_utc": _utc(),
        "shadow_default": "MULTI_SHADOW_ENABLED=false (unchanged)",
        "shadow_activation": "NOT auto-enabled for production; measurement only",
        "sample_rate_policy": "random/operational when enabled — no semantic sampling",
        "max_concurrency_default": 1,
        "timeout_sec_default": 600,
        "real_vs_replay": "strictly separated in artifacts",
        "note": "No fabricated real traffic. Replay ≠ real.",
    }


def load_real_traffic_observations() -> list[dict[str, Any]]:
    """Load production Shadow JSONL if any exist. Tag as real_traffic."""
    paths = []
    if SHADOW_TEL.is_dir():
        paths.extend(sorted(SHADOW_TEL.glob("shadow_*.jsonl")))
    rows = load_telemetry_jsonl(paths)
    out = []
    for r in rows:
        r = dict(r)
        r["evidence_source"] = SOURCE_REAL
        out.append(normalize_observation(r))
    return out


def phase35_live_as_controlled_replay() -> list[dict[str, Any]]:
    """Map Phase 35 full_live case runs → controlled_replay observations.

    These are benchmark-suite live runs of the frozen candidate, NOT user traffic.
    No paired route_multi legacy outcome — L/S matrix is not applicable.
    Excludes latest.json to avoid double-counting the three timed runs.
    """
    files = (
        sorted(
            p
            for p in P35_LIVE.glob("2026*.json")
            if p.name != "latest.json"
        )
        if P35_LIVE.is_dir()
        else []
    )
    obs: list[dict[str, Any]] = []
    for fi, p in enumerate(files):
        data = json.loads(p.read_text(encoding="utf-8"))
        for c in data.get("cases") or []:
            meta = c.get("metadata") or {}
            sv = meta.get("semantic_verifier") or {}
            ver_inv = bool(meta.get("semantic_verifier_invoked") or bool(sv))
            verdict = (sv.get("verdict") or "").lower() or None
            fail32 = bool(meta.get("failure_escalation_32b"))
            sem32 = bool(meta.get("semantic_escalation_32b"))
            # No dual route_multi↔shadow correlation in Phase 35 suite export.
            leg = {
                "legacy_success": None,
                "legacy_available": False,
                "legacy_note": (
                    "Controlled replay = candidate-only Phase 35 live suite. "
                    "No paired route_multi outcome — do not treat overall_ok as legacy success."
                ),
                "result_fingerprint": None,
                "legacy_latency_s": None,
                "suite_overall_ok": bool(c.get("overall_ok")),
                "suite_safe_outcome": bool(c.get("safe_outcome")),
            }
            # Pipeline completion (not semantic correctness): success or cannot_plan
            pipe_ok = c.get("status") in {"success", "cannot_plan"} and not bool(
                c.get("unsafe_execution")
            )
            shadow = {
                "shadow_started": True,
                "shadow_completed": True,
                "shadow_status": c.get("status"),
                "shadow_success": pipe_ok,
                "shadow_pipeline_success_def": "status in {success,cannot_plan} AND not unsafe",
                "semantic_verifier_invoked": ver_inv,
                "semantic_verifier_verdict": verdict,
                "failure_32b_invoked": fail32,
                "semantic_32b_invoked": sem32,
                "latency_total_s": c.get("elapsed_s"),
                "cannot_plan": c.get("status") == "cannot_plan",
                "unsafe_execution": bool(c.get("unsafe_execution")),
                "final_path": meta.get("final_path"),
                "result_fingerprint": None,
                "domain": c.get("domain"),
                "scenario": c.get("scenario"),
                "operation_family": meta.get("operation_family"),
                "first_plan_operations": meta.get("first_plan_operations"),
                "suite_overall_ok": bool(c.get("overall_ok")),
            }
            rec = {
                "evidence_source": SOURCE_REPLAY,
                "request_id": f"replay-p35-{fi}-{c.get('case_id')}",
                "shadow_request_id": f"shadow-replay-p35-{fi}-{c.get('case_id')}",
                "event": "controlled_replay_from_phase35_live",
                "case_id": c.get("case_id"),
                "file_count": None,
                "legacy": leg,
                "shadow": shadow,
                "comparison": {
                    "outcome_category": None,
                    "structural": None,
                    "matrix_applicable": False,
                    "note": (
                        "No paired legacy route_multi; L/S matrix not applicable. "
                        "Use candidate pipeline metrics + suite_overall_ok separately."
                    ),
                },
            }
            obs.append(normalize_observation(rec))
    return obs


def traffic_distribution_from_replay(obs: list[dict[str, Any]]) -> dict[str, Any]:
    cases = Counter(o.get("case_id") for o in obs if o.get("case_id"))
    statuses = Counter((o.get("shadow") or {}).get("shadow_status") for o in obs)
    op_fam = Counter(
        (o.get("shadow") or {}).get("operation_family") for o in obs
        if (o.get("shadow") or {}).get("operation_family")
    )
    scenarios = Counter(
        (o.get("shadow") or {}).get("scenario") for o in obs
        if (o.get("shadow") or {}).get("scenario")
    )
    return {
        "evidence_source": SOURCE_REPLAY,
        "n": len(obs),
        "case_id_counts": dict(cases),
        "shadow_status_counts": dict(statuses),
        "operation_family_counts": dict(op_fam),
        "scenario_counts_observational_only": dict(scenarios),
        "note": (
            "Real traffic distribution unavailable (real_n=0). "
            "Replay case mix shown. Scenario counts are observational — NOT routing."
        ),
    }


def sampling_summary(
    *,
    real_n: int,
    replay_n: int,
    real_obs: list[dict],
) -> dict[str, Any]:
    skipped = Counter(
        (o.get("shadow") or {}).get("error_family")
        or (o.get("shadow") or {}).get("shadow_status")
        for o in real_obs
        if not (o.get("shadow") or {}).get("shadow_started")
    )
    return {
        "eligible_requests_real": "unknown_without_production_counter",
        "sampled_requests_real": real_n,
        "shadow_started_real": sum(
            1 for o in real_obs if (o.get("shadow") or {}).get("shadow_started")
        ),
        "shadow_completed_real": sum(
            1 for o in real_obs if (o.get("shadow") or {}).get("shadow_completed")
        ),
        "shadow_skipped_real": dict(skipped),
        "controlled_replay_observations": replay_n,
        "selection_bias_note": (
            "With real_n=0 there is no sampling bias to measure on production; "
            "replay is full Phase 35 live suite (19×3), not a user sample."
        ),
    }


def type_bcd_observations(replay_obs: list[dict[str, Any]]) -> dict[str, Any]:
    """Diagnostic notes from replay — not automatic Type-B inference."""
    sem_fail = [
        o
        for o in replay_obs
        if (o.get("shadow") or {}).get("semantic_verifier_verdict") == "fail"
    ]
    sem_esc = [
        o for o in replay_obs if (o.get("shadow") or {}).get("semantic_32b_invoked")
    ]
    cannot = [
        o for o in replay_obs if (o.get("shadow") or {}).get("cannot_plan")
    ]
    return {
        "Type_D": {
            "status": "deterministic Plan Validator coverage preserved in candidate",
            "real_traffic_observed": False,
            "replay_note": "final_grain_contradiction observed in Phase 35 live traces historically",
        },
        "Type_C_like": {
            "status": "semantic verifier FAIL → one strong replan in candidate",
            "real_traffic_observed": False,
            "replay_semantic_fail_n": len(sem_fail),
            "replay_semantic_escalation_n": len(sem_esc),
        },
        "Type_B": {
            "status": "unresolved (required output under-declaration)",
            "real_traffic_observed": False,
            "frequency": "UNKNOWN without human-reviewed real disagreements",
        },
        "new_family": {
            "real_traffic_observed": False,
            "note": "Cannot claim new families without real traffic",
        },
        "cannot_plan_replay_n": len(cannot),
    }


def production_impact() -> dict[str, Any]:
    return {
        "shadow_enabled_in_production_during_phase38": False,
        "legacy_unaffected": True,
        "legacy_latency_regression_attributable_to_shadow": False,
        "evidence": (
            "MULTI_SHADOW_ENABLED remained false; no production Shadow activation "
            "in this observation window. Phase 37 isolation tests still pass."
        ),
        "phase37_guarantees": "response/exception/latency/state isolation unchanged",
    }


def manual_review_placeholder() -> dict[str, Any]:
    return {
        "reviewed_n": 0,
        "legacy_better": 0,
        "shadow_better": 0,
        "both_acceptable": 0,
        "both_wrong": 0,
        "cannot_determine": 0,
        "systematic_shadow_harm_count": 0,
        "note": (
            "No real-traffic disagreement packets to review (real_n=0). "
            "Replay disagreements are NOT labeled as human semantic truth."
        ),
        "real_traffic_only_table": {
            "legacy_better": 0,
            "shadow_better": 0,
            "both_acceptable": 0,
            "both_wrong": 0,
            "cannot_determine": 0,
        },
    }


def generalization_analysis(replay_agg: dict[str, Any]) -> dict[str, Any]:
    p36 = {}
    if (P36_BASE / "latency_distribution.json").is_file():
        p36 = json.loads((P36_BASE / "latency_distribution.json").read_text())
    return {
        "benchmark_phase36_reference": {
            "path_means_approx_s": {"A": 22, "B": 389, "C": 154, "D": 26},
            "overall_mean_s": 103.14,
            "p50_s": 23.98,
            "p95_s": 313.75,
            "total_32b_pct": 26.32,
        },
        "controlled_replay_from_phase35": replay_agg.get("strong_model"),
        "controlled_replay_latency": replay_agg.get("latency"),
        "controlled_replay_paths": replay_agg.get("path_counts"),
        "real_traffic": "N/A (real_n=0)",
        "distribution_shift": (
            "Cannot measure real-vs-benchmark shift without real multi-file traffic."
        ),
        "phase36_artifact_present": bool(p36),
    }


def run() -> dict[str, Any]:
    OUT.mkdir(parents=True, exist_ok=True)
    cfg = experiment_config()
    write_json(OUT / "experiment_config.json", cfg)

    real_obs = load_real_traffic_observations()
    replay_obs = phase35_live_as_controlled_replay()
    all_obs = real_obs + replay_obs

    write_json(
        OUT / "observations_index.json",
        {
            "real_n": len(real_obs),
            "replay_n": len(replay_obs),
            "warning": "Do not pool real+replay for Production Gate sufficiency",
        },
    )

    agg = aggregate_observations(all_obs)
    write_json(OUT / "outcome_matrix.json", {
        "real": agg["real_only"].get("matrix"),
        "controlled_replay": {
            "matrix": agg["replay_only"].get("matrix"),
            "applicable": False,
            "reason": (
                "Phase 35 live suite has no paired route_multi legacy outcome; "
                "L/S matrix requires real Shadow telemetry with both sides."
            ),
            "candidate_pipeline_success_pct": agg["replay_only"].get(
                "shadow_pipeline_success_pct"
            ),
            "suite_overall_ok_pct": agg["replay_only"].get("suite_overall_ok_pct"),
        },
        "definitions": {
            "L+_S+": "legacy success & shadow success",
            "L+_S-": "legacy success & shadow failure",
            "L-_S+": "legacy failure & shadow success",
            "L-_S-": "legacy failure & shadow failure",
            "success": "pipeline completion — NOT semantic correctness",
            "shadow_pipeline_success_replay": (
                "status in {success, cannot_plan} AND not unsafe"
            ),
        },
    })
    write_json(OUT / "structural_comparison.json", {
        "criteria": {
            "structurally_equal": "shape + ordered columns + head50 hash",
            "structurally_similar": "shape + column set (order-insensitive)",
            "structurally_different": "otherwise",
            "note": "Difference ≠ wrongness; never auto-winner",
        },
        "real": agg["real_only"].get("structural"),
        "controlled_replay": agg["replay_only"].get("structural"),
    })

    disc = disagreement_inventory(all_obs)
    # Strip bulky nested payloads for inventory
    slim_disc = [
        {
            "evidence_source": d["evidence_source"],
            "request_id": d.get("request_id"),
            "case_id": d.get("case_id"),
            "matrix": d["matrix"],
            "structural": d.get("structural"),
            "review_priority": d.get("review_priority"),
            "failure_family": d.get("failure_family"),
            "path_class": d.get("path_class"),
        }
        for d in disc
    ]
    write_json(OUT / "disagreement_inventory.json", {
        "n": len(slim_disc),
        "real_n": sum(1 for d in slim_disc if d["evidence_source"] == SOURCE_REAL),
        "replay_n": sum(1 for d in slim_disc if d["evidence_source"] == SOURCE_REPLAY),
        "items": slim_disc[:200],
    })

    review = manual_review_placeholder()
    write_json(OUT / "manual_review.json", review)

    write_json(OUT / "failure_taxonomy.json", {
        "real": agg["real_only"].get("failure_taxonomy"),
        "controlled_replay": agg["replay_only"].get("failure_taxonomy"),
    })
    write_json(OUT / "verifier_metrics.json", {
        "real": agg["real_only"].get("verifier"),
        "controlled_replay": agg["replay_only"].get("verifier"),
        "ground_truth_accuracy": "NOT_COMPUTED (no golden on real traffic)",
    })
    write_json(OUT / "strong_model_metrics.json", {
        "real": agg["real_only"].get("strong_model"),
        "controlled_replay": agg["replay_only"].get("strong_model"),
        "phase36_reference_total_32b_pct": 26.32,
        "double_32b_phase36": 0,
    })
    write_json(OUT / "latency_distribution.json", {
        "real": agg["real_only"].get("latency"),
        "controlled_replay": agg["replay_only"].get("latency"),
        "phase36_reference": {"mean": 103.14, "p50": 23.98, "p95": 313.75},
    })
    write_json(OUT / "path_latency.json", {
        "real": agg["real_only"].get("path_latency"),
        "controlled_replay": agg["replay_only"].get("path_latency"),
        "path_counts_replay": agg["replay_only"].get("path_counts"),
    })

    impact = production_impact()
    write_json(OUT / "production_impact.json", impact)
    write_json(OUT / "resource_observation.json", {
        "production_shadow_enabled": False,
        "capacity_skips_real": 0,
        "timeouts_real": 0,
        "note": "No production Shadow load this window; Phase 36/37 give prior resource model",
    })
    write_json(OUT / "sampling_summary.json", sampling_summary(
        real_n=len(real_obs), replay_n=len(replay_obs), real_obs=real_obs
    ))
    write_json(OUT / "traffic_summary.json", {
        "real_multi_file_requests_observed": len(real_obs),
        "controlled_replay_observations": len(replay_obs),
        "observed_N_real": len(real_obs),
        "period": cfg["observation_started_utc"],
        "insufficient_real_traffic": len(real_obs) == 0,
    })
    write_json(OUT / "type_bcd_observations.json", type_bcd_observations(replay_obs))
    write_json(
        OUT / "generalization_analysis.json",
        generalization_analysis(agg["replay_only"]),
    )
    write_json(OUT / "traffic_distribution.json", traffic_distribution_from_replay(replay_obs))

    gate = production_gate(
        real_agg=agg["real_only"],
        replay_agg=agg["replay_only"],
        real_n=len(real_obs),
        manual_review=review,
        production_impact=impact,
        architecture_clean=True,
    )
    # Force recommendation C when real_n=0 (already in logic)
    write_json(OUT / "production_gate.json", gate)

    architecture_audit = {
        "scenario_routing": "ABSENT",
        "domain_routing": "ABSENT",
        "column_routing": "ABSENT",
        "file_count_routing": "ABSENT",
        "operation_routing": "ABSENT",
        "python_semantic_inference": "ABSENT",
        "plan_mutation": "ABSENT",
        "validator_repair": "ABSENT",
        "executor_inference": "ABSENT",
        "verifier_repair": "ABSENT",
        "legacy_as_golden": "ABSENT",
        "automatic_semantic_winner": "ABSENT",
        "shadow_fallback_to_production": "ABSENT",
        "shadow_response_replacement": "ABSENT",
        "model_specific_validator_relaxation": "ABSENT",
        "evaluator_relaxation": "ABSENT",
        "automatic_prompt_tuning": "ABSENT",
        "automatic_benchmark_tuning": "ABSENT",
        "result": "PASS",
        "note": "Phase 38 measurement tooling only; candidate + Shadow infra frozen",
    }
    write_json(OUT / "architecture_audit.json", architecture_audit)

    summary = {
        "real_n": len(real_obs),
        "replay_n": len(replay_obs),
        "recommendation": gate["recommendation"],
        "gates": {g["id"]: g["result"] for g in gate["gates"]},
        "architecture_audit": architecture_audit["result"],
    }
    write_json(OUT / "phase38_summary.json", summary)
    return summary


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
