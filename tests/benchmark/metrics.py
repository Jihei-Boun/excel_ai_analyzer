"""Benchmark metrics aggregation and result persistence."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


FAILURE_CATEGORIES = (
    "routing_error",
    "plan_generation_error",
    "plan_validation_error",
    "execution_error",
    "result_validation_error",
    "wrong_column",
    "wrong_operation",
    "wrong_filter",
    "wrong_result",
    "interpreter_grounding_error",
    "fallback",
    "crash",
    "safe_failure_ok",
    "none",
)


@dataclass
class LevelScores:
    routing: bool | None = None
    plan: bool | None = None
    execution: bool | None = None
    interpretation: bool | None = None


@dataclass
class CaseResult:
    case_id: str
    domain: str
    profile: str
    question: str
    mode: str  # deterministic | live
    ok: bool
    levels: LevelScores = field(default_factory=LevelScores)
    route_observed: str | None = None
    failure_category: str = "none"
    analysis_plan_direct: bool = False
    legacy_fallback: bool = False
    pandasai_fallback: bool = False
    first_plan_success: bool = False
    retry_success: bool = False
    retry_exhausted: bool = False
    semantic_warning: bool = False
    details: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


def _rate(num: int, den: int) -> float:
    if den <= 0:
        return 0.0
    return round(100.0 * num / den, 2)


def summarize_results(
    results: list[CaseResult],
    *,
    model: str | None = None,
    mode: str = "deterministic",
) -> dict[str, Any]:
    total = len(results)
    by_domain: dict[str, list[CaseResult]] = defaultdict(list)
    for r in results:
        by_domain[r.domain].append(r)

    def _block(items: list[CaseResult]) -> dict[str, Any]:
        n = len(items)
        routing_checked = [i for i in items if i.levels.routing is not None]
        plan_checked = [i for i in items if i.levels.plan is not None]
        exec_checked = [i for i in items if i.levels.execution is not None]
        interp_checked = [i for i in items if i.levels.interpretation is not None]
        return {
            "total_cases": n,
            "overall_ok_rate": _rate(sum(1 for i in items if i.ok), n),
            "routing_success_rate": _rate(
                sum(1 for i in routing_checked if i.levels.routing), len(routing_checked)
            ),
            "plan_valid_rate": _rate(
                sum(1 for i in plan_checked if i.levels.plan), len(plan_checked)
            ),
            "execution_success_rate": _rate(
                sum(1 for i in exec_checked if i.levels.execution), len(exec_checked)
            ),
            "result_accuracy": _rate(
                sum(1 for i in exec_checked if i.levels.execution), len(exec_checked)
            ),
            "interpretation_grounding_rate": _rate(
                sum(1 for i in interp_checked if i.levels.interpretation),
                len(interp_checked),
            ),
            "analysis_plan_direct_success": sum(1 for i in items if i.analysis_plan_direct),
            "analysis_plan_direct_rate": _rate(
                sum(1 for i in items if i.analysis_plan_direct), n
            ),
            "legacy_fallback_count": sum(1 for i in items if i.legacy_fallback),
            "legacy_fallback_rate": _rate(sum(1 for i in items if i.legacy_fallback), n),
            "pandasai_fallback_count": sum(1 for i in items if i.pandasai_fallback),
            "pandasai_fallback_rate": _rate(
                sum(1 for i in items if i.pandasai_fallback), n
            ),
            "fallback_rate": _rate(
                sum(1 for i in items if i.legacy_fallback or i.pandasai_fallback), n
            ),
            "first_plan_success": sum(1 for i in items if i.first_plan_success),
            "retry_success": sum(1 for i in items if i.retry_success),
            "retry_exhausted": sum(1 for i in items if i.retry_exhausted),
            "planner_retry_rate": _rate(
                sum(1 for i in items if i.retry_success or i.retry_exhausted), n
            ),
            "semantic_warning_rate": _rate(
                sum(1 for i in items if i.semantic_warning), n
            ),
            "failure_categories": dict(
                Counter(i.failure_category for i in items if not i.ok or i.failure_category not in {"none", "safe_failure_ok"})
            ),
            "fallback_reasons": dict(
                Counter(
                    str((i.details or {}).get("fallback_reason") or "unspecified")
                    for i in items
                    if i.legacy_fallback or i.pandasai_fallback or (i.details or {}).get("fallback_reason")
                )
            ),
        }

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "model": model,
        "total_cases": total,
        "overall": _block(results),
        "by_domain": {dom: _block(items) for dom, items in sorted(by_domain.items())},
        "cases": [r.to_dict() for r in results],
    }
    return summary


def save_summary(summary: dict[str, Any], results_dir: Path) -> Path:
    results_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    path = results_dir / f"{stamp}.json"
    payload = _json_safe(summary)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    latest = results_dir / "latest.json"
    latest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _json_safe(obj: Any) -> Any:
    """JSON dump용 — tuple dict keys 등 non-JSON 타입을 문자열로 정규화."""
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for key, val in obj.items():
            if isinstance(key, (str, int, float, bool)) or key is None:
                sk = key if isinstance(key, str) or key is None else str(key)
            else:
                sk = str(key)
            out[str(sk) if sk is not None else "null"] = _json_safe(val)
        return out
    if isinstance(obj, (list, tuple)):
        return [_json_safe(x) for x in obj]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return str(obj)


def load_summary(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def compare_summaries(baseline: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    """Lightweight before/after comparison (no dashboard)."""

    def _pick(block: dict[str, Any], key: str) -> float:
        return float((block or {}).get(key) or 0.0)

    base_o = baseline.get("overall") or {}
    cur_o = current.get("overall") or {}
    keys = [
        "overall_ok_rate",
        "routing_success_rate",
        "plan_valid_rate",
        "result_accuracy",
        "analysis_plan_direct_rate",
        "fallback_rate",
        "pandasai_fallback_rate",
        "planner_retry_rate",
        "semantic_warning_rate",
    ]
    overall_delta = {
        k: {
            "baseline": _pick(base_o, k),
            "current": _pick(cur_o, k),
            "delta": round(_pick(cur_o, k) - _pick(base_o, k), 2),
        }
        for k in keys
    }

    domains = sorted(
        set((baseline.get("by_domain") or {})) | set((current.get("by_domain") or {}))
    )
    domain_delta = {}
    for dom in domains:
        b = (baseline.get("by_domain") or {}).get(dom) or {}
        c = (current.get("by_domain") or {}).get(dom) or {}
        domain_delta[dom] = {
            "result_accuracy": {
                "baseline": _pick(b, "result_accuracy"),
                "current": _pick(c, "result_accuracy"),
                "delta": round(_pick(c, "result_accuracy") - _pick(b, "result_accuracy"), 2),
            },
            "analysis_plan_direct_rate": {
                "baseline": _pick(b, "analysis_plan_direct_rate"),
                "current": _pick(c, "analysis_plan_direct_rate"),
                "delta": round(
                    _pick(c, "analysis_plan_direct_rate")
                    - _pick(b, "analysis_plan_direct_rate"),
                    2,
                ),
            },
        }
    return {
        "baseline_generated_at": baseline.get("generated_at"),
        "current_generated_at": current.get("generated_at"),
        "overall_delta": overall_delta,
        "domain_delta": domain_delta,
    }
