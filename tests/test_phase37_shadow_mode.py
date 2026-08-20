"""Phase 37 Shadow Mode infrastructure tests — isolation & kill switch."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pandas as pd
import pytest

from core.routing.route_types import SingleRouteOutcome
from core.shadow.config import ShadowConfig, load_shadow_config
from core.shadow.fingerprint import (
    dataframe_fingerprint,
    outcome_category,
    structural_compare,
)
from core.shadow.hook import finish_with_shadow, legacy_observation_from_outcome
from core.shadow.snapshot import build_shadow_snapshot
from core.shadow.worker import (
    get_inflight_for_tests,
    reset_shadow_worker_for_tests,
    schedule_shadow,
    schedule_test_sleep_shadow,
    set_force_runner_for_tests,
)


@pytest.fixture(autouse=True)
def _reset_shadow(tmp_path, monkeypatch):
    reset_shadow_worker_for_tests()
    set_force_runner_for_tests(None)
    monkeypatch.delenv("MULTI_SHADOW_ENABLED", raising=False)
    monkeypatch.delenv("MULTI_SHADOW_INLINE_FOR_TESTS", raising=False)
    yield
    reset_shadow_worker_for_tests()
    set_force_runner_for_tests(None)


def _frames() -> list[tuple[str, pd.DataFrame]]:
    return [
        ("a", pd.DataFrame({"id": [1, 2], "x": [10, 20]})),
        ("b", pd.DataFrame({"id": [1, 2], "y": [3, 4]})),
    ]


def test_shadow_disabled_noop(tmp_path):
    cfg = ShadowConfig(enabled=False, telemetry_dir=tmp_path)
    snap = build_shadow_snapshot(
        prompt="join files",
        named_frames=_frames(),
        base_url="http://localhost:11434",
        model="qwen2.5:7b",
    )
    out = SingleRouteOutcome(reply="ok", dataframe=pd.DataFrame({"id": [1]}))
    same = finish_with_shadow(out, snapshot=snap, config=cfg)
    assert same is out
    assert same.reply == "ok"
    assert list(tmp_path.glob("*.jsonl")) == []


def test_shadow_enabled_legacy_unchanged(tmp_path):
    cfg = ShadowConfig(
        enabled=True,
        telemetry_dir=tmp_path,
        inline_for_tests=True,
        max_concurrency=1,
        queue_size=2,
    )

    def fake_runner(snapshot, config=None):  # noqa: ANN001
        # Mutate shadow copy
        for df in snapshot.sources.values():
            df["mutated"] = 1
        return {
            "shadow_started": True,
            "shadow_completed": True,
            "shadow_status": "success",
            "shadow_success": True,
            "result_fingerprint": dataframe_fingerprint(pd.DataFrame({"z": [1]})),
            "latency_total_s": 0.01,
        }

    set_force_runner_for_tests(fake_runner)
    frames = _frames()
    original_cols = list(frames[0][1].columns)
    snap = build_shadow_snapshot(
        prompt="join",
        named_frames=frames,
        base_url="http://x",
        model="m",
    )
    legacy_df = pd.DataFrame({"id": [1, 2]})
    outcome = SingleRouteOutcome(
        reply="legacy",
        dataframe=legacy_df,
        operation_name="structured_integrate",
    )
    returned = finish_with_shadow(outcome, snapshot=snap, config=cfg)
    assert returned.reply == "legacy"
    assert list(returned.dataframe.columns) == ["id"]
    assert list(frames[0][1].columns) == original_cols  # original not mutated
    assert "mutated" not in frames[0][1].columns


def test_shadow_exception_isolated(tmp_path):
    cfg = ShadowConfig(enabled=True, telemetry_dir=tmp_path, inline_for_tests=True)

    def boom(snapshot, config=None):  # noqa: ANN001
        raise RuntimeError("shadow exploded")

    set_force_runner_for_tests(boom)
    snap = build_shadow_snapshot(
        prompt="x", named_frames=_frames(), base_url="http://x", model="m"
    )
    outcome = SingleRouteOutcome(reply="legacy-ok", dataframe=None)
    # force_runner raises inside _execute_job which catches — finish still ok
    returned = finish_with_shadow(outcome, snapshot=snap, config=cfg)
    assert returned.reply == "legacy-ok"
    files = list(tmp_path.glob("shadow_*.jsonl"))
    assert files
    line = files[0].read_text(encoding="utf-8").strip().splitlines()[-1]
    rec = json.loads(line)
    assert rec["shadow"]["error_family"] == "shadow_infrastructure_error"


def test_shadow_slow_does_not_block_legacy(tmp_path):
    cfg = ShadowConfig(
        enabled=True,
        telemetry_dir=tmp_path,
        inline_for_tests=False,
        max_concurrency=2,
        queue_size=4,
    )
    t0 = time.time()
    sched = schedule_test_sleep_shadow(1.5, config=cfg, request_id="slow1")
    elapsed = time.time() - t0
    assert sched["shadow_scheduled"] is True
    assert elapsed < 0.5  # must not wait for 1.5s sleep
    # Wait for background to finish
    deadline = time.time() + 5
    while get_inflight_for_tests() > 0 and time.time() < deadline:
        time.sleep(0.05)
    time.sleep(0.2)
    files = list(tmp_path.glob("shadow_*.jsonl"))
    assert files


def test_shadow_capacity_skip(tmp_path):
    cfg = ShadowConfig(
        enabled=True,
        telemetry_dir=tmp_path,
        inline_for_tests=False,
        max_concurrency=1,
        queue_size=1,  # capacity = 1
    )

    def slow(snapshot, config=None):  # noqa: ANN001
        time.sleep(0.8)
        return {
            "shadow_started": True,
            "shadow_completed": True,
            "shadow_status": "success",
            "shadow_success": True,
            "latency_total_s": 0.8,
        }

    set_force_runner_for_tests(slow)
    snap = build_shadow_snapshot(
        prompt="x", named_frames=_frames(), base_url="http://x", model="m"
    )
    leg = {"legacy_success": True, "result_fingerprint": None}
    s1 = schedule_shadow(snap, legacy_observation=leg, config=cfg)
    assert s1["shadow_scheduled"] is True
    # Immediately flood
    skipped = 0
    for i in range(5):
        snap_i = build_shadow_snapshot(
            prompt=f"x{i}",
            named_frames=_frames(),
            base_url="http://x",
            model="m",
            request_id=f"r{i}",
        )
        s = schedule_shadow(snap_i, legacy_observation=leg, config=cfg)
        if s.get("shadow_skipped_reason") == "shadow_skipped_capacity":
            skipped += 1
    assert skipped >= 1
    deadline = time.time() + 5
    while get_inflight_for_tests() > 0 and time.time() < deadline:
        time.sleep(0.05)


def test_shadow_no_shared_mutation(tmp_path):
    cfg = ShadowConfig(enabled=True, telemetry_dir=tmp_path, inline_for_tests=True)
    frames = _frames()
    snap = build_shadow_snapshot(
        prompt="x", named_frames=frames, base_url="http://x", model="m"
    )

    def mutate(snapshot, config=None):  # noqa: ANN001
        snapshot.sources["a"].loc[:, "id"] = 999
        return {
            "shadow_started": True,
            "shadow_completed": True,
            "shadow_status": "success",
            "shadow_success": True,
            "latency_total_s": 0.01,
        }

    set_force_runner_for_tests(mutate)
    schedule_shadow(
        snap,
        legacy_observation={"legacy_success": True},
        config=cfg,
    )
    assert int(frames[0][1]["id"].iloc[0]) == 1


def test_structural_compare_not_winner():
    a = dataframe_fingerprint(pd.DataFrame({"x": [1]}))
    b = dataframe_fingerprint(pd.DataFrame({"x": [1]}))
    assert structural_compare(a, b) == "structurally_equal"
    assert outcome_category(
        legacy_success=True, shadow_success=True, structural="structurally_equal"
    ).startswith("legacy_success_shadow_success")


def test_default_config_off():
    cfg = load_shadow_config()
    assert cfg.enabled is False


def test_route_multi_shadow_off_no_schedule(monkeypatch, tmp_path):
    """Shadow OFF: route_multi system path unchanged; no telemetry."""
    monkeypatch.setenv("MULTI_SHADOW_ENABLED", "false")
    from core.routing.route_multi import route_multi_prompt

    out = route_multi_prompt(
        "요약해줘",
        named_frames=_frames(),
        base_url="http://localhost:11434",
        model="qwen2.5:7b",
        context_label=None,
        filter_df=None,
    )
    assert out.reply
    assert list(tmp_path.glob("*.jsonl")) == []


def test_shadow_does_not_replace_legacy_fields(tmp_path):
    cfg = ShadowConfig(enabled=True, telemetry_dir=tmp_path, inline_for_tests=True)

    def fake(snapshot, config=None):  # noqa: ANN001
        return {
            "shadow_started": True,
            "shadow_completed": True,
            "shadow_status": "success",
            "shadow_success": True,
            "result_fingerprint": dataframe_fingerprint(
                pd.DataFrame({"shadow_only": [1, 2, 3]})
            ),
            "latency_total_s": 0.01,
        }

    set_force_runner_for_tests(fake)
    snap = build_shadow_snapshot(
        prompt="x", named_frames=_frames(), base_url="http://x", model="m"
    )
    legacy = SingleRouteOutcome(
        reply="from-legacy",
        dataframe=pd.DataFrame({"legacy_col": [0]}),
        operation_name="structured_integrate",
    )
    out = finish_with_shadow(legacy, snapshot=snap, config=cfg)
    assert out.reply == "from-legacy"
    assert list(out.dataframe.columns) == ["legacy_col"]
    assert "shadow_only" not in out.dataframe.columns


def test_legacy_observation_integrate_failed():
    o = SingleRouteOutcome(
        reply="fail",
        dataframe=pd.DataFrame({"a": [1]}),
        operation_name="structured_integrate_failed",
    )
    leg = legacy_observation_from_outcome(o)
    assert leg["legacy_success"] is False
