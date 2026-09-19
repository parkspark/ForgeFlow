"""Unity project validation and versioned Humanoid FBX imports."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from forgeflow.domain.job import Job
from forgeflow.services.job_service import sha256_file

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
    source_hash = sha256_file(source)
    relative_root = Path("Assets") / "ForgeFlow" / safe_job_id(job.job_id) / "Models"
    absolute_root = project / relative_root
    absolute_root.mkdir(parents=True, exist_ok=True)
    version = 1
    while True:
        relative = relative_root / f"v{version:03d}" / "Character_humanoid.fbx"
        destination = project / relative
        if not destination.exists():
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
