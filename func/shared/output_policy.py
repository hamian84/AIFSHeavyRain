"""활성 파이프라인 내부 호환 엔진의 출력정책 보조 함수입니다.

운영 산출물 모드는 ``operational``과 ``debug``만 사용합니다. 과거 ``audit``
값은 CLI 호환을 위해 ``debug``로 매핑하되 운영 디렉터리에 audit 산출물을
생성하지 않습니다.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from func.shared.pipeline_common import (
    add_output_mode_argument as _add_output_mode_argument,
    resolve_output_mode as _resolve_output_mode,
    sanitize_failure_text as _sanitize,
)


@dataclass(frozen=True)
class OutputPolicy:
    mode: str = "operational"

    def should_write(self, artifact_type: str) -> bool:
        if self.mode == "debug":
            return True
        return artifact_type not in {
            "provider_raw",
            "provider_metadata",
            "intermediate_parsed_json",
            "detailed_validation",
            "payload_diagnostics",
            "execution_snapshot",
        }

    def manifest_fields(self, artifact_types: Iterable[str]) -> dict[str, object]:
        return {
            "output_mode": self.mode,
            "generated_artifact_types": [
                item for item in artifact_types if self.should_write(str(item))
            ],
        }


def add_output_mode_argument(parser: argparse.ArgumentParser) -> None:
    _add_output_mode_argument(parser)


def resolve_output_mode(value: str | None) -> OutputPolicy:
    return OutputPolicy(_resolve_output_mode(value))


def sanitize_failure_text(value: object, *, project_root: str | None = None) -> str:
    return _sanitize(value, root=Path(project_root or ".").resolve())
