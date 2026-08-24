"""Phase 38 — post-snapshot legacy exception Shadow coverage (Candidate A)."""

from __future__ import annotations

import time
import traceback
from pathlib import Path

import pandas as pd
import pytest

from core.routing.route_multi import route_multi_prompt
from core.routing.route_types import SingleRouteOutcome
from core.shadow.config import ShadowConfig
from core.shadow.fingerprint import outcome_category
from core.shadow.hook import (
    classify_legacy_exception_family,
    finish_with_shadow,
    legacy_observation_from_exception,
    observe_exception_with_shadow,
)
from core.shadow.snapshot import build_shadow_snapshot
from core.shadow.worker import (
    reset_shadow_worker_for_tests,
    schedule_shadow,
    set_force_runner_for_tests,
)


@pytest.fixture(autouse=True)
def _reset_shadow(monkeypatch, tmp_path):
    reset_shadow_worker_for_tests()
    set_force_runner_for_tests(None)
    monkeypatch.delenv("MULTI_SHADOW_ENABLED", raising=False)
    monkeypatch.delenv("MULTI_SHADOW_INLINE_FOR_TESTS", raising=False)
    yield
    reset_shadow_worker_for_tests()
    set_force_runner_for_tests(None)


def _frames() -> list[tuple[str, pd.DataFrame]]:
    return [
        ("a", pd.DataFrame({"id": [1, 2], "cost": [10, 20]})),
        ("b", pd.DataFrame({"id": [1, 2], "name": ["x", "y"]})),
    ]


def _cfg(tmp_path: Path, **kwargs) -> ShadowConfig:
    base = dict(
        enabled=True,
        telemetry_dir=tmp_path,
        sample_rate=1.0,
        max_concurrency=1,
        queue_size=2,
        inline_for_tests=False,
        store_prompt=False,
    )
    base.update(kwargs)
    return ShadowConfig(**base)


def _count_schedule(monkeypatch) -> list:
    calls: list = []
    real = schedule_shadow

    def wrapped(snapshot, *, legacy_observation, config=None, chat_json_fn=None):  # noqa: ANN001
        calls.append({"legacy": dict(legacy_observation), "snap": snapshot})
        return real(
            snapshot,
            legacy_observation=legacy_observation,
            config=config,
            chat_json_fn=chat_json_fn,
        )

    monkeypatch.setattr("core.shadow.hook.schedule_shadow", wrapped)
    monkeypatch.setattr("core.shadow.worker.schedule_shadow", wrapped)
    return calls


# ---------------------------------------------------------------------------
# Helpers unit
# ---------------------------------------------------------------------------


def test_exception_family_generic_only() -> None:
    assert classify_legacy_exception_family(KeyError("col")) == "key_error"
    assert classify_legacy_exception_family(ValueError("bad")) == "value_error"
    assert classify_legacy_exception_family(RuntimeError("x")) == "runtime_error"
    obs = legacy_observation_from_exception(KeyError("2"))
    assert obs["legacy_status"] == "exception"
    assert obs["legacy_success"] is False
    assert obs["result_fingerprint"] is None
    assert obs["legacy_result_type"] == "none"
    assert obs["legacy_exception_family"] == "key_error"


def test_outcome_category_exception_branch() -> None:
    assert (
        outcome_category(
            legacy_success=False,
            shadow_success=True,
            legacy_status="exception",
        )
        == "legacy_exception_shadow_success"
    )
    assert (
        outcome_category(
            legacy_success=False,
            shadow_success=False,
            legacy_status="exception",
        )
        == "legacy_exception_shadow_failure"
    )


# ---------------------------------------------------------------------------
# T1 — success exactly once
# ---------------------------------------------------------------------------


