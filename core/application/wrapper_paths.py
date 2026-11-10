"""실제 경로 기준의 허용 루트 검사와 입력 사본."""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from core.application.wrapper_config import OperatorConfig


class PathRejected(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class PreparedInput:
    source: Path
    copy_path: Path
    display_name: str
    sheet: str | int
    source_sha256: str
    copy_sha256: str
    inode: tuple[int, int]


def real_path(path: Path) -> Path:
    return Path(os.path.realpath(path))


def is_inside(path: Path, root: Path) -> bool:
    try:
        real_path(path).relative_to(real_path(root))
    except ValueError:
        return False
    return True


def _regular_file(path: Path) -> os.stat_result:
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise PathRejected("missing_file", "입력을 읽을 수 없다.") from exc
    if stat.S_ISLNK(info.st_mode):
        raise PathRejected("symlink_rejected", "심볼릭 링크는 입력으로 허용하지 않는다.")
    if not stat.S_ISREG(info.st_mode):
        raise PathRejected("not_a_file", "일반 파일만 입력으로 허용한다.")
    return info


def resolve_input(path: Path, roots: tuple[Path, ...], *, extensions: tuple[str, ...]) -> Path:
    info = _regular_file(path)
    if path.suffix.lower() not in extensions:
        raise PathRejected("unsupported_type", "허용되지 않은 파일 형식이다.")
    real = real_path(path)
    if not any(is_inside(real, root) for root in roots):
        raise PathRejected("outside_root", "입력이 허용 루트 밖에 있다.")
    if not stat.S_ISREG(real.stat().st_mode):
        raise PathRejected("not_a_file", "일반 파일만 입력으로 허용한다.")
    if info.st_size < 0:
        raise PathRejected("invalid_file", "입력 크기를 확인하지 못했다.")
    return real


def assert_size(info_size: int, *, max_file_bytes: int) -> None:
    if info_size > max_file_bytes:
        raise PathRejected("file_too_large", "입력 파일이 크기 한도를 넘는다.")


def ensure_directory(path: Path, root: Path) -> Path:
    parent = real_path(path.parent)
    if not is_inside(parent, root) and parent != real_path(root):
        raise PathRejected("outside_root", "출력 부모가 허용 루트 밖에 있다.")
    path.mkdir(parents=True, exist_ok=True)
    created = real_path(path)
    if not is_inside(created, root) and created != real_path(root):
        raise PathRejected("outside_root", "생성한 출력 경로가 허용 루트 밖에 있다.")
    if not created.is_dir():
        raise PathRejected("not_a_directory", "출력 경로가 디렉터리가 아니다.")
    return created


def copy_regular_file(source: Path, dest: Path) -> tuple[str, tuple[int, int]]:
    """검사 직후 O_NOFOLLOW로 연다. inode가 바뀌면 복사하지 않는다."""
    before = _regular_file(source)
    flags = os.O_RDONLY | os.O_NOFOLLOW
    fd = os.open(source, flags)
    try:
        current = os.fstat(fd)
        if (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino):
            raise PathRejected("path_changed", "검사 뒤에 입력 경로가 바뀌었다.")
        if not stat.S_ISREG(current.st_mode):
            raise PathRejected("not_a_file", "일반 파일만 입력으로 허용한다.")
        with os.fdopen(fd, "rb") as handle:
            fd = -1
            payload = handle.read()
    finally:
        if fd >= 0:
            os.close(fd)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(payload)
    os.chmod(dest, 0o444)
    digest = hashlib.sha256(payload).hexdigest()
    return digest, (before.st_dev, before.st_ino)


def sha256_file(path: Path) -> str | None:
    try:
        if not path.is_file():
            return None
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def same_inode(left: Path, right: Path) -> bool:
    try:
        a = os.stat(left)
        b = os.stat(right)
    except OSError:
        return False
    return (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)


def conflicts_with_inputs(dest: Path, inputs: list[PreparedInput]) -> bool:
    dest_real = real_path(dest)
    for item in inputs:
        if dest_real == item.source or same_inode(dest_real, item.source):
            return True
    return False


def locate_output_file(path: Path, root: Path) -> tuple[Path | None, str]:
    try:
        candidate = Path(path)
    except TypeError:
        return None, "invalid_artifact"
    if not candidate.is_absolute():
        return None, "artifact_outside_workspace"
    try:
        real = real_path(candidate)
    except OSError:
        return None, "artifact_missing"
    if not is_inside(real, root):
        return None, "artifact_outside_workspace"
    try:
        info = os.lstat(real)
    except OSError:
        return None, "artifact_missing"
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        return None, "artifact_not_file"
    return real, ""


def config_roots_ready(config: OperatorConfig) -> None:
    for root in (config.workspace_root, config.publish_root, *config.allowed_input_roots):
        if not root.is_dir():
            raise PathRejected("outside_root", "허용 루트를 사용할 수 없다.")
