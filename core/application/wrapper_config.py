"""운영 설정. 요청 JSON은 이 값들을 고치지 못한다."""

from __future__ import annotations

import hashlib
import json
import math
import os
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ConfigError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class OperatorConfig:
    python_path: Path
    repo_cwd: Path
    allowed_input_roots: tuple[Path, ...]
    workspace_root: Path
    publish_root: Path
    model_base_url: str
    model_name: str
    allowed_profiles: tuple[str, ...]
    max_input_files: int
    max_file_bytes: int
    max_total_bytes: int
    max_request_bytes: int
    max_timeout_seconds: float
    default_timeout_seconds: float
    grace_seconds: float
    lock_wait_seconds: float
    max_stdout_bytes: int
    max_stderr_bytes: int
    allowed_extensions: tuple[str, ...]
    source_path: Path


def _real_dir(path: Path, *, label: str, create: bool = False) -> Path:
    raw = Path(path)
    if create and not raw.exists():
        parent = Path(os.path.realpath(raw.parent))
        if not parent.is_dir():
            raise ConfigError("invalid_config", f"{label}의 부모가 디렉터리가 아니다.")
        raw.mkdir(parents=False, exist_ok=False)
    if not raw.exists():
        raise ConfigError("invalid_config", f"{label}가 없다.")
    real = Path(os.path.realpath(raw))
    if not real.is_dir():
        raise ConfigError("invalid_config", f"{label}가 디렉터리가 아니다.")
    if create:
        created = Path(os.path.realpath(raw))
        if created != real:
            raise ConfigError("invalid_config", f"{label} 생성 결과가 허용 경로 밖이다.")
    return real


_MISSING = object()


def _present(payload: dict[str, Any], key: str) -> Any:
    if key not in payload:
        return _MISSING
    return payload[key]


def _positive_int(payload: dict[str, Any], key: str, default: int) -> int:
    """생략한 키만 기본값을 쓴다. 0이나 bool은 기본값으로 바꾸지 않는다."""
    value = _present(payload, key)
    if value is _MISSING:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConfigError("invalid_config", f"{key}는 양의 정수여야 한다.")
    return value


