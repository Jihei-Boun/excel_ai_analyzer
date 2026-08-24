# Phase 38 Blocker Fix — Shadow Validation Contract Mapping

## Summary

Shadow telemetry adapter in `core/shadow/runner.py` now maps Integration pipeline
results via canonical fields (`.valid` / `.success`), not legacy `.ok`.

## Timeout evidence preservation (audit only — not changed)

Current behavior (`worker._execute_job` after `run_shadow_pipeline`):

- If `elapsed > timeout_sec`, overwrites `shadow_status` and `error_family` to
  `shadow_timeout` even when `error_message` / traceback already record a pipeline
  exception.

Observation #1 effect:

- Actual failure: `AttributeError` on `.ok`
- Final labels: `shadow_timeout` (because 989s > 600s)

Recommendation (separate follow-up, not this fix):

- Keep `shadow_timeout=true` as additive flag
- Do **not** overwrite `shadow_status` / `error_family` when an exception family
  is already set
- Or store `timeout_exceeded` alongside `error_family=shadow_pipeline_exception`

## Recommendation

A — Fix validated; resume limited Shadow observation after ops re-enable
(with Shadow still default OFF until explicitly enabled).
