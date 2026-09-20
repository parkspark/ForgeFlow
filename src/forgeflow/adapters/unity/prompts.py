"""ForgeFlow context and human-review policy included in Unity prompts."""

from __future__ import annotations

import json
from pathlib import Path

from forgeflow.domain.job import Job
from forgeflow.services.path_utils import same_path

from .project import safe_job_id, validate_import_selection


def rig_status(job: Job) -> str:
    lineage: list[str] = []
    current = job.unity_input_path
    while current and current not in lineage:
        lineage.append(current)
        artifact = next((item for item in job.artifacts if same_path(item.path, current)), None)
        current = artifact.parent_path if artifact else None
    for request in reversed(job.rigging_requests):
        if not request.report_path:
            continue
        try:
            payload = json.loads(Path(request.report_path).read_text(encoding="utf-8"))
            if any(
                same_path(str(payload.get(key) or ""), path)
                for key in ("fbx", "blend")
                for path in lineage
            ):
                return str(payload.get("status") or "unknown")
        except (OSError, json.JSONDecodeError):
            continue
    return "unknown"


def build_effective_prompt(
    job: Job,
    project: Path,
    user_text: str,
    *,
    include_asset: bool = False,
    include_current_scene: bool = True,
    human_review_feedback: str | None = None,
) -> str:
    """Build a prompt using an already validated project path."""
    assets = "(none selected)"
    if include_asset:
        validate_import_selection(job, project)
        assets = (
            f"- Selected Humanoid FBX (exact required path): {job.unity_asset_path}\n"
            f"- Rig report: {rig_status(job)}\n"
            "- Use this exact selected FBX. Do not search for or substitute another version."
            "\n- The FBX filename and rig report do not establish Unity Avatar readiness. "
            "For Humanoid animation, use unity_configure_humanoid and read back "
            "humanoidReady, avatar.isValid and avatar.isHuman before connecting animation. "
            "Report failed mapping explicitly instead of claiming the character is ready. "
            "Use dedicated animation tools to enumerate stable clip identities, save the "
            "controller and inspect playback; static screenshots alone do not verify animation."
            "\n- Place the FBX in EDIT mode with unity_instantiate_prefab when available. "
            "For a new test scene, first use unity_create_scene with template=basic "
            f"under Assets/ForgeFlow/{safe_job_id(job.job_id)}/, then place the selected FBX. "
            "When unity_frame_character is supported, call it with target=<instance path>, "
            f"output_root=Assets/ForgeFlow/{safe_job_id(job.job_id)}/ and framing_ratio=0.68. "
            "It backs up camera/light settings, fits all Renderer bounds using FOV/aspect "
            "and points the camera toward the character from its front. Save the scene "
            "after fitting and inspect fullyInViewport, framingRatio and any limitation. "
            "If width prevents 60–75% image height, report the width limitation explicitly. "
            "Capture both Edit and Play screenshots to compare framing. "
            "Auto-fit is only allowed in scenes newly created by the Bridge; preserve "
            "existing user scenes. If the tool is unavailable, report the Bridge upgrade "
            "requirement. Do not create runtime placement scripts or a C# type named Model."
        )
    scene = job.latest_unity_scene_path if include_current_scene else None
    parts = [
        "[ForgeFlow Context]",
        "Unity project:",
        str(project),
        "",
        "Current active ForgeFlow job:",
        f"{job.job_id} — {job.name}",
        "",
        "Available imported assets:",
        assets,
        "",
        "Known latest scene:",
        scene or "(none selected)",
        "",
        "Safety:",
        "- Work only in the selected Unity project.",
        "- Preserve existing user scenes and assets unless the user explicitly asks to modify them.",
        f"- Prefer Assets/ForgeFlow/{safe_job_id(job.job_id)}/ for newly created assets.",
        "- Release simulated input and stop Play Mode after verification.",
        "- Report created and modified asset paths.",
        "- Do not claim subjective dynamic quality as automatically verified.",
    ]
    if include_asset:
        parts.append(
            f"- For this selected ForgeFlow asset request, create new assets under "
            f"Assets/ForgeFlow/{safe_job_id(job.job_id)}/ unless the user names another path."
        )
    if human_review_feedback:
        parts.extend(["", "[Human Review Feedback]", human_review_feedback.strip()])
    parts.extend(
        [
            "",
            "[User Request]",
            user_text,
            "",
            "[Human Review Policy]",
            "Dynamic motion, animation quality, controls, camera feel, timing, and visual polish will be reviewed by a human. Perform the requested implementation and available objective checks, then leave clear instructions for human Play Mode review.",
        ]
    )
    return "\n".join(parts)
