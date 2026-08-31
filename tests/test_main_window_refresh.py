from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from forgeflow.ui.main_window import MainWindow


_APP = QApplication.instance() or QApplication([])
_APP.setQuitOnLastWindowClosed(False)


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
