from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtWidgets import QMessageBox

from forgeflow.domain.artifact import Artifact
from forgeflow.services.job_service import JobService
from forgeflow.ui.main_window import MainWindow
from tests.asset_fixtures import glb_bytes


def test_startup_recovers_settings_without_hiding_existing_jobs(config, image, monkeypatch, qapp):
    job = JobService(config.jobs_root).create("보존할 작업", image)
    config.save()
    config.save()
    damaged = b'{"jobs_root": '
    config.config_path.write_bytes(damaged)
    messages = []
    opened = []
    monkeypatch.setattr(
        "forgeflow.ui.main_window.QMessageBox.warning", lambda *args: messages.append(args)
    )
    monkeypatch.setattr(MainWindow, "edit_settings", lambda self: opened.append(True))
    window = MainWindow(run_environment_checks=False)
    try:
        window.show()
        qapp.processEvents()
        assert window.config.jobs_root == config.jobs_root
        assert window.current_job.job_id == job.job_id
        assert window.config_recovery_banner.isVisible()
        assert window.config_warnings
        assert config.config_path.read_bytes() == damaged
        assert not messages
        window.config_recovery_details.click()
        assert len(messages) == 1
        assert str(config.config_path) in messages[0][2]
        window.config_recovery_settings.click()
        assert opened == [True]
    finally:
        window.close()


def test_startup_recovers_one_invalid_field_and_keeps_valid_job_root(config, image, qapp):
    job = JobService(config.jobs_root).create("사용자 작업", image)
    config.save()
    payload = json.loads(config.config_path.read_text(encoding="utf-8"))
    payload["ollama_model"] = None
    config.config_path.write_text(json.dumps(payload), encoding="utf-8")
    window = MainWindow(run_environment_checks=False)
    try:
        window.show()
        qapp.processEvents()
        assert window.config.jobs_root == config.jobs_root
        assert window.current_job.job_id == job.job_id
        assert window.config_recovery_banner.isVisible()
        assert window.config.ollama_model
    finally:
        window.close()


def test_valid_settings_start_without_recovery_banner(config, qapp):
    config.save()
    window = MainWindow(run_environment_checks=False)
    try:
        window.show()
        qapp.processEvents()
        assert window.config == config
        assert not window.config_warnings
        assert not window.config_recovery_banner.isVisible()
    finally:
        window.close()


def test_legacy_rigging_confirmation_explains_missing_registered_hash(config, image, monkeypatch):
    window = MainWindow(config, run_environment_checks=False)
    try:
        job = window.jobs.create("이전 작업", image)
        source = window.jobs.job_directory(job.job_id) / "modeling" / "source.glb"
        source.write_bytes(glb_bytes())
        window.jobs.add_artifact(job, Artifact("glb", str(source), "modeling", job.created_at))
        window.current_job = job
        confirmations = []

        def decline(*args):
            confirmations.append(args[2])
            return QMessageBox.StandardButton.No

        monkeypatch.setattr("forgeflow.ui.main_window.QMessageBox.question", decline)
        window.start_rigging(str(source), 12345)
        assert len(confirmations) == 1
        assert "등록 당시 해시가 없는 이전 결과" in confirmations[0]
        assert "현재 파일을 이번 실행의 기준" in confirmations[0]
        assert job.artifacts[0].sha256 is None
        assert not job.rigging_requests
        assert not list((Path(window.jobs.root) / job.job_id / "rigging").iterdir())
    finally:
        window.close()
