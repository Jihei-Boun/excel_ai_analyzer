"""Bounded Shadow worker pool — production never waits; overflow drops Shadow."""

from __future__ import annotations

import atexit
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from core.shadow.config import ShadowConfig, load_shadow_config
from core.shadow.fingerprint import (
    dataframe_fingerprint,
    outcome_category,
    structural_compare,
)
from core.shadow.runner import run_shadow_pipeline, run_shadow_sleep_only
from core.shadow.snapshot import ShadowRequestSnapshot
from core.shadow.telemetry import append_telemetry_record, new_base_record

_lock = threading.Lock()
_executor: ThreadPoolExecutor | None = None
_reserved = 0  # submitted jobs (queued or running) — capacity gate
_config_cache: ShadowConfig | None = None
# Test hooks
_force_runner: Callable[..., dict[str, Any]] | None = None


def reset_shadow_worker_for_tests() -> None:
    """Tear down executor between tests."""
    global _executor, _reserved, _config_cache, _force_runner
    with _lock:
        if _executor is not None:
            _executor.shutdown(wait=False, cancel_futures=True)
        _executor = None
        _reserved = 0
        _config_cache = None
        _force_runner = None


def set_force_runner_for_tests(fn: Callable[..., dict[str, Any]] | None) -> None:
    global _force_runner
    _force_runner = fn


def get_inflight_for_tests() -> int:
    with _lock:
        return _reserved


def _get_config(override: ShadowConfig | None = None) -> ShadowConfig:
    global _config_cache
    if override is not None:
        return override
    if _config_cache is None:
        _config_cache = load_shadow_config()
    return _config_cache


def reload_config_for_tests() -> ShadowConfig:
    global _config_cache
    _config_cache = load_shadow_config()
    return _config_cache


def _ensure_executor(cfg: ShadowConfig) -> ThreadPoolExecutor:
    global _executor
    with _lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(
                max_workers=cfg.max_concurrency,
                thread_name_prefix="multi_shadow",
            )
            atexit.register(_shutdown_executor)
        return _executor


def _shutdown_executor() -> None:
    global _executor
    with _lock:
        if _executor is not None:
            _executor.shutdown(wait=False, cancel_futures=True)
            _executor = None


def should_sample(cfg: ShadowConfig, *, rng: random.Random | None = None) -> bool:
    if not cfg.enabled:
        return False
    if cfg.sample_rate >= 1.0:
        return True
    if cfg.sample_rate <= 0.0:
        return False
    r = rng or random.Random()
    return r.random() < cfg.sample_rate


