"""Phase 16: IntegrationPlan Validator (no execution, no semantic repair).

validate_integration_plan(understanding, plan) → IntegrationValidationResult
Does not mutate plan. Does not choose keys/ops/metrics.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from core.integrate.integration_contracts import resolve_aggregate_alias
from core.integrate.integration_plan_types import (
    AGGREGATE_FUNCTIONS,
    FILTER_OPERATORS,
    FINAL_GRAIN_COLLAPSED,
    FINAL_GRAIN_ROW_LEVEL,
    FINAL_GRAIN_VALUES,
    INTEGRATION_ATOMIC_OPS,
    JOIN_HOW,
    IntegrationPlan,
    IntegrationStep,
)
from core.integrate.integration_validation_types import (
    SEVERITY_ERROR,
    SEVERITY_INFO,
    SEVERITY_WARNING,
    IntegrationValidationIssue,
    IntegrationValidationResult,
)
from core.integrate.relationship_types import CrossFileUnderstanding

# Amplification thresholds (config-like constants)
AMP_WARNING_RATIO = 2.0
AMP_ERROR_RATIO = 10.0
NULL_KEY_WARNING = 0.05
NULL_KEY_STRONG = 0.25
LOW_MATCH_WARNING = 0.25
UNION_OVERLAP_STRICT = 0.5  # shared/max(cols) below → incompatible for aligned


@dataclass
class _ColumnMeta:
    name: str
    dtype_family: str = "other"
    null_ratio: float = 0.0
    uniqueness_ratio: float = 0.0
    distinct_count: int = 0


@dataclass
class _DatasetMeta:
    name: str
    kind: str  # source | intermediate
    row_count: int | None
    columns: dict[str, _ColumnMeta] = field(default_factory=dict)
    may_contain_summary_rows: bool = False


def validate_integration_plan(
    understanding: CrossFileUnderstanding | dict[str, Any],
    plan: IntegrationPlan | dict[str, Any],
    *,
    user_prompt: str | None = None,
) -> IntegrationValidationResult:
    """Validate IntegrationPlan against CrossFileUnderstanding.

    Never mutates ``plan``. Never rewrites ops/keys/metrics.
    """
    und = (
        understanding.to_dict()
        if isinstance(understanding, CrossFileUnderstanding)
        else dict(understanding)
    )
    # Deep-copy plan view so immutability is obvious even if caller passes dict
    plan_obj = plan if isinstance(plan, IntegrationPlan) else None
    plan_dict = copy.deepcopy(plan.to_dict() if plan_obj else dict(plan))
    if plan_obj is None:
        # Accept dict shaped like IntegrationPlan without re-parsing semantics
        from core.integrate.integration_plan_types import integration_plan_from_dict

        try:
            plan_obj = integration_plan_from_dict(plan_dict)
        except Exception as exc:  # noqa: BLE001
            return IntegrationValidationResult(
                valid=False,
                errors=[
                    IntegrationValidationIssue(
                        code="invalid_plan_shape",
                        severity=SEVERITY_ERROR,
                        message=f"Plan failed structural parse: {exc}",
                    )
                ],
                metadata={"phase": 16},
            )

    # Snapshot for immutability check
    plan_before = copy.deepcopy(plan_obj.to_dict())

    errors: list[IntegrationValidationIssue] = []
    warnings: list[IntegrationValidationIssue] = []
    infos: list[IntegrationValidationIssue] = []
    lineage: list[dict[str, Any]] = []

    def err(code: str, message: str, *, step_id: str | None = None, **details: Any) -> None:
        errors.append(
            IntegrationValidationIssue(
                code=code,
                severity=SEVERITY_ERROR,
                message=message,
                step_id=step_id,
                details=details,
            )
        )

    def warn(code: str, message: str, *, step_id: str | None = None, **details: Any) -> None:
        warnings.append(
            IntegrationValidationIssue(
                code=code,
                severity=SEVERITY_WARNING,
                message=message,
                step_id=step_id,
                details=details,
            )
        )

    def info(code: str, message: str, *, step_id: str | None = None, **details: Any) -> None:
        infos.append(
            IntegrationValidationIssue(
                code=code,
                severity=SEVERITY_INFO,
                message=message,
                step_id=step_id,
                details=details,
            )
        )

    # --- cannot_plan is a successful safe outcome ---
    if plan_obj.status == "cannot_plan":
        if plan_obj.steps:
            err("cannot_plan_has_steps", "cannot_plan must have empty steps[]")
        if plan_obj.final_output not in (None, ""):
            err(
                "cannot_plan_has_final_output",
                "cannot_plan must not set final_output",
                final_output=plan_obj.final_output,
            )
        if not (plan_obj.reason or "").strip():
            warn("cannot_plan_missing_reason", "cannot_plan should include a reason")
        info(
            "cannot_plan_accepted",
            "Plan status cannot_plan is a valid safe outcome (not an execution failure).",
            ambiguities=list(plan_obj.ambiguities),
        )
        result = IntegrationValidationResult(
            valid=not errors,
            errors=errors,
            warnings=warnings,
            infos=infos,
            metadata={"phase": 16, "status": "cannot_plan"},
            lineage=[],
        )
        assert plan_obj.to_dict() == plan_before
        return result

    if plan_obj.status != "planned":
        err("invalid_status", f"Unknown status: {plan_obj.status!r}")
        return IntegrationValidationResult(valid=False, errors=errors, metadata={"phase": 16})

    # --- planned ---
    if not plan_obj.steps:
        err("empty_steps", "planned status requires at least one step")
    if not plan_obj.final_output:
        err("missing_final_output", "planned status requires final_output")

    datasets = _build_source_datasets(und)
    source_names = set(datasets.keys())
    produced: set[str] = set()
    seen_step_ids: set[str] = set()

    pairwise_index = _index_pairwise(und)
    relationship_index = _index_relationships(und)

    for step in plan_obj.steps:
        if not step.id:
            err("missing_step_id", "Step id is required", step_id=None)
        elif step.id in seen_step_ids:
            err("duplicate_step_id", f"Duplicate step id: {step.id}", step_id=step.id)
        else:
            seen_step_ids.add(step.id)

        if step.op not in INTEGRATION_ATOMIC_OPS:
            err(
                "unsupported_operation",
                f"Unsupported operation {step.op!r}",
                step_id=step.id,
                op=step.op,
            )
            continue

        # Resolve inputs
        for inp in step.inputs:
            if inp not in datasets and inp not in produced:
                err(
                    "nonexistent_input",
                    f"Input {inp!r} is neither a source nor a prior step output",
                    step_id=step.id,
                    input=inp,
                )

        if step.output in source_names:
            err(
                "output_collides_with_source",
                f"Output {step.output!r} collides with a source file name",
                step_id=step.id,
                output=step.output,
            )
        if step.output in produced:
            err(
                "duplicate_output",
                f"Output {step.output!r} already produced by an earlier step",
                step_id=step.id,
                output=step.output,
            )

        # Op-specific (only if inputs mostly resolve)
        input_metas = [datasets[i] for i in step.inputs if i in datasets]
        if len(input_metas) == len(step.inputs):
            _validate_step(
                step,
                input_metas,
                pairwise_index=pairwise_index,
                relationship_index=relationship_index,
                err=err,
                warn=warn,
                info=info,
                lineage=lineage,
            )
            out_meta = _simulate_output(step, input_metas)
            if out_meta is not None:
                datasets[step.output] = out_meta
                produced.add(step.output)
                if lineage:
                    lineage[-1]["output_columns"] = sorted(out_meta.columns.keys())
                    if step.op == "join":
                        from core.integrate.integration_contracts import JOIN_SUFFIXES

                        lineage[-1]["join_suffixes"] = list(JOIN_SUFFIXES)
        else:
            # Still mark output as unknown placeholder to catch later deps softly
            produced.add(step.output)
            datasets[step.output] = _DatasetMeta(
                name=step.output, kind="intermediate", row_count=None, columns={}
            )

    if plan_obj.final_output and plan_obj.final_output not in datasets:
        err(
            "unresolved_final_output",
            f"final_output {plan_obj.final_output!r} is not a known source/step output",
            final_output=plan_obj.final_output,
        )
    elif plan_obj.final_output:
        info(
            "final_output_resolved",
            f"final_output resolves to {plan_obj.final_output!r}",
            final_output=plan_obj.final_output,
        )
        _validate_final_output_requirements(
            plan_obj,
            datasets=datasets,
            err=err,
            warn=warn,
            info=info,
        )

    # Plan immutability
    assert plan_obj.to_dict() == plan_before, "validator must not mutate IntegrationPlan"

    # user_prompt reserved for Phase 18 — ignore for semantic alignment hardcoding
    _ = user_prompt

    return IntegrationValidationResult(
        valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
        infos=infos,
        metadata={
            "phase": 16,
            "status": "planned",
            "amp_warning_ratio": AMP_WARNING_RATIO,
            "amp_error_ratio": AMP_ERROR_RATIO,
            "datasets": sorted(datasets.keys()),
        },
        lineage=lineage,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _validate_final_output_requirements(
    plan_obj: IntegrationPlan,
    *,
    datasets: dict[str, _DatasetMeta],
    err,
    warn,
    info,
) -> None:
    """Consistency checks for Planner-declared final_output_requirements only.

    Does not infer user intent. Does not rewrite the plan.
    """
    req = plan_obj.final_output_requirements
    if req is None or req.is_empty:
        info(
            "final_output_requirements_absent",
            "Plan has no final_output_requirements; grain/field consistency "
            "checks are skipped (optional Phase 24 contract).",
        )
        return

    grain = (req.grain or "").strip().lower() or None
    if grain and grain not in FINAL_GRAIN_VALUES:
        err(
            "invalid_final_grain",
            f"final_output_requirements.grain {grain!r} is not supported",
            grain=grain,
        )
        return

    ops = [s.op for s in plan_obj.steps]
    has_aggregate = "aggregate" in ops
    final_id = plan_obj.final_output
    final_step = next((s for s in plan_obj.steps if s.output == final_id), None)
    aggregate_produces_final = bool(final_step and final_step.op == "aggregate")
    # Also: final is select/rename of an aggregate output
    upstream_agg = False
    if final_step and final_step.op != "aggregate":
        produced_by_agg = {s.output for s in plan_obj.steps if s.op == "aggregate"}
        frontier = set(final_step.inputs)
        seen: set[str] = set()
        while frontier:
            cur = frontier.pop()
            if cur in seen:
                continue
            seen.add(cur)
            if cur in produced_by_agg:
                upstream_agg = True
                break
            prev = next((s for s in plan_obj.steps if s.output == cur), None)
            if prev:
                frontier.update(prev.inputs)

    collapses = aggregate_produces_final or upstream_agg or (
        has_aggregate and final_step is None
    )

    if grain in FINAL_GRAIN_ROW_LEVEL and (aggregate_produces_final or upstream_agg):
        # Phase 30: blocking ERROR (was WARNING since Phase 24).
        # Validates Planner-declared row-level grain vs collapsing aggregate that
        # feeds final_output — no new user-intent inference.
        # Aggregate alone is not blocked; grain=group/summary + aggregate remains valid.
        err(
            "final_grain_contradiction",
            "Declared final grain is row-level (detail/entity), but the plan "
            "collapses rows with aggregate before/at final_output. "
            "Align grain with the plan (use group/summary if aggregating) "
            "or remove the collapsing aggregate if row-level output was intended.",
            grain=grain,
            has_aggregate=has_aggregate,
            aggregate_produces_final=aggregate_produces_final,
            upstream_aggregate=upstream_agg,
        )
    elif grain in FINAL_GRAIN_COLLAPSED and not has_aggregate:
        warn(
            "final_grain_contradiction",
            "Declared final grain is group/summary, but the plan has no aggregate step. "
            "Align grain with the plan or add the aggregate required for that grain.",
            grain=grain,
        )
    elif grain == "group" and has_aggregate:
        # Prefer non-empty group_by on the aggregate that feeds final
        agg_steps = [s for s in plan_obj.steps if s.op == "aggregate"]
        relevant = [
            s
            for s in agg_steps
            if s.output == final_id
            or (final_step and s.output in (final_step.inputs or []))
        ] or agg_steps[-1:]
        for s in relevant:
            gb = [str(x) for x in ((s.params or {}).get("group_by") or [])]
            if not gb:
                warn(
                    "final_grain_group_without_group_by",
                    "Declared grain=group but aggregate has empty group_by "
                    "(global summary). Prefer grain=summary or set group_by.",
                    step_id=s.id,
                )

    # Required columns must exist on simulated final schema
    final_meta = datasets.get(str(final_id)) if final_id else None
    if final_meta is not None and req.required_columns:
        missing = [c for c in req.required_columns if c not in final_meta.columns]
        if missing:
            loss_trace = _required_column_loss_trace(
                plan_obj, datasets=datasets, missing=missing
            )
            err(
                "final_required_field_missing",
                "Declared final_output_requirements.required_columns are not "
                "present in the simulated final schema. Later transformations "
                "may have dropped them permanently.",
                missing_columns=missing,
                final_output=final_id,
                available_columns=sorted(final_meta.columns.keys())[:40],
                loss_trace=loss_trace,
            )
            for item in loss_trace:
                if item.get("status") == "permanently_lost":
                    err(
                        "required_field_permanently_lost",
                        "Declared required column was available earlier but was "
                        "removed and no later step can recreate it.",
                        column=item.get("column"),
                        lost_at_step=item.get("lost_at_step"),
                        lost_at_op=item.get("lost_at_op"),
                        last_seen_output=item.get("last_seen_output"),
                    )
                elif item.get("status") == "never_materializable":
                    err(
                        "required_field_not_materializable",
                        "Declared required column is not present on any simulated "
                        "dataset and no step creates it (rename/aggregate alias).",
                        column=item.get("column"),
                    )
        else:
            info(
                "final_required_fields_present",
                "Declared required_columns are present on simulated final schema",
                required_columns=list(req.required_columns),
            )

    # Strong grain contradiction: row-level declaration + collapsing aggregate
    # that drops declared required fields (not merely a mislabeled grain).
    if (
        grain in FINAL_GRAIN_ROW_LEVEL
        and (aggregate_produces_final or upstream_agg)
        and req.required_columns
        and final_meta is not None
    ):
        missing_req = [c for c in req.required_columns if c not in final_meta.columns]
        if missing_req:
            err(
                "final_grain_contradiction",
                "Declared row-level final grain conflicts with a collapsing "
                "aggregate that permanently removes declared required columns. "
                "Align grain/requirements with the plan, or revise transformations "
                "so the declared final-output contract remains satisfiable.",
                grain=grain,
                missing_columns=missing_req,
            )

    if req.one_row_represents:
        info(
            "final_one_row_represents_declared",
            "Planner declared one_row_represents (observability only)",
            one_row_represents=req.one_row_represents,
        )

    # Structural projection: detail/entity + select drops upstream join keys
    _check_join_keys_dropped_by_select(
        plan_obj,
        grain=grain,
        datasets=datasets,
        err=err,
    )


def _collect_upstream_join_keys(plan_obj: IntegrationPlan, final_id: str | None) -> set[str]:
    """Join key column names reachable upstream of final_output (structural)."""
    if not final_id:
        return set()
    keys: set[str] = set()
    by_out = {s.output: s for s in plan_obj.steps}
    frontier = {final_id}
    seen: set[str] = set()
    while frontier:
        cur = frontier.pop()
        if cur in seen:
            continue
        seen.add(cur)
        step = by_out.get(cur)
        if not step:
            continue
        if step.op == "join":
            params = step.params or {}
            for k in list(params.get("left_keys") or []) + list(params.get("right_keys") or []):
                if str(k).strip():
                    keys.add(str(k).strip())
        for inp in step.inputs or []:
            frontier.add(inp)
    return keys


def _check_join_keys_dropped_by_select(
    plan_obj: IntegrationPlan,
    *,
    grain: str | None,
    datasets: dict[str, _DatasetMeta],
    err,
) -> None:
    """Row-level grain + select removing upstream join keys → ERROR.

    Evidence for retry — does not prescribe non-key columns to keep.
    """
    if grain not in FINAL_GRAIN_ROW_LEVEL:
        return
    final_id = plan_obj.final_output
    final_step = next((s for s in plan_obj.steps if s.output == final_id), None)
    if not final_step or final_step.op != "select_columns":
        return
    join_keys = _collect_upstream_join_keys(plan_obj, final_id)
    if not join_keys:
        return
    if not final_step.inputs:
        return
    src_name = final_step.inputs[0]
    src_meta = datasets.get(src_name)
    final_meta = datasets.get(str(final_id)) if final_id else None
    if not src_meta or not final_meta:
        return
    present_before = [k for k in sorted(join_keys) if k in src_meta.columns]
    dropped = [k for k in present_before if k not in final_meta.columns]
    if dropped:
        err(
            "join_key_dropped_in_final_projection",
            "Declared row-level final grain (detail/entity), but select_columns "
            "removed join key column(s) that identify rows on the prior dataset. "
            "The final projection no longer matches the declared output semantics. "
            "Reconsider whether select_columns is necessary, or whether identifying "
            "keys must remain available through the final output.",
            dropped_join_keys=dropped,
            select_step=final_step.id,
            grain=grain,
        )


def _step_can_create_column(step: IntegrationStep, column: str) -> bool:
    """Structural materialization only (rename target / aggregate alias / group_by)."""
    params = step.params or {}
    if step.op == "rename_columns":
        mapping = params.get("mapping") or {}
        return column in {str(v) for v in mapping.values()}
    if step.op == "aggregate":
        if column in {str(g) for g in (params.get("group_by") or [])}:
            return True
        for m in params.get("metrics") or []:
            if isinstance(m, dict) and resolve_aggregate_alias(m) == column:
                return True
    return False



def _required_column_loss_trace(
    plan_obj: IntegrationPlan,
    *,
    datasets: dict[str, _DatasetMeta],
    missing: list[str],
) -> list[dict[str, Any]]:
    """Trace declared required columns that are absent from final schema.

    Distinguishes:
    - permanently_lost: present on an intermediate, removed, not recreated later
    - never_materializable: never appears and no step creates it
    - available_now / materializable_later are not returned for missing finals
    """
    out: list[dict[str, Any]] = []
    steps = list(plan_obj.steps)
    # Walk lineage in order using simulated datasets after each step
    for col in missing:
        last_seen_step: str | None = None
        last_seen_output: str | None = None
        lost_at_step: str | None = None
        lost_at_op: str | None = None
        seen_anywhere = False
        for i, step in enumerate(steps):
            meta = datasets.get(step.output)
            cols = set((meta.columns if meta else {}).keys())
            if col in cols:
                seen_anywhere = True
                last_seen_step = step.id
                last_seen_output = step.output
                continue
            # absent on this output
            if seen_anywhere and lost_at_step is None:
                # was present earlier → lost here unless later materializes
                later = steps[i + 1 :]
                if any(_step_can_create_column(s, col) for s in later):
                    continue
                # check if any later output regains it via simulation
                regained = any(
                    col in ((datasets.get(s.output).columns if datasets.get(s.output) else {}))
                    for s in later
                )
                if regained:
                    continue
                lost_at_step = step.id
                lost_at_op = step.op
        if lost_at_step:
            out.append(
                {
                    "column": col,
                    "status": "permanently_lost",
                    "lost_at_step": lost_at_step,
                    "lost_at_op": lost_at_op,
                    "last_seen_step": last_seen_step,
                    "last_seen_output": last_seen_output,
                }
            )
        else:
            creatable = any(_step_can_create_column(s, col) for s in steps)
            # Also: present on a source but never flowed to final
            on_source = any(
                col in (meta.columns or {})
                for name, meta in datasets.items()
                if meta.kind == "source"
            )
            if not creatable and not on_source and not seen_anywhere:
                out.append({"column": col, "status": "never_materializable"})
            elif seen_anywhere or on_source:
                out.append(
                    {
                        "column": col,
                        "status": "permanently_lost",
                        "lost_at_step": lost_at_step,
                        "lost_at_op": lost_at_op,
                        "last_seen_step": last_seen_step,
                        "last_seen_output": last_seen_output,
                    }
                )
            else:
                out.append({"column": col, "status": "never_materializable"})
    return out


def _build_source_datasets(und: dict[str, Any]) -> dict[str, _DatasetMeta]:
    out: dict[str, _DatasetMeta] = {}
    for p in und.get("file_profiles") or []:
        if not isinstance(p, dict):
            continue
        sid = str(p.get("source_id") or "")
        if not sid:
            continue
        cols: dict[str, _ColumnMeta] = {}
        obs = p.get("observations") or {}
        for c in obs.get("columns") or []:
            if not isinstance(c, dict):
                continue
            name = str(c.get("name") or "")
            if not name:
                continue
            cols[name] = _ColumnMeta(
                name=name,
                dtype_family=str(c.get("dtype_family") or "other"),
                null_ratio=float(c.get("null_ratio") or 0.0),
                uniqueness_ratio=float(c.get("uniqueness_ratio") or 0.0),
                distinct_count=int(c.get("distinct_count") or 0),
            )
        # Fallback names-only
        for name in obs.get("column_names") or []:
            n = str(name)
            if n and n not in cols:
                cols[n] = _ColumnMeta(name=n)
        out[sid] = _DatasetMeta(
            name=sid,
            kind="source",
            row_count=int(p["row_count"]) if p.get("row_count") is not None else None,
            columns=cols,
            may_contain_summary_rows=_guess_summary_presence(obs),
        )
    return out


def _guess_summary_presence(obs: dict[str, Any]) -> bool:
    """Observational hint only — does not select detail rows."""
    blob = " ".join(
        str(x)
        for col in (obs.get("columns") or [])
        for x in (col.get("sample_values") or [])
        if isinstance(col, dict)
    ).lower()
    return any(tok in blob for tok in ("소계", "합계", "총계", "subtotal", "total", "grand"))


def _index_pairwise(und: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    idx: dict[tuple[str, str], dict[str, Any]] = {}
    for o in und.get("pairwise_observations") or []:
        if not isinstance(o, dict):
            continue
        a, b = str(o.get("left_source") or ""), str(o.get("right_source") or "")
        if a and b:
            idx[(a, b)] = o
            idx[(b, a)] = o
    return idx


def _index_relationships(und: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    idx: dict[tuple[str, str], dict[str, Any]] = {}
    for r in und.get("relationships") or []:
        if not isinstance(r, dict):
            continue
        a, b = str(r.get("left_source") or ""), str(r.get("right_source") or "")
        if a and b:
            idx[(a, b)] = r
            idx[(b, a)] = r
    return idx


def _pair_stats(
    pairwise_index: dict[tuple[str, str], dict[str, Any]],
    left: str,
    right: str,
    left_key: str,
    right_key: str,
) -> dict[str, Any] | None:
    obs = pairwise_index.get((left, right))
    if not obs:
        return None
    # Orientation: stored left_source may not match join left
    stored_left = str(obs.get("left_source") or "")
    flip = stored_left == right
    for pair in obs.get("candidate_pairs") or []:
        if not isinstance(pair, dict):
            continue
        pl, pr = str(pair.get("left_column") or ""), str(pair.get("right_column") or "")
        if flip:
            pl, pr = pr, pl
        if pl == left_key and pr == right_key:
            return pair
        if not flip and stored_left == left and pl == left_key and pr == right_key:
            return pair
    return None


def _key_ambiguity_from_pairwise(
    pairwise_index: dict[tuple[str, str], dict[str, Any]],
    left: str,
    right: str,
) -> dict[str, Any]:
    obs = pairwise_index.get((left, right))
    if not obs:
        return {}
    amb = obs.get("key_ambiguity_observation")
    return dict(amb) if isinstance(amb, dict) else {}


def _composite_stats(
    pairwise_index: dict[tuple[str, str], dict[str, Any]],
    left: str,
    right: str,
    left_keys: list[str],
    right_keys: list[str],
) -> dict[str, Any] | None:
    obs = pairwise_index.get((left, right))
    if not obs:
        return None
    stored_left = str(obs.get("left_source") or "")
    flip = stored_left == right
    want_l = list(left_keys)
    want_r = list(right_keys)
    for item in obs.get("composite_key_observations") or []:
        if not isinstance(item, dict):
            continue
        cl = [str(x) for x in (item.get("left_columns") or [])]
        cr = [str(x) for x in (item.get("right_columns") or [])]
        if flip:
            cl, cr = cr, cl
        # Order-independent match (Planner key order may differ from observation)
        if sorted(cl) == sorted(want_l) and sorted(cr) == sorted(want_r) and len(cl) == len(want_l):
            return item
    return None


def _dtypes_compatible(a: str, b: str) -> bool:
    if a == b:
        return True
    if {a, b} <= {"numeric", "string"}:
        return True  # coded ids may normalize
    return False


def _validate_step(
    step: IntegrationStep,
    inputs: list[_DatasetMeta],
    *,
    pairwise_index: dict[tuple[str, str], dict[str, Any]],
    relationship_index: dict[tuple[str, str], dict[str, Any]],
    err,
    warn,
    info,
    lineage: list[dict[str, Any]],
) -> None:
    op = step.op
    if op == "rename_columns":
        _v_rename(step, inputs[0], err=err, lineage=lineage)
    elif op == "filter_rows":
        _v_filter(step, inputs[0], err=err, warn=warn, lineage=lineage)
    elif op == "union_rows":
        _v_union(step, inputs, err=err, warn=warn, info=info, lineage=lineage)
    elif op == "join":
        _v_join(
            step,
            inputs,
            pairwise_index=pairwise_index,
            relationship_index=relationship_index,
            err=err,
            warn=warn,
            info=info,
            lineage=lineage,
        )
    elif op == "aggregate":
        _v_aggregate(step, inputs[0], err=err, warn=warn, lineage=lineage)
    elif op == "select_columns":
        _v_select(step, inputs[0], err=err, lineage=lineage)


def _v_rename(step: IntegrationStep, src: _DatasetMeta, *, err, lineage) -> None:
    mapping = step.params.get("mapping") or {}
    used: list[str] = []
    targets: list[str] = []
    for src_col, dst_col in mapping.items():
        s, d = str(src_col), str(dst_col)
        used.append(s)
        if not d.strip():
            err("empty_rename_target", "Rename produced an empty column name", step_id=step.id)
        if s not in src.columns:
            err(
                "nonexistent_column",
                f"Rename source column {s!r} not found in {src.name}",
                step_id=step.id,
                column=s,
                dataset=src.name,
            )
        targets.append(d)
    # collision: two sources → same target, or target equals untouched column
    from collections import Counter

    for t, n in Counter(targets).items():
        if n > 1:
            err(
                "rename_target_collision",
                f"Multiple columns rename to {t!r}",
                step_id=step.id,
                target=t,
            )
    remaining = {c for c in src.columns if c not in mapping}
    for t in targets:
        if t in remaining:
            err(
                "rename_target_collision",
                f"Rename target {t!r} collides with an existing column",
                step_id=step.id,
                target=t,
            )
    lineage.append(
        {
            "step_id": step.id,
            "op": step.op,
            "inputs": list(step.inputs),
            "output": step.output,
            "columns_used": [f"{src.name}.{c}" for c in used],
        }
    )


def _v_filter(step: IntegrationStep, src: _DatasetMeta, *, err, warn, lineage) -> None:
    cols_used: list[str] = []
    for i, cond in enumerate(step.params.get("conditions") or []):
        if not isinstance(cond, dict):
            err("invalid_filter_condition", f"conditions[{i}] must be object", step_id=step.id)
            continue
        col = str(cond.get("column") or "")
        op = str(cond.get("operator") or "")
        cols_used.append(col)
        if col not in src.columns:
            err(
                "nonexistent_column",
                f"Filter column {col!r} not found in {src.name}",
                step_id=step.id,
                column=col,
            )
            continue
        if op not in FILTER_OPERATORS:
            err(
                "unsupported_filter_operator",
                f"Unsupported filter operator {op!r}",
                step_id=step.id,
                operator=op,
            )
        meta = src.columns[col]
        right_col = cond.get("right_column")
        if right_col is not None and str(right_col).strip():
            rc = str(right_col).strip()
            cols_used.append(rc)
            if rc not in src.columns:
                err(
                    "nonexistent_column",
                    f"Filter right_column {rc!r} not found in {src.name}",
                    step_id=step.id,
                    column=rc,
                )
            # Explicit column-vs-column — do not treat value as a column name.
            continue
        val = cond.get("value")
        if op in {"gt", "gte", "lt", "lte"} and meta.dtype_family == "string":
            # comparing string with inequality may be invalid
            if not isinstance(val, (int, float)) and meta.dtype_family != "numeric":
                warn(
                    "filter_dtype_mismatch",
                    f"Inequality filter on non-numeric column {col!r}",
                    step_id=step.id,
                    column=col,
                    dtype_family=meta.dtype_family,
                )
        if op in {"gt", "gte", "lt", "lte"} and meta.dtype_family == "numeric":
            if not isinstance(val, (int, float)) and not (
                isinstance(val, str) and val.replace(".", "", 1).isdigit()
            ):
                warn(
                    "filter_value_not_numeric",
                    f"Numeric comparison value looks non-numeric for {col!r}",
                    step_id=step.id,
                    value=val,
                )
    lineage.append(
        {
            "step_id": step.id,
            "op": step.op,
            "inputs": list(step.inputs),
            "output": step.output,
            "columns_used": [f"{src.name}.{c}" for c in cols_used],
        }
    )


def _v_union(
    step: IntegrationStep,
    inputs: list[_DatasetMeta],
    *,
    err,
    warn,
    info,
    lineage,
) -> None:
    if len(inputs) < 2:
        err("union_needs_two_inputs", "union_rows requires ≥2 inputs", step_id=step.id)
        return
    policy = str(step.params.get("column_policy") or "aligned")
    base_cols = set(inputs[0].columns)
    for other in inputs[1:]:
        other_cols = set(other.columns)
        shared = base_cols & other_cols
        only_left = base_cols - other_cols
        only_right = other_cols - base_cols
        denom = max(len(base_cols), len(other_cols), 1)
        overlap = len(shared) / denom
        info(
            "union_schema_overlap",
            f"Union schema overlap between {inputs[0].name} and {other.name}",
            step_id=step.id,
            shared=sorted(shared)[:20],
            only_left=sorted(only_left)[:20],
            only_right=sorted(only_right)[:20],
            overlap_ratio=round(overlap, 4),
            column_policy=policy,
        )
        if policy == "aligned" and (only_left or only_right):
            if overlap < UNION_OVERLAP_STRICT or (
                only_left and only_right and overlap <= UNION_OVERLAP_STRICT
            ):
                err(
                    "union_incompatible_schema",
                    "union_rows with aligned policy but schemas are largely incompatible",
                    step_id=step.id,
                    overlap_ratio=round(overlap, 4),
                    only_left=sorted(only_left)[:12],
                    only_right=sorted(only_right)[:12],
                )
            else:
                warn(
                    "union_partial_schema",
                    "union_rows aligned policy with partial column mismatch",
                    step_id=step.id,
                    only_left=sorted(only_left)[:12],
                    only_right=sorted(only_right)[:12],
                )
        # dtype checks on shared
        for col in shared:
            a = inputs[0].columns[col].dtype_family
            b = other.columns[col].dtype_family
            if not _dtypes_compatible(a, b):
                err(
                    "union_dtype_incompatible",
                    f"Column {col!r} dtype families incompatible for union ({a} vs {b})",
                    step_id=step.id,
                    column=col,
                    left_dtype=a,
                    right_dtype=b,
                )
            elif a != b:
                warn(
                    "union_dtype_coercion",
                    f"Column {col!r} may need dtype coercion ({a} vs {b})",
                    step_id=step.id,
                    column=col,
                )
    lineage.append(
        {
            "step_id": step.id,
            "op": step.op,
            "inputs": list(step.inputs),
            "output": step.output,
            "columns_used": sorted(
                {f"{ds.name}.{c}" for ds in inputs for c in ds.columns}
            )[:40],
        }
    )


def _v_join(
    step: IntegrationStep,
    inputs: list[_DatasetMeta],
    *,
    pairwise_index,
    relationship_index,
    err,
    warn,
    info,
    lineage,
) -> None:
    if len(inputs) != 2:
        err("join_needs_two_inputs", "join requires exactly 2 inputs", step_id=step.id)
        return
    left, right = inputs[0], inputs[1]
    left_keys = [str(x) for x in (step.params.get("left_keys") or [])]
    right_keys = [str(x) for x in (step.params.get("right_keys") or [])]
    how = str(step.params.get("how") or "inner").lower()
    if how not in JOIN_HOW:
        err("invalid_join_how", f"Unsupported join how={how!r}", step_id=step.id)
    if not left_keys or not right_keys:
        err("missing_join_keys", "join requires left_keys and right_keys", step_id=step.id)
        return
    if len(left_keys) != len(right_keys):
        err("join_key_length_mismatch", "left_keys/right_keys length mismatch", step_id=step.id)

    for k in left_keys:
        if k not in left.columns:
            err(
                "nonexistent_column",
                f"Left join key {k!r} not in {left.name}",
                step_id=step.id,
                column=k,
                side="left",
            )
    for k in right_keys:
        if k not in right.columns:
            err(
                "nonexistent_column",
                f"Right join key {k!r} not in {right.name}",
                step_id=step.id,
                column=k,
                side="right",
            )

    # Relationship consistency (label is evidence, not op force — except unsafe forced joins)
    rel = relationship_index.get((left.name, right.name))
    if rel:
        label = str(rel.get("relationship") or "")
        if label == "unrelated":
            err(
                "join_against_unrelated",
                "Plan joins sources labeled unrelated in CrossFileUnderstanding",
                step_id=step.id,
                relationship=label,
                left=left.name,
                right=right.name,
            )
        elif label in {"insufficient_evidence", "ambiguous"}:
            err(
                "ambiguous_key_selection"
                if label == "ambiguous"
                else "insufficient_evidence_forced_join",
                "Plan forces a join despite ambiguous/insufficient relationship evidence",
                step_id=step.id,
                relationship=label,
                ambiguities=list(rel.get("ambiguities") or []),
                key_candidates=list(rel.get("key_candidates") or [])[:6],
            )
        elif label in {
            "join_candidate",
            "master_detail_candidate",
            "lookup_candidate",
            "partial_overlap",
            "same_schema",
            "compatible_schema",
        }:
            info(
                "relationship_evidence",
                f"Relationship evidence for join: {label}",
                step_id=step.id,
                relationship=label,
            )

    # Observational singleton-key ambiguity (independent of LLM label).
    # Composite joins (len>=2) are NOT treated as ambiguous singletons.
    if len(left_keys) == 1 and len(right_keys) == 1:
        amb = _key_ambiguity_from_pairwise(pairwise_index, left.name, right.name)
        if amb.get("near_tied") and len(amb.get("tied_pairs") or []) >= 2:
            planned = (left_keys[0], right_keys[0])
            tied = {
                (str(t.get("left_column")), str(t.get("right_column")))
                for t in (amb.get("tied_pairs") or [])
            }
            # Orientation may flip in stored pairwise
            tied |= {(b, a) for a, b in list(tied)}
            other_tied = [t for t in tied if t != planned and (t[1], t[0]) != planned]
            if planned in tied or (planned[1], planned[0]) in tied:
                if other_tied:
                    err(
                        "ambiguous_key_selection",
                        "Multiple singleton key candidates have near-tied observational "
                        "evidence; plan selects one without resolving ambiguity. "
                        "Validator will not choose a key.",
                        step_id=step.id,
                        planned_left_key=left_keys[0],
                        planned_right_key=right_keys[0],
                        evidence_gap=amb.get("evidence_gap"),
                        tied_pairs=list(amb.get("tied_pairs") or [])[:6],
                    )

    # Per-key checks; composite uses composite uniqueness when available
    card = "unknown"
    overlap = None
    left_u = right_u = None
    left_null = right_null = None
    is_composite = len(left_keys) >= 2

    if is_composite:
        comp = _composite_stats(
            pairwise_index, left.name, right.name, left_keys, right_keys
        )
        if comp:
            left_u = float(comp.get("left_uniqueness") or 0.0)
            right_u = float(comp.get("right_uniqueness") or 0.0)
            card = str(comp.get("cardinality_evidence") or "unknown")
            info(
                "composite_key_stats",
                "Observed composite key uniqueness",
                step_id=step.id,
                left_keys=left_keys,
                right_keys=right_keys,
                left_uniqueness=left_u,
                right_uniqueness=right_u,
                cardinality_evidence=card,
            )
        else:
            # Do not infer many_to_many from per-column uniqueness alone for composites.
            card = "unknown"
            info(
                "composite_key_no_observation",
                "Composite join without composite uniqueness observation; "
                "skipping per-column many_to_many inference",
                step_id=step.id,
            )

    for lk, rk in zip(left_keys, right_keys):
        if lk not in left.columns or rk not in right.columns:
            continue
        lc, rc = left.columns[lk], right.columns[rk]
        if not is_composite:
            left_u, right_u = lc.uniqueness_ratio, rc.uniqueness_ratio
        left_null, right_null = lc.null_ratio, rc.null_ratio
        if not _dtypes_compatible(lc.dtype_family, rc.dtype_family):
            err(
                "incompatible_key_dtype",
                f"Join key dtypes incompatible: {lk}({lc.dtype_family}) vs {rk}({rc.dtype_family})",
                step_id=step.id,
                left_key=lk,
                right_key=rk,
            )
        elif lc.dtype_family != rc.dtype_family:
            warn(
                "key_dtype_coercion",
                f"Join keys may need normalization: {lc.dtype_family} vs {rc.dtype_family}",
                step_id=step.id,
                left_key=lk,
                right_key=rk,
            )
        if left_null >= NULL_KEY_STRONG or right_null >= NULL_KEY_STRONG:
            warn(
                "high_null_join_key",
                "Join key has high null ratio",
                step_id=step.id,
                left_null_ratio=left_null,
                right_null_ratio=right_null,
            )
        elif left_null >= NULL_KEY_WARNING or right_null >= NULL_KEY_WARNING:
            warn(
                "null_join_key",
                "Join key has elevated null ratio",
                step_id=step.id,
                left_null_ratio=left_null,
                right_null_ratio=right_null,
            )

        if is_composite:
            continue

        stats = _pair_stats(pairwise_index, left.name, right.name, lk, rk)
        if stats:
            card = str(stats.get("cardinality_evidence") or "unknown")
            overlap = stats.get("value_overlap_ratio")
            info(
                "join_key_pair_stats",
                "Observed pair stats for selected join keys",
                step_id=step.id,
                left_key=lk,
                right_key=rk,
                cardinality_evidence=card,
                value_overlap_ratio=overlap,
                left_uniqueness=stats.get("left_uniqueness"),
                right_uniqueness=stats.get("right_uniqueness"),
            )
            if overlap is not None and float(overlap) < LOW_MATCH_WARNING and how == "inner":
                warn(
                    "low_key_overlap",
                    "Inner join with low key value overlap may drop most rows",
                    step_id=step.id,
                    value_overlap_ratio=overlap,
                    how=how,
                )
        else:
            if left_u is not None and right_u is not None:
                if left_u >= 0.98 and right_u >= 0.98:
                    card = "one_to_one"
                elif left_u >= 0.98 and right_u < 0.98:
                    card = "one_to_many"
                elif right_u >= 0.98 and left_u < 0.98:
                    card = "many_to_one"
                elif left_u < 0.95 and right_u < 0.95:
                    card = "many_to_many"
                else:
                    card = "unknown"

    info("join_cardinality", f"Cardinality evidence: {card}", step_id=step.id, cardinality=card)

    if card == "many_to_many":
        err(
            "many_to_many_join_risk",
            "Selected join keys appear non-unique on both sides (many-to-many) "
            "and may multiply rows. Validator will not pick another key.",
            step_id=step.id,
            cardinality=card,
            left_uniqueness=left_u,
            right_uniqueness=right_u,
        )

    # Amplification estimate (no pandas join)
    est, amp = _estimate_amplification(
        left_rows=left.row_count,
        right_rows=right.row_count,
        left_uniqueness=left_u,
        right_uniqueness=right_u,
        overlap_ratio=float(overlap) if overlap is not None else None,
        cardinality=card,
    )
    if est is not None and amp is not None:
        info(
            "amplification_estimate",
            "Estimated join row amplification (approx, not executed)",
            step_id=step.id,
            estimated_join_rows=est,
            amplification_ratio=round(amp, 4),
        )
        if amp >= AMP_ERROR_RATIO:
            err(
                "extreme_row_amplification",
                "Estimated join amplification is extremely high",
                step_id=step.id,
                estimated_join_rows=est,
                amplification_ratio=round(amp, 4),
                threshold=AMP_ERROR_RATIO,
            )
        elif amp >= AMP_WARNING_RATIO:
            warn(
                "mild_row_amplification",
                "Estimated join amplification is elevated",
                step_id=step.id,
                estimated_join_rows=est,
                amplification_ratio=round(amp, 4),
                threshold=AMP_WARNING_RATIO,
            )

    lineage.append(
        {
            "step_id": step.id,
            "op": step.op,
            "inputs": list(step.inputs),
            "output": step.output,
            "columns_used": [
                *[f"{left.name}.{k}" for k in left_keys],
                *[f"{right.name}.{k}" for k in right_keys],
            ],
            "join_how": how,
            "cardinality_evidence": card,
        }
    )


def _estimate_amplification(
    *,
    left_rows: int | None,
    right_rows: int | None,
    left_uniqueness: float | None,
    right_uniqueness: float | None,
    overlap_ratio: float | None,
    cardinality: str,
) -> tuple[float | None, float | None]:
    if not left_rows or not right_rows:
        return None, None
    lu = max(float(left_uniqueness or 0.0), 1e-6)
    ru = max(float(right_uniqueness or 0.0), 1e-6)
    # avg multiplicity ≈ 1/uniqueness
    left_mult = 1.0 / lu
    right_mult = 1.0 / ru
    ov = float(overlap_ratio) if overlap_ratio is not None else 0.5
    # approx overlapping distinct keys
    left_distinct = max(left_rows * lu, 1.0)
    right_distinct = max(right_rows * ru, 1.0)
    overlap_keys = ov * min(left_distinct, right_distinct)
    if cardinality == "one_to_one":
        est = overlap_keys
    elif cardinality == "one_to_many":
        est = overlap_keys * right_mult
    elif cardinality == "many_to_one":
        est = overlap_keys * left_mult
    else:
        est = overlap_keys * left_mult * right_mult
    base = float(max(left_rows, right_rows))
    amp = float(est / base) if base else None
    return float(est), amp


def _v_aggregate(step: IntegrationStep, src: _DatasetMeta, *, err, warn, lineage) -> None:
    group_by = [str(x) for x in (step.params.get("group_by") or [])]
    metrics = step.params.get("metrics") or []
    for g in group_by:
        if g not in src.columns:
            err(
                "nonexistent_column",
                f"aggregate group_by column {g!r} missing in {src.name}",
                step_id=step.id,
                column=g,
            )
    aliases: list[str] = []
    cols_used = list(group_by)
    for i, m in enumerate(metrics):
        if not isinstance(m, dict):
            err("invalid_metric", f"metrics[{i}] must be object", step_id=step.id)
            continue
        col = str(m.get("column") or "")
        fn = str(m.get("function") or m.get("fn") or "").lower()
        from core.integrate.integration_contracts import resolve_aggregate_alias

        alias = resolve_aggregate_alias(m if isinstance(m, dict) else {})
        cols_used.append(col)
        aliases.append(alias)
        if col not in src.columns:
            err(
                "nonexistent_column",
                f"aggregate metric column {col!r} missing in {src.name}",
                step_id=step.id,
                column=col,
            )
            continue
        if fn not in AGGREGATE_FUNCTIONS:
            err(
                "unsupported_aggregation",
                f"Unsupported aggregation function {fn!r}",
                step_id=step.id,
                function=fn,
            )
        dtype = src.columns[col].dtype_family
        if fn in {"sum", "mean", "median", "min", "max"} and dtype == "string":
            err(
                "aggregate_non_numeric",
                f"Cannot apply {fn} to string column {col!r}",
                step_id=step.id,
                column=col,
                function=fn,
                dtype_family=dtype,
            )
        elif fn in {"sum", "mean", "median"} and dtype not in {"numeric", "other"}:
            if dtype != "numeric":
                warn(
                    "aggregate_dtype_uncertain",
                    f"Aggregation {fn} on {dtype} column {col!r} may be invalid",
                    step_id=step.id,
                    column=col,
                )
    from collections import Counter

    for a, n in Counter(aliases).items():
        if n > 1:
            err(
                "aggregate_alias_collision",
                f"Duplicate aggregate output alias {a!r}",
                step_id=step.id,
                alias=a,
            )

    if src.may_contain_summary_rows:
        warn(
            "possible_subtotal_double_count",
            "Aggregation input may contain subtotal/total-like rows; "
            "re-summing may double count. Validator will not auto-filter detail rows.",
            step_id=step.id,
            dataset=src.name,
        )

    lineage.append(
        {
            "step_id": step.id,
            "op": step.op,
            "inputs": list(step.inputs),
            "output": step.output,
            "columns_used": [f"{src.name}.{c}" for c in cols_used],
            "metrics": [
                {
                    "column": m.get("column"),
                    "function": m.get("function") or m.get("fn"),
                    "alias": m.get("alias"),
                }
                for m in metrics
                if isinstance(m, dict)
            ],
        }
    )


def _v_select(step: IntegrationStep, src: _DatasetMeta, *, err, lineage) -> None:
    columns = [str(c) for c in (step.params.get("columns") or [])]
    if not columns:
        err("empty_select", "select_columns columns[] is empty", step_id=step.id)
    seen: set[str] = set()
    for c in columns:
        if c in seen:
            err("duplicate_select_column", f"Duplicate column in select: {c}", step_id=step.id)
        seen.add(c)
        if c not in src.columns:
            err(
                "nonexistent_column",
                f"select_columns column {c!r} missing in {src.name}",
                step_id=step.id,
                column=c,
            )
    lineage.append(
        {
            "step_id": step.id,
            "op": step.op,
            "inputs": list(step.inputs),
            "output": step.output,
            "columns_used": [f"{src.name}.{c}" for c in columns],
        }
    )


def _simulate_output(step: IntegrationStep, inputs: list[_DatasetMeta]) -> _DatasetMeta | None:
    """Symbolic schema for downstream dependency checks (not execution)."""
    if not inputs:
        return None
    if step.op == "rename_columns":
        src = inputs[0]
        mapping = {str(k): str(v) for k, v in (step.params.get("mapping") or {}).items()}
        cols: dict[str, _ColumnMeta] = {}
        for name, meta in src.columns.items():
            new_name = mapping.get(name, name)
            cols[new_name] = _ColumnMeta(
                name=new_name,
                dtype_family=meta.dtype_family,
                null_ratio=meta.null_ratio,
                uniqueness_ratio=meta.uniqueness_ratio,
                distinct_count=meta.distinct_count,
            )
        return _DatasetMeta(
            name=step.output,
            kind="intermediate",
            row_count=src.row_count,
            columns=cols,
            may_contain_summary_rows=src.may_contain_summary_rows,
        )
    if step.op in {"filter_rows"}:
        src = inputs[0]
        return _DatasetMeta(
            name=step.output,
            kind="intermediate",
            row_count=src.row_count,
            columns=dict(src.columns),
            may_contain_summary_rows=src.may_contain_summary_rows,
        )
    if step.op == "select_columns":
        src = inputs[0]
        cols = {
            c: src.columns[c]
            for c in (step.params.get("columns") or [])
            if str(c) in src.columns
        }
        return _DatasetMeta(
            name=step.output,
            kind="intermediate",
            row_count=src.row_count,
            columns={str(k): v for k, v in cols.items()},
            may_contain_summary_rows=src.may_contain_summary_rows,
        )
    if step.op == "union_rows":
        cols: dict[str, _ColumnMeta] = {}
        rows = 0
        summary = False
        for ds in inputs:
            summary = summary or ds.may_contain_summary_rows
            if ds.row_count:
                rows += ds.row_count
            for name, meta in ds.columns.items():
                cols.setdefault(name, meta)
        return _DatasetMeta(
            name=step.output,
            kind="intermediate",
            row_count=rows or None,
            columns=cols,
            may_contain_summary_rows=summary,
        )
    if step.op == "join":
        from core.integrate.integration_contracts import join_output_column_names

        left, right = inputs[0], inputs[1]
        left_keys = [str(k) for k in (step.params.get("left_keys") or [])]
        right_keys = [str(k) for k in (step.params.get("right_keys") or [])]
        out_names = join_output_column_names(
            left.columns.keys(),
            right.columns.keys(),
            left_keys=left_keys,
            right_keys=right_keys,
        )
        # Map output names → column meta (prefer left, then right; suffix strip for collisions)
        cols: dict[str, _ColumnMeta] = {}
        for name in out_names:
            base = name
            if name.endswith("_left") and name[: -len("_left")] in left.columns:
                base = name[: -len("_left")]
                meta = left.columns[base]
            elif name.endswith("_right") and name[: -len("_right")] in right.columns:
                base = name[: -len("_right")]
                meta = right.columns[base]
            elif name in left.columns:
                meta = left.columns[name]
            elif name in right.columns:
                meta = right.columns[name]
            else:
                meta = _ColumnMeta(name=name, dtype_family="unknown")
            cols[name] = _ColumnMeta(
                name=name,
                dtype_family=meta.dtype_family,
                null_ratio=meta.null_ratio,
                uniqueness_ratio=meta.uniqueness_ratio,
                distinct_count=meta.distinct_count,
            )
        return _DatasetMeta(
            name=step.output,
            kind="intermediate",
            row_count=None,
            columns=cols,
            may_contain_summary_rows=left.may_contain_summary_rows
            or right.may_contain_summary_rows,
        )
    if step.op == "aggregate":
        src = inputs[0]
        cols: dict[str, _ColumnMeta] = {}
        for g in step.params.get("group_by") or []:
            g = str(g)
            if g in src.columns:
                cols[g] = src.columns[g]
        for m in step.params.get("metrics") or []:
            if not isinstance(m, dict):
                continue
            from core.integrate.integration_contracts import resolve_aggregate_alias

            alias = resolve_aggregate_alias(m)
            if alias:
                cols[alias] = _ColumnMeta(name=alias, dtype_family="numeric")
        return _DatasetMeta(
            name=step.output,
            kind="intermediate",
            row_count=None,
            columns=cols,
            may_contain_summary_rows=False,
        )
    return None
