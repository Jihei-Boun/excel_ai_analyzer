# Phase 37 — Multi-file Shadow Mode Infrastructure

## Executive summary

Shadow Mode infrastructure is in place: **legacy `route_multi` remains the sole user-facing response source**; the frozen Phase 35 Integration Pipeline can run in a **background ThreadPoolExecutor** with kill switch **OFF by default**, capacity drops, JSONL telemetry, and objective structural comparison only. **Recommendation: A — Shadow Infrastructure Ready** for limited real-traffic observation (Phase 38), with Shadow still **disabled** until ops enables it.

---

## Runtime audit

| Topic | Finding |
|-------|---------|
| Streamlit | Sync blocking `process_user_prompt` → `route_multi_prompt`; `st.rerun` after reply |
| Background jobs | None existed; Phase 37 adds module-level `ThreadPoolExecutor` |
| Session/data | Uploads in `data/uploads`; frames in `session_state` |
| Shadow snapshot | Deep-copied DataFrames at schedule time — no session_state refs |
| LLM | Sync HTTP to Ollama; Shadow runs on worker threads (separate from UI thread) |
| Chosen architecture | **Fire-and-forget thread pool** after legacy outcome; never join before return |

Streamlit rerun does not cancel in-flight Shadow workers (daemon pool). Jobs are disposable; capacity gate prefers drop over backlog growth.

---

## Shadow architecture

```text
route_multi (system commands) → return (no shadow)

route_multi (aggregate / integrate / PandasAI)
  → build immutable ShadowRequestSnapshot (if MULTI_SHADOW_ENABLED)
  → run legacy path → SingleRouteOutcome
  → finish_with_shadow: schedule background job → return SAME outcome

Shadow worker:
  build_cross_file_understanding
  → run_integration_pipeline_semantic_experimental (frozen)
  → JSONL telemetry + structural compare vs legacy fingerprint
```

Modules: `core/shadow/{config,snapshot,fingerprint,telemetry,runner,worker,hook}.py`  
Hook site: `core/routing/route_multi.py` (`_finish` only; reply/dataframe never replaced).

---

## Isolation guarantees

| Guarantee | Mechanism |
|-----------|-----------|
| Response | `finish_with_shadow` returns the identical `SingleRouteOutcome` object fields unchanged |
| Exception | All shadow schedule/execute errors swallowed; recorded as `shadow_infrastructure_error` |
| Latency | `ThreadPoolExecutor.submit` — no wait for Shadow before return |
| State | DataFrame `copy(deep=True)` in snapshot; no shared session_state keys |

---

## Configuration

| Env | Default | Meaning |
|-----|---------|---------|
| `MULTI_SHADOW_ENABLED` | **false** | Kill switch |
| `MULTI_SHADOW_SAMPLE_RATE` | 1.0 | Random sample when enabled (no semantic rules) |
| `MULTI_SHADOW_MAX_CONCURRENCY` | 1 | Worker threads |
| `MULTI_SHADOW_QUEUE_SIZE` | 8 | Capacity ≈ concurrency × queue |
| `MULTI_SHADOW_TIMEOUT_SEC` | 600 | Operational mark `shadow_timeout` |
| `MULTI_SHADOW_TELEMETRY_DIR` | `data/shadow_telemetry` | JSONL sink |
| `MULTI_SHADOW_STORE_PROMPT` | false | Prompt hash only unless true |
| `MULTI_SHADOW_INLINE_FOR_TESTS` | false | Test sync path |

Pipeline version tag: `phase35_semantic_escalation_v1`  
Schema version: `1`

---

## Telemetry schema (stored)

Stored: request/shadow IDs, timestamps, file_count, source_names, prompt_hash, optional prompt, legacy meta + fingerprint, shadow statuses, verifier verdict/reason/evidence, model call attribution, 32B flags, latency, plans (structured), structural comparison category, error_family.

**Not stored by default:** full Excel rows, uploaded bytes, session_state, prompt text.

---

## Legacy / Shadow comparison

Objective only:

- success/failure pairing categories
- `structurally_equal` / `structurally_different` via shape, columns, head-50 hash

**No** semantic winner, no auto golden, no fallback to shadow reply.

---

## Failure / latency / resource (dry-run)

See `benchmark_results/multi/phase37/`:

- `failure_isolation.json` — injected exception → legacy reply unchanged
- `latency_isolation.json` — 1.2s sleep shadow → legacy wait &lt; 0.5s
- `resource_protection.json` — capacity skip when saturated
- `shadow_enabled_dry_run.json` — correlation IDs + telemetry

---

## Data handling

No prior repo privacy policy. Phase 37 uses minimize-by-default JSONL under `MULTI_SHADOW_TELEMETRY_DIR`. No automated retention.

---

## Regression

- Phase 37 unit tests: PASS
- Deterministic multi benchmark: 100% / unsafe 0
- Candidate planner/verifier/validators: **unchanged**
- Default Shadow OFF → production behavior path preserved (system commands untouched; LLM paths only add no-op schedule when disabled)

---

## Architecture audit

All checklist items PASS (see `architecture_audit.json`). Forbidden changes (DSL, prompts, validators, semantic routing, winner selection, response replacement) not introduced.

---

## Recommendation

### **A — Shadow Infrastructure Ready**

Limited real-traffic Shadow evaluation (Phase 38) can proceed **after ops explicitly sets** `MULTI_SHADOW_ENABLED=true` with conservative concurrency/sampling. Phase 37 does **not** auto-activate Shadow.

**Not** production replacement ready (Phase 36 latency remains).

### Phase 38

Limited Real-Traffic Shadow Evaluation — analyze disagreement, path mix, resource impact. Still no user-response swap.

---

## Principle held

> Shadow observes production; Shadow does not participate in production.  
> A Shadow result is evidence, not an answer.
