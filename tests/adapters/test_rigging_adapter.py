from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path

import pytest

from forgeflow.adapters.rigging_adapter import RIGGING_KINDS, RiggingAdapter
from forgeflow.domain.artifact import Artifact
from forgeflow.domain.job import RiggingRequest, utc_now
from forgeflow.services.job_service import JobService, sha256_file
from tests.asset_fixtures import glb_bytes, png_bytes


def make_adapter(config, image, *, jobs_root: Path | None = None):
    if jobs_root is not None:
        config = replace(config, jobs_root=jobs_root)
    scripts = config.modeling_root / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "rig_humanoid.ps1").write_text("param()", encoding="utf-8")
    (scripts / "render_preview.py").write_text("# mock", encoding="utf-8")
    jobs = JobService(config.jobs_root)
    job = jobs.create("rig-test", image)
    source = jobs.job_directory(job.job_id) / "modeling" / "source.glb"
    source.write_bytes(glb_bytes("original-glb"))
    jobs.add_artifact(
        job, Artifact("glb", str(source), "modeling", utc_now(), sha256=sha256_file(source))
    )
    return config, jobs, job, RiggingAdapter(config, jobs), source


def add_blender_glb(jobs, job, version: int, payload: bytes = b"edited") -> Path:
    path = jobs.job_directory(job.job_id) / "blender" / f"v{version:03d}" / "op" / "result.glb"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(glb_bytes(payload))
    jobs.add_artifact(
        job,
        Artifact("glb", str(path), "blender", utc_now(), version=version, sha256=sha256_file(path)),
    )
    return path.resolve()


def produce(run, *, omit: str | None = None, empty: str | None = None, **overrides):
    paths = RiggingAdapter.expected_paths(run.execution_directory, run.request.safe_name)
    for kind, path in paths.items():
        if kind in {"rig_report", omit}:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"" if kind == empty else ("mock-" + kind).encode())
    report = {
        "status": "PASS",
        "bone_count": 24,
        "missing_required_bones": [],
        "vertex_count": 100,
        "weighted_vertices": 100,
        "max_influences": 4,
        "blend": str(paths["humanoid_blend"].resolve()),
        "fbx": str(paths["humanoid_fbx"].resolve()),
    }
    report.update(overrides)
    if omit != "rig_report":
        paths["rig_report"].write_text(json.dumps(report), encoding="utf-8")
    return paths


def test_schema_v1_migrates_to_v4_without_losing_fields(config, image):
    jobs = JobService(config.jobs_root)
    job = jobs.create("legacy", image)
    path = jobs.job_directory(job.job_id) / "job.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["schema_version"] = 1
    payload["generation_settings"]["legacy_value"] = "keep"
    for key in (
        "rigging_requests",
        "rigging_input_path",
        "humanoid_fbx_path",
        "humanoid_blend_path",
        "unity_input_path",
    ):
        payload.pop(key, None)
    payload["stages"].pop("rigging", None)
    path.write_text(json.dumps(payload), encoding="utf-8")
    restored = jobs.load(job.job_id)
    assert restored.schema_version == 4
    assert restored.stages["rigging"].status == "pending"
    assert restored.generation_settings["legacy_value"] == "keep"
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 4


def test_future_schema_is_rejected(config, image):
    jobs = JobService(config.jobs_root)
    job = jobs.create("future", image)
    path = jobs.job_directory(job.job_id) / "job.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["schema_version"] = 99
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="미래"):
        jobs.load(job.job_id)


def test_default_input_prefers_latest_blender_then_modeling(config, image):
    _config, jobs, job, adapter, source = make_adapter(config, image)
    assert adapter.select_input(job).path == source.resolve()
    older = add_blender_glb(jobs, job, 1, b"old")
    newest = add_blender_glb(jobs, job, 3, b"new")
    assert adapter.select_input(job).path == newest
    assert adapter.select_input(job, older).path == older


def test_explicit_glb_must_belong_to_job(config, image, tmp_path):
    _config, _jobs, job, adapter, _source = make_adapter(config, image)
    external = tmp_path / "external.glb"
    external.write_bytes(glb_bytes())
    with pytest.raises(ValueError, match="등록된"):
        adapter.select_input(job, external)


