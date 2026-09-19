from forgeflow.domain.artifact import Artifact
from forgeflow.services.pipeline_service import PipelineService
from forgeflow.ui.main_window import MainWindow


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
        assert window.tabs.currentWidget() is window.blender_panel
        window.next_steps["blender"].buttons["rigging"].click()
        assert window.tabs.currentWidget() is window.rigging_panel
        assert not window.next_steps["rigging"].buttons["unity"].isEnabled()
        fbx = tmp_path / "humanoid.fbx"
        fbx.write_bytes(b"fbx")
        job.unity_input_path = str(fbx)
        window._render_job()
        window.next_steps["rigging"].buttons["unity"].click()
        assert window.tabs.currentWidget() is window.unity_panel
        assert not window.unity.running
        assert job.stages["rigging"].status == "pending"
        assert not window.rigging_panel.confirm_humanoid.isChecked()
        glb.unlink()
        window.go_to_next_step("blender")
        assert window.tabs.currentWidget() is window.unity_panel
        assert not window.next_steps["modeling"].buttons["blender"].isEnabled()
        with monkeypatch.context() as patch:
            patch.setattr(PipelineService, "busy", property(lambda self: True))
            window._update_next_steps()
            assert not window.next_steps["rigging"].buttons["unity"].isEnabled()
    finally:
        window.close()
