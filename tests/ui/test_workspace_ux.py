from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, QSize
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QDialogButtonBox

from forgeflow.domain.artifact import Artifact
from forgeflow.domain.job import UnitySession
from forgeflow.ui.dialogs.settings_dialog import SettingsDialog
from forgeflow.ui.main_window import MainWindow
from forgeflow.ui.panels.modeling_panel import ModelingPanel


def save_image(path: Path, width=500, height=300):
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor("#557799"))
    assert image.save(str(path))
    return path


def test_empty_workspace_starts_with_image_action_then_identifies_selected_job(
    config, image, monkeypatch, qapp
):
    window = MainWindow(config, run_environment_checks=False)
    try:
        window.show()
        qapp.processEvents()
        assert window.workspace_stack.currentWidget() is window.welcome_page
        assert not window.tabs.isVisible()
        monkeypatch.setattr(
            "forgeflow.ui.main_window.QFileDialog.getOpenFileName", lambda *args: (str(image), "")
        )
        monkeypatch.setattr(
            "forgeflow.ui.main_window.QInputDialog.getText",
            lambda *args, **kwargs: ("새 작업", True),
        )
        window.welcome_create.click()
        qapp.processEvents()
        assert window.current_job.name == "새 작업"
        assert window.workspace_stack.currentWidget() is window.tabs
        assert window.workspace_title.accessibleName() == "새 작업"
        assert not window.pipeline.busy
    finally:
        window.close()


def test_modeling_preview_clears_previous_image_and_keeps_input_result_distinct(config, tmp_path):
    window = MainWindow(config, run_environment_checks=False)
    panel = window.modeling_panel
    try:
        first = window.jobs.create("원본 있음", save_image(tmp_path / "input.png"))
        result = save_image(tmp_path / "output.png", 300, 500)
        first.artifacts.append(Artifact("preview", str(result), "modeling", first.created_at))
        panel.set_job(first)
        assert not panel.preview.pixmap().isNull()
        assert not panel.result_preview.pixmap().isNull()
        assert panel.preview_tabs.currentWidget() is panel.result_preview
        panel.preview_tabs.setCurrentWidget(panel.preview)
        panel.set_job(first)
        assert panel.preview_tabs.currentWidget() is panel.preview
        second = window.jobs.create("원본 유실", tmp_path / "input.png")
        Path(second.input_image_path).unlink()
        panel.set_job(second)
        assert panel.preview.pixmap().isNull()
        assert "읽을 수 없습니다" in panel.preview.text()
        assert panel.result_preview.pixmap().isNull()
    finally:
        window.close()


def test_modeling_without_job_does_not_offer_executable_actions():
    panel = ModelingPanel()
    assert not panel.generate.isEnabled()
    assert not panel.retry.isEnabled()
    assert not panel.folder.isEnabled()


def test_background_job_events_do_not_replace_current_chat_connection_or_tab(config, image):
    window = MainWindow(config, run_environment_checks=False)
    try:
        first = window.jobs.create("실행 중인 작업", image)
        second = window.jobs.create("지금 읽는 작업", image)
        window.current_job = second
        window._render_job()
        window._show_panel(window.modeling_panel)
        window.unity.job = first
        window.unity.event_received.emit({"type": "assistant_text", "text": "다른 작업 응답"})
        window.unity.session_changed.emit(UnitySession("s1", "C:/OtherProject", status="ready"))
        assert "다른 작업 응답" not in window.unity_panel.chat.toPlainText()
        assert window.unity_panel.session is None
        window.pipeline._active_job = first
        window.pipeline.plan_ready.emit(first, None)
        window._operation_finished("rigging", True, "다른 작업 리깅 완료")
        assert window.tabs.currentWidget() is window.tab_pages[window.modeling_panel]
        window.unity.job = second
        window.unity.event_received.emit({"type": "assistant_text", "text": "현재 작업 응답"})
        assert "현재 작업 응답" in window.unity_panel.chat.toPlainText()
    finally:
        window.close()


@pytest.mark.parametrize("font_size", [13, 20])
def test_settings_large_text_keeps_save_visible_and_errors_reachable(config, qapp, font_size):
    dialog = SettingsDialog(config, load_models=False)
    try:
        dialog.setStyleSheet(f"QWidget {{ font-size: {font_size}px; }}")
        for status in dialog.model_status.values():
            status.setText(
                "목록 조회 실패\n서버에 연결할 수 없습니다.\n모델명을 직접 입력할 수 있습니다."
            )
        dialog.resize(680, 480)
        dialog.show()
        for _ in range(4):
            qapp.processEvents()
        assert dialog.size() == QSize(680, 480)
        save = dialog.buttons.button(QDialogButtonBox.StandardButton.Save)
        assert save.visibleRegion().boundingRect().contains(save.rect())
        assert dialog.rect().contains(save.rect().translated(save.mapTo(dialog, QPoint())))
        dialog.edits["jobs_root"].clear()
        save.click()
        for _ in range(4):
            qapp.processEvents()
        assert dialog.error_label.text()
        assert not dialog.error_label.visibleRegion().isEmpty()
    finally:
        dialog.close()


def test_rigging_log_routing_requires_current_running_rigging_context(config, monkeypatch):
    window = MainWindow(config, run_environment_checks=False)
    seen = []
    monkeypatch.setattr(
        window.rigging_panel, "append_log", lambda line, **kwargs: seen.append(line)
    )
    monkeypatch.setattr(type(window.pipeline), "busy", property(lambda self: False))
    try:
        window.pipeline._operation = "rigging"
        window._route_rigging_log("[VRAM] next operation preparation")
        assert seen == []
        monkeypatch.setattr(type(window.pipeline), "busy", property(lambda self: True))
        window.pipeline._operation = "modeling"
        window._route_rigging_log("model generation")
        assert seen == []
        window.pipeline._operation = "rigging_preview_rest"
        window._route_rigging_log("rigging preview")
        assert seen == ["rigging preview"]
    finally:
        monkeypatch.undo()
        window.close()
