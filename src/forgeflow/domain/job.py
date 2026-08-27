from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from .artifact import Artifact


SCHEMA_VERSION = 1
STAGES = ("modeling", "blender")
STATUSES = {"pending", "running", "completed", "failed", "cancelled"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class StageState:
    status: str = "pending"
    started_at: str | None = None
    completed_at: str | None = None
    attempts: int = 0
    error: str | None = None
    log_path: str | None = None


@dataclass
class BlenderRequest:
    request: str
    input_path: str
    output_directory: str
    version: int
    status: str = "planning"
    plan: dict[str, Any] | None = None
    plan_sha256: str | None = None
    proposal_path: str | None = None
    session_path: str | None = None
    approved_at: str | None = None
    created_at: str = field(default_factory=utc_now)


@dataclass
class Job:
    job_id: str
    name: str
    created_at: str
    updated_at: str
    input_image_path: str
    current_stage: str = "modeling"
    stages: dict[str, StageState] = field(
        default_factory=lambda: {stage: StageState() for stage in STAGES}
    )
    generation_settings: dict[str, Any] = field(
        default_factory=lambda: {"model_id": "pixal3d-1024", "seed": 42}
    )
    artifacts: list[Artifact] = field(default_factory=list)
    blender_input_path: str | None = None
    blender_requests: list[BlenderRequest] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Job":
        stages = {
            name: StageState(**state)
            for name, state in value.get("stages", {}).items()
        }
        for name in STAGES:
            stages.setdefault(name, StageState())
        artifacts = [Artifact.from_dict(item) for item in value.get("artifacts", [])]
        requests = [BlenderRequest(**item) for item in value.get("blender_requests", [])]
        return cls(
            schema_version=int(value.get("schema_version", SCHEMA_VERSION)),
            job_id=value["job_id"],
            name=value["name"],
            created_at=value["created_at"],
            updated_at=value["updated_at"],
            input_image_path=value["input_image_path"],
            current_stage=value.get("current_stage", "modeling"),
            stages=stages,
            generation_settings=value.get("generation_settings", {}),
            artifacts=artifacts,
            blender_input_path=value.get("blender_input_path"),
            blender_requests=requests,
            errors=value.get("errors", []),
        )

    @property
    def latest_blender_request(self) -> BlenderRequest | None:
        return self.blender_requests[-1] if self.blender_requests else None

