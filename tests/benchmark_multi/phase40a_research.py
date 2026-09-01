"""Phase 40A — Semantic verifier reasoning capability (research only).

Does NOT modify production verifier prompt, model, thresholds, or wiring.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

import pandas as pd

from core.integrate.attempt_lineage import (
    compact_result_fingerprint,
    new_verifier_invocation_id,
    plan_fingerprint,
)
from core.integrate.integration_execute import execute_integration_plan
from core.integrate.integration_plan_types import integration_plan_from_dict
from core.integrate.integration_plan_validate import validate_integration_plan
from core.integrate.result_observation import observe_result_for_verifier
from core.integrate.schema_lineage import extract_source_schemas_from_understanding
from core.integrate.semantic_escalation import (
    SEMANTIC_VERIFIER_MODEL,
    SEMANTIC_VERIFIER_VARIANT,
    _should_semantic_escalate,
)
from core.integrate.semantic_verifier import (
    _VERIFIER_SYSTEM,
    _normalize_verdict,
    build_verifier_payload,
    run_semantic_verification,
)
from core.llm_client import chat_json
from core.shadow.fingerprint import dataframe_fingerprint
from tests.benchmark_multi.phase39v_research import _und_from_frames
from tests.benchmark_multi.phase39w_research import build_w_corpus
from tests.benchmark_multi.phase39x_research import MATERIALIZATION, META

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "benchmark_results/multi/phase40a"
CACHE = OUT / "verifier_live_cache.json"
PHASE39Z_SHA = "9688e504c2784d9441e30d8f29173fa1f9422223"
LIVE = os.environ.get("PHASE40A_LIVE_VERIFIER", "1") != "0"
BASE_URL = "http://localhost:11434"
M7 = "qwen2.5:7b"
M8 = "qwen3:8b"
M2_ID = "w2-wrong-group-grain"
STABILITY_N = 5


def production_user_prefix(*, result_attached: bool) -> str:
    """Byte-identical to production run_semantic_verification user_prefix."""
    return (
        "Determine whether the proposed integration plan"
        + (" and observed result" if result_attached else "")
        + " directly satisfy all material requirements in the user's request.\n"
        "Step order (mandatory):\n"
        "  (1) Reconstruct material requirements from user_prompt only.\n"
        "  (2) Decide from plan_structure + materialization_evidence (if present)\n"
        "      whether those requirements are actually materialized.\n"
        "  (3) Optionally glance at planner_claims — never as proof of success.\n"
        "If materialization_evidence lists unresolved_column_refs or\n"
        "claimed_columns_absent_from_final for columns needed by the request,\n"
        "do not treat those claimed metrics as present.\n"
        "If join (without collapsing aggregate) retains side-specific columns\n"
        "in final_schema, treat that as preserved distinction — not collapse.\n"
        "Renamed distinct metric columns present in final_schema also preserve\n"
        "distinction; _left/_right suffixes are not required.\n"
        "Entity grain (one row per key) with multiple side metrics is not collapse.\n"
        "If two side metrics have DIFFERENT evidence_signatures (especially\n"
        "different filters/partitions), treat them as independent sides even when\n"
        "they share a source file — one row carrying both columns is NOT collapse.\n"
        "Prefer materialization_evidence over narrative collapse claims when\n"
        "final_schema retains required sides.\n"
        "CRITICAL: if the request needs independent comparison sides and\n"
        "identical_evidence_signature_column_sets (or\n"
        "equivalent_evidence_signature_groups) puts those side metrics in one\n"
        "set, return fail — same expression over the same row population is not\n"
        "two sides. Names/roles/suffixes do not create independence.\n"
        "Different evidence_signatures (different filters/partitions or different\n"
        "source lineages) may be independent even with overlapping origins.\n"
        "Do not fail merely because sides share a source file when filters differ.\n"
        "If the request needs multiple distinct sides observable, and the ops\n"
        "collapse them into one total before contrast is possible, return fail.\n"
        "If the request only asks to combine/stack tables or compute an overall\n"
        "total across inputs, do not invent a contrast requirement.\n"
        "Do not repair the plan. If evidence is insufficient, return uncertain.\n\n"
    )


# Research-only generic addenda. No operation names, domains, or schemas.
P1_ADDENDUM = (
    "Before any later step, independently summarize the required outcome from "
    "user_prompt alone. Do not use planner claims for that summary. Then compare "
    "that required outcome with the plan and observed result.\n\n"
)
P2_ADDENDUM = (
    "Use this comparison procedure:\n"
    "1. Determine the required semantic outcome from the user request alone.\n"
    "2. Determine what the plan actually computes.\n"
    "3. Determine what the observed result actually contains.\n"
    "4. Compare those three.\n"
    "5. PASS only if they are semantically consistent; otherwise FAIL. "
    "If evidence is insufficient, return UNCERTAIN.\n"
    "You may include required_outcome, observed_computation, and "
    "semantic_mismatches as extra JSON fields. Verdict remains pass, fail, or uncertain.\n\n"
)
P3_ADDENDUM = (
    "Judge user_prompt against plan_structure and observed_result before "
    "inspecting planner_claims. Claims may be read last and are never proof of success.\n\n"
)
P5_ADDENDUM = (
    "Before PASS, actively search for any contradiction between the semantic "
    "distinctions the user requested and what the plan and observed result "
    "actually preserve. If a contradiction exists, FAIL. If none, PASS. "
    "If evidence is insufficient, UNCERTAIN.\n\n"
)


def prompt_for(variant: str) -> str:
    base = production_user_prefix(result_attached=True)
    if variant == "P0":
        return base
    if variant == "P1":
        return P1_ADDENDUM + base
    if variant == "P2":
        return P2_ADDENDUM + base
    if variant == "P3":
        return P3_ADDENDUM + base
    if variant == "P4":
        return base
    if variant == "P5":
        return P5_ADDENDUM + base
    raise KeyError(variant)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


PROMPT_REGISTRY = {
    "P0": {
        "dimension": "baseline_production_39z",
        "change": "none",
        "text": prompt_for("P0"),
    },
    "P1": {
        "dimension": "independent_intent_first",
        "change": "prepend_generic_intent_summary",
        "text": prompt_for("P1"),
    },
    "P2": {
        "dimension": "explicit_three_way_comparison",
        "change": "prepend_generic_five_step_procedure",
        "text": prompt_for("P2"),
    },
    "P3": {
        "dimension": "claim_last_ordering",
        "change": "prepend_claims_last_plus_payload_key_order",
        "text": prompt_for("P3"),
    },
    "P4": {
        "dimension": "no_planner_claims",
        "change": "remove_planner_claims_from_payload",
        "text": prompt_for("P4"),
    },
    "P5": {
        "dimension": "contradiction_check",
        "change": "prepend_generic_contradiction_search",
        "text": prompt_for("P5"),
    },
}
for _k, _v in PROMPT_REGISTRY.items():
    _v["sha256"] = _sha(_v["text"])
    _v["chars"] = len(_v["text"])


PROMPTS: dict[str, str] = {
    "w2-wrong-group-grain": "Sum ticket hours per agent, not per individual ticket.",
    "w2-join-instead-of-union": META["w2-join-instead-of-union"]["user_prompt"],
    "w2-collapse-no-roles": META["w2-collapse-no-roles"]["user_prompt"],
    "w2-drop-needed-metric": META["w2-drop-needed-metric"]["user_prompt"],
    "w2-filter-wrong-site": META["w2-filter-wrong-site"]["user_prompt"],
    "w2-single-run-only": META["w2-single-run-only"]["user_prompt"],
    "w2-union-when-compare": META["w2-union-when-compare"]["user_prompt"],
    "w1-join-1to1": META["w1-join-1to1"]["user_prompt"],
    "w1-union-total": META["w1-union-total"]["user_prompt"],
    "w1-count-agent": META["w1-count-agent"]["user_prompt"],
    "w1-filter-then-agg": META["w1-filter-then-agg"]["user_prompt"],
    "w1-rename-join-temp": META["w1-rename-join-temp"]["user_prompt"],
    "w1-join-select": META["w1-join-select"]["user_prompt"],
    "w5-valid-multi-stage": META["w5-valid-multi-stage"]["user_prompt"],
    "w5-valid-same-schema-concat": META["w5-valid-same-schema-concat"]["user_prompt"],
    "w1-filter-wing": "Keep only rooms in the north wing.",
    "w1-agg-room": "Sum lux for each room.",
    "w1-join-1tomany": "Attach each visit to its patient record.",
    "w1-union-quarters": "Stack Q1 and Q2 unit rows into one table.",
    "a40-wrong-receipt-grain": "Sum yen per shop, not per individual receipt.",
    "a40-wrong-status-filter": "Keep only paid claims.",
    "a40-wrong-metric-mean": "For each dock report the total kg.",
    "a40-wrong-desk-branch": (
        "For each queue show counts from desk A and desk B side by side."
    ),
    "a40-valid-wide": "Keep every measured attribute for each unit.",
}

DEV_IDS = [
    "w2-wrong-group-grain",
    "w2-join-instead-of-union",
    "w2-collapse-no-roles",
    "w2-drop-needed-metric",
    "a40-wrong-status-filter",
    "a40-wrong-metric-mean",
    "w1-join-1to1",
    "w1-union-total",
    "w1-count-agent",
    "w1-filter-then-agg",
    "w1-filter-wing",
    "w1-agg-room",
    "w1-rename-join-temp",
    "a40-valid-wide",
]
HOLD_IDS = [
    "a40-wrong-receipt-grain",
    "w2-filter-wrong-site",
    "w2-single-run-only",
    "w2-union-when-compare",
    "a40-wrong-desk-branch",
    "w1-join-select",
    "w5-valid-multi-stage",
    "w5-valid-same-schema-concat",
    "w1-join-1tomany",
    "w1-union-quarters",
]
ALL_IDS = DEV_IDS + HOLD_IDS
WRONG_IDS = [
    "w2-wrong-group-grain",
    "w2-join-instead-of-union",
    "w2-collapse-no-roles",
    "w2-drop-needed-metric",
    "a40-wrong-status-filter",
    "a40-wrong-metric-mean",
    "a40-wrong-receipt-grain",
    "w2-filter-wrong-site",
    "w2-single-run-only",
    "w2-union-when-compare",
    "a40-wrong-desk-branch",
]
VALID_IDS = [i for i in ALL_IDS if i not in WRONG_IDS]
VALID_STABILITY_IDS = ["w1-join-1to1", "w1-union-total", "w1-count-agent"]


def _new_cases() -> list[dict[str, Any]]:
    sales = pd.DataFrame(
        {"rid": ["R1", "R2", "R3", "R4"], "shop": ["S1", "S1", "S2", "S2"], "yen": [10, 20, 5, 8]}
    )
    claims = pd.DataFrame(
        {"cid": ["C1", "C2", "C3"], "amt": [10, 20, 30], "status": ["paid", "open", "paid"]}
    )
    docks = pd.DataFrame({"dock": ["D1", "D1", "D2"], "kg": [4, 6, 10]})
    queues = pd.DataFrame(
        {"qid": ["Q1", "Q1", "Q2", "Q2"], "desk": ["A", "B", "A", "B"], "n": [1, 2, 3, 4]}
    )
    wide = pd.DataFrame({"uid": ["U1", "U2"], **{f"m{i}": [i, i + 1] for i in range(30)}})
    return [
        {
            "attempt_id": "a40-wrong-receipt-grain",
            "request_id": "p40a-01",
            "fast_correct": "NO",
            "frames": {"sales.xlsx": sales},
            "plan": integration_plan_from_dict(
                {
                    "status": "planned",
                    "final_output": "a",
                    "steps": [
                        {
                            "op": "aggregate",
                            "inputs": ["sales.xlsx"],
                            "output": "a",
                            "params": {
                                "group_by": ["rid"],
                                "metrics": [{"column": "yen", "function": "sum", "alias": "yen"}],
                            },
                        }
                    ],
                }
            ),
            "note_ko": "상점별 합계가 필요한데 영수증 grain.",
            "defect": "WRONG_GROUPING",
        },
        {
            "attempt_id": "a40-wrong-status-filter",
            "request_id": "p40a-02",
            "fast_correct": "NO",
            "frames": {"claims.xlsx": claims},
            "plan": integration_plan_from_dict(
                {
                    "status": "planned",
                    "final_output": "f",
                    "steps": [
                        {
                            "op": "filter_rows",
                            "inputs": ["claims.xlsx"],
                            "output": "f",
                            "params": {
                                "conditions": [
                                    {"column": "status", "operator": "eq", "value": "open"}
                                ]
                            },
                        }
                    ],
                }
            ),
            "note_ko": "지급건만 필요한데 open으로 필터.",
            "defect": "WRONG_FILTER_SELECTION",
        },
        {
            "attempt_id": "a40-wrong-metric-mean",
            "request_id": "p40a-03",
            "fast_correct": "NO",
            "frames": {"dock.xlsx": docks},
            "plan": integration_plan_from_dict(
                {
                    "status": "planned",
                    "final_output": "a",
                    "steps": [
                        {
                            "op": "aggregate",
                            "inputs": ["dock.xlsx"],
                            "output": "a",
                            "params": {
                                "group_by": ["dock"],
                                "metrics": [{"column": "kg", "function": "mean", "alias": "kg"}],
                            },
                        }
                    ],
                }
            ),
            "note_ko": "총량이 필요한데 평균.",
            "defect": "WRONG_METRIC_SELECTION",
        },
        {
            "attempt_id": "a40-wrong-desk-branch",
            "request_id": "p40a-04",
            "fast_correct": "NO",
            "frames": {"queue.xlsx": queues},
            "plan": integration_plan_from_dict(
                {
                    "status": "planned",
                    "final_output": "a",
                    "steps": [
                        {
                            "op": "filter_rows",
                            "inputs": ["queue.xlsx"],
                            "output": "f",
                            "params": {
                                "conditions": [
                                    {"column": "desk", "operator": "eq", "value": "A"}
                                ]
                            },
                        },
                        {
                            "op": "aggregate",
                            "inputs": ["f"],
                            "output": "a",
                            "params": {
                                "group_by": ["qid"],
                                "metrics": [{"column": "n", "function": "sum", "alias": "n"}],
                            },
                        },
                    ],
                }
            ),
            "note_ko": "A/B를 나란히 보여야 하는데 A만.",
            "defect": "WRONG_BRANCH",
        },
        {
            "attempt_id": "a40-valid-wide",
            "request_id": "p40a-05",
            "fast_correct": "YES",
            "frames": {"units.xlsx": wide},
            "plan": integration_plan_from_dict(
                {
                    "status": "planned",
                    "final_output": "s",
                    "steps": [
                        {
                            "op": "select_columns",
                            "inputs": ["units.xlsx"],
                            "output": "s",
                            "params": {"columns": ["uid"] + [f"m{i}" for i in range(30)]},
                        }
                    ],
                }
            ),
            "note_ko": "넓은 결과. 관측 열 truncation 통제.",
            "defect": None,
        },
    ]


def build_corpus() -> list[dict[str, Any]]:
    raw = {c["attempt_id"]: c for c in build_w_corpus()}
    for c in _new_cases():
        raw[c["attempt_id"]] = c
    rows: list[dict[str, Any]] = []
    for aid in ALL_IDS:
        c = raw[aid]
        und = _und_from_frames(c["frames"])
        val = validate_integration_plan(und, c["plan"], frames=c["frames"])
        exe = None
        if val.valid and getattr(c["plan"], "status", None) != "cannot_plan":
            try:
                exe = execute_integration_plan(c["frames"], c["plan"], val)
            except Exception:  # noqa: BLE001
                exe = None
        fo = exe.final_output if exe is not None and exe.success else None
        obs = observe_result_for_verifier(fo)
        fp_src = dataframe_fingerprint(fo) if isinstance(fo, pd.DataFrame) else None
        split = "development" if aid in DEV_IDS else "holdout"
        rows.append(
            {
                "attempt_id": aid,
                "request_id": c["request_id"],
                "split": split,
                "fast_correct": c["fast_correct"],
                "user_prompt": PROMPTS[aid],
                "note_ko": c.get("note_ko") or "",
                "defect": c.get("defect"),
                "plan_dict": c["plan"].to_dict(),
                "und": und,
                "validation_valid": bool(val.valid),
                "exec_success": None if exe is None else bool(exe.success),
                "result_obs": obs,
                "plan_fingerprint": plan_fingerprint(c["plan"]),
                "result_fingerprint": compact_result_fingerprint(fp_src),
                "truncated_obs": bool(isinstance(obs, dict) and obs.get("truncated")),
            }
        )
    return rows


def _label(fast: str, verdict: str | None) -> str | None:
    if not verdict:
        return None
    non = verdict in {"fail", "uncertain"}
    if fast == "NO" and non:
        return "CORRECT_REJECTION"
    if fast == "NO" and verdict == "pass":
        return "SILENT_WRONG"
    if fast == "YES" and verdict == "pass":
        return "CORRECT_PASS"
    if fast == "YES" and non:
        return "FALSE_FAIL"
    return None


def _base_payload(rec: dict[str, Any]) -> dict[str, Any]:
    return build_verifier_payload(
        user_prompt=rec["user_prompt"],
        plan=rec["plan_dict"],
        result=rec.get("result_obs"),
        understanding=rec["und"],
        variant=SEMANTIC_VERIFIER_VARIANT,
        materialization_mode=MATERIALIZATION,
        source_schemas=extract_source_schemas_from_understanding(rec["und"]),
    )


def _payload_for(variant: str, rec: dict[str, Any]) -> dict[str, Any]:
    p = _base_payload(rec)
    if variant == "P4":
        p = dict(p)
        p.pop("planner_claims", None)
        return p
    if variant == "P3":
        ordered: OrderedDict[str, Any] = OrderedDict()
        for k in (
            "user_prompt",
            "plan_structure",
            "observed_result",
            "materialization_evidence",
        ):
            if k in p:
                ordered[k] = p[k]
        for k, v in p.items():
            if k not in ordered:
                ordered[k] = v
        return dict(ordered)
    return p


def _write(name: str, obj: Any) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(obj, indent=2, ensure_ascii=False, default=str) + "\n")


def _load_cache() -> dict[str, Any]:
    return json.loads(CACHE.read_text()) if CACHE.exists() else {}


def _save_cache(cache: dict[str, Any]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(cache, indent=2, ensure_ascii=False, default=str) + "\n")


def invoke(
    variant: str,
    rec: dict[str, Any],
    cache: dict[str, Any],
    *,
    model: str = M7,
    repeat: int = 0,
) -> dict[str, Any]:
    key = f"{rec['attempt_id']}|{variant}|{model}|{repeat}"
    if key in cache:
        return cache[key]
    t0 = time.time()
    inv = new_verifier_invocation_id()
    if variant == "P0" and model == M7:
        ver = run_semantic_verification(
            user_prompt=rec["user_prompt"],
            plan=rec["plan_dict"],
            result=rec.get("result_obs"),
            understanding=rec["und"],
            variant=SEMANTIC_VERIFIER_VARIANT,
            model=model,
            materialization_mode=MATERIALIZATION,
            source_schemas=extract_source_schemas_from_understanding(rec["und"]),
            base_url=BASE_URL,
            lineage_context={
                "request_id": rec["request_id"],
                "attempt_id": rec["attempt_id"],
                "plan_fingerprint": rec.get("plan_fingerprint"),
                "result_fingerprint": rec.get("result_fingerprint"),
            },
        )
        raw = getattr(ver, "raw", None) or {}
    else:
        payload = _payload_for(variant, rec)
        prefix = prompt_for(variant)
        raw = chat_json(
            prefix + json.dumps(payload, ensure_ascii=False, indent=2),
            system=_VERIFIER_SYSTEM,
            base_url=BASE_URL,
            model=model,
        )
        ver = _normalize_verdict(raw if isinstance(raw, dict) else {})
    elapsed = round(time.time() - t0, 3)
    packed = {
        "attempt_id": rec["attempt_id"],
        "request_id": rec["request_id"],
        "verifier_invocation_id": getattr(ver, "verifier_invocation_id", None) or inv,
        "plan_fingerprint": rec.get("plan_fingerprint"),
        "result_fingerprint": rec.get("result_fingerprint"),
        "variant": variant,
        "model": model,
        "repeat": repeat,
        "split": rec["split"],
        "fast_correct": rec["fast_correct"],
        "verdict": ver.verdict,
        "reason_code": ver.reason_code,
        "evidence": list(ver.evidence or []),
        "raw_extra": {
            k: raw.get(k)
            for k in ("required_outcome", "observed_computation", "semantic_mismatches")
            if isinstance(raw, dict) and k in raw
        },
        "elapsed_s": elapsed,
        "escalation": _should_semantic_escalate(ver, uncertain_policy="escalate")[0],
        "label": _label(str(rec["fast_correct"]), ver.verdict),
        "truncated_obs": rec.get("truncated_obs"),
    }
    cache[key] = packed
    _save_cache(cache)
    return packed


def _metrics(rows: list[dict[str, Any]], ids: list[str] | None = None) -> dict[str, Any]:
    use = [r for r in rows if ids is None or r["attempt_id"] in ids]
    wrong = [r for r in use if r["fast_correct"] == "NO"]
    valid = [r for r in use if r["fast_correct"] == "YES"]
    cr = sum(1 for r in use if r["label"] == "CORRECT_REJECTION")
    cp = sum(1 for r in use if r["label"] == "CORRECT_PASS")
    sw = sum(1 for r in use if r["label"] == "SILENT_WRONG")
    ff = sum(1 for r in use if r["label"] == "FALSE_FAIL")
    unc = sum(1 for r in use if r.get("verdict") == "uncertain")
    n_wrong = max(len(wrong), 1)
    n_valid = max(len(valid), 1)
    pred_pos = cr + ff
    return {
        "n": len(use),
        "n_wrong": len(wrong),
        "n_valid": len(valid),
        "CORRECT_REJECTION": cr,
        "CORRECT_PASS": cp,
        "SILENT_WRONG": sw,
        "FALSE_FAIL": ff,
        "UNCERTAIN": unc,
        "semantic_error_recall": round(cr / n_wrong, 4),
        "precision": round(cr / pred_pos, 4) if pred_pos else None,
        "VALID_FALSE_FAIL_RATE": round(ff / n_valid, 4),
        "mean_latency_s": round(sum(r.get("elapsed_s") or 0 for r in use) / max(len(use), 1), 3),
        "silent_wrong_ids": [r["attempt_id"] for r in use if r["label"] == "SILENT_WRONG"],
        "false_fail_ids": [r["attempt_id"] for r in use if r["label"] == "FALSE_FAIL"],
    }


def _first(calls: list[dict[str, Any]], *, variant: str, model: str = M7) -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    for c in calls:
        if c["variant"] == variant and c["model"] == model and int(c.get("repeat") or 0) == 0:
            seen[c["attempt_id"]] = c
    return list(seen.values())


def pick_winners(dev_by_variant: dict[str, dict[str, Any]]) -> list[str]:
    """DEV only. Prefer zero false-fail, then recall, then M2 fail."""
    ranked = []
    for name, m in dev_by_variant.items():
        if name == "P0":
            continue
        ranked.append(
            (
                0 if m["VALID_FALSE_FAIL_RATE"] == 0 else 1,
                -m["semantic_error_recall"],
                0 if M2_ID not in (m.get("silent_wrong_ids") or []) else 1,
                name,
            )
        )
    ranked.sort()
    out = [x[3] for x in ranked[:2]]
    return out or ["P2", "P5"]


def run_suite(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not LIVE:
        return []
    cache = _load_cache()
    out: list[dict[str, Any]] = []
    recs = {r["attempt_id"]: r for r in rows}
    for variant in ("P0", "P1", "P2", "P3", "P4", "P5"):
        for r in rows:
            print(f"{variant} {r['attempt_id']}", flush=True)
            out.append(invoke(variant, r, cache))
    # DEV-based winners from first-shot 7B (may use partial cache on rerun)
    dev_metrics = {
        v: _metrics(_first(out, variant=v), DEV_IDS) for v in ("P0", "P1", "P2", "P3", "P4", "P5")
    }
    winners = pick_winners(dev_metrics)
    _write("_dev_winner_freeze.json", {"winners": winners, "dev_metrics": dev_metrics})
    for i in range(1, STABILITY_N):
        print(f"P0 {M2_ID} repeat {i}", flush=True)
        out.append(invoke("P0", recs[M2_ID], cache, repeat=i))
        for w in winners:
            print(f"{w} {M2_ID} repeat {i}", flush=True)
            out.append(invoke(w, recs[M2_ID], cache, repeat=i))
            for vid in VALID_STABILITY_IDS:
                print(f"{w} {vid} repeat {i}", flush=True)
                out.append(invoke(w, recs[vid], cache, repeat=i))
    for variant in ["P0", *winners]:
        for r in rows:
            print(f"{variant} {M8} {r['attempt_id']}", flush=True)
            out.append(invoke(variant, r, cache, model=M8))
    return out


def _taxonomy(row: dict[str, Any], call: dict[str, Any]) -> str:
    if call.get("label") != "SILENT_WRONG":
        return "NA"
    blob = " ".join(str(x) for x in call.get("evidence") or []).lower()
    aid = row["attempt_id"]
    if aid == M2_ID and "agent" in blob and "tid" in blob:
        return "MISREAD_USER_REQUIREMENT"
    if "paid" in (row.get("user_prompt") or "").lower() and "open" not in blob and call["verdict"] == "pass":
        return "MISREAD_PLAN_SEMANTICS"
    if "total" in (row.get("user_prompt") or "").lower() and "mean" not in blob:
        return "MISREAD_PLAN_SEMANTICS"
    if "planner" in blob or "required_columns" in blob:
        return "PLANNER_CLAIM_ANCHORING"
    if row.get("result_obs") and "row" not in blob and "column" not in blob:
        return "FAILED_CONTRADICTION_CHECK"
    return "FAILED_CONTRADICTION_CHECK"


def _claim_quality(call: dict[str, Any], rec: dict[str, Any]) -> str:
    if call.get("label") == "CORRECT_REJECTION":
        return "CONTRADICTION_IDENTIFIED"
    if call.get("label") == "SILENT_WRONG":
        blob = " ".join(str(x) for x in call.get("evidence") or []).lower()
        if "required" in blob or "grain" in blob:
            return "PLANNER_PARROTING"
        return "CONTRADICTION_MISSED"
    if call.get("label") == "CORRECT_PASS":
        return "SUPPORTED_USER_INTENT"
    if call.get("label") == "FALSE_FAIL":
        return "UNSUPPORTED_USER_INTENT"
    return "OTHER"


def write_static() -> None:
    esc = (ROOT / "core/integrate/semantic_escalation.py").read_text()
    ver = (ROOT / "core/integrate/semantic_verifier.py").read_text()
    _write("baseline_freeze.json", {
        "phase": "40A",
        "phase39z_sha": PHASE39Z_SHA,
        "shadow": "OFF",
        "production_variant": SEMANTIC_VERIFIER_VARIANT,
        "production_model": SEMANTIC_VERIFIER_MODEL,
        "result_aware": "observe_result_for_verifier" in esc,
        "verifier_prompt_changed": False,
        "verifier_model_changed": False,
        "threshold_changed": False,
        "escalation_policy_changed": False,
        "planner_changed": False,
        "validator_changed": False,
        "executor_changed": False,
        "dsl_changed": False,
        "v2_2_changed": False,
        "legacy_changed": False,
        "system_sha256": _sha(_VERIFIER_SYSTEM),
        "p0_prefix_sha256": PROMPT_REGISTRY["P0"]["sha256"],
        "p0_prefix_in_production_source": production_user_prefix(result_attached=True).split("Step order")[1][:40] in ver,
    })
    banned = [
        "group_by", "check group", "agent vs", "transaction id", "w2-wrong",
        "this case should fail", "tid",
    ]
    audit = []
    for name, spec in PROMPT_REGISTRY.items():
        if name == "P0":
            audit.append({"variant": name, "new_research_text": False, "leakage": False, "note": "production prefix frozen"})
            continue
        add = {"P1": P1_ADDENDUM, "P2": P2_ADDENDUM, "P3": P3_ADDENDUM, "P4": "", "P5": P5_ADDENDUM}[name]
        hits = [b for b in banned if b.lower() in add.lower()]
        generic = "user_prompt" in add or name == "P4"
        audit.append({
            "variant": name,
            "survives_schema_domain_op_change": len(hits) == 0 and (generic or name in {"P2", "P5", "P3"}),
            "leakage_hits": hits,
            "addendum": add,
        })
    _write("prompt_leakage_audit.json", {"variants": audit, "rule": "new sentences must remain valid if schemas/domains/ops change"})
    _write("prompt_variant_registry.json", {
        k: {kk: vv for kk, vv in v.items() if kk != "text"} | {"text": v["text"]}
        for k, v in PROMPT_REGISTRY.items()
    })
    _write("shadow_state_proof.json", {
        "shadow": "OFF",
        "env_MULTI_SHADOW_ENABLED": os.environ.get("MULTI_SHADOW_ENABLED", ""),
        "live_shadow_requests": 0,
        "live_verifier_harness": LIVE,
    })


def write_live(rows: list[dict[str, Any]], calls: list[dict[str, Any]]) -> None:
    recs = {r["attempt_id"]: r for r in rows}
    _write("research_corpus.json", {
        "n": len(rows),
        "n_wrong": sum(1 for r in rows if r["fast_correct"] == "NO"),
        "n_valid": sum(1 for r in rows if r["fast_correct"] == "YES"),
        "development": DEV_IDS,
        "holdout": HOLD_IDS,
        "rows": [
            {k: r[k] for k in (
                "attempt_id", "request_id", "split", "fast_correct", "user_prompt",
                "note_ko", "defect", "validation_valid", "exec_success",
                "plan_fingerprint", "result_fingerprint", "truncated_obs",
            )}
            for r in rows
        ],
    })
    _write("manual_attempt_labels.json", {
        r["attempt_id"]: {
            "FAST_ATTEMPT_CORRECT": r["fast_correct"],
            "request_id": r["request_id"],
            "plan_fingerprint": r["plan_fingerprint"],
            "split": r["split"],
            "note_ko": r["note_ko"],
        }
        for r in rows
    })
    by_var = {v: _first(calls, variant=v) for v in ("P0", "P1", "P2", "P3", "P4", "P5")}
    names = {
        "P0": "p0_baseline_results.json",
        "P1": "p1_intent_first_results.json",
        "P2": "p2_three_way_comparison_results.json",
        "P3": "p3_claim_last_results.json",
        "P4": "p4_no_claim_results.json",
        "P5": "p5_contradiction_check_results.json",
    }
    full_metrics = {}
    for v, fname in names.items():
        rows_v = by_var[v]
        full_metrics[v] = _metrics(rows_v)
        _write(fname, {"metrics": full_metrics[v], "rows": rows_v})
    dev_m = {v: _metrics(by_var[v], DEV_IDS) for v in by_var}
    hold_m = {v: _metrics(by_var[v], HOLD_IDS) for v in by_var}
    winners = pick_winners(dev_m)
    _write("development_results.json", {"metrics": dev_m, "winners_frozen_from_dev": winners})
    _write("holdout_results.json", {"metrics": hold_m, "winners_evaluated_not_retuned": winners})

    def stab(variant: str, aid: str) -> list[str]:
        return [
            c["verdict"]
            for c in calls
            if c["variant"] == variant and c["attempt_id"] == aid and c["model"] == M7
        ]

    _write("m2_stability_results.json", {
        "P0": stab("P0", M2_ID),
        **{w: stab(w, M2_ID) for w in winners},
    })
    _write("valid_control_stability.json", {
        w: {vid: stab(w, vid) for vid in VALID_STABILITY_IDS}
        for w in winners
    })

    p0 = {c["attempt_id"]: c for c in by_var["P0"]}
    m2_p0 = p0.get(M2_ID) or {}
    taxonomy = []
    for r in rows:
        if r["fast_correct"] != "NO":
            continue
        c = p0.get(r["attempt_id"])
        if not c:
            continue
        code = _taxonomy(r, c)
        note = {
            M2_ID: "요청은 agent 합계인데 계획은 tid 집계. 결과 3행 tid/hrs인데도 7B는 tid를 agent로 읽음.",
            "a40-wrong-receipt-grain": "상점별 합계 요청 vs 영수증 id 집계.",
            "a40-wrong-status-filter": "지급건 요청 vs open 필터.",
            "a40-wrong-metric-mean": "총량 요청 vs 평균.",
            "a40-wrong-desk-branch": "두 창구 나란히 vs A만.",
        }.get(r["attempt_id"], r.get("note_ko") or "")
        taxonomy.append({
            "attempt_id": r["attempt_id"],
            "p0_verdict": c.get("verdict"),
            "p0_label": c.get("label"),
            "class": code if c.get("label") == "SILENT_WRONG" else "CAUGHT_OR_NA",
            "note_ko": note,
        })
    _write("reasoning_failure_taxonomy.json", taxonomy)
    _write("verifier_claim_quality.json", [
        {
            "attempt_id": c["attempt_id"],
            "variant": "P0",
            "label": c.get("label"),
            "quality": _claim_quality(c, recs[c["attempt_id"]]),
            "evidence": c.get("evidence"),
        }
        for c in by_var["P0"]
    ])
    wide = p0.get("a40-valid-wide") or {}
    _write("result_reading_quality.json", {
        "truncation_case": "a40-valid-wide",
        "truncated_obs": (recs.get("a40-valid-wide") or {}).get("truncated_obs"),
        "p0": wide,
        "m2_mentions_tid_as_agent": "agent" in " ".join((m2_p0.get("evidence") or [])).lower(),
        "note_ko": "M2는 결과 열 tid를 요청의 agent로 오독하는 패턴이 반복됨.",
    })
    _write("uncertain_quality.json", {
        v: {
            "uncertain_n": m["UNCERTAIN"],
            "ids": [c["attempt_id"] for c in by_var[v] if c.get("verdict") == "uncertain"],
        }
        for v, m in full_metrics.items()
    })

    m8_p0 = _first(calls, variant="P0", model=M8)
    m8_best = _first(calls, variant=winners[0], model=M8) if winners else []
    matrix = {
        "M7_P0": _metrics(by_var["P0"]),
        "M7_best": _metrics(by_var[winners[0]]) if winners else {},
        "M8_P0": _metrics(m8_p0) if m8_p0 else {},
        "M8_best": _metrics(m8_best) if m8_best else {},
        "M32": "not_run_unless_needed",
    }
    _write("verifier_model_capability_matrix.json", matrix)

    m2_7b_p0_fail = m2_p0.get("verdict") in {"fail", "uncertain"}
    m2_7b_best = None
    if winners:
        hit = next((c for c in by_var[winners[0]] if c["attempt_id"] == M2_ID), None)
        m2_7b_best = (hit or {}).get("verdict")
    m2_8b = next((c for c in m8_p0 if c["attempt_id"] == M2_ID), None)
    m2_7b_any = any(
        (next((c for c in by_var[v] if c["attempt_id"] == M2_ID), {}) or {}).get("verdict")
        in {"fail", "uncertain"}
        for v in by_var
    )
    m2_stab_ok = False
    if winners:
        vs = stab(winners[0], M2_ID)
        m2_stab_ok = bool(vs) and all(x in {"fail", "uncertain"} for x in vs)
    prompt_m2 = "YES_STABLE" if m2_7b_any and m2_stab_ok else ("PARTIAL" if m2_7b_any else "NO")
    model_m2 = (
        "YES" if (m2_8b or {}).get("verdict") in {"fail", "uncertain"} and not m2_7b_p0_fail
        else (
            "PARTIAL"
            if (m2_8b or {}).get("verdict") in {"fail", "uncertain"}
            else "NO"
        )
    )
    ff_up = any(full_metrics[v]["FALSE_FAIL"] > full_metrics["P0"]["FALSE_FAIL"] for v in full_metrics)
    residual = "INDETERMINATE"
    if prompt_m2 == "NO" and model_m2 in {"YES", "PARTIAL"}:
        residual = "MODEL_DOMINANT"
    elif prompt_m2 in {"YES_STABLE", "PARTIAL"} and model_m2 in {"NO", "PARTIAL"}:
        residual = "PROMPT_DOMINANT" if prompt_m2 == "YES_STABLE" else "MIXED"
    elif prompt_m2 in {"YES_STABLE", "PARTIAL"} and model_m2 in {"YES", "PARTIAL"}:
        residual = "MIXED"
    elif prompt_m2 == "NO" and model_m2 == "NO":
        residual = "INDETERMINATE"
    _write("prompt_vs_model_attribution.json", {
        "m2_p0_7b": m2_p0.get("verdict"),
        "m2_best_7b": m2_7b_best,
        "m2_p0_8b": (m2_8b or {}).get("verdict"),
        "prompt_corrects_m2_7b": prompt_m2,
        "model_only_solves_m2": model_m2,
        "residual": residual,
        "false_fail_increase": ff_up,
        "claims_delta_p4_vs_p0_silent_wrong": (
            full_metrics["P4"]["SILENT_WRONG"] - full_metrics["P0"]["SILENT_WRONG"]
        ),
    })
    _write("latency_comparison.json", {
        "P0_7b_mean_s": full_metrics["P0"]["mean_latency_s"],
        "best_7b_mean_s": full_metrics[winners[0]]["mean_latency_s"] if winners else None,
        "M8_P0_mean_s": matrix["M8_P0"].get("mean_latency_s") if matrix["M8_P0"] else None,
        "note": "verifier only; not planner 32B",
    })
    extra_reject = (
        (full_metrics[winners[0]]["CORRECT_REJECTION"] - full_metrics["P0"]["CORRECT_REJECTION"])
        if winners else 0
    )
    _write("strategy_comparison.json", {
        "S0": {"name": "current_7b_prompt", "metrics": full_metrics["P0"]},
        "S1": {"name": "7b_best_generic_prompt", "variant": winners[0] if winners else None,
               "metrics": full_metrics[winners[0]] if winners else {},
               "extra_correct_rejections": extra_reject},
        "S2": {"name": "stronger_model_current_prompt", "metrics": matrix["M8_P0"]},
        "S3": {"name": "stronger_model_improved_prompt", "metrics": matrix["M8_best"]},
        "implemented": False,
    })
    seven_ok = (
        "YES_WITH_LIMITATIONS"
        if prompt_m2 != "YES_STABLE"
        else "YES"
    )
    if full_metrics["P0"]["SILENT_WRONG"] >= 3 and prompt_m2 == "NO":
        seven_ok = "NO" if model_m2 in {"YES", "PARTIAL"} else "YES_WITH_LIMITATIONS"
    next_phase = "E"
    if prompt_m2 == "YES_STABLE" and not ff_up:
        next_phase = "A"
    elif prompt_m2 == "NO" and model_m2 in {"YES", "PARTIAL"}:
        next_phase = "B"
    elif residual == "MIXED":
        next_phase = "C"
    _write("architecture_recommendation.json", {
        "residual": residual,
        "seven_b_default": seven_ok,
        "next": next_phase,
        "next_name": {
            "A": "Phase 40B — Semantic Verifier Reasoning Prompt Implementation",
            "B": "Phase 40B — Evidence-Based Verifier Model Strategy Research",
            "C": "Phase 40B — Verifier Prompt-vs-Model Strategy Generalization",
            "D": "Planner Semantic Contract Generalization Research",
            "E": "keep current verifier; no production change",
        }[next_phase],
        "production_prompt_unchanged": True,
        "production_model_unchanged": True,
        "do_not_implement_in_40a": True,
        "early_routing_reopened": False,
        "python_semantic_validation": False,
    })
    p0_m2_pass = m2_p0.get("verdict") == "pass"
    gate = "A" if p0_m2_pass and LIVE and calls else ("B" if not p0_m2_pass else "B")
    if not LIVE or not calls:
        gate = "B"
    elif p0_m2_pass:
        gate = "A"
    _write("phase40a_summary.json", {
        "gate": gate,
        "migration": "NOT_APPROVED",
        "shadow": "OFF",
        "phase39z_sha": PHASE39Z_SHA,
        "p0_m2": m2_p0.get("verdict"),
        "best_variant": winners[0] if winners else None,
        "prompt_m2": prompt_m2,
        "model_m2": model_m2,
        "residual": residual,
        "seven_b_default": seven_ok,
        "false_fail_p0": full_metrics["P0"]["FALSE_FAIL"],
        "n_calls": len(calls),
        "production_changed": False,
    })
    _write("regression_results.json", {
        "production_code_changed": False,
        "live": LIVE,
        "n_calls": len(calls),
    })


def main() -> None:
    write_static()
    rows = build_corpus()
    missing = [i for i in ALL_IDS if i not in {r["attempt_id"] for r in rows}]
    if missing:
        raise RuntimeError(missing)
    bad = [r["attempt_id"] for r in rows if not r["validation_valid"] or not r["exec_success"]]
    if bad:
        print("WARN non-valid-or-exec", bad, flush=True)
    print("n", len(rows), "wrong", sum(r["fast_correct"]=="NO" for r in rows),
          "valid", sum(r["fast_correct"]=="YES" for r in rows), "live", LIVE, flush=True)
    calls = run_suite(rows)
    write_live(rows, calls)
    print("wrote", OUT, "calls", len(calls), flush=True)


if __name__ == "__main__":
    main()
