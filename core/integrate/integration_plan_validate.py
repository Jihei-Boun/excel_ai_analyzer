"""Phase 16: IntegrationPlan Validator (no execution, no semantic repair).

validate_integration_plan(understanding, plan) → IntegrationValidationResult
Does not mutate plan. Does not choose keys/ops/metrics.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from core.integrate.integration_plan_types import (
    AGGREGATE_FUNCTIONS,
    FILTER_OPERATORS,
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

    # Per-key checks (use first key pair for cardinality/amplification; composite uses all)
    card = "unknown"
    overlap = None
    left_u = right_u = None
    left_null = right_null = None
    for lk, rk in zip(left_keys, right_keys):
        if lk not in left.columns or rk not in right.columns:
            continue
        lc, rc = left.columns[lk], right.columns[rk]
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
            # Derive cardinality evidence from uniqueness alone (observation)
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
        alias = str(m.get("alias") or col)
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
        left, right = inputs[0], inputs[1]
        cols = dict(left.columns)
        for name, meta in right.columns.items():
            if name in cols:
                cols[f"{right.name}__{name}"] = meta  # collision placeholder name
            else:
                cols[name] = meta
        # row count unknown precisely
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
            alias = str(m.get("alias") or m.get("column") or "")
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
