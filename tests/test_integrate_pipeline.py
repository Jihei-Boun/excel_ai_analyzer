"""범용 구조화 통합 파이프라인·엔진 회귀 테스트."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from core.excel_loader import load_excel
from core.integrate_pipeline import (
    looks_like_structural_integrate,
    run_integrate_pipeline,
    split_sources_and_examples,
)
from core.plan_engine import execute_plan
from core.plan_types import DerivedRowSpec, ExecutionPlan, FileSchema
from core.plan_validate import validate_integrate_result
from core.prompt_router import route_multi_prompt
from core.profile_loader import clear_profile_cache, load_budget_profile, load_generic_profile


UPLOADS = Path(__file__).resolve().parents[1] / "data" / "uploads"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
GOLD = FIXTURES / "예실대비표_통합결과.xlsx"
TWIN = UPLOADS / "03_트윈_예실대비표.xlsx"
BUNSAN = UPLOADS / "04_분산_예실대비표.xlsx"


def test_file_schema_header_rows_tolerates_dict_items() -> None:
    """LLM이 header_rows에 dict를 넣어도 int()로 깨지지 않아야 한다."""
    schema = FileSchema.from_dict(
        {
            "header_rows": [{"row": 0}, {"index": 1}],
            "identifier_columns": ["코드"],
            "label_columns": ["이름"],
            "additive_columns": ["금액"],
            "non_additive_columns": [],
            "summary_row_labels": [],
            "column_renames": {},
            "notes": [],
        },
        source="a.xlsx",
    )
    assert schema.header_rows == [0, 1]

    schema2 = FileSchema.from_dict(
        {"header_rows": {"start": 0, "end": 1}},
        source="b.xlsx",
    )
    assert schema2.header_rows == [0, 1]

    schema3 = FileSchema.from_dict({"header_rows": [0, {"x": "nope"}, 1]}, source="c.xlsx")
    assert schema3.header_rows == [0, 1]


def _norm_code(value: object) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    try:
        number = float(str(value).replace(",", ""))
        if number.is_integer():
            return str(int(number))
        return str(number)
    except ValueError:
        text = str(value).strip()
        return text or None


def _detail_codes(df: pd.DataFrame) -> list[str]:
    return [
        code
        for code in (_norm_code(value) for value in df["비용코드"])
        if code is not None
    ]


def _sample_plan(twin: pd.DataFrame, sources: list[str]) -> ExecutionPlan:
    amount_cols = [
        col
        for col in twin.columns
        if col not in ("비목분류", "비용명", "비용명_2")
    ]
    return ExecutionPlan(
        operation="aggregate_merge",
        sources=sources,
        group_keys=["비용코드"],
        aggregations={col: "sum" for col in amount_cols},
        renames={"비용명": "비용코드", "비용명_2": "비용명"},
        summary_row_labels=["소계", "소 계", "내부흡수액", "외부유출액", "합계", "합 계"],
        derived_rows=[
            DerivedRowSpec(type="subtotal", label="소계", group_by="비목분류"),
            DerivedRowSpec(
                type="summary",
                label="내부흡수액",
                composition="codes",
                codes=["121", "123"],
                code_column="비용코드",
            ),
            DerivedRowSpec(type="summary", label="외부유출액", composition="remainder"),
            DerivedRowSpec(type="grand_total", label="합계"),
        ],
        group_display_column="비목분류",
        sort_by=["비용코드"],
        blank_repeated_group_labels=True,
        include_normalized_source_sheets=True,
        integrated_sheet_name="통합",
        sheet_name_map={sources[0]: "트윈", sources[1]: "분산"},
        column_order=["비목분류", "비용코드", "비용명", *amount_cols],
    )


@pytest.mark.skipif(not TWIN.is_file() or not BUNSAN.is_file(), reason="sample uploads missing")
@pytest.mark.skipif(not GOLD.is_file(), reason="gold fixture missing")
def test_aggregate_merge_matches_gold_structure_and_values() -> None:
    twin = load_excel(TWIN)
    bunsan = load_excel(BUNSAN)
    sources = ["03_트윈_예실대비표.xlsx", "04_분산_예실대비표.xlsx"]
    plan = _sample_plan(twin, sources)
    executed = execute_plan(
        plan,
        {sources[0]: twin, sources[1]: bunsan},
    )

    assert list(executed["sheets"].keys()) == ["트윈", "분산", "통합"]

    gold_book = pd.read_excel(GOLD, sheet_name=None)
    assert list(gold_book.keys()) == ["트윈", "분산", "통합"]

    integrated = executed["integrated"]
    gold = gold_book["통합"]
    assert list(integrated.columns) == list(gold.columns)
    assert _detail_codes(integrated) == _detail_codes(gold)

    amount_cols = [col for col in plan.column_order if col in plan.aggregations]
    for label in ("소계", "내부흡수액", "외부유출액", "합계"):
        out_rows = integrated[
            integrated["비목분류"].astype(str).str.replace(" ", "", regex=False) == label
        ]
        gold_rows = gold[
            gold["비목분류"].astype(str).str.replace(" ", "", regex=False) == label
        ]
        assert len(out_rows) == len(gold_rows), label

    # 상세 + 푸터 숫자 비교
    def keyed(df: pd.DataFrame) -> dict[tuple, pd.Series]:
        mapping: dict[tuple, pd.Series] = {}
        for _, row in df.iterrows():
            code = _norm_code(row.get("비용코드"))
            cat = row.get("비목분류")
            cat_text = None if pd.isna(cat) else str(cat).strip()
            key = ("code", code) if code else ("label", cat_text)
            mapping[key] = row
        return mapping

    out_map = keyed(integrated)
    gold_map = keyed(gold)
    assert set(out_map) == set(gold_map)
    for key, out_row in out_map.items():
        gold_row = gold_map[key]
        for col in amount_cols:
            left = 0.0 if pd.isna(out_row[col]) else float(out_row[col])
            right = 0.0 if pd.isna(gold_row[col]) else float(gold_row[col])
            assert abs(left - right) <= 0.5, (key, col, left, right)

    report = validate_integrate_result(
        plan=plan,
        source_details=executed["source_details"],
        integrated_details=executed["integrated_details"],
        integrated=integrated,
    )
    assert report.ok


def test_looks_like_structural_integrate_is_generic() -> None:
    assert looks_like_structural_integrate("두 파일을 통합해줘")
    assert looks_like_structural_integrate("merge these workbooks")
    assert not looks_like_structural_integrate("실행예산 합계를 알려줘")


def test_split_sources_and_examples() -> None:
    frames = [
        ("a.xlsx", pd.DataFrame({"x": [1]})),
        ("b.xlsx", pd.DataFrame({"x": [2]})),
        ("예실대비표_통합결과.xlsx", pd.DataFrame({"x": [3]})),
    ]
    sources, examples = split_sources_and_examples(frames)
    assert len(sources) == 2
    assert len(examples) == 1


def test_semantic_hints_loaded_as_hints_only() -> None:
    clear_profile_cache()
    budget = load_budget_profile()
    generic = load_generic_profile()
    assert budget.get("semantic_hints")
    assert "likely_identifier_concepts" in budget["semantic_hints"]
    assert generic.get("semantic_hints")
    # 힌트는 있어도 전용 integrator 키는 없어야 한다
    assert "integrator" not in budget
    assert "budget_integrator" not in budget


@pytest.mark.skipif(not TWIN.is_file() or not BUNSAN.is_file(), reason="sample uploads missing")
def test_pipeline_with_mocked_llm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    twin = load_excel(TWIN)
    bunsan = load_excel(BUNSAN)
    named = [
        ("03_트윈_예실대비표.xlsx", twin),
        ("04_분산_예실대비표.xlsx", bunsan),
    ]
    plan = _sample_plan(twin, [named[0][0], named[1][0]])

    schema_payload = {
        "header_rows": [0, 1],
        "identifier_columns": ["비용명"],
        "label_columns": ["비목분류", "비용명_2"],
        "additive_columns": list(plan.aggregations),
        "non_additive_columns": [],
        "summary_row_labels": plan.summary_row_labels,
        "column_renames": plan.renames,
        "notes": [],
    }

    calls = {"n": 0}

    def fake_chat_json(prompt: str, **kwargs):
        calls["n"] += 1
        if "operation, sources" in prompt or '"operation"' in prompt and "User request" in prompt:
            return plan.to_dict()
        if "User request:" in prompt:
            return plan.to_dict()
        return dict(schema_payload)

    monkeypatch.setattr(
        "core.integrate_pipeline.MERGES_DIR",
        tmp_path,
    )
    result = run_integrate_pipeline(
        "두 파일을 통합해줘",
        named,
        base_url="http://localhost:11434",
        model="dummy",
        use_budget_profile=True,
        export=True,
        chat_json_fn=fake_chat_json,
        # schemas/plan을 None으로 두어 mock 경로를 탄다
    )
    assert result.validation.ok
    assert result.workbook_path
    assert Path(result.workbook_path).is_file()
    assert calls["n"] >= 2
    book = pd.read_excel(result.workbook_path, sheet_name=None)
    assert "통합" in book
    assert _detail_codes(book["통합"]) == _detail_codes(
        pd.read_excel(GOLD, sheet_name="통합")
    )


@pytest.mark.skipif(not TWIN.is_file() or not BUNSAN.is_file(), reason="sample uploads missing")
def test_route_multi_uses_structured_integrate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    twin = load_excel(TWIN)
    bunsan = load_excel(BUNSAN)
    named = [
        ("03_트윈_예실대비표.xlsx", twin),
        ("04_분산_예실대비표.xlsx", bunsan),
    ]
    plan = _sample_plan(twin, [named[0][0], named[1][0]])

    def fake_chat_json(prompt: str, **kwargs):
        if "User request:" in prompt:
            return plan.to_dict()
        return {
            "header_rows": [0, 1],
            "identifier_columns": ["비용명"],
            "label_columns": ["비목분류", "비용명_2"],
            "additive_columns": list(plan.aggregations),
            "summary_row_labels": plan.summary_row_labels,
            "column_renames": plan.renames,
        }

    monkeypatch.setattr("core.integrate_pipeline.MERGES_DIR", tmp_path)

    def fake_try(prompt, named_frames, **kwargs):
        return run_integrate_pipeline(
            prompt,
            named_frames,
            chat_json_fn=fake_chat_json,
            export=True,
            **{k: v for k, v in kwargs.items() if k != "chat_json_fn"},
        )

    monkeypatch.setattr(
        "core.route_multi.try_integrate_pipeline",
        fake_try,
    )

    outcome = route_multi_prompt(
        "선택한 파일들을 통합해줘",
        named_frames=named,
        base_url="http://localhost:11434",
        model="dummy",
        use_budget_profile=True,
        context_label=None,
        filter_df=None,
    )
    assert outcome.operation_name == "structured_integrate"
    assert outcome.dataframe is not None
    assert "통합" in outcome.reply or "실행 계획" in outcome.reply
    assert outcome.meta.get("workbook_bytes")
