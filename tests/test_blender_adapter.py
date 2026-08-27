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
    request = BlenderRequest("확대", str(source), str(jobs.job_directory(job.job_id) / "blender" / "v001"), 1)
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
    proposal.write_text(json.dumps({
        "schema_version": 1,
        "prompt": "test",
        "plan_sha256": plan_hash(plan),
        "result": {"status": "denied", "summary": "approval required", "plan": plan, "log_path": "session.json"},
    }), encoding="utf-8")
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
    request = BlenderRequest("확대", str(source), str(output), 1, status="running", plan=plan, plan_sha256=plan_hash(plan))
    execution = output / "execution.json"
    execution.write_text(json.dumps({
        "plan_sha256": request.plan_sha256,
        "result": {"status": "completed", "artifacts": files, "log_path": "session.json"},
    }), encoding="utf-8")
    artifacts = BlenderAdapter(config, jobs).collect_execution(request, execution, original)
    assert {item.kind for item in artifacts} == {"glb", "blend", "fbx"}
    assert sha256_file(source) == original
    source.write_bytes(b"tampered")
    with pytest.raises(RuntimeError, match="원본 파일 해시"):
        BlenderAdapter(config, jobs).collect_execution(request, execution, original)