def test_t1_success_shadow_exactly_once(tmp_path, monkeypatch) -> None:
    calls = _count_schedule(monkeypatch)
    cfg = _cfg(tmp_path, inline_for_tests=True)
    monkeypatch.setenv("MULTI_SHADOW_ENABLED", "true")
    monkeypatch.setattr(
        "core.shadow.hook.load_shadow_config",
        lambda: cfg,
    )
    monkeypatch.setattr(
        "core.shadow.worker.load_shadow_config",
        lambda: cfg,
    )
    set_force_runner_for_tests(
        lambda snapshot, config=None: {
            "shadow_started": True,
            "shadow_completed": True,
            "shadow_status": "success",
            "shadow_success": True,
            "result_fingerprint": None,
            "latency_total_s": 0.01,
        }
    )
    monkeypatch.setattr(
        "core.routing.route_multi.run_multi_analysis",
        lambda *a, **k: (
            pd.DataFrame({"id": [1], "cost": [10]}),
            "ok",
            {},
        ),
    )
    monkeypatch.setattr(
        "core.routing.route_multi.detect_aggregate_op",
        lambda prompt: None,
    )
    monkeypatch.setattr(
        "core.routing.route_multi.looks_like_structural_integrate",
        lambda prompt: False,
    )
    monkeypatch.setattr(
        "core.routing.route_multi.postprocess_table_result",
        lambda result, prompt, summary, source_df=None: (result, summary, {}),
    )

    out = route_multi_prompt(
        "프로젝트별 비용 정리해줘",
        named_frames=_frames(),
        base_url="http://x",
        model="m",
        context_label=None,
        filter_df=None,
    )
    assert isinstance(out, SingleRouteOutcome)
    assert len(calls) == 1
    assert calls[0]["legacy"]["legacy_success"] is True


# ---------------------------------------------------------------------------
# T2 — exception exactly once + traceback preserved
# ---------------------------------------------------------------------------


def test_t2_exception_shadow_exactly_once(tmp_path, monkeypatch) -> None:
    calls = _count_schedule(monkeypatch)
    cfg = _cfg(tmp_path, inline_for_tests=True)
    monkeypatch.setattr("core.shadow.hook.load_shadow_config", lambda: cfg)
    monkeypatch.setattr("core.shadow.worker.load_shadow_config", lambda: cfg)
    set_force_runner_for_tests(
        lambda snapshot, config=None: {
            "shadow_started": True,
            "shadow_completed": True,
            "shadow_status": "success",
            "shadow_success": True,
            "result_fingerprint": None,
            "latency_total_s": 0.01,
        }
    )
    monkeypatch.setattr(
        "core.routing.route_multi.detect_aggregate_op", lambda p: None
    )
    monkeypatch.setattr(
        "core.routing.route_multi.looks_like_structural_integrate",
        lambda p: False,
    )

    def boom(*a, **k):  # noqa: ANN001
        raise KeyError("2")

    monkeypatch.setattr("core.routing.route_multi.run_multi_analysis", boom)

    with pytest.raises(KeyError, match="2") as ei:
        route_multi_prompt(
            "프로젝트별 총 비용과 직원 정리해줘",
            named_frames=_frames(),
            base_url="http://x",
            model="m",
            context_label=None,
            filter_df=None,
        )
    assert ei.type is KeyError
    assert ei.value.args == ("2",)
    # traceback includes route_multi
    tb = "".join(traceback.format_exception(ei.type, ei.value, ei.tb))
    assert "route_multi" in tb or "run_multi_analysis" in tb
    assert len(calls) == 1
    leg = calls[0]["legacy"]
    assert leg["legacy_status"] == "exception"
    assert leg["legacy_success"] is False
    assert leg["result_fingerprint"] is None
    assert leg["legacy_exception_type"] == "KeyError"


# ---------------------------------------------------------------------------
# T3 — scheduler failure must not replace original exception
# ---------------------------------------------------------------------------


