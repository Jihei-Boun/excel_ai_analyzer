"""Deterministic schema materialization evidence for Semantic Verifier (Phase 39D/39F/39H).

Python owns ONLY structural column survival / alias / join-suffix naming /
origin tracing / observable expression-lineage fingerprints.
It must NOT decide comparison intent, side semantics, or pass/fail.
"""

from __future__ import annotations

import json
from typing import Any

from core.integrate.integration_contracts import (
    join_output_column_names,
    resolve_aggregate_alias,
)


def _cols_of(schemas: dict[str, list[str]], name: str) -> list[str]:
    return list(schemas.get(name) or [])


def _record_missing(
    missing: list[dict[str, Any]],
    *,
    step_id: str,
    op: str,
    column: str,
    available: list[str],
    context: str,
) -> None:
    missing.append(
        {
            "step_id": step_id,
            "op": op,
            "column": column,
            "available_columns": list(available),
            "context": context,
        }
    )


def _clone_origins(origins: dict[str, list[dict[str, str]]]) -> dict[str, list[dict[str, str]]]:
    return {k: [dict(x) for x in v] for k, v in origins.items()}


def _origins_of(
    table_origins: dict[str, dict[str, list[dict[str, str]]]], name: str
) -> dict[str, list[dict[str, str]]]:
    return _clone_origins(table_origins.get(name) or {})


def _canon_origins(origins: list[dict[str, str]] | None) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for o in origins or []:
        if isinstance(o, dict) and o.get("source") and o.get("column"):
            item = {"source": str(o["source"]), "column": str(o["column"])}
            key = (item["source"], item["column"])
            if key in seen:
                continue
            seen.add(key)
            rows.append(item)
    rows.sort(key=lambda x: (x["source"], x["column"]))
    return rows


