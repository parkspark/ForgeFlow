from forgeflow.adapters.blender_adapter import BlenderAdapter
from forgeflow.domain.artifact import Artifact
from forgeflow.domain.job import BlenderRequest, UnitySession, UnityTurn, utc_now
from forgeflow.services.job_service import JobService
from forgeflow.services.lineage import invalidate_unity
from forgeflow.ui.panels.blender_panel import BlenderPanel
from forgeflow.ui.panels.unity_panel import UnityPanel


def test_rigged_blend_is_selectable_and_cancels_previous_input_plan(config, image, tmp_path):
    jobs = JobService(config.jobs_root)
    job = jobs.create("round trip", image)
    source, rigged = tmp_path / "model.glb", tmp_path / "rigged.blend"
    source.write_bytes(b"model fixture")
    rigged.write_bytes(b"rig fixture")
    job.blender_input_path = str(source)
    job.artifacts = [
        Artifact("glb", str(source), "modeling", utc_now()),
        Artifact("humanoid_blend", str(rigged), "rigging", utc_now(), version=1),
    ]
    job.blender_requests.append(
        BlenderRequest("old plan", str(source), str(tmp_path), 1, status="awaiting_approval")
    )
    panel = BlenderPanel()
    try:
        panel.set_job(job)
        assert panel.inputs.findData(str(rigged)) >= 0
        BlenderAdapter(config, jobs).select_input(job, str(rigged))
        panel.set_job(job)
        assert "리깅 후 편집" in panel.mode_label.text()
        assert job.latest_blender_request.status == "cancelled"
        assert panel.propose.isEnabled()
        assert not panel.approve.isEnabled()
    finally:
        panel.close()


def test_unity_presets_preserve_draft_and_old_approval_is_history(config, image):
    job = JobService(config.jobs_root).create("animation", image)
    job.unity_asset_path = "Assets/Character.fbx"
    job.unity_turns.append(UnityTurn("t1", "s1", "old", "old", status="succeeded"))
    panel = UnityPanel()
    try:
        panel.set_job(job)
        panel.set_session(UnitySession(
            "s1", "C:/UnityProject", status="ready",
            project_identity={
                "bridgeVersion": "0.6.0", "supportedTools": ["unity_configure_humanoid"],
            },
        ))
        panel.input.setPlainText("기존 요청")
        panel.avatar_preset.click()
        assert panel.input.toPlainText().startswith("기존 요청")
        assert "Avatar" in panel.input.toPlainText()
        assert panel.include_asset.isChecked()
        invalidate_unity(job, "새 버전으로 다시 가져오기 필요")
        panel.set_job(job)
        assert not panel.accept_button.isEnabled()
        assert not panel.avatar_preset.isEnabled()
        assert "이전 입력 버전" in panel.review_next_step.text()
        assert "다시 가져오기" in panel.asset_label.text()
    finally:
        panel.close()
