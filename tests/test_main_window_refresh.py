from __future__ import annotations

import os
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from forgeflow.ui.main_window import MainWindow
from forgeflow.config import AppConfig


_APP = QApplication.instance() or QApplication([])
_APP.setQuitOnLastWindowClosed(False)


def test_model_status_shows_each_model_and_missing_state(config):
    window = MainWindow(config, run_environment_checks=False)
    try:
        window._environment_ready({
            "ollama_blender": {"ok": True, "status": "설치됨", "detail": "ready"},
            "ollama_unity": {"ok": False, "status": "모델 없음", "detail": "missing"},
        })
        blender = window.environment_labels["ollama_blender"]
        unity = window.environment_labels["ollama_unity"]
        assert config.ollama_model in blender.text()
        assert "설치됨" in blender.text()
        assert config.unity_agent_model in unity.text()
        assert "모델 없음" in unity.text()
        assert unity.property("envState") == "error"
    finally:
        window.close()


def test_saved_settings_survive_theme_change_and_reopening(config, tmp_path, monkeypatch):
    window = MainWindow(config, run_environment_checks=False)
    updated = replace(config, unity_agent_model="new-model", jobs_root=tmp_path / "new-jobs")
    opened = []

    class Dialog:
        def __init__(self, original, parent):
            opened.append(original)

        def exec(self):
            return len(opened) == 1

        def value(self, original):
            return updated

    monkeypatch.setattr("forgeflow.ui.main_window.SettingsDialog", Dialog)
    monkeypatch.setattr("forgeflow.ui.main_window.QMessageBox.information", lambda *args: None)
    try:
        window.edit_settings()
        window.theme_selector.setCurrentIndex(window.theme_selector.findData("light"))
        saved = AppConfig.load()
        assert saved.unity_agent_model == "new-model"
        assert saved.jobs_root == updated.jobs_root
        assert saved.theme == "light"
        window.edit_settings()
        assert opened[-1] == saved
        assert window.config.jobs_root == config.jobs_root
        assert window.unity.config.unity_agent_model == config.unity_agent_model
    finally:
        window.close()


def test_job_change_updates_cached_list_without_full_disk_reload(config, image, monkeypatch):
    window = MainWindow(config, run_environment_checks=False)
    assert window.windowTitle() == "ForgeFlow - 이미지, 3d 모델링, Blender, Unity 통합 워크플로"
    job = window.jobs.create("incremental", image)
    window.current_job = job
    window.job_list = [job]
    window.project_panel.set_jobs([job], job.job_id)

    def unexpected_reload(*_args, **_kwargs):
        raise AssertionError("incremental job updates must not reload every job.json")

    monkeypatch.setattr(window.jobs, "list_jobs", unexpected_reload)
    monkeypatch.setattr(window.jobs, "load", unexpected_reload)
    job.name = "updated"
    window._job_changed(job)

    assert window.current_job is job
    assert window.project_panel.jobs.count() == 1
    assert "updated" in window.project_panel.jobs.item(0).text()
    window.close()
