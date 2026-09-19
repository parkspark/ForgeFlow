from __future__ import annotations

import json
import sys
from pathlib import Path

from forgeflow.adapters.blender_adapter import BlenderAdapter, plan_hash
from forgeflow.adapters.modeling_adapter import ModelingAdapter
from forgeflow.domain.job import BlenderRequest
from forgeflow.domain.process import ProcessCommand
from forgeflow.services.job_service import JobService, sha256_file
from forgeflow.services.process_service import SyncProcessRunner


def test_mock_engine_full_non_ui_pipeline(config, image):
    jobs = JobService(config.jobs_root)
    job = jobs.create("mock-e2e", image)
    run = jobs.job_directory(job.job_id) / ".runs" / "modeling-001" / "source"
    code = (
        "from pathlib import Path; p=Path(r'%s'); p.mkdir(parents=True); "
        "[(p/f'source.{x}').write_bytes(('mock-'+x).encode()) for x in ('glb','blend','fbx')]; print('mock modeling complete')"
    ) % str(run)
    assert (
        SyncProcessRunner().run(ProcessCommand(sys.executable, ["-c", code], config.jobs_root)) == 0
    )
    modeled = ModelingAdapter(config, jobs).collect_generation(job, run.parent)
    for item in modeled:
        jobs.add_artifact(job, item)
    source = Path(next(item.path for item in modeled if item.kind == "glb"))
    job.blender_input_path = str(source)
    output = jobs.job_directory(job.job_id) / "blender" / "v001"
    operation = output / "mock-operation"
    operation.mkdir(parents=True)
    plan = {
        "steps": [
            {
                "number": 1,
                "tool": "asset.set_smooth_shading",
                "arguments": {
                    "input_path": str(source),
                    "output_directory": str(output),
                    "target": "all",
                },
                "description": "전체 smooth shading",
            }
        ],
        "requires_approval": True,
    }
    proposal = output / "proposal.json"
    proposal.write_text(
        json.dumps({"plan_sha256": plan_hash(plan), "result": {"status": "denied", "plan": plan}}),
        encoding="utf-8",
    )
    request = BlenderRequest(
        "전체 smooth shading", str(source), str(output), 1, proposal_path=str(proposal)
    )
    BlenderAdapter(config, jobs).load_proposal(request)
    assert not list(operation.glob("*")), "approval 전에 write artifact가 없어야 한다"
    original = sha256_file(source)
    produced = []
    for suffix in ("glb", "blend", "fbx"):
        path = operation / f"mock.{suffix}"
        path.write_bytes(("edited-" + suffix).encode())
        produced.append(str(path))
    execution = output / "execution.json"
    execution.write_text(
        json.dumps(
            {
                "plan_sha256": request.plan_sha256,
                "result": {"status": "completed", "artifacts": produced},
            }
        ),
        encoding="utf-8",
    )
    artifacts = BlenderAdapter(config, jobs).collect_execution(request, execution, original)
    for item in artifacts:
        jobs.add_artifact(job, item)
    jobs.save(job)
    restored = JobService(config.jobs_root).load(job.job_id)
    assert len(restored.artifacts) == 6
    assert sha256_file(source) == original