@pytest.mark.parametrize("name, message", [("asset.fbx", "GLB"), ("missing.glb", "존재")])
def test_invalid_input_rejected(config, image, tmp_path, name, message):
    _config, _jobs, _job, adapter, _source = make_adapter(config, image)
    path = tmp_path / name
    if path.suffix == ".fbx":
        path.write_bytes(b"x")
    with pytest.raises(ValueError, match=message):
        adapter.validate_input(path)


def test_safe_ascii_name_and_version_increment(config, image):
    _config, jobs, job, adapter, _source = make_adapter(config, image)
    name = adapter.safe_name("작업-job_ABC", 1)
    assert name == "rig_job_ABC_v001"
    assert re.fullmatch(r"[A-Za-z0-9_-]+", name)
    occupied = jobs.job_directory(job.job_id) / "rigging" / "v001"
    occupied.mkdir(parents=True)
    (occupied / "partial.log").write_text("failed")
    command, run = adapter.build_rigging(job)
    assert run.request.version == 2
    assert run.final_directory.name == "v002"
    assert "v001" not in str(command.arguments[-3])


def test_direct_execution_and_command_arguments(config, image):
    _config, _jobs, job, adapter, source = make_adapter(config, image)
    command, run = adapter.build_rigging(job, source, 12345)
    assert not run.staged
    assert run.execution_input == source.resolve()
    assert run.execution_directory == run.final_directory
    assert command.executable == config.powershell
    assert command.arguments[command.arguments.index("-InputGlb") + 1] == str(source.resolve())
    assert command.arguments[command.arguments.index("-Name") + 1] == run.request.safe_name
    assert command.arguments[command.arguments.index("-OutputDirectory") + 1] == str(
        run.final_directory
    )
    assert command.arguments[command.arguments.index("-Seed") + 1] == "12345"


def test_space_path_uses_safe_staging(config, image, tmp_path, monkeypatch):
    staging = tmp_path.parent / "safe-staging"
    monkeypatch.setenv("LOCALAPPDATA", str(staging))
    _config, jobs, job, adapter, _source = make_adapter(config, image)
    spaced = tmp_path / "input with spaces" / "person.glb"
    spaced.parent.mkdir()
    spaced.write_bytes(glb_bytes("space-input"))
    jobs.add_artifact(
        job,
        Artifact("glb", str(spaced), "blender", utc_now(), version=4, sha256=sha256_file(spaced)),
    )
    _command, run = adapter.build_rigging(job, spaced)
    assert run.staged
    assert " " not in str(run.execution_input)
    assert run.execution_input.read_bytes() == spaced.read_bytes()
    produce(run)
    artifacts, _report = adapter.collect_rigging(run)
    assert all(Path(item.path).parent == run.final_directory for item in artifacts)
    assert sha256_file(spaced) == run.request.input_sha256


def test_space_in_staging_root_is_rejected(config, image, tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "unsafe staging"))
    _config, jobs, job, adapter, _source = make_adapter(config, image)
    spaced = tmp_path / "input with spaces" / "person.glb"
    spaced.parent.mkdir()
    spaced.write_bytes(glb_bytes("space-input"))
    jobs.add_artifact(
        job,
        Artifact("glb", str(spaced), "blender", utc_now(), version=1, sha256=sha256_file(spaced)),
    )
    with pytest.raises(ValueError, match="staging 경로에 공백"):
        adapter.build_rigging(job, spaced)


@pytest.mark.parametrize("seed", [-1, 2_147_483_648, "1.5", True])
def test_seed_validation(config, image, seed):
    _config, _jobs, job, adapter, _source = make_adapter(config, image)
    with pytest.raises(ValueError, match="Seed"):
        adapter.build_rigging(job, seed=seed)


