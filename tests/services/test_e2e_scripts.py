from __future__ import annotations

import importlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from forgeflow.domain.job import BlenderRequest
from forgeflow.domain.process import ProcessCommand
from forgeflow.services.job_service import JobService


@pytest.fixture
def e2e_modules(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts" / "e2e"))
    return {
        name: importlib.import_module(name) for name in ("_process", "_evidence", "run_unity_e2e")
    }


def test_live_process_log_is_persisted_before_timeout(e2e_modules, tmp_path, monkeypatch):
    module = e2e_modules["_process"]
    path = tmp_path / "logs" / "live.log"

    def fail_after_output(self, command, receive, timeout):
        assert path.is_file()
        receive("OUT", "엔진 시작")
        assert "엔진 시작" in path.read_text(encoding="utf-8")
        raise subprocess.TimeoutExpired(command.executable, timeout)

    monkeypatch.setattr(module.SyncProcessRunner, "run", fail_after_output)
    with pytest.raises(subprocess.TimeoutExpired):
        module.run_command(ProcessCommand("fake-engine", [], tmp_path), path, 0.1)
    text = path.read_text(encoding="utf-8")
    assert "[OUT] 엔진 시작" in text
    assert "[ERROR] TimeoutExpired" in text


@pytest.mark.parametrize("name", ["write", "fbx"])
@pytest.mark.parametrize("automated", ["partial", "unavailable", "failed"])
def test_unity_mutation_requires_verified_checks(e2e_modules, name, automated):
    turn = SimpleNamespace(
        status="succeeded", automated_status=automated, human_review_status="pending", error=None
    )
    with pytest.raises(RuntimeError, match="requires verified"):
        e2e_modules["run_unity_e2e"].validate_turn(name, turn)


def test_unity_verified_result_preserves_pending_human_review(e2e_modules):
    module = e2e_modules["run_unity_e2e"]
    turn = SimpleNamespace(
        status="succeeded", automated_status="verified", human_review_status="pending", error=None
    )
    module.validate_turn("fbx", turn)
    assert turn.human_review_status == "pending"
    turn.human_review_status = "accepted"
    with pytest.raises(RuntimeError, match="leave human review pending"):
        module.validate_turn("fbx", turn)


def test_e2e_config_override_preserves_saved_settings(e2e_modules, config, tmp_path, monkeypatch):
    module = e2e_modules["_evidence"]
    monkeypatch.setattr(module.AppConfig, "load", lambda: config)
    assert module.load_config(None) is config
    selected = module.load_config(tmp_path / "override-jobs")
    assert selected.jobs_root == tmp_path / "override-jobs"
    assert selected.modeling_root == config.modeling_root
    assert selected.blender_executable == config.blender_executable
    assert selected.unity_agent_model == config.unity_agent_model
    assert not config.config_path.exists()


def test_evidence_failure_finishes_running_job_and_request(
    e2e_modules, config, image, tmp_path, monkeypatch
):
    module = e2e_modules["_evidence"]
    fallback = tmp_path / "run-evidence.json"
    monkeypatch.setattr(module, "default_evidence_path", lambda name: fallback)
    jobs = JobService(config.jobs_root)
    job = jobs.create("failed E2E", image)
    request = BlenderRequest("request", "source.glb", "output", 1)
    jobs.add_blender_request(job, request)
    path = jobs.job_directory(job.job_id) / "logs" / "evidence.json"
    audit = module.E2EEvidence("test")
    with audit:
        audit.bind_job(jobs, job, path)
        jobs.set_stage(job, "blender", "running")
        audit.checkpoint(phase="blender_proposal")
        assert json.loads(path.read_text(encoding="utf-8"))["status"] == "running"
        raise subprocess.TimeoutExpired("engine", 1)
    assert audit.exit_code == 1
    for evidence_path in (fallback, path):
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        assert evidence["status"] == "failed"
        assert evidence["phase"] == "blender_proposal"
        assert "TimeoutExpired" in evidence["error"]
        assert evidence["finished_at"]
    restored = jobs.load(job.job_id)
    assert restored.stages["blender"].status == "failed"
    assert restored.latest_blender_request.status == "failed"


def test_e2e_failure_preserves_other_stage_work(e2e_modules, config, image, tmp_path, monkeypatch):
    module = e2e_modules["_evidence"]
    fallback = tmp_path / "run-evidence.json"
    monkeypatch.setattr(module, "default_evidence_path", lambda name: fallback)
    jobs = JobService(config.jobs_root)
    job = jobs.create("existing work", image)
    request = BlenderRequest("request", "source.glb", "output", 1, status="awaiting_approval")
    jobs.add_blender_request(job, request)
    jobs.set_stage(job, "modeling", "running")
    audit = module.E2EEvidence("unity")
    with audit:
        audit.bind_job(jobs, job, tmp_path / "job-evidence.json")
        audit.checkpoint(phase="unity_session")
        raise RuntimeError("Unity connection unavailable")
    restored = jobs.load(job.job_id)
    assert restored.stages["unity"].status == "failed"
    assert restored.stages["modeling"].status == "running"
    assert restored.latest_blender_request.status == "awaiting_approval"
