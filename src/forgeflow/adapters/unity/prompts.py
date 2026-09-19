"""ForgeFlow context and human-review policy included in Unity prompts."""

from __future__ import annotations

import json
from pathlib import Path

from forgeflow.domain.job import Job

from .project import safe_job_id


def rig_status(job: Job) -> str:
    request = job.latest_rigging_request
    if request and request.report_path:
        try:
            payload = json.loads(Path(request.report_path).read_text(encoding="utf-8"))
            return str(payload.get("status") or "unknown")
        except (OSError, json.JSONDecodeError):
            pass
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
        if not job.unity_asset_path:
            raise ValueError("컨텍스트에 포함할 Unity Asset이 아직 없습니다.")
        assets = (
            f"- Selected Humanoid FBX (exact required path): {job.unity_asset_path}\n"
            f"- Rig report: {rig_status(job)}\n"
            "- Use this exact selected FBX. Do not search for or substitute another version."
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