def schedule_shadow(
    snapshot: ShadowRequestSnapshot,
    *,
    legacy_observation: dict[str, Any],
    config: ShadowConfig | None = None,
    chat_json_fn: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Fire-and-forget Shadow. Never blocks on pipeline completion.

    Returns immediate schedule status for the caller (not shadow result).
    """
    global _reserved
    cfg = _get_config(config)
    sched: dict[str, Any] = {
        "shadow_scheduled": False,
        "shadow_skipped_reason": None,
        "request_id": snapshot.request_id,
        "shadow_request_id": snapshot.shadow_request_id,
    }
    if not cfg.enabled:
        sched["shadow_skipped_reason"] = "shadow_disabled"
        return sched
    if not should_sample(cfg):
        sched["shadow_skipped_reason"] = "shadow_not_sampled"
        return sched

    with _lock:
        capacity = cfg.max_concurrency * max(1, cfg.queue_size)
        if _reserved >= capacity:
            sched["shadow_skipped_reason"] = "shadow_skipped_capacity"
            _write_skip_record(cfg, snapshot, legacy_observation, "shadow_skipped_capacity")
            return sched

    if cfg.inline_for_tests:
        _execute_job(snapshot, legacy_observation, cfg, chat_json_fn)
        sched["shadow_scheduled"] = True
        sched["shadow_mode"] = "inline_for_tests"
        return sched

    ex = _ensure_executor(cfg)

    def _job() -> None:
        global _reserved
        try:
            _execute_job(snapshot, legacy_observation, cfg, chat_json_fn)
        finally:
            with _lock:
                _reserved = max(0, _reserved - 1)

    try:
        with _lock:
            capacity = cfg.max_concurrency * max(1, cfg.queue_size)
            if _reserved >= capacity:
                sched["shadow_skipped_reason"] = "shadow_skipped_capacity"
                _write_skip_record(
                    cfg, snapshot, legacy_observation, "shadow_skipped_capacity"
                )
                return sched
            _reserved += 1
        ex.submit(_job)
        sched["shadow_scheduled"] = True
        sched["shadow_mode"] = "background"
        return sched
    except Exception as exc:  # noqa: BLE001
        with _lock:
            _reserved = max(0, _reserved - 1)
        sched["shadow_skipped_reason"] = "shadow_queue_rejected"
        sched["error"] = f"{type(exc).__name__}: {exc}"
        _write_skip_record(cfg, snapshot, legacy_observation, "shadow_queue_rejected")
        return sched


def _write_skip_record(
    cfg: ShadowConfig,
    snapshot: ShadowRequestSnapshot,
    legacy_observation: dict[str, Any],
    reason: str,
) -> None:
    rec = new_base_record(
        schema_version=cfg.schema_version,
        pipeline_version=cfg.pipeline_version,
        request_id=snapshot.request_id,
        shadow_request_id=snapshot.shadow_request_id,
    )
    rec.update(
        {
            "event": "shadow_skipped",
            "error_family": reason,
            "shadow_status": reason,
            "legacy": legacy_observation,
            "file_count": snapshot.file_count,
            "prompt_hash": snapshot.prompt_hash,
            "prompt": snapshot.prompt_for_telemetry(),
        }
    )
    append_telemetry_record(cfg.telemetry_dir, rec)


def _execute_job(
    snapshot: ShadowRequestSnapshot,
    legacy_observation: dict[str, Any],
    cfg: ShadowConfig,
    chat_json_fn: Callable[..., dict[str, Any]] | None,
) -> None:
    t0 = time.time()
    rec = new_base_record(
        schema_version=cfg.schema_version,
        pipeline_version=cfg.pipeline_version,
        request_id=snapshot.request_id,
        shadow_request_id=snapshot.shadow_request_id,
    )
    rec["event"] = "shadow_observation"
    rec["file_count"] = snapshot.file_count
    rec["source_names"] = list(snapshot.source_names)
    rec["prompt_hash"] = snapshot.prompt_hash
    rec["prompt"] = snapshot.prompt_for_telemetry()
    rec["legacy"] = legacy_observation

    try:
        if _force_runner is not None:
            shadow_out = _force_runner(snapshot, config=cfg)
        else:
            # Timeout wrapper: cooperative check after run (hard kill of LLM not available)
            shadow_out = run_shadow_pipeline(
                snapshot, config=cfg, chat_json_fn=chat_json_fn
            )
            elapsed = time.time() - t0
            if elapsed > cfg.timeout_sec:
                shadow_out["shadow_timeout"] = True
                shadow_out["error_family"] = "shadow_timeout"
                shadow_out["shadow_status"] = "shadow_timeout"
    except Exception as exc:  # noqa: BLE001
        shadow_out = {
            "shadow_started": True,
            "shadow_completed": True,
            "shadow_status": "shadow_infrastructure_error",
            "error_family": "shadow_infrastructure_error",
            "error_message": f"{type(exc).__name__}: {exc}",
            "shadow_success": False,
            "latency_total_s": round(time.time() - t0, 3),
        }

    rec["shadow"] = shadow_out
    legacy_ok = bool(legacy_observation.get("legacy_success"))
    shadow_ok = bool(shadow_out.get("shadow_success"))
    structural = structural_compare(
        legacy_observation.get("result_fingerprint"),
        shadow_out.get("result_fingerprint"),
    )
    rec["comparison"] = {
        "outcome_category": outcome_category(
            legacy_success=legacy_ok,
            shadow_success=shadow_ok,
            structural=structural if legacy_ok and shadow_ok else None,
        ),
        "structural": structural,
        "note": "Objective metadata only — not semantic correctness",
    }
    append_telemetry_record(cfg.telemetry_dir, rec)


def schedule_test_sleep_shadow(
    seconds: float,
    *,
    config: ShadowConfig,
    request_id: str = "test-sleep",
) -> dict[str, Any]:
    """Schedule a sleep-only shadow job (latency isolation tests)."""
    global _reserved
    if not config.enabled:
        return {"shadow_scheduled": False, "shadow_skipped_reason": "shadow_disabled"}

    def _job() -> None:
        global _reserved
        with _lock:
            _reserved += 1
        try:
            out = run_shadow_sleep_only(seconds)
            rec = new_base_record(
                schema_version=config.schema_version,
                pipeline_version=config.pipeline_version,
                request_id=request_id,
                shadow_request_id=f"shadow-{request_id}",
            )
            rec["event"] = "shadow_test_sleep"
            rec["shadow"] = out
            append_telemetry_record(config.telemetry_dir, rec)
        finally:
            with _lock:
                _reserved = max(0, _reserved - 1)

    if config.inline_for_tests:
        _job()
        return {"shadow_scheduled": True, "shadow_mode": "inline_for_tests"}
    ex = _ensure_executor(config)
    ex.submit(_job)
    return {"shadow_scheduled": True, "shadow_mode": "background"}