def _finite_number(value: Any, key: str, *, minimum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError("invalid_config", f"{key}는 숫자여야 한다.")
    try:
        number = float(value)
    except (OverflowError, ValueError, ArithmeticError) as exc:
        raise ConfigError("invalid_config", f"{key}를 유한한 실수로 바꾸지 못했다.") from exc
    if not math.isfinite(number) or number < minimum:
        raise ConfigError("invalid_config", f"{key}는 {minimum:g} 이상의 유한한 값이어야 한다.")
    return number


def _timeout_setting(payload: dict[str, Any], key: str, default: float | None) -> float:
    value = _present(payload, key)
    if value is _MISSING:
        if default is None:
            raise ConfigError("invalid_config", f"{key}가 필요하다.")
        return default
    return _finite_number(value, key, minimum=1e-9)


def _optional_non_negative(payload: dict[str, Any], key: str, default: float) -> float:
    """유예 시간과 잠금 대기는 0을 허용한다. 음수와 비유한 값은 거절한다."""
    value = _present(payload, key)
    if value is _MISSING:
        return default
    return _finite_number(value, key, minimum=0)


def _default_timeout(payload: dict[str, Any], max_timeout: float) -> float:
    value = _present(payload, "default_timeout_seconds")
    if value is _MISSING:
        return min(30.0, max_timeout)
    chosen = _finite_number(value, "default_timeout_seconds", minimum=1e-9)
    if chosen > max_timeout:
        raise ConfigError("invalid_config", "기본 timeout이 최대 timeout을 넘는다.")
    return chosen


def load_operator_config(path: str | Path) -> OperatorConfig:
    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError("invalid_config", "운영 설정을 읽지 못했다.") from exc
    if not isinstance(payload, dict):
        raise ConfigError("invalid_config", "운영 설정은 객체여야 한다.")

    python_raw = Path(str(payload.get("python_path") or "")).expanduser()
    # venv 실행 파일은 심볼릭 링크다. realpath로 풀면 패키지가 없는 시스템 Python이 된다.
    try:
        python_info = os.lstat(python_raw)
    except OSError as exc:
        raise ConfigError("invalid_config", "python_path를 확인하지 못했다.") from exc
    if not stat.S_ISREG(python_info.st_mode) and not stat.S_ISLNK(python_info.st_mode):
        raise ConfigError("invalid_config", "python_path가 실행 파일이 아니다.")
    if not python_raw.is_file():
        raise ConfigError("invalid_config", "python_path가 일반 파일이 아니다.")
    python_path = python_raw if python_raw.is_absolute() else Path.cwd() / python_raw
    repo_cwd = _real_dir(Path(str(payload.get("repo_cwd") or "")), label="repo_cwd")
    roots = payload.get("allowed_input_roots")
    if not isinstance(roots, list) or not roots:
        raise ConfigError("invalid_config", "allowed_input_roots가 필요하다.")
    allowed = tuple(_real_dir(Path(str(item)), label="allowed_input_roots") for item in roots)
    workspace = _real_dir(
        Path(str(payload.get("workspace_root") or "")),
        label="workspace_root",
        create=True,
    )
    publish = _real_dir(
        Path(str(payload.get("publish_root") or "")),
        label="publish_root",
        create=True,
    )
    model_url = str(payload.get("model_base_url") or "").strip()
    model_name = str(payload.get("model_name") or "").strip()
    if not model_url.startswith(("http://", "https://")) or any(ch.isspace() for ch in model_url):
        raise ConfigError("invalid_config", "model_base_url은 http(s) 주소여야 한다.")
    if not model_name or any(ch.isspace() for ch in model_name):
        raise ConfigError("invalid_config", "model_name이 필요하다.")
    profiles = payload.get("allowed_profiles") or ["generic"]
    if not isinstance(profiles, list) or not all(isinstance(item, str) and item for item in profiles):
        raise ConfigError("invalid_config", "allowed_profiles가 필요하다.")
    extensions = payload.get("allowed_extensions") or [".xlsx", ".xls", ".xlsm", ".csv"]
    if not isinstance(extensions, list):
        raise ConfigError("invalid_config", "allowed_extensions가 필요하다.")
    max_timeout = _timeout_setting(payload, "max_timeout_seconds", 180)
    return OperatorConfig(
        python_path=python_path,
        repo_cwd=repo_cwd,
        allowed_input_roots=allowed,
        workspace_root=workspace,
        publish_root=publish,
        model_base_url=model_url,
        model_name=model_name,
        allowed_profiles=tuple(profiles),
        max_input_files=_positive_int(payload, "max_input_files", 8),
        max_file_bytes=_positive_int(payload, "max_file_bytes", 20_000_000),
        max_total_bytes=_positive_int(payload, "max_total_bytes", 50_000_000),
        max_request_bytes=_positive_int(payload, "max_request_bytes", 1_000_000),
        max_timeout_seconds=max_timeout,
        default_timeout_seconds=_default_timeout(payload, max_timeout),
        grace_seconds=_optional_non_negative(payload, "grace_seconds", 2),
        lock_wait_seconds=_optional_non_negative(payload, "lock_wait_seconds", 5),
        max_stdout_bytes=_positive_int(payload, "max_stdout_bytes", 2_000_000),
        max_stderr_bytes=_positive_int(payload, "max_stderr_bytes", 2_000_000),
        allowed_extensions=tuple(str(item).lower() for item in extensions),
        source_path=Path(os.path.realpath(source)),
    )


def _git(repo: Path, args: list[str]) -> str | None:
    try:
        done = subprocess.run(
            ["git", *args],
            cwd=str(repo),
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    if done.returncode != 0:
        return None
    return done.stdout


def source_identity(repo: Path) -> dict[str, Any]:
    """HEAD와 작업 트리 바이트만 기록한다. 없는 값은 null이다."""
    head = _git(repo, ["rev-parse", "HEAD"])
    diff = _git(repo, ["diff", "HEAD"])
    untracked = _git(repo, ["ls-files", "--others", "--exclude-standard"])
    files: list[dict[str, str]] = []
    names = [
        "core/application/wrapper.py",
        "core/application/wrapper_config.py",
        "core/application/wrapper_paths.py",
        "core/application/wrapper_run.py",
        "core/constants.py",
        "core/io/export_utils.py",
        "core/integrate/integrate_pipeline.py",
        "core/display/chart_utils.py",
        "core/pai/pandasai_setup.py",
    ]
    if untracked:
        for line in untracked.splitlines():
            if line.endswith((".py", ".md")) and line.startswith(("core/", "tests/", "docs/development/dev2")):
                names.append(line)
    seen: set[str] = set()
    for name in names:
        if name in seen:
            continue
        seen.add(name)
        path = repo / name
        if not path.is_file():
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        files.append({"path": name, "sha256": digest})
    return {
        "git_head": None if head is None else head.strip(),
        "worktree_dirty": bool((diff or "").strip() or (untracked or "").strip()),
        "diff_sha256": None if diff is None else hashlib.sha256(diff.encode("utf-8")).hexdigest(),
        "source_files": files,
        "source": "direct" if head is not None else "unknown",
    }
