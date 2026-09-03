"""Streamlit-free production facade for Excel analysis.

Call flow (v1, one-shot):
  parse/validate request
  → load_tabular / use_profile
  → route_single_prompt | route_multi_prompt
  → normalize SingleRouteOutcome into the JSON contract

Does not call ui.chat.process_user_prompt, fake session_state, or the
Candidate integration pipeline (run_integration_pipeline).
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from core.application.artifacts import (
    dataframe_preview,
    json_safe,
    materialize_existing_file,
    request_output_dir,
    write_bytes_artifact,
    write_dataframe_xlsx,
    write_manifest,
)
from core.application.contracts import (
    CONTRACT_VERSION,
    DEFAULT_TIMEOUT_SECONDS,
    MAX_TIMEOUT_SECONDS,
    PREVIEW_ROW_LIMIT,
    SAFE_OUTCOME_META_KEYS,
    SUPPORTED_ANALYSIS_MODES,
    SUPPORTED_EXTENSIONS,
    SUPPORTED_OPERATIONS,
    AnalyzeRequest,
    AnalyzeResponse,
    Artifact,
    ContractError,
    ErrorInfo,
    InputSource,
    ModelConfig,
    SafetyInfo,
    TimingInfo,
    WarningItem,
    empty_result_data,
    error_response,
)
from core.constants import DEFAULT_OLLAMA_BASE_URL, DEFAULT_OLLAMA_MODEL
from core.io.excel_loader import (
    CSV_SHEET_NAME,
    is_csv_path,
    is_excel_path,
    load_tabular,
)
from core.profile_loader import list_profile_names, load_profile, use_profile
from core.routing.route_multi import route_multi_prompt
from core.routing.route_single import route_single_prompt
from core.routing.route_types import SingleRouteOutcome
from core.summary.file_summary import build_file_summary, is_summary_request

_UNSAFE_OUTPUT_MARKERS = ("안전하지 않은 코드", "shouldn't use", "malicious")


@dataclass
class _LoadedInput:
    source: InputSource
    path: Path
    frame: pd.DataFrame
    frame_snapshot: pd.DataFrame
    sheet_name: str | int
    sheet_names: list[str]
    label: str
    original_bytes: bytes


def analyze_excel(request: AnalyzeRequest | dict[str, Any] | None) -> dict[str, Any]:
    """Public Python entry: JSON-like request → JSON-like response."""
    started = time.perf_counter()
    request_id = ""
    try:
        parsed = parse_analyze_request(request)
        request_id = parsed.request_id
        response = _execute(parsed, started=started)
    except ContractError as exc:
        response = error_response(
            request_id=request_id or _peek_request_id(request),
            status="invalid_request",
            code=exc.code,
            message=exc.message,
            stage=exc.stage,
            retryable=exc.retryable,
            elapsed_ms=_elapsed_ms(started),
        )
    except Exception as exc:
        response = _exception_response(
            request_id=request_id or _peek_request_id(request),
            exc=exc,
            elapsed_ms=_elapsed_ms(started),
        )
    return json_safe(response.to_dict())


def parse_analyze_request(payload: AnalyzeRequest | dict[str, Any] | None) -> AnalyzeRequest:
    if isinstance(payload, AnalyzeRequest):
        _validate_parsed(payload)
        return payload
    if not isinstance(payload, dict):
        raise ContractError("invalid_request", "Request must be a JSON object.")

    version = str(payload.get("contract_version") or "").strip()
    if version != CONTRACT_VERSION:
        raise ContractError(
            "unsupported_contract_version",
            f"Unsupported contract_version: {version!r}. Expected {CONTRACT_VERSION}.",
        )

    request_id = payload.get("request_id")
    if request_id is None or not str(request_id).strip():
        raise ContractError("missing_request_id", "request_id is required.")
    request_id = str(request_id).strip()

    operation = str(payload.get("operation") or "").strip() or "analyze"
    if operation not in SUPPORTED_OPERATIONS:
        raise ContractError(
            "invalid_operation",
            f"Unsupported operation: {operation!r}.",
        )

    analysis_mode = str(payload.get("analysis_mode") or "").strip().lower()
    if analysis_mode not in SUPPORTED_ANALYSIS_MODES:
        raise ContractError(
            "invalid_analysis_mode",
            "analysis_mode must be 'single' or 'multi'.",
        )

    user_prompt = payload.get("user_prompt")
    if not isinstance(user_prompt, str) or not user_prompt.strip():
        raise ContractError("missing_user_prompt", "user_prompt is required.")

    profile_name = str(payload.get("profile_name") or "generic").strip().lower() or "generic"
    try:
        load_profile(profile_name)
    except ValueError as exc:
        raise ContractError("invalid_profile", str(exc)) from exc

    model = _parse_model(payload.get("model"))
    timeout_seconds = _parse_timeout(payload.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))
    output_directory = payload.get("output_directory")
    if not isinstance(output_directory, str) or not output_directory.strip():
        raise ContractError(
            "invalid_output_directory",
            "output_directory is required and must be an absolute path.",
        )
    # Structural check only; directory is created later.
    from core.application.artifacts import resolve_output_root

    resolve_output_root(output_directory.strip())

    inputs = _parse_inputs(payload.get("inputs"), analysis_mode=analysis_mode)
    parsed = AnalyzeRequest(
        contract_version=CONTRACT_VERSION,
        request_id=request_id,
        operation=operation,
        inputs=tuple(inputs),
        user_prompt=user_prompt.strip(),
        analysis_mode=analysis_mode,
        profile_name=profile_name,
        model=model,
        timeout_seconds=timeout_seconds,
        output_directory=str(output_directory).strip(),
    )
    _validate_parsed(parsed)
    return parsed


def _validate_parsed(request: AnalyzeRequest) -> None:
    if request.analysis_mode == "single" and len(request.inputs) != 1:
        raise ContractError(
            "invalid_input_count",
            "analysis_mode 'single' requires exactly one input.",
        )
    if request.analysis_mode == "multi" and len(request.inputs) < 2:
        raise ContractError(
            "invalid_input_count",
            "analysis_mode 'multi' requires at least two inputs.",
        )
    if request.profile_name not in set(list_profile_names()):
        raise ContractError(
            "invalid_profile",
            f"Unknown profile: {request.profile_name!r}.",
        )


def _parse_model(raw: Any) -> ModelConfig:
    if raw is None:
        return ModelConfig(base_url=DEFAULT_OLLAMA_BASE_URL, name=DEFAULT_OLLAMA_MODEL)
    if not isinstance(raw, dict):
        raise ContractError("invalid_model", "model must be an object.")
    base_url = str(raw.get("base_url") or DEFAULT_OLLAMA_BASE_URL).strip()
    name = str(raw.get("name") or DEFAULT_OLLAMA_MODEL).strip()
    if not base_url or not name:
        raise ContractError("invalid_model", "model.base_url and model.name are required.")
    return ModelConfig(base_url=base_url, name=name)


def _parse_timeout(raw: Any) -> float:
    if raw is None:
        return DEFAULT_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise ContractError("invalid_timeout", "timeout_seconds must be a positive number.") from exc
    if not math.isfinite(value) or value <= 0 or value > MAX_TIMEOUT_SECONDS:
        raise ContractError(
            "invalid_timeout",
            "timeout_seconds must be a finite number greater than 0.",
        )
    return value


def _parse_inputs(raw: Any, *, analysis_mode: str) -> list[InputSource]:
    if not isinstance(raw, list) or not raw:
        expected = "exactly one input" if analysis_mode == "single" else "at least two inputs"
        raise ContractError("invalid_input_count", f"inputs must contain {expected}.")
    seen: set[str] = set()
    parsed: list[InputSource] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ContractError("invalid_input", f"inputs[{index}] must be an object.")
        source_id = str(item.get("source_id") or "").strip()
        if not source_id:
            raise ContractError("missing_source_id", f"inputs[{index}].source_id is required.")
        if source_id in seen:
            raise ContractError(
                "duplicate_source_id",
                f"Duplicate source_id: {source_id!r}.",
            )
        seen.add(source_id)
        path_raw = item.get("path")
        if not isinstance(path_raw, str) or not path_raw.strip():
            raise ContractError("missing_input_path", f"inputs[{index}].path is required.")
        path = Path(path_raw.strip())
        if not path.is_absolute():
            raise ContractError(
                "invalid_input_path",
                f"inputs[{index}].path must be an absolute path.",
            )
        suffix = path.suffix.lower()
        if suffix not in SUPPORTED_EXTENSIONS:
            raise ContractError(
                "unsupported_extension",
                f"Unsupported file extension: {suffix or path.name!r}.",
            )
        display_name = str(item.get("display_name") or path.name).strip() or path.name
        parsed.append(
            InputSource(
                source_id=source_id,
                path=str(path),
                sheet=_parse_sheet(item.get("sheet", 0), index=index),
                display_name=display_name,
            )
        )
    return parsed


def _parse_sheet(raw: Any, *, index: int) -> str | int:
    if raw is None:
        return 0
    if isinstance(raw, bool):
        raise ContractError("invalid_sheet", f"inputs[{index}].sheet is invalid.")
    if isinstance(raw, int):
        if raw < 0:
            raise ContractError("invalid_sheet", f"inputs[{index}].sheet must be >= 0.")
        return raw
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    raise ContractError("invalid_sheet", f"inputs[{index}].sheet is invalid.")


def _execute(request: AnalyzeRequest, *, started: float) -> AnalyzeResponse:
    dest_dir = request_output_dir(request.output_directory, request.request_id)
    loaded = [_load_input(item) for item in request.inputs]
    try:
        with use_profile(request.profile_name):
            if request.analysis_mode == "single":
                outcome = _run_single(request, loaded[0])
                route = "route_single_prompt"
            else:
                outcome = _run_multi(request, loaded)
                route = "route_multi_prompt"
        return _normalize_outcome(
            request,
            outcome,
            route=route,
            dest_dir=dest_dir,
            elapsed_ms=_elapsed_ms(started),
        )
    finally:
        _assert_inputs_unchanged(loaded)


def _load_input(source: InputSource) -> _LoadedInput:
    path = Path(source.path)
    if not path.is_file():
        raise ContractError(
            "missing_file",
            f"Input file not found: {path.name}.",
            stage="file_loading",
        )
    original_bytes = path.read_bytes()
    if is_csv_path(path):
        sheet_names = [CSV_SHEET_NAME]
        sheet_name: str | int = CSV_SHEET_NAME
        if isinstance(source.sheet, str) and source.sheet not in {CSV_SHEET_NAME, path.name}:
            raise ContractError(
                "invalid_sheet",
                f"CSV input {source.source_id!r} has no sheet named {source.sheet!r}.",
                stage="file_loading",
            )
        if isinstance(source.sheet, int) and source.sheet not in {0}:
            raise ContractError(
                "invalid_sheet",
                f"CSV input {source.source_id!r} only supports sheet 0.",
                stage="file_loading",
            )
        frame = load_tabular(path)
    elif is_excel_path(path):
        try:
            excel = pd.ExcelFile(path)
            sheet_names = [str(name) for name in excel.sheet_names]
        except Exception as exc:
            raise ContractError(
                "invalid_file",
                f"Could not read Excel workbook: {path.name}.",
                stage="file_loading",
            ) from exc
        if not sheet_names:
            raise ContractError(
                "invalid_sheet",
                f"Workbook {path.name} has no sheets.",
                stage="file_loading",
            )
        sheet_name = _resolve_sheet(source.sheet, sheet_names, source_id=source.source_id)
        try:
            frame = load_tabular(path, sheet_name=sheet_name)
        except Exception as exc:
            raise ContractError(
                "invalid_sheet",
                f"Could not load sheet {sheet_name!r} from {path.name}.",
                stage="file_loading",
            ) from exc
    else:
        raise ContractError(
            "unsupported_extension",
            f"Unsupported file extension: {path.suffix!r}.",
            stage="file_loading",
        )
    copied = frame.copy()
    return _LoadedInput(
        source=source,
        path=path,
        frame=copied,
        frame_snapshot=copied.copy(),
        sheet_name=sheet_name,
        sheet_names=sheet_names,
        label=source.display_name,
        original_bytes=original_bytes,
    )


def _resolve_sheet(
    requested: str | int,
    sheet_names: list[str],
    *,
    source_id: str,
) -> str:
    if isinstance(requested, int):
        if requested >= len(sheet_names):
            raise ContractError(
                "invalid_sheet",
                f"Sheet index {requested} is out of range for {source_id!r}.",
                stage="file_loading",
            )
        return sheet_names[requested]
    if requested in sheet_names:
        return requested
    raise ContractError(
        "invalid_sheet",
        f"Sheet {requested!r} was not found for {source_id!r}.",
        stage="file_loading",
    )


def _run_single(request: AnalyzeRequest, loaded: _LoadedInput) -> SingleRouteOutcome:
    frame = loaded.frame.copy()
    summary_text = None
    if is_summary_request(request.user_prompt):
        summary_text = build_file_summary(
            frame,
            file_name=loaded.source.display_name,
            sheet_name=str(loaded.sheet_name),
            sheet_names=list(loaded.sheet_names),
            file_path=loaded.path,
            profile_name=request.profile_name,
        )
    return route_single_prompt(
        request.user_prompt,
        full_df=frame,
        source_df=frame,
        context_label=None,
        base_url=request.model.base_url,
        model=request.model.name,
        profile_name=request.profile_name,
        prior_aggregate_df=None,
        prior_aggregate_prompt=None,
        prior_user_prompt=None,
        last_assistant_df=None,
        summary_text=summary_text,
    )


def _run_multi(request: AnalyzeRequest, loaded: list[_LoadedInput]) -> SingleRouteOutcome:
    labels = _unique_frame_labels(loaded)
    named_frames = [(label, item.frame.copy()) for label, item in zip(labels, loaded)]
    sheet_info = {
        label: {
            "current_sheet": item.sheet_name,
            "sheet_names": list(item.sheet_names),
            "path": str(item.path),
        }
        for label, item in zip(labels, loaded)
    }
    return route_multi_prompt(
        request.user_prompt,
        named_frames=named_frames,
        base_url=request.model.base_url,
        model=request.model.name,
        profile_name=request.profile_name,
        context_label=None,
        filter_df=None,
        sheet_info=sheet_info,
        unit_label="파일",
        request_id=request.request_id,
        case_id=None,
    )


def _unique_frame_labels(loaded: list[_LoadedInput]) -> list[str]:
    used: dict[str, int] = {}
    labels: list[str] = []
    for item in loaded:
        base = item.source.display_name or item.path.name
        count = used.get(base, 0)
        used[base] = count + 1
        if count == 0:
            labels.append(base)
        else:
            labels.append(f"{base} [{item.source.source_id}]")
    # If the first occurrence later collides conceptually, keep source_id stable
    # in metadata; labels only disambiguate router named_frames.
    if len(set(labels)) != len(labels):
        labels = [f"{item.source.display_name} [{item.source.source_id}]" for item in loaded]
    return labels


def _normalize_outcome(
    request: AnalyzeRequest,
    outcome: SingleRouteOutcome,
    *,
    route: str,
    dest_dir: Path,
    elapsed_ms: int,
) -> AnalyzeResponse:
    validation_failed = outcome.operation_name == "structured_integrate_failed"
    unsafe_blocked = _looks_like_unsafe_block(outcome.reply)
    warnings = _warnings_from_outcome(outcome)
    artifacts: list[Artifact] = []

    preview = dataframe_preview(outcome.dataframe, limit=PREVIEW_ROW_LIMIT)
    data = empty_result_data(route=route)
    data.update(preview)
    data["analysis_mode"] = request.analysis_mode
    if outcome.operation_name:
        data["operation_name"] = outcome.operation_name
    safe_meta = _allowlisted_meta(outcome.meta)
    data.update(safe_meta)

    if not validation_failed:
        artifacts.extend(
            _materialize_artifacts(outcome, dest_dir=dest_dir)
        )
    else:
        warnings.append(
            WarningItem(
                code="structural_validation_failed",
                message="Structural integration failed validation; workbook artifact was blocked.",
                stage="structural_validation",
            )
        )

    if artifacts:
        write_manifest(dest_dir, artifacts)

    safety = SafetyInfo(
        validation_status="failed" if validation_failed else "passed",
        unsafe_output_blocked=unsafe_blocked,
    )
    if validation_failed:
        return AnalyzeResponse(
            contract_version=CONTRACT_VERSION,
            request_id=request.request_id,
            status="validation_failed",
            text=str(outcome.reply or ""),
            data=data,
            artifacts=artifacts,
            warnings=warnings,
            safety=safety,
            timing=TimingInfo(elapsed_ms=elapsed_ms),
            error=ErrorInfo(
                code="structural_validation_failed",
                message=_public_error_message(
                    outcome.reply or "Structural integration validation failed."
                ),
                stage="structural_validation",
                retryable=False,
            ),
        )
    return AnalyzeResponse(
        contract_version=CONTRACT_VERSION,
        request_id=request.request_id,
        status="success",
        text=str(outcome.reply or ""),
        data=data,
        artifacts=artifacts,
        warnings=warnings,
        safety=safety,
        timing=TimingInfo(elapsed_ms=elapsed_ms),
    )


def _materialize_artifacts(
    outcome: SingleRouteOutcome,
    *,
    dest_dir: Path,
) -> list[Artifact]:
    artifacts: list[Artifact] = []
    meta = outcome.meta if isinstance(outcome.meta, dict) else {}
    validation_ok = outcome.operation_name != "structured_integrate_failed"

    workbook_bytes = meta.get("workbook_bytes")
    workbook_path = meta.get("workbook_path")
    if validation_ok and isinstance(workbook_bytes, (bytes, bytearray)) and workbook_bytes:
        artifacts.append(
            write_bytes_artifact(
                dest_dir,
                "integrated_result.xlsx",
                bytes(workbook_bytes),
                kind="workbook",
                artifact_id="integrated-workbook",
            )
        )
    elif validation_ok and workbook_path:
        copied = materialize_existing_file(
            dest_dir,
            workbook_path,
            filename="integrated_result.xlsx",
            kind="workbook",
            artifact_id="integrated-workbook",
        )
        if copied is not None:
            artifacts.append(copied)

    chart_path = meta.get("chart_path")
    if chart_path:
        copied = materialize_existing_file(
            dest_dir,
            chart_path,
            kind="chart",
            artifact_id="chart",
        )
        if copied is not None:
            artifacts.append(copied)

    if (
        isinstance(outcome.dataframe, pd.DataFrame)
        and not outcome.dataframe.empty
        and validation_ok
    ):
        artifacts.append(
            write_dataframe_xlsx(
                dest_dir,
                outcome.dataframe,
                filename="result.xlsx",
                artifact_id="result-table",
            )
        )
    return artifacts


def _allowlisted_meta(meta: dict | None) -> dict[str, Any]:
    if not isinstance(meta, dict):
        return {}
    out: dict[str, Any] = {}
    for key in SAFE_OUTCOME_META_KEYS:
        if key not in meta:
            continue
        value = meta[key]
        if key == "workbook_sheets" and value:
            out[key] = [str(item) for item in value]
        else:
            out[key] = json_safe(value)
    return out


def _warnings_from_outcome(outcome: SingleRouteOutcome) -> list[WarningItem]:
    warnings: list[WarningItem] = []
    meta = outcome.meta if isinstance(outcome.meta, dict) else {}
    note = meta.get("filter_note")
    if note:
        warnings.append(
            WarningItem(code="filter_note", message=str(note), stage="routing")
        )
    summary = meta.get("integrate_validation")
    if summary and outcome.operation_name == "structured_integrate_failed":
        warnings.append(
            WarningItem(
                code="integrate_validation",
                message=str(summary),
                stage="structural_validation",
            )
        )
    return warnings


def _looks_like_unsafe_block(reply: str | None) -> bool:
    text = str(reply or "").lower()
    return any(marker.lower() in text for marker in _UNSAFE_OUTPUT_MARKERS)


def _assert_inputs_unchanged(loaded: list[_LoadedInput]) -> None:
    for item in loaded:
        current = item.path.read_bytes()
        if current != item.original_bytes:
            raise RuntimeError(
                f"Headless analysis must not modify input file {item.path.name}."
            )
        if not item.frame.equals(item.frame_snapshot):
            raise RuntimeError(
                f"Headless analysis must not mutate source DataFrame {item.source.source_id}."
            )


def _peek_request_id(payload: AnalyzeRequest | dict[str, Any] | None) -> str:
    if isinstance(payload, AnalyzeRequest):
        return payload.request_id
    if isinstance(payload, dict):
        return str(payload.get("request_id") or "")
    return ""


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


def _public_error_message(message: str, *, limit: int = 500) -> str:
    text = str(message or "").strip()
    lowered = text.lower()
    if "traceback" in lowered:
        text = text.split("Traceback", 1)[0].strip() or "An analysis error occurred."
    text = text.replace("\x00", "")
    if len(text) > limit:
        return text[: limit - 1] + "…"
    return text


def _exception_response(
    *,
    request_id: str,
    exc: BaseException,
    elapsed_ms: int,
) -> AnalyzeResponse:
    status, code, stage, retryable = _classify_exception(exc)
    return error_response(
        request_id=request_id,
        status=status,
        code=code,
        message=_public_error_message(str(exc) or exc.__class__.__name__),
        stage=stage,
        retryable=retryable,
        elapsed_ms=elapsed_ms,
        safety=SafetyInfo(
            validation_status="not_applicable",
            unsafe_output_blocked=_looks_like_unsafe_block(str(exc)),
        ),
    )


def _classify_exception(exc: BaseException) -> tuple[str, str, str, bool]:
    if isinstance(exc, KeyboardInterrupt):
        return "cancelled", "cancelled", "execution", True
    try:
        import requests
    except ImportError:
        requests = None  # type: ignore[assignment]
    if requests is not None:
        if isinstance(exc, requests.exceptions.Timeout):
            return "timeout", "model_timeout", "model", True
        if isinstance(exc, requests.exceptions.ConnectionError):
            return "model_unavailable", "model_unavailable", "model", True
        if isinstance(exc, requests.exceptions.HTTPError):
            text = str(exc).lower()
            if "404" in text or "not found" in text:
                return "model_unavailable", "model_not_found", "model", True
    if isinstance(exc, TimeoutError):
        return "timeout", "timeout", "execution", True
    name = exc.__class__.__name__.lower()
    text = str(exc).lower()
    if "cancelled" in name:
        return "cancelled", "cancelled", "execution", True
    if "timeout" in name or "timed out" in text:
        return "timeout", "timeout", "execution", True
    if "connection refused" in text or "failed to establish" in text:
        return "model_unavailable", "model_unavailable", "model", True
    if "model" in text and ("not found" in text or "does not exist" in text):
        return "model_unavailable", "model_not_found", "model", True
    if "cannot_plan" in text:
        return "cannot_plan", "cannot_plan", "planning", False
    return "execution_failed", "execution_failed", "execution", True