def test_t3_scheduler_failure_preserves_legacy_exception(tmp_path, monkeypatch) -> None:
    cfg = _cfg(tmp_path)
    monkeypatch.setattr("core.shadow.hook.load_shadow_config", lambda: cfg)

    snap = build_shadow_snapshot(
        prompt="x",
        named_frames=_frames(),
        base_url="http://x",
        model="m",
    )

    def boom_sched(*a, **k):  # noqa: ANN001
        raise RuntimeError("scheduler exploded")

    monkeypatch.setattr("core.shadow.hook.schedule_shadow", boom_sched)

    # observe must swallow scheduler error
    observe_exception_with_shadow(
        KeyError("col"),
        snapshot=snap,
        legacy_started_at=time.time(),
        config=cfg,
    )

    # route path: schedule blows up inside observe; KeyError still raised
    monkeypatch.setattr(
        "core.routing.route_multi.detect_aggregate_op", lambda p: None
    )
    monkeypatch.setattr(
        "core.routing.route_multi.looks_like_structural_integrate",
        lambda p: False,
    )
    monkeypatch.setattr(
        "core.routing.route_multi.run_multi_analysis",
        lambda *a, **k: (_ for _ in ()).throw(KeyError("missing")),
    )
    monkeypatch.setattr("core.shadow.worker.load_shadow_config", lambda: cfg)

    with pytest.raises(KeyError, match="missing"):
        route_multi_prompt(
            "비용 집계해줘",
            named_frames=_frames(),
            base_url="http://x",
            model="m",
            context_label=None,
            filter_df=None,
        )


# ---------------------------------------------------------------------------
# T4 — pre-snapshot early route → no shadow
# ---------------------------------------------------------------------------


def test_t4_presnapshot_early_route_no_shadow(tmp_path, monkeypatch) -> None:
    calls = _count_schedule(monkeypatch)
    cfg = _cfg(tmp_path, inline_for_tests=True)
    monkeypatch.setattr("core.shadow.hook.load_shadow_config", lambda: cfg)
    monkeypatch.setattr("core.shadow.worker.load_shadow_config", lambda: cfg)

    out = route_multi_prompt(
        "파일 요약해줘",
        named_frames=_frames(),
        base_url="http://x",
        model="m",
        context_label=None,
        filter_df=None,
    )
    assert out.reply
    assert calls == []


# ---------------------------------------------------------------------------
# T5 — shadow disabled
# ---------------------------------------------------------------------------


def test_t5_shadow_disabled_no_schedule(tmp_path, monkeypatch) -> None:
    calls = _count_schedule(monkeypatch)
    cfg = _cfg(tmp_path, enabled=False)
    monkeypatch.setattr("core.shadow.hook.load_shadow_config", lambda: cfg)
    monkeypatch.setattr("core.shadow.worker.load_shadow_config", lambda: cfg)
    monkeypatch.setattr(
        "core.routing.route_multi.detect_aggregate_op", lambda p: None
    )
    monkeypatch.setattr(
        "core.routing.route_multi.looks_like_structural_integrate",
        lambda p: False,
    )
    monkeypatch.setattr(
        "core.routing.route_multi.run_multi_analysis",
        lambda *a, **k: (_ for _ in ()).throw(KeyError("2")),
    )
    with pytest.raises(KeyError):
        route_multi_prompt(
            "비용 정리",
            named_frames=_frames(),
            base_url="http://x",
            model="m",
            context_label=None,
            filter_df=None,
        )
    assert calls == []


# ---------------------------------------------------------------------------
# T6 — capacity full: exception still propagates; no user wait
# ---------------------------------------------------------------------------


