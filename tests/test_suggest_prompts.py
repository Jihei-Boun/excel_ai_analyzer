"""컬럼 기반 추천 질문 생성 단위 테스트."""

from __future__ import annotations

import pandas as pd

from core.suggest_prompts import suggest_example_prompts


def test_budget_mode_uses_fixed_prompts() -> None:
    df = pd.DataFrame({"상품": ["A", "B"], "매출": [100, 200]})
    prompts = suggest_example_prompts(df, profile_name="budget", limit=4)
    joined = " ".join(prompts)
    assert "실행예산" in joined or "비목" in joined or "비용명" in joined
    assert all("매출" not in p for p in prompts)


def test_generic_dynamic_from_columns() -> None:
    df = pd.DataFrame(
        {
            "지역": ["서울", "부산", "서울", "대구"],
            "매출": [1000, 2000, 1500, 800],
            "비용명": [121, 201, 121, 142],
        }
    )
    prompts = suggest_example_prompts(df, profile_name="generic", limit=4)
    joined = " ".join(prompts)
    assert "파일을 요약해줘" in prompts
    assert "지역" in joined
    assert "매출" in joined
    # 코드성 숫자 컬럼(비용명)은 합계 후보에서 제외
    assert "비용명 합계" not in joined
    assert "연구활동비" not in joined
    assert "계획예산" not in joined
    assert "집행률" not in joined


def test_sales_profile_prompts() -> None:
    prompts = suggest_example_prompts(None, profile_name="sales", limit=4)
    joined = " ".join(prompts)
    assert "매출" in joined or "상품" in joined
    assert "실행예산" not in joined


def test_use_profile_context_switches_labels() -> None:
    from core.profile_loader import preferred_labels_for, use_profile

    with use_profile("sales"):
        labels = preferred_labels_for()
    assert "상품명" in labels
    assert "비목분류" not in labels


def test_empty_df_falls_back_to_generic() -> None:
    prompts = suggest_example_prompts(None, profile_name="generic", limit=4)
    assert "파일을 요약해줘" in prompts
    assert len(prompts) <= 4


def test_multi_file_fallback() -> None:
    prompts = suggest_example_prompts(
        None,
        profile_name="generic",
        multi_file=True,
        limit=4,
    )
    assert any("파일" in p for p in prompts)
