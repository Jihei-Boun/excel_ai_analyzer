"""프로필 YAML 로딩 단위 테스트."""

from __future__ import annotations

from core import constants
from core.profile_loader import (
    PROFILES_DIR,
    clear_profile_cache,
    load_budget_profile,
    load_column_hints,
)


def test_profiles_dir_exists() -> None:
    assert (PROFILES_DIR / "column_hints.yaml").is_file()
    assert (PROFILES_DIR / "budget.yaml").is_file()


def test_column_hints_load() -> None:
    clear_profile_cache()
    hints = load_column_hints()
    assert "예산" in hints["amount_column_hints"]
    assert "비용명" in hints["item_column_hints"]
    assert hints["group_column_suffixes"]


def test_budget_profile_load() -> None:
    clear_profile_cache()
    budget = load_budget_profile()
    assert budget["detect_min_hits"] >= 1
    assert "계획예산" in budget["column_hints"]
    assert "내부흡수액" in budget["footer_labels"]
    assert "예실대비표" in budget["intro"]


def test_constants_reexport_matches_yaml() -> None:
    clear_profile_cache()
    hints = load_column_hints()
    budget = load_budget_profile()
    # constants는 import 시점에 로드되므로 값이 동일한지만 확인
    assert set(constants.AMOUNT_COLUMN_HINTS) == set(hints["amount_column_hints"])
    assert set(constants.BUDGET_FOOTER_LABELS) == set(budget["footer_labels"])
    assert constants.BUDGET_DETECT_MIN_HITS == budget["detect_min_hits"]
