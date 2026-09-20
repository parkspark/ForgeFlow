"""Keep historical results while invalidating selections derived from old inputs."""

from __future__ import annotations

from pathlib import Path

from forgeflow.domain.job import Job

from .path_utils import same_path


def invalidate_unity(job: Job, reason: str, *, clear_input: bool = False) -> None:
    job.lineage_revision += 1
    state = job.stages["unity"]
    state.status = "stale"
    state.stale_reason = reason
    state.error = None
    job.unity_asset_path = None
    job.unity_import_source_path = None
    job.unity_import_source_sha256 = None
    job.unity_import_project_path = None
    job.latest_unity_scene_path = None
    job.latest_unity_screenshot_path = None
    if clear_input:
        job.unity_input_path = None


def invalidate_rigging(job: Job, reason: str) -> None:
    state = job.stages["rigging"]
    if state.status != "pending" or job.humanoid_fbx_path:
        state.status = "stale"
        state.stale_reason = reason
        state.error = None
    job.humanoid_fbx_path = None
    job.humanoid_blend_path = None
    invalidate_unity(job, reason, clear_input=True)


def is_rigged_input(job: Job, path: str | Path) -> bool:
    """Follow registered parents rather than assuming every BLEND contains a rig."""
    seen: set[str] = set()
    current = str(path)
    while current and current not in seen:
        seen.add(current)
        artifact = next((item for item in job.artifacts if same_path(item.path, current)), None)
        if artifact is None:
            return False
        if artifact.stage == "rigging" and artifact.kind in {
            "humanoid_blend",
            "humanoid_fbx",
            "unity_blend",
            "unity_fbx",
            "rigged_glb",
            "skin_fbx",
        }:
            return True
        current = artifact.parent_path or ""
    return False