def _canon_filters(filters: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for f in filters or []:
        if not isinstance(f, dict):
            continue
        out.append(
            {
                "column": str(f.get("column") or f.get("left_column") or ""),
                "op": str(f.get("op") or f.get("operator") or ""),
                "value": f.get("value"),
                "right_column": str(f.get("right_column") or "") or None,
            }
        )
    out.sort(
        key=lambda x: (
            x["column"],
            x["op"],
            str(x.get("value")),
            str(x.get("right_column")),
        )
    )
    return out


def _population(
    *,
    kind: str,
    inputs: list[str] | None = None,
    filters: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "inputs": sorted(str(x) for x in (inputs or [])),
        "filters": _canon_filters(filters),
    }


def _normalize_expr(expr: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(expr, dict):
        return None
    agg = expr.get("aggregate")
    if isinstance(agg, dict):
        agg_n: dict[str, str] | None = {
            "function": str(agg.get("function") or ""),
            "input_column": str(agg.get("input_column") or ""),
        }
    else:
        agg_n = None
    pop = expr.get("row_population") if isinstance(expr.get("row_population"), dict) else {}
    return {
        "op_family": str(expr.get("op_family") or "unknown"),
        "aggregate": agg_n,
        "group_by": [str(x) for x in (expr.get("group_by") or [])],
        "row_population": _population(
            kind=str(pop.get("kind") or "unknown"),
            inputs=list(pop.get("inputs") or []),
            filters=list(pop.get("filters") or []),
        ),
        "source_origins": _canon_origins(list(expr.get("source_origins") or [])),
    }


def _expr_key(expr: dict[str, Any] | None) -> str:
    n = _normalize_expr(expr)
    return json.dumps(n, sort_keys=True, ensure_ascii=False, default=str)


def _copy_expr(expr: dict[str, Any] | None) -> dict[str, Any] | None:
    return _normalize_expr(expr)


def _source_expr(file_name: str, column: str) -> dict[str, Any]:
    return {
        "op_family": "source_column",
        "aggregate": None,
        "group_by": [],
        "row_population": _population(kind="source", inputs=[file_name], filters=[]),
        "source_origins": [{"source": file_name, "column": column}],
    }


def _exprs_of(
    table_exprs: dict[str, dict[str, dict[str, Any]]], name: str
) -> dict[str, dict[str, Any]]:
    src = table_exprs.get(name) or {}
    return {k: _copy_expr(v) for k, v in src.items() if _copy_expr(v) is not None}  # type: ignore[misc]


def _pop_of(
    table_populations: dict[str, dict[str, Any]], name: str
) -> dict[str, Any]:
    p = table_populations.get(name)
    if isinstance(p, dict):
        return _population(
            kind=str(p.get("kind") or "unknown"),
            inputs=list(p.get("inputs") or []),
            filters=list(p.get("filters") or []),
        )
    return _population(kind="unknown", inputs=[name], filters=[])


def _join_output_with_origins(
    left_cols: list[str],
    right_cols: list[str],
    *,
    left_keys: list[str],
    right_keys: list[str],
    left_origins: dict[str, list[dict[str, str]]],
    right_origins: dict[str, list[dict[str, str]]],
) -> tuple[list[str], dict[str, list[dict[str, str]]]]:
    """Map join output columns to deterministic origins (structural only)."""
    out_cols = join_output_column_names(
        left_cols, right_cols, left_keys=left_keys, right_keys=right_keys
    )
    left_set = set(left_cols)
    right_set = set(right_cols)
    lk = set(left_keys)
    rk = set(right_keys)
    out_origins: dict[str, list[dict[str, str]]] = {}
    for c in out_cols:
        if c.endswith("_left") and c[: -len("_left")] in left_set:
            base = c[: -len("_left")]
            out_origins[c] = list(left_origins.get(base) or [{"source": "left", "column": base}])
        elif c.endswith("_right") and c[: -len("_right")] in right_set:
            base = c[: -len("_right")]
            out_origins[c] = list(right_origins.get(base) or [{"source": "right", "column": base}])
        elif c in left_set:
            out_origins[c] = list(left_origins.get(c) or [{"source": "left", "column": c}])
            if c in rk or (c in right_set and c in lk):
                right_o = right_origins.get(c) or []
                merged = list(out_origins[c])
                for o in right_o:
                    if o not in merged:
                        merged.append(dict(o))
                out_origins[c] = merged
        elif c in right_set:
            out_origins[c] = list(right_origins.get(c) or [{"source": "right", "column": c}])
        else:
            out_origins[c] = []
    return out_cols, out_origins


def _join_output_exprs(
    out_cols: list[str],
    left_cols: list[str],
    right_cols: list[str],
    *,
    left_keys: list[str],
    right_keys: list[str],
    left_exprs: dict[str, dict[str, Any]],
    right_exprs: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    left_set = set(left_cols)
    right_set = set(right_cols)
    out: dict[str, dict[str, Any]] = {}
    for c in out_cols:
        if c.endswith("_left") and c[: -len("_left")] in left_set:
            base = c[: -len("_left")]
            e = _copy_expr(left_exprs.get(base))
            if e:
                out[c] = e
        elif c.endswith("_right") and c[: -len("_right")] in right_set:
            base = c[: -len("_right")]
            e = _copy_expr(right_exprs.get(base))
            if e:
                out[c] = e
        elif c in left_set and c in left_exprs:
            e = _copy_expr(left_exprs.get(c))
            if e:
                out[c] = e
        elif c in right_set and c in right_exprs:
            e = _copy_expr(right_exprs.get(c))
            if e:
                out[c] = e
    return out


def _simulate_step(
    step: dict[str, Any],
    schemas: dict[str, list[str]],
    table_origins: dict[str, dict[str, list[dict[str, str]]]],
    table_exprs: dict[str, dict[str, dict[str, Any]]],
    table_populations: dict[str, dict[str, Any]],
    missing: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> tuple[
    list[str] | None,
    dict[str, list[dict[str, str]]] | None,
    dict[str, dict[str, Any]] | None,
    dict[str, Any] | None,
]:
    op = str(step.get("op") or "")
    step_id = str(step.get("id") or "")
    inputs = [str(x) for x in (step.get("inputs") or [])]
    params = step.get("params") or {}
    if not isinstance(params, dict):
        params = {}

    if op == "rename_columns":
        if not inputs:
            return None, None, None, None
        src = _cols_of(schemas, inputs[0])
        src_o = _origins_of(table_origins, inputs[0])
        src_e = _exprs_of(table_exprs, inputs[0])
        mapping = {str(k): str(v) for k, v in (params.get("mapping") or {}).items()}
        for src_col in mapping:
            if src_col not in src:
                _record_missing(
                    missing,
                    step_id=step_id,
                    op=op,
                    column=src_col,
                    available=src,
                    context="rename_source",
                )
        out = [mapping.get(c, c) for c in src]
        out_o: dict[str, list[dict[str, str]]] = {}
        out_e: dict[str, dict[str, Any]] = {}
        for c in src:
            new_c = mapping.get(c, c)
            out_o[new_c] = list(src_o.get(c) or [])
            e = _copy_expr(src_e.get(c))
            if e:
                out_e[new_c] = e
        events.append(
            {
                "step_id": step_id,
                "op": op,
                "mapping": dict(mapping),
                "input": inputs[0],
                "note": "Rename preserves column identity under a new name.",
            }
        )
        return out, out_o, out_e, _pop_of(table_populations, inputs[0])

    if op == "filter_rows":
        if not inputs:
            return None, None, None, None
        src = _cols_of(schemas, inputs[0])
        src_o = _origins_of(table_origins, inputs[0])
        src_e = _exprs_of(table_exprs, inputs[0])
        parent_pop = _pop_of(table_populations, inputs[0])
        new_filters = list(parent_pop.get("filters") or [])
        for cond in params.get("conditions") or []:
            if not isinstance(cond, dict):
                continue
            for key in ("column", "left_column", "right_column"):
                col = cond.get(key)
                if col and str(col) not in src:
                    _record_missing(
                        missing,
                        step_id=step_id,
                        op=op,
                        column=str(col),
                        available=src,
                        context=f"filter:{key}",
                    )
            new_filters.append(dict(cond))
        pop = _population(
            kind="filtered",
            inputs=list(parent_pop.get("inputs") or [inputs[0]]),
            filters=new_filters,
        )
        out_e = {}
        for c, e in src_e.items():
            ne = _copy_expr(e)
            if not ne:
                continue
            # Carry filter into column population when still source-like or prior filtered.
            if ne.get("op_family") in {"source_column", "filtered_column"} or not ne.get(
                "aggregate"
            ):
                ne["op_family"] = (
                    "filtered_column"
                    if ne.get("op_family") == "source_column"
                    else str(ne.get("op_family") or "filtered_column")
                )
                ne["row_population"] = pop
            out_e[c] = ne
        return list(src), src_o, out_e, pop

    if op == "select_columns":
        if not inputs:
            return None, None, None, None
        src = _cols_of(schemas, inputs[0])
        src_o = _origins_of(table_origins, inputs[0])
        src_e = _exprs_of(table_exprs, inputs[0])
        selected: list[str] = []
        out_o = {}
        out_e = {}
        for c in params.get("columns") or []:
            c = str(c)
            if c not in src:
                _record_missing(
                    missing,
                    step_id=step_id,
                    op=op,
                    column=c,
                    available=src,
                    context="select",
                )
            else:
                selected.append(c)
                out_o[c] = list(src_o.get(c) or [])
                e = _copy_expr(src_e.get(c))
                if e:
                    out_e[c] = e
        return selected, out_o, out_e, _pop_of(table_populations, inputs[0])

    if op == "union_rows":
        cols: list[str] = []
        seen: set[str] = set()
        out_o = {}
        out_e: dict[str, dict[str, Any]] = {}
        union_inputs: list[str] = []
        for inp in inputs:
            pop_i = _pop_of(table_populations, inp)
            for x in pop_i.get("inputs") or [inp]:
                if x not in union_inputs:
                    union_inputs.append(str(x))
            src_o = _origins_of(table_origins, inp)
            src_e = _exprs_of(table_exprs, inp)
            for c in _cols_of(schemas, inp):
                if c not in seen:
                    seen.add(c)
                    cols.append(c)
                    out_o[c] = list(src_o.get(c) or [])
                    e = _copy_expr(src_e.get(c))
                    if e:
                        # After union, row population is the combined stack.
                        e["row_population"] = _population(
                            kind="union", inputs=union_inputs, filters=[]
                        )
                        out_e[c] = e
                else:
                    for o in src_o.get(c) or []:
                        if o not in out_o[c]:
                            out_o[c].append(dict(o))
                    # Merge origins into existing expr; population stays union.
                    if c in out_e:
                        existing = out_e[c]
                        merged_origins = _canon_origins(
                            list(existing.get("source_origins") or [])
                            + list((src_e.get(c) or {}).get("source_origins") or [])
                            + list(src_o.get(c) or [])
                        )
                        existing["source_origins"] = merged_origins
                        existing["row_population"] = _population(
                            kind="union", inputs=union_inputs, filters=[]
                        )
        pop = _population(kind="union", inputs=union_inputs, filters=[])
        return cols, out_o, out_e, pop

    if op == "join":
        if len(inputs) < 2:
            return None, None, None, None
        left = _cols_of(schemas, inputs[0])
        right = _cols_of(schemas, inputs[1])
        left_o = _origins_of(table_origins, inputs[0])
        right_o = _origins_of(table_origins, inputs[1])
        left_e = _exprs_of(table_exprs, inputs[0])
        right_e = _exprs_of(table_exprs, inputs[1])
        left_keys = [
            str(k) for k in (params.get("left_keys") or params.get("left_on") or [])
        ]
        right_keys = [
            str(k) for k in (params.get("right_keys") or params.get("right_on") or [])
        ]
        for k in left_keys:
            if k not in left:
                _record_missing(
                    missing,
                    step_id=step_id,
                    op=op,
                    column=k,
                    available=left,
                    context="join_left_key",
                )
        for k in right_keys:
            if k not in right:
                _record_missing(
                    missing,
                    step_id=step_id,
                    op=op,
                    column=k,
                    available=right,
                    context="join_right_key",
                )
        out, out_o = _join_output_with_origins(
            left,
            right,
            left_keys=left_keys,
            right_keys=right_keys,
            left_origins=left_o,
            right_origins=right_o,
        )
        out_e = _join_output_exprs(
            out,
            left,
            right,
            left_keys=left_keys,
            right_keys=right_keys,
            left_exprs=left_e,
            right_exprs=right_e,
        )
        left_pop = _pop_of(table_populations, inputs[0])
        right_pop = _pop_of(table_populations, inputs[1])
        pop = _population(
            kind="join",
            inputs=sorted(
                set(list(left_pop.get("inputs") or []) + list(right_pop.get("inputs") or []))
            ),
            filters=[],
        )
        events.append(
            {
                "step_id": step_id,
                "op": op,
                "left_input": inputs[0],
                "right_input": inputs[1],
                "left_keys": left_keys,
                "right_keys": right_keys,
                "surviving_columns": list(out),
                "note": (
                    "Join may retain columns from both inputs. "
                    "Name collisions become *_left / *_right. "
                    "One row per key with multiple metric columns is side-by-side "
                    "survival, not by itself an aggregation collapse."
                ),
            }
        )
        return out, out_o, out_e, pop

    if op == "aggregate":
        if not inputs:
            return None, None, None, None
        src = _cols_of(schemas, inputs[0])
        src_o = _origins_of(table_origins, inputs[0])
        src_e = _exprs_of(table_exprs, inputs[0])
        parent_pop = _pop_of(table_populations, inputs[0])
        out = []
        out_o = {}
        out_e = {}
        group_by = [str(g) for g in (params.get("group_by") or [])]
        for g in group_by:
            if g not in src:
                _record_missing(
                    missing,
                    step_id=step_id,
                    op=op,
                    column=g,
                    available=src,
                    context="aggregate_group_by",
                )
            else:
                out.append(g)
                out_o[g] = list(src_o.get(g) or [])
                e = _copy_expr(src_e.get(g))
                if e:
                    out_e[g] = e
        for m in params.get("metrics") or []:
            if not isinstance(m, dict):
                continue
            col = str(m.get("column") or "")
            if col not in src:
                _record_missing(
                    missing,
                    step_id=step_id,
                    op=op,
                    column=col,
                    available=src,
                    context="aggregate_metric",
                )
                continue
            alias = resolve_aggregate_alias(m)
            if not alias:
                continue
            out.append(alias)
            out_o[alias] = list(src_o.get(col) or [])
            input_expr = _copy_expr(src_e.get(col)) or {}
            # Prefer input column origins; fall back to carried origins.
            origins = _canon_origins(
                list(src_o.get(col) or [])
                or list(input_expr.get("source_origins") or [])
            )
            out_e[alias] = {
                "op_family": "aggregate",
                "aggregate": {
                    "function": str(m.get("function") or ""),
                    "input_column": col,
                },
                "group_by": list(group_by),
                "row_population": parent_pop,
                "source_origins": origins,
            }
        seen_out: set[str] = set()
        uniq: list[str] = []
        for c in out:
            if c not in seen_out:
                seen_out.add(c)
                uniq.append(c)
        events.append(
            {
                "step_id": step_id,
                "op": op,
                "group_by": list(group_by),
                "produced_metrics": [
                    c for c in uniq if c not in set(group_by)
                ],
                "note": "Aggregate keeps group_by + produced metrics; other columns do not survive.",
            }
        )
        # Aggregated table population is the grouped parent population (filters retained).
        return uniq, out_o, out_e, parent_pop

    return None, None, None, None


def build_schema_lineage(
    plan: dict[str, Any] | None,
    source_schemas: dict[str, list[str]] | None,
    *,
    include_intermediates: bool = True,
) -> dict[str, Any]:
    """Return structural materialization evidence (no semantic judgments)."""
    source_schemas = source_schemas or {}
    schemas: dict[str, list[str]] = {
        str(k): [str(c) for c in (v or [])] for k, v in source_schemas.items()
    }
    table_origins: dict[str, dict[str, list[dict[str, str]]]] = {
        str(k): {str(c): [{"source": str(k), "column": str(c)}] for c in (v or [])}
        for k, v in source_schemas.items()
    }
    table_exprs: dict[str, dict[str, dict[str, Any]]] = {
        str(k): {str(c): _source_expr(str(k), str(c)) for c in (v or [])}
        for k, v in source_schemas.items()
    }
    table_populations: dict[str, dict[str, Any]] = {
        str(k): _population(kind="source", inputs=[str(k)], filters=[])
        for k in source_schemas
    }
    missing: list[dict[str, Any]] = []
    step_outputs: dict[str, list[str]] = {}
    step_origins: dict[str, dict[str, list[dict[str, str]]]] = {}
    events: list[dict[str, Any]] = []
    notes = [
        "Structural schema evidence only. Column presence ≠ semantic adequacy.",
        "Join name collisions become *_left / *_right by executor contract.",
        "Do not treat planner_claims as proof that claimed columns exist.",
        "final_column_origins traces each final column to source file+column when deterministically known.",
        "Distinct final metric columns with different source origins are independently surviving columns.",
        "Entity grain (one row per key) with multiple side-specific metric columns is not aggregation collapse by itself.",
        "final_column_evidence_signatures fingerprint observable expression + row-population lineage.",
        "equivalent_evidence_signature_groups lists final columns that share an identical deterministic signature — not a pass/fail judgment.",
        "Same source origin set alone does NOT prove identical evidence; partition/filter ancestry may distinguish sides.",
    ]

    if not isinstance(plan, dict):
        return {
            "source_schemas": schemas,
            "step_outputs": {},
            "final_schema": [],
            "final_column_origins": {},
            "final_column_evidence_signatures": {},
            "equivalent_evidence_signature_groups": [],
            "identical_evidence_signature_column_sets": [],
            "structural_events": [],
            "unresolved_column_refs": [],
            "claimed_columns_absent_from_final": [],
            "notes": notes,
        }

    for step in plan.get("steps") or []:
        if not isinstance(step, dict):
            continue
        out_name = str(step.get("output") or "")
        cols, origins, exprs, pop = _simulate_step(
            step,
            schemas,
            table_origins,
            table_exprs,
            table_populations,
            missing,
            events,
        )
        if cols is None or not out_name:
            continue
        schemas[out_name] = list(cols)
        table_origins[out_name] = origins or {}
        table_exprs[out_name] = exprs or {}
        if pop is not None:
            table_populations[out_name] = pop
        if include_intermediates:
            step_outputs[out_name] = list(cols)
            step_origins[out_name] = _clone_origins(origins or {})

    final_name = plan.get("final_output")
    final_schema = list(schemas.get(str(final_name), [])) if final_name else []
    final_column_origins = (
        _clone_origins(table_origins.get(str(final_name), {})) if final_name else {}
    )
    final_exprs_raw = table_exprs.get(str(final_name), {}) if final_name else {}
    final_column_evidence_signatures: dict[str, dict[str, Any]] = {}
    for col in final_schema:
        e = _normalize_expr(final_exprs_raw.get(col))
        if e is not None:
            final_column_evidence_signatures[col] = e

    claimed_absent: list[str] = []
    req = plan.get("final_output_requirements")
    if isinstance(req, dict) and final_schema is not None:
        final_set = set(final_schema)
        for c in req.get("required_columns") or []:
            if str(c) not in final_set:
                claimed_absent.append(str(c))
        for role in req.get("output_roles") or []:
            if not isinstance(role, dict):
                continue
            for c in role.get("columns") or []:
                if str(c) not in final_set:
                    claimed_absent.append(str(c))
        seen: set[str] = set()
        dedup: list[str] = []
        for c in claimed_absent:
            if c not in seen:
                seen.add(c)
                dedup.append(c)
        claimed_absent = dedup

    source_files_in_final = sorted(
        {
            o.get("source")
            for origins in final_column_origins.values()
            for o in origins
            if isinstance(o, dict) and o.get("source")
        }
    )

    origin_to_cols: dict[tuple[str, str], list[str]] = {}
    for col, origins in final_column_origins.items():
        if not isinstance(origins, list) or len(origins) != 1:
            continue
        o = origins[0]
        if not isinstance(o, dict):
            continue
        key = (str(o.get("source") or ""), str(o.get("column") or ""))
        if not key[0] or not key[1]:
            continue
        origin_to_cols.setdefault(key, []).append(col)
    shared_singleton_origin_groups = [
        {
            "source": src_name,
            "column": col_name,
            "final_columns": cols,
        }
        for (src_name, col_name), cols in sorted(origin_to_cols.items())
        if len(cols) >= 2
    ]
    if shared_singleton_origin_groups:
        notes.append(
            "Structural provenance: some final columns share an identical singleton "
            "source.column origin (see shared_singleton_origin_groups). Distinct names "
            "alone do not imply distinct source values."
        )

    # Deterministic equivalence of observable expression lineage (not semantic judgment).
    sig_to_cols: dict[str, list[str]] = {}
    for col, sig in final_column_evidence_signatures.items():
        sig_to_cols.setdefault(_expr_key(sig), []).append(col)
    equivalent_evidence_signature_groups = [
        {
            "evidence_signature": json.loads(k),
            "final_columns": cols,
        }
        for k, cols in sorted(sig_to_cols.items(), key=lambda kv: kv[0])
        if len(cols) >= 2
    ]
    identical_evidence_signature_column_sets = [
        list(g["final_columns"])
        for g in equivalent_evidence_signature_groups
    ]
    if equivalent_evidence_signature_groups:
        notes.append(
            "Structural provenance: some final columns share an identical "
            "evidence_signature (see equivalent_evidence_signature_groups / "
            "identical_evidence_signature_column_sets). "
            "Aliases/roles alone do not create independent evidence. "
            "Different partition/filter ancestry yields different signatures."
        )

    evidence: dict[str, Any] = {
        "source_schemas": {
            k: list(v) for k, v in schemas.items() if k in source_schemas
        },
        "final_schema": final_schema,
        "final_column_origins": final_column_origins,
        "final_column_evidence_signatures": final_column_evidence_signatures,
        "equivalent_evidence_signature_groups": equivalent_evidence_signature_groups,
        "identical_evidence_signature_column_sets": identical_evidence_signature_column_sets,
        "source_files_represented_in_final": source_files_in_final,
        "shared_singleton_origin_groups": shared_singleton_origin_groups,
        "unresolved_column_refs": missing,
        "claimed_columns_absent_from_final": claimed_absent,
        "notes": notes,
    }
    if include_intermediates:
        evidence["step_outputs"] = step_outputs
        evidence["step_column_origins"] = step_origins
        evidence["structural_events"] = events
    return evidence



def extract_source_schemas_from_understanding(
    understanding: dict[str, Any] | None,
) -> dict[str, list[str]]:
    """Best-effort column lists from cross-file understanding dict."""
    out: dict[str, list[str]] = {}
    if not isinstance(understanding, dict):
        return out
    for p in understanding.get("file_profiles") or []:
        if not isinstance(p, dict):
            continue
        sid = p.get("source_id") or p.get("name")
        if not sid:
            continue
        obs = p.get("observations") or {}
        cols: list[str] = []
        for c in obs.get("columns") or []:
            if isinstance(c, dict) and c.get("name"):
                cols.append(str(c["name"]))
            elif isinstance(c, str):
                cols.append(c)
        out[str(sid)] = cols
    return out
