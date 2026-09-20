from forgeflow.domain.artifact import Artifact
from forgeflow.services.pipeline_service import PipelineService
from forgeflow.ui.main_window import MainWindow
from forgeflow.ui.panels.next_step_panel import NextStepPanel


def test_next_steps_require_results_and_only_navigate(config, image, tmp_path, monkeypatch):
    window = MainWindow(config, run_environment_checks=False)
    try:
        job = window.jobs.create("다음 단계", image)
        window.current_job = job
        window._render_job()
        assert not window.next_steps["modeling"].buttons["blender"].isEnabled()
        glb = tmp_path / "source.glb"
        glb.write_bytes(b"glb")
        job.blender_input_path = str(glb)
        job.artifacts.append(Artifact("glb", str(glb), "modeling", "2026-09-13"))
        window._render_job()
        window.next_steps["modeling"].buttons["blender"].click()
        assert window.tabs.currentWidget().widget() is window.blender_panel
        window.next_steps["blender"].buttons["rigging"].click()
        assert window.tabs.currentWidget().widget() is window.rigging_panel
        assert not window.next_steps["rigging"].buttons["unity"].isEnabled()
        fbx = tmp_path / "humanoid.fbx"
        fbx.write_bytes(b"fbx")
        job.unity_input_path = str(fbx)
        window._render_job()
        window.next_steps["rigging"].buttons["unity"].click()
        assert window.tabs.currentWidget().widget() is window.unity_panel
        assert not window.unity.running
        assert job.stages["rigging"].status == "pending"
        assert not window.rigging_panel.confirm_humanoid.isChecked()
        glb.unlink()
        window.go_to_next_step("blender")
        assert window.tabs.currentWidget().widget() is window.unity_panel
        assert not window.next_steps["modeling"].buttons["blender"].isEnabled()
        with monkeypatch.context() as patch:
            patch.setattr(PipelineService, "busy", property(lambda self: True))
            window._update_next_steps()
            assert not window.next_steps["rigging"].buttons["unity"].isEnabled()
    finally:
        window.close()


def test_each_blocked_destination_explains_what_is_missing_without_hover():
    panel = NextStepPanel([("blender", "Blender에서 편집"), ("rigging", "리깅하기")])
    try:
        panel.update_state(
            "결과를 다음 단계에서 사용하세요.",
            {
                "blender": (True, "Blender 화면으로 이동"),
                "rigging": (False, "등록된 GLB가 필요합니다."),
            },
        )
        assert panel.buttons["blender"].isEnabled()
        assert not panel.buttons["rigging"].isEnabled()
        assert "리깅하기: 등록된 GLB가 필요합니다." in panel.blocked_reasons.text()
        assert not panel.blocked_reasons.isHidden()
        assert panel.buttons["rigging"].accessibleDescription() == "등록된 GLB가 필요합니다."
        assert not panel.navigation_hint.isHidden()
        panel.update_state(
            "현재 작업을 기다리세요.",
            {
                "blender": (False, "현재 작업을 기다리세요."),
                "rigging": (False, "현재 작업을 기다리세요."),
            },
        )
        assert panel.blocked_reasons.isHidden()
        assert panel.navigation_hint.isHidden()
        assert not panel.buttons["blender"].isEnabled()
    finally:
        panel.close()


def test_partial_availability_cannot_leave_a_stale_action_enabled():
    panel = NextStepPanel([("unity", "Unity에서 사용")])
    try:
        panel.update_state("Unity에서 사용하세요.", {"unity": (True, "Unity 화면으로 이동")})
        panel.update_state("준비 상태 확인 중", {})
        assert not panel.buttons["unity"].isEnabled()
        assert "준비 상태를 확인" in panel.blocked_reasons.text()
    finally:
        panel.close()
