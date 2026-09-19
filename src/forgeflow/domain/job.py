from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from .artifact import Artifact

SCHEMA_VERSION = 3
STAGES = ("modeling", "blender", "rigging", "unity")
STATUSES = {"pending", "running", "awaiting_review", "completed", "failed", "cancelled"}


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
class RiggingRequest:
    input_path: str
    input_sha256: str
    output_directory: str
    version: int
    seed: int
    safe_name: str
    status: str = "pending"
    report_path: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    error: str | None = None
    preview_warning: str | None = None
    created_at: str = field(default_factory=utc_now)


@dataclass
class UnitySession:
    session_id: str
    project_path: str
    project_identity: dict[str, Any] | None = None
    model: str | None = None
    status: str = "starting"
    started_at: str = field(default_factory=utc_now)
    closed_at: str | None = None
    error: str | None = None


@dataclass
class UnityTurn:
    turn_id: str
    session_id: str
    user_text: str
    effective_prompt: str
    repair_existing: bool = False
    status: str = "running"
    assistant_text: str = ""
    started_at: str | None = None
    completed_at: str | None = None
    run_log_path: str | None = None
    jsonl_log_path: str | None = None
    receipt_path: str | None = None
    screenshot_paths: list[str] = field(default_factory=list)
    changed_assets: list[str] = field(default_factory=list)
    automated_status: str = "unavailable"
    requested_checks: list[Any] = field(default_factory=list)
    measured_checks: list[Any] = field(default_factory=list)
    skipped_checks: list[Any] = field(default_factory=list)
    unmapped_requirements: list[Any] = field(default_factory=list)
    human_review_status: str = "pending"
    human_review_note: str = ""
    human_reviewed_at: str | None = None
    error: str | None = None
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
    rigging_requests: list[RiggingRequest] = field(default_factory=list)
    rigging_input_path: str | None = None
    humanoid_fbx_path: str | None = None
    humanoid_blend_path: str | None = None
    unity_input_path: str | None = None
    unity_project_path: str | None = None
    unity_asset_path: str | None = None
    unity_sessions: list[UnitySession] = field(default_factory=list)
    unity_turns: list[UnityTurn] = field(default_factory=list)
    latest_unity_scene_path: str | None = None
    latest_unity_screenshot_path: str | None = None
    errors: list[dict[str, Any]] = field(default_factory=list)
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Job":
        schema_version = int(value.get("schema_version", 1))
        if schema_version > SCHEMA_VERSION:
            raise ValueError(
                f"지원하지 않는 미래 Job 스키마 버전입니다: {schema_version} (현재 {SCHEMA_VERSION})"
            )
        if schema_version < 1:
            raise ValueError(f"잘못된 Job 스키마 버전입니다: {schema_version}")
        stages = {name: StageState(**state) for name, state in value.get("stages", {}).items()}
        for name in STAGES:
            stages.setdefault(name, StageState())
        artifacts = [Artifact.from_dict(item) for item in value.get("artifacts", [])]
        requests = [BlenderRequest(**item) for item in value.get("blender_requests", [])]
        rigging_requests = [RiggingRequest(**item) for item in value.get("rigging_requests", [])]
        unity_sessions = [UnitySession(**item) for item in value.get("unity_sessions", [])]
        unity_turns = [UnityTurn(**item) for item in value.get("unity_turns", [])]
        return cls(
            schema_version=SCHEMA_VERSION,
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
            rigging_requests=rigging_requests,
            rigging_input_path=value.get("rigging_input_path"),
            humanoid_fbx_path=value.get("humanoid_fbx_path"),
            humanoid_blend_path=value.get("humanoid_blend_path"),
            unity_input_path=value.get("unity_input_path"),
            unity_project_path=value.get("unity_project_path"),
            unity_asset_path=value.get("unity_asset_path"),
            unity_sessions=unity_sessions,
            unity_turns=unity_turns,
            latest_unity_scene_path=value.get("latest_unity_scene_path"),
            latest_unity_screenshot_path=value.get("latest_unity_screenshot_path"),
            errors=value.get("errors", []),
        )

    @property
    def latest_blender_request(self) -> BlenderRequest | None:
        return self.blender_requests[-1] if self.blender_requests else None

    @property
    def latest_rigging_request(self) -> RiggingRequest | None:
        return self.rigging_requests[-1] if self.rigging_requests else None

    @property
    def latest_unity_session(self) -> UnitySession | None:
        return self.unity_sessions[-1] if self.unity_sessions else None

    @property
    def latest_unity_turn(self) -> UnityTurn | None:
        return self.unity_turns[-1] if self.unity_turns else None
