"""측정기 --engine 옵션: 앱 스위치를 켠 채로 돌리고, 끝나면 원래대로 돌려 놓는다. 모델은 부르지 않는다."""

from __future__ import annotations

import os

import pytest

from core.analysis import step_engine
from tests.benchmark import runner


@pytest.fixture
def seen_engine(monkeypatch):
    seen: list[bool] = []

    def fake_suite(**_kwargs):
        seen.append(step_engine.enabled())
        return {"overall": {}}

    monkeypatch.setattr(runner, "_run_suite", fake_suite)
    monkeypatch.delenv(step_engine.ENGINE_ENV, raising=False)
    return seen


@pytest.mark.parametrize(("engine", "on"), [("step", True), ("plan", False)])
def test_engine_flag_sets_the_app_switch_and_restores_it(seen_engine, engine, on) -> None:
    summary = runner.run_suite(live=True, save=False, engine=engine)
    assert seen_engine == [on]
    assert summary["engine"] == engine
    assert step_engine.ENGINE_ENV not in os.environ


def test_command_line_takes_the_engine(seen_engine) -> None:
    assert runner.main(["--live", "--no-save", "--engine", "step"]) == 0
    assert seen_engine == [True]