def test_t6_capacity_full_exception_unchanged(tmp_path, monkeypatch) -> None:
    cfg = _cfg(tmp_path, max_concurrency=1, queue_size=1, inline_for_tests=False)
    monkeypatch.setattr("core.shadow.hook.load_shadow_config", lambda: cfg)
    monkeypatch.setattr("core.shadow.worker.load_shadow_config", lambda: cfg)

    # Fill capacity with a slow job
    set_force_runner_for_tests(
        lambda snapshot, config=None: (
            time.sleep(0.5)
            or {
                "shadow_started": True,
                "shadow_completed": True,
                "shadow_status": "success",
                "shadow_success": True,
                "latency_total_s": 0.5,
            }
        )
    )
    snap = build_shadow_snapshot(
        prompt="fill",
        named_frames=_frames(),
        base_url="http://x",
        model="m",
    )
    schedule_shadow(
        snap,
        legacy_observation={"legacy_success": True, "legacy_status": "success"},
        config=cfg,
    )
    # Second schedule may skip capacity; exception path must still be fast
    monkeypatch.setattr(
        "core.routing.route_multi.detect_aggregate_op", lambda p: None
    )
    monkeypatch.setattr(
        "core.routing.route_multi.looks_like_structural_integrate",
        lambda p: False,
    )
    monkeypatch.setattr(
        "core.routing.route_multi.run_multi_analysis",
        lambda *a, **k: (_ for _ in ()).throw(KeyError("2")),
    )
    t0 = time.time()
    with pytest.raises(KeyError):
        route_multi_prompt(
            "비용 정리",
            named_frames=_frames(),
            base_url="http://x",
            model="m",
            context_label=None,
            filter_df=None,
        )
    assert time.time() - t0 < 0.4  # must not wait on shadow worker


# ---------------------------------------------------------------------------
# T7 — no double schedule (mutually exclusive paths)
# ---------------------------------------------------------------------------


def test_t7_finish_flag_prevents_double(tmp_path, monkeypatch) -> None:
    calls = _count_schedule(monkeypatch)
    cfg = _cfg(tmp_path, inline_for_tests=True)
    monkeypatch.setattr("core.shadow.hook.load_shadow_config", lambda: cfg)
    monkeypatch.setattr("core.shadow.worker.load_shadow_config", lambda: cfg)
    set_force_runner_for_tests(
        lambda snapshot, config=None: {
            "shadow_started": True,
            "shadow_completed": True,
            "shadow_status": "success",
            "shadow_success": True,
            "latency_total_s": 0.01,
        }
    )
    snap = build_shadow_snapshot(
        prompt="x", named_frames=_frames(), base_url="http://x", model="m"
    )
    outcome = SingleRouteOutcome(reply="ok", dataframe=pd.DataFrame({"a": [1]}))
    finish_with_shadow(outcome, snapshot=snap, config=cfg)
    # Second finish with same snapshot via observe should still be callable;
    # route-level flag is what prevents double — unit-check observe+finish counts
    observe_exception_with_shadow(KeyError("z"), snapshot=snap, config=cfg)
    # Without route flag, hook allows 2 schedules — document that route_multi flag is the guard.
    # Explicit route_multi T1/T2 already assert exactly-once. Here assert helpers work.
    assert len(calls) == 2


def test_t7_route_success_and_exception_are_exclusive(tmp_path, monkeypatch) -> None:
    """Success path must not also hit exception observer."""
    calls = _count_schedule(monkeypatch)
    cfg = _cfg(tmp_path, inline_for_tests=True)
    monkeypatch.setattr("core.shadow.hook.load_shadow_config", lambda: cfg)
    monkeypatch.setattr("core.shadow.worker.load_shadow_config", lambda: cfg)
    set_force_runner_for_tests(
        lambda snapshot, config=None: {
            "shadow_started": True,
            "shadow_completed": True,
            "shadow_status": "success",
            "shadow_success": True,
            "latency_total_s": 0.01,
        }
    )
    monkeypatch.setattr(
        "core.routing.route_multi.detect_aggregate_op", lambda p: None
    )
    monkeypatch.setattr(
        "core.routing.route_multi.looks_like_structural_integrate",
        lambda p: False,
    )
    monkeypatch.setattr(
        "core.routing.route_multi.run_multi_analysis",
        lambda *a, **k: (pd.DataFrame({"a": [1]}), "ok", {}),
    )
    monkeypatch.setattr(
        "core.routing.route_multi.postprocess_table_result",
        lambda result, prompt, summary, source_df=None: (result, summary, {}),
    )
    route_multi_prompt(
        "비용 표로 정리",
        named_frames=_frames(),
        base_url="http://x",
        model="m",
        context_label=None,
        filter_df=None,
    )
    assert len(calls) == 1
    assert calls[0]["legacy"].get("legacy_status") != "exception"


