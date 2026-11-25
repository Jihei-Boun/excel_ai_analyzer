"""작업용 프로세스를 찍어내는 서버(forkserver)에서만 읽는 모듈.

numpy·pandas는 일부 모듈을 처음 쓰는 순간에 파일에서 읽는다 (numpy.rec, pandas의
pivot_table이 쓰는 reshape 모듈 등). 작업용 프로세스는 코드를 돌리기 전에 새 파일 열기를
막으므로, 그때 읽으려 하면 정상 계산도 실패한다. 그래서 서버가 뜰 때 두 패키지의 하위
모듈을 모두 미리 읽어 둔다. 복제된 프로세스는 이미 읽힌 상태로 시작한다.

빼는 것: 읽기만 해도 프로그램이 실행되는 모듈(__main__), 시험 코드, 컴파일 도구.
"""

from __future__ import annotations

import importlib
import pkgutil
import warnings

import numpy
import pandas

_SKIP_PARTS = {"__main__", "tests", "conftest", "f2py", "distutils", "_pyinstaller"}


def _import_all(package) -> None:
    for info in pkgutil.walk_packages(package.__path__, package.__name__ + ".", onerror=lambda _name: None):
        if _SKIP_PARTS & set(info.name.split(".")):
            continue
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                importlib.import_module(info.name)
        except Exception:  # noqa: BLE001 — 선택 의존성이 없는 모듈은 건너뛴다
            pass


for _package in (numpy, pandas):
    _import_all(_package)
