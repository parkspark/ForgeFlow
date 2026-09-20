"""Unity project validation and versioned Humanoid FBX imports."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from forgeflow.domain.job import Job
from forgeflow.services.job_service import sha256_file
from forgeflow.services.path_utils import same_path

UNITY_PROJECT_MARKERS = ("Assets", "ProjectSettings")


@dataclass(frozen=True)
class UnityImportResult:
    source_path: str
    source_sha256: str
    asset_path: str
    absolute_path: str
    version: int


def safe_job_id(value: str) -> str:
    safe = "".join(char if char.isalnum() or char in "-_" else "_" for char in value)
    return safe.strip("_-") or "job"


def validate_project(project_path: str | Path) -> Path:
    project = Path(project_path).expanduser().resolve(strict=True)
    if not project.is_dir():
        raise ValueError(f"Unity 프로젝트 폴더가 아닙니다: {project}")
    missing = [name for name in UNITY_PROJECT_MARKERS if not (project / name).is_dir()]
    if missing:
        raise ValueError(f"Unity 프로젝트가 아닙니다({', '.join(missing)} 폴더 없음): {project}")
    return project


def copy_humanoid_fbx(job: Job, project: Path) -> UnityImportResult:
    """Copy the selected FBX into a new version in a validated Unity project."""
    if not job.unity_input_path:
        raise ValueError("현재 Job에 4단계 Humanoid FBX가 없습니다.")
    source = Path(job.unity_input_path).expanduser().resolve(strict=True)
    if not source.is_file() or source.suffix.lower() != ".fbx":
        raise ValueError(f"유효한 Humanoid FBX가 아닙니다: {source}")
    if source.stat().st_size == 0:
        raise ValueError("리깅 FBX가 비어 있습니다. 리깅 결과를 다시 확인하세요.")
    source_hash = sha256_file(source)
    registered = next((item for item in job.artifacts if same_path(item.path, source)), None)
    if registered and registered.sha256 and registered.sha256 != source_hash:
        raise ValueError("등록된 FBX의 내용이 변경되었습니다. 새 버전으로 생성해 주세요.")
    relative_root = Path("Assets") / "ForgeFlow" / safe_job_id(job.job_id) / "Models"
    absolute_root = project / relative_root
    absolute_root.mkdir(parents=True, exist_ok=True)
    version = 1
    while True:
        relative = relative_root / f"v{version:03d}" / "Character_humanoid.fbx"
        destination = project / relative
        if not destination.parent.exists():
            break
        version += 1
    destination.parent.mkdir(parents=True, exist_ok=False)
    shutil.copy2(source, destination)
    if sha256_file(destination) != source_hash:
        raise RuntimeError("Unity 프로젝트로 복사한 FBX의 SHA-256이 원본과 다릅니다.")
    return UnityImportResult(
        source_path=str(source),
        source_sha256=source_hash,
        asset_path=relative.as_posix(),
        absolute_path=str(destination.resolve()),
        version=version,
    )


def validate_import_selection(job: Job, project: Path) -> None:
    """Validate legacy imports too: compare actual bytes with the current source."""
    if not job.unity_asset_path or not job.unity_input_path:
        raise ValueError("현재 결과를 Unity 프로젝트로 먼저 가져오세요.")
    if job.unity_import_project_path and not same_path(job.unity_import_project_path, project):
        raise ValueError("다른 Unity 프로젝트의 결과입니다. 선택한 프로젝트로 다시 가져오세요.")
    relative = Path(job.unity_asset_path)
    imported = (project / relative).resolve()
    if relative.is_absolute() or not imported.is_relative_to((project / "Assets").resolve()):
        raise ValueError("Unity Asset 경로가 프로젝트 Assets 폴더를 벗어났습니다.")
    source = Path(job.unity_input_path).resolve()
    if not source.is_file() or not imported.is_file():
        raise ValueError("현재 FBX 또는 가져온 Asset이 없습니다. 다시 가져오세요.")
    digest = sha256_file(source)
    if source.stat().st_size == 0 or sha256_file(imported) != digest:
        raise ValueError("가져온 Asset과 현재 리깅 결과가 다릅니다. 다시 가져오세요.")
    if job.unity_import_source_sha256 and digest != job.unity_import_source_sha256:
        raise ValueError("가져온 뒤 원본 FBX가 변경되었습니다. 다시 가져오세요.")
    if job.unity_import_source_path and not same_path(job.unity_import_source_path, source):
        raise ValueError("선택한 FBX 버전이 바뀌었습니다. 다시 가져오세요.")
