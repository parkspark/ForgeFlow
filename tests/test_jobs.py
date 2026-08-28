from __future__ import annotations

import json
from pathlib import Path

import pytest

from forgeflow.domain.artifact import Artifact
from forgeflow.domain.job import BlenderRequest, utc_now
from forgeflow.services.job_service import JobService


def test_job_creation_and_atomic_json(config, image):
    service = JobService(config.jobs_root)
    job = service.create("한글 작업", image, {"seed": 7})
    directory = service.job_directory(job.job_id)
    payload = json.loads((directory / "job.json").read_text(encoding="utf-8"))
    assert payload["schema_version"] == 3
    assert payload["stages"]["rigging"]["status"] == "pending"
    assert payload["stages"]["unity"]["status"] == "pending"
    assert payload["name"] == "한글 작업"
    assert Path(payload["input_image_path"]).read_bytes() == image.read_bytes()
    assert not (directory / "job.json.tmp").exists()


def test_restart_recovery_marks_running_failed(config, image):
    service = JobService(config.jobs_root)
    job = service.create("복구", image)
    service.set_stage(job, "modeling", "running")
    restarted = JobService(config.jobs_root)
    recovered = restarted.recover_interrupted()[0]
    assert recovered.stages["modeling"].status == "failed"
    assert "중단" in recovered.stages["modeling"].error


def test_restart_recovery_marks_unity_turn_and_session_failed(config, image):
    from forgeflow.domain.job import UnitySession, UnityTurn

    service = JobService(config.jobs_root)
    job = service.create("unity recovery", image)
    job.unity_sessions.append(UnitySession("s", r"C:\Game", status="ready"))
    job.unity_turns.append(UnityTurn("t", "s", "request", "effective", status="running"))
    service.set_stage(job, "unity", "running")
    recovered = JobService(config.jobs_root).recover_interrupted()[0]
    assert recovered.stages["unity"].status == "failed"
    assert recovered.latest_unity_turn.status == "failed"
    assert recovered.latest_unity_session.status == "failed"


@pytest.mark.parametrize("suffix", [".gif", ".webp", ".txt"])
def test_image_extension_validation_rejects(config, tmp_path, suffix):
    candidate = tmp_path / f"input{suffix}"
    candidate.write_bytes(b"x")
    with pytest.raises(ValueError, match="PNG"):
        JobService(config.jobs_root).create("bad", candidate)


def test_empty_image_rejected(config, tmp_path):
    candidate = tmp_path / "empty.png"
    candidate.write_bytes(b"")
    with pytest.raises(ValueError, match="비어"):
        JobService(config.jobs_root).create("bad", candidate)


def test_blender_version_increments(config, image):
    service = JobService(config.jobs_root)
    job = service.create("versions", image)
    assert service.next_blender_version(job) == 1
    job.blender_requests.append(BlenderRequest("x", "C:\\x.glb", str(service.job_directory(job.job_id) / "blender" / "v001"), 1))
    service.add_artifact(job, Artifact("glb", str(service.job_directory(job.job_id) / "blender" / "v002" / "x.glb"), "blender", utc_now(), version=2))
    assert service.next_blender_version(job) == 3


def test_invalid_job_id_and_corrupt_mismatch_blocked(config, image):
    service = JobService(config.jobs_root)
    with pytest.raises(ValueError, match="작업 ID"):
        service.job_directory("../outside")
    job = service.create("safe", image)
    path = service.job_directory(job.job_id) / "job.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["job_id"] = "different"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="일치"):
        service.load(job.job_id)


def test_agent_environment_forces_utf8(config, tmp_path):
    environment = config.agent_environment(tmp_path / "sessions")
    assert environment["PYTHONUTF8"] == "1"
    assert environment["PYTHONIOENCODING"] == "utf-8"