@pytest.mark.parametrize(
    "case, overrides, message",
    [
        ("status", {"status": "FAIL"}, "status"),
        ("missing", {"missing_required_bones": ["Hips"]}, "missing_required_bones"),
        ("weighted", {"weighted_vertices": 99}, "weighted_vertices"),
        ("influence-low", {"max_influences": 0}, "max_influences"),
        ("influence-high", {"max_influences": 5}, "max_influences"),
        ("bad-path", {"fbx": "C:/wrong/result.fbx"}, "경로"),
    ],
)
def test_report_validation_failures(config, image, case, overrides, message):
    _config, _jobs, job, adapter, _source = make_adapter(config, image)
    _command, run = adapter.build_rigging(job)
    produce(run, **overrides)
    with pytest.raises(RuntimeError, match=message):
        adapter.collect_rigging(run)


@pytest.mark.parametrize(
    "mode, kind, message", [("omit", "skin_fbx", "없습니다"), ("empty", "unity_fbx", "0바이트")]
)
def test_required_output_missing_or_empty(config, image, mode, kind, message):
    _config, _jobs, job, adapter, _source = make_adapter(config, image)
    _command, run = adapter.build_rigging(job)
    produce(run, **{mode: kind})
    with pytest.raises(RuntimeError, match=message):
        adapter.collect_rigging(run)


def test_input_hash_change_detected_and_no_artifacts_registered(config, image):
    _config, _jobs, job, adapter, source = make_adapter(config, image)
    _command, run = adapter.build_rigging(job)
    produce(run)
    source.write_bytes(b"tampered")
    with pytest.raises(RuntimeError, match="해시"):
        adapter.collect_rigging(run)
    assert not [item for item in job.artifacts if item.stage == "rigging"]
    assert job.unity_input_path is None


def test_success_artifacts_and_unity_input_registration(config, image):
    _config, jobs, job, adapter, source = make_adapter(config, image)
    _command, run = adapter.build_rigging(job)
    jobs.add_rigging_request(job, run.request)
    produce(run)
    artifacts, report = adapter.collect_rigging(run)
    adapter.register_success(job, run, artifacts)
    assert {item.kind for item in artifacts} == set(RIGGING_KINDS)
    assert all(item.stage == "rigging" and item.version == 1 and item.sha256 for item in artifacts)
    assert all(item.parent_path == str(source.resolve()) for item in artifacts)
    assert job.unity_input_path == job.humanoid_fbx_path
    assert Path(job.unity_input_path).is_file()
    assert report["status"] == "PASS"
    restored = jobs.load(job.job_id)
    assert restored.unity_input_path == job.unity_input_path


def test_restart_recovers_running_rigging_request(config, image):
    jobs = JobService(config.jobs_root)
    job = jobs.create("recover-rig", image)
    request = RiggingRequest(
        "C:/input.glb",
        "a" * 64,
        "C:/output",
        1,
        12345,
        "rig_job_v001",
        status="running",
        started_at=utc_now(),
    )
    jobs.add_rigging_request(job, request)
    jobs.set_stage(job, "rigging", "running")
    restored = JobService(config.jobs_root).recover_interrupted()[0]
    assert restored.stages["rigging"].status == "failed"
    assert restored.latest_rigging_request.status == "failed"


def test_mock_unirig_full_adapter_pipeline(config, image):
    _config, jobs, job, adapter, _source = make_adapter(config, image)
    command, run = adapter.build_rigging(job, seed=777)
    assert "-File" in command.arguments and run.request.seed == 777
    jobs.add_rigging_request(job, run.request)
    produce(run)
    artifacts, _report = adapter.collect_rigging(run)
    adapter.register_success(job, run, artifacts)
    for pose in (False, True):
        _preview_command, output, kind = adapter.build_preview(run, pose)
        output.write_bytes(png_bytes())
        jobs.add_artifact(job, adapter.collect_preview(run, output, kind))
    jobs.set_stage(job, "rigging", "completed")
    restored = jobs.load(job.job_id)
    assert restored.stages["rigging"].status == "completed"
    assert {"rest_preview", "pose_preview"} <= {item.kind for item in restored.artifacts}
    assert restored.unity_input_path and Path(restored.unity_input_path).is_file()
