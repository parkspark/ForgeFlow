from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from forgeflow.domain.artifact import Artifact
from forgeflow.domain.job import BlenderRequest, Job, STATUSES, utc_now


ALLOWED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class JobService:
    def __init__(self, root: Path):
        self.root = root.expanduser().resolve(strict=False)
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def validate_image(path: str | Path) -> Path:
        source = Path(path).expanduser().resolve(strict=True)
        if not source.is_file():
            raise ValueError("선택한 이미지가 파일이 아닙니다.")
        if source.suffix.lower() not in ALLOWED_IMAGE_EXTENSIONS:
            raise ValueError("PNG, JPG, JPEG 이미지만 사용할 수 있습니다.")
        if source.stat().st_size <= 0:
            raise ValueError("입력 이미지가 비어 있습니다.")
        return source

    def job_directory(self, job_id: str) -> Path:
        if not job_id or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for character in job_id):
            raise ValueError("잘못된 작업 ID입니다.")
        path = (self.root / job_id).resolve(strict=False)
        if path.parent != self.root:
            raise ValueError("작업 경로가 저장 루트를 벗어났습니다.")
        return path

    def create(self, name: str, image_path: str | Path, settings: dict[str, object] | None = None) -> Job:
        source = self.validate_image(image_path)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        job_id = f"job-{timestamp}-{uuid4().hex[:8]}"
        directory = self.job_directory(job_id)
        for child in ("input", "modeling", "blender", "logs", ".runs"):
            (directory / child).mkdir(parents=True, exist_ok=False)
        copied = directory / "input" / f"reference{source.suffix.lower()}"
        shutil.copy2(source, copied)
        now = utc_now()
        job = Job(
            job_id=job_id,
            name=name.strip() or source.stem,
            created_at=now,
            updated_at=now,
            input_image_path=str(copied.resolve()),
        )
        if settings:
            job.generation_settings.update(settings)
        self.save(job)
        return job

    def save(self, job: Job) -> Path:
        directory = self.job_directory(job.job_id)
        directory.mkdir(parents=True, exist_ok=True)
        job.updated_at = utc_now()
        destination = directory / "job.json"
        temporary = destination.with_suffix(".json.tmp")
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(job.to_dict(), handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        return destination

    def load(self, job_id: str) -> Job:
        path = self.job_directory(job_id) / "job.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        job = Job.from_dict(payload)
        if job.job_id != job_id:
            raise ValueError("job.json의 작업 ID가 폴더와 일치하지 않습니다.")
        return job

    def list_jobs(self) -> list[Job]:
        jobs: list[Job] = []
        for path in self.root.glob("*/job.json"):
            try:
                jobs.append(self.load(path.parent.name))
            except (OSError, ValueError, KeyError, json.JSONDecodeError, TypeError):
                continue
        return sorted(jobs, key=lambda item: item.updated_at, reverse=True)

    def recover_interrupted(self) -> list[Job]:
        recovered: list[Job] = []
        for job in self.list_jobs():
            changed = False
            for name, stage in job.stages.items():
                if stage.status == "running":
                    stage.status = "failed"
                    stage.error = "앱 종료로 실행이 중단되었습니다. 재시도할 수 있습니다."
                    stage.completed_at = utc_now()
                    job.errors.append({"stage": name, "message": stage.error, "at": utc_now()})
                    changed = True
            if changed:
                self.save(job)
            recovered.append(job)
        return recovered

    def set_stage(self, job: Job, stage_name: str, status: str, error: str | None = None, log_path: Path | None = None) -> None:
        if stage_name not in job.stages or status not in STATUSES:
            raise ValueError("잘못된 단계 또는 상태입니다.")
        stage = job.stages[stage_name]
        stage.status = status
        if status == "running":
            stage.started_at = utc_now()
            stage.completed_at = None
            stage.attempts += 1
            stage.error = None
        if status in {"completed", "failed", "cancelled"}:
            stage.completed_at = utc_now()
        if error:
            stage.error = error
            job.errors.append({"stage": stage_name, "message": error, "at": utc_now()})
        if log_path:
            stage.log_path = str(log_path.resolve())
        job.current_stage = stage_name
        self.save(job)

    def add_artifact(self, job: Job, artifact: Artifact) -> None:
        resolved = str(Path(artifact.path).resolve(strict=False))
        if any(Path(item.path).resolve(strict=False) == Path(resolved) for item in job.artifacts):
            return
        artifact.path = resolved
        job.artifacts.append(artifact)
        self.save(job)

    def next_blender_version(self, job: Job) -> int:
        used = {item.version for item in job.artifacts if item.stage == "blender" and item.version}
        used.update(request.version for request in job.blender_requests)
        return max(used, default=0) + 1

    def add_blender_request(self, job: Job, request: BlenderRequest) -> None:
        job.blender_requests.append(request)
        self.save(job)

