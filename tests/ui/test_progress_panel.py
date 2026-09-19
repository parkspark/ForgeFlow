from forgeflow.ui.main_window import MainWindow
from forgeflow.ui.panels.progress_panel import ProgressPanel


def test_progress_tracks_whole_operation_and_resets_next_run(monkeypatch):
    clock = [10.0]
    monkeypatch.setattr("forgeflow.ui.panels.progress_panel.monotonic", lambda: clock[0])
    panel = ProgressPanel()
    panel.on_event({"type": "stage_started", "stage": "modeling_vram"})
    clock[0] = 75.0
    panel.on_event({"type": "stage_started", "stage": "modeling"})
    assert panel.elapsed.text() == "경과 01:05"
    panel.append("model output")
    panel.details.setChecked(True)
    assert not panel.log.isHidden()
    panel.finish("modeling", True, "완료")
    assert not panel.timer.isActive()
    assert not panel.cancel.isEnabled()
    assert "model output" in panel.log.toPlainText()
    clock[0] = 100.0
    panel.on_event({"type": "stage_started", "stage": "inspect"})
    assert panel.elapsed.text() == "경과 00:00"
    assert "model output" not in panel.log.toPlainText()
    panel.finish("inspect", False, "실패")
    panel.close()


def test_start_stays_on_modeling_tab_and_cancel_reaches_pipeline(config, image, monkeypatch):
    window = MainWindow(config, run_environment_checks=False)
    try:
        window.current_job = window.jobs.create("진행 테스트", image)
        window.tabs.setCurrentWidget(window.modeling_panel)
        cancelled = []

        def start(job):
            window.pipeline.event_received.emit({"type": "stage_started", "stage": "modeling"})

        monkeypatch.setattr(window.pipeline, "start_modeling", start)
        # Existing signal connection is tested by observing cancellation state on a fake running process.
        monkeypatch.setattr(type(window.pipeline.process), "running", property(lambda self: True))
        monkeypatch.setattr(window.pipeline.process, "cancel", lambda: cancelled.append(True))
        start(window.current_job)
        window.progress_panel.cancel.click()
        assert cancelled == [True]
        assert not window.progress_panel.cancel.isEnabled()
        monkeypatch.setattr(type(window.pipeline.process), "running", property(lambda self: False))
        window.pipeline._cancelled = False
        window.start_modeling()
        assert window.tabs.currentWidget() is window.modeling_panel
        window.pipeline.operation_finished.emit("modeling", True, "완료")
        assert not window.progress_panel.active
    finally:
        window.close()