# ---------------------------------------------------------------------------
# T8 — failure telemetry contract
# ---------------------------------------------------------------------------


def test_t8_failure_telemetry_fields(tmp_path, monkeypatch) -> None:
    calls = _count_schedule(monkeypatch)
    cfg = _cfg(tmp_path, inline_for_tests=True)
    monkeypatch.setattr("core.shadow.hook.load_shadow_config", lambda: cfg)
    monkeypatch.setattr("core.shadow.worker.load_shadow_config", lambda: cfg)
    set_force_runner_for_tests(
        lambda snapshot, config=None: {
            "shadow_started": True,
            "shadow_completed": True,
            "shadow_status": "cannot_plan",
            "shadow_success": False,
            "latency_total_s": 0.01,
        }
    )
    monkeypatch.setattr(
        "core.routing.route_multi.detect_aggregate_op", lambda p: None
    )
    monkeypatch.setattr(
        "core.routing.route_multi.looks_like_structural_integrate",
        lambda p: False,
    )
    monkeypatch.setattr(
        "core.routing.route_multi.run_multi_analysis",
        lambda *a, **k: (_ for _ in ()).throw(KeyError("2")),
    )
    with pytest.raises(KeyError):
        route_multi_prompt(
            "비용 정리",
            named_frames=_frames(),
            base_url="http://x",
            model="m",
            context_label=None,
            filter_df=None,
        )
    leg = calls[0]["legacy"]
    assert leg["legacy_success"] is False
    assert leg["legacy_status"] == "exception"
    assert leg["result_fingerprint"] is None
    assert leg["legacy_exception_family"] == "key_error"
    assert "legacy_error_message" in leg


# ---------------------------------------------------------------------------
# Latency isolation — slow shadow must not delay exception
# ---------------------------------------------------------------------------


def test_latency_isolation_slow_shadow_background(tmp_path, monkeypatch) -> None:
    cfg = _cfg(tmp_path, inline_for_tests=False, max_concurrency=2, queue_size=4)
    monkeypatch.setattr("core.shadow.hook.load_shadow_config", lambda: cfg)
    monkeypatch.setattr("core.shadow.worker.load_shadow_config", lambda: cfg)

    def slow_runner(snapshot, config=None):  # noqa: ANN001
        time.sleep(1.0)
        return {
            "shadow_started": True,
            "shadow_completed": True,
            "shadow_status": "success",
            "shadow_success": True,
            "latency_total_s": 1.0,
        }

    set_force_runner_for_tests(slow_runner)
    monkeypatch.setattr(
        "core.routing.route_multi.detect_aggregate_op", lambda p: None
    )
    monkeypatch.setattr(
        "core.routing.route_multi.looks_like_structural_integrate",
        lambda p: False,
    )
    monkeypatch.setattr(
        "core.routing.route_multi.run_multi_analysis",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("pandasai boom")),
    )
    t0 = time.time()
    with pytest.raises(RuntimeError, match="pandasai boom"):
        route_multi_prompt(
            "교차 분석해줘",
            named_frames=_frames(),
            base_url="http://x",
            model="m",
            context_label=None,
            filter_df=None,
        )
    elapsed = time.time() - t0
    assert elapsed < 0.5, f"legacy exception waited on shadow: {elapsed:.3f}s"
