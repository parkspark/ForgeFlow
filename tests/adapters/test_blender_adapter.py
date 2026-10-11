from __future__ import annotations

import json
from pathlib import Path

import pytest

from forgeflow.adapters.blender_adapter import BlenderAdapter, plan_hash
from forgeflow.domain.job import BlenderRequest
from forgeflow.services.job_service import JobService, sha256_file


def _plan(source: Path, output: Path) -> dict:
    return {
        "steps": [
            {
                "number": 1,
                "tool": "asset.transform",
                "arguments": {
                    "input_path": str(source.resolve()),
                    "output_directory": str(output.resolve()),
                    "target": "geometry_0",
                    "scale": [1.05, 1.05, 1.05],
                },
                "description": "geometry_0 확대",
            }
        ],
        "requires_approval": True,
    }


def test_writes_cannot_start_before_approval(config, image):
    jobs = JobService(config.jobs_root)
    job = jobs.create("approval", image)
    source = jobs.job_directory(job.job_id) / "modeling" / "source.glb"
    source.write_bytes(b"original")
    job.blender_input_path = str(source)
    request = BlenderRequest(
        "확대", str(source), str(jobs.job_directory(job.job_id) / "blender" / "v001"), 1
    )
    job.blender_requests.append(request)
    with pytest.raises(RuntimeError, match="승인"):
        BlenderAdapter(config, jobs).build_execute(job, request)


def test_session_json_plan_parsing(config, image):
    jobs = JobService(config.jobs_root)
    job = jobs.create("session", image)
    source = jobs.job_directory(job.job_id) / "modeling" / "source.glb"
    source.write_bytes(b"original")
    output = jobs.job_directory(job.job_id) / "blender" / "v001"
    output.mkdir(parents=True)
    plan = _plan(source, output)
    proposal = output / "proposal.json"
    proposal.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "prompt": "test",
                "plan_sha256": plan_hash(plan),
                "result": {
                    "status": "denied",
                    "summary": "approval required",
                    "plan": plan,
                    "log_path": "session.json",
                },
            }
        ),
        encoding="utf-8",
    )
    request = BlenderRequest("확대", str(source), str(output), 1, proposal_path=str(proposal))
    BlenderAdapter(config, jobs).load_proposal(request)
    assert request.status == "awaiting_approval"
    assert request.plan_sha256 == plan_hash(plan)


def test_execution_artifacts_and_source_immutability(config, image):
    jobs = JobService(config.jobs_root)
    job = jobs.create("immutable", image)
    source = jobs.job_directory(job.job_id) / "modeling" / "source.glb"
    source.write_bytes(b"original-source")
    original = sha256_file(source)
    output = jobs.job_directory(job.job_id) / "blender" / "v001"
    operation = output / "operation-id"
    operation.mkdir(parents=True)
    files = []
    for suffix in ("glb", "blend", "fbx"):
        path = operation / f"result.{suffix}"
        path.write_bytes(("changed-" + suffix).encode())
        files.append(str(path))
    plan = _plan(source, output)
    request = BlenderRequest(
        "확대",
        str(source),
        str(output),
        1,
        status="running",
        plan=plan,
        plan_sha256=plan_hash(plan),
    )
    execution = output / "execution.json"
    execution.write_text(
        json.dumps(
            {
                "plan_sha256": request.plan_sha256,
                "result": {"status": "completed", "artifacts": files, "log_path": "session.json"},
            }
        ),
        encoding="utf-8",
    )
    artifacts = BlenderAdapter(config, jobs).collect_execution(request, execution, original)
    assert {item.kind for item in artifacts} == {"glb", "blend", "fbx"}
    assert sha256_file(source) == original
    source.write_bytes(b"tampered")
    with pytest.raises(RuntimeError, match="원본 파일 해시"):
        BlenderAdapter(config, jobs).collect_execution(request, execution, original)


@pytest.mark.parametrize("failure", ["missing_python", "input_hash"])
def test_proposal_preparation_failure_does_not_reserve_version(config, image, monkeypatch, failure):
    jobs = JobService(config.jobs_root)
    job = jobs.create("retry preparation", image)
    root = jobs.job_directory(job.job_id)
    source = root / "modeling" / "source.blend"
    source.write_bytes(b"original Blender input")
    job.blender_input_path = str(source)
    config.agent_python.parent.mkdir(parents=True)
    config.agent_python.touch()
    adapter = BlenderAdapter(config, jobs)

    def failed_hash(_path):
        raise PermissionError("input hash unavailable")

    with monkeypatch.context() as patch:
        if failure == "missing_python":
            config.agent_python.unlink()
        else:
            patch.setattr("forgeflow.adapters.blender_adapter.sha256_file", failed_hash)
        with pytest.raises((ValueError, PermissionError)):
            adapter.build_proposal(job, "색상 수정")

    assert not list((root / "blender").iterdir())
    assert not job.blender_requests
    config.agent_python.touch()
    _command, request = adapter.build_proposal(job, "색상 수정")
    assert request.version == 1
    assert Path(request.output_directory).is_dir()


def test_proposal_skips_empty_and_nonempty_orphan_version_folders(config, image):
    jobs = JobService(config.jobs_root)
    job = jobs.create("preserve orphan versions", image)
    root = jobs.job_directory(job.job_id)
    source = root / "modeling" / "source.blend"
    source.write_bytes(b"original Blender input")
    job.blender_input_path = str(source)
    config.agent_python.parent.mkdir(parents=True)
    config.agent_python.touch()
    empty = root / "blender" / "v001"
    occupied = root / "blender" / "v002"
    empty.mkdir()
    occupied.mkdir()
    old_log = occupied / "planning.log"
    old_log.write_bytes(b"old orphan diagnostics")

    _command, request = BlenderAdapter(config, jobs).build_proposal(job, "색상 수정")

    assert request.version == 3
    assert Path(request.output_directory).name == "v003"
    assert empty.is_dir() and not list(empty.iterdir())
    assert old_log.read_bytes() == b"old orphan diagnostics"
