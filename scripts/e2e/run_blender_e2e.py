from __future__ import annotations

import argparse
import json
from pathlib import Path

from _evidence import E2EEvidence, load_config
from _process import run_command

from forgeflow.adapters.blender_adapter import BlenderAdapter
from forgeflow.domain.artifact import Artifact
from forgeflow.domain.job import utc_now
from forgeflow.services.job_service import JobService, sha256_file


def dimensions(payload: dict, target: str) -> list[float]:
    for item in payload.get("data", {}).get("objects", []):
        if item.get("name") == target:
            return [float(value) for value in item["dimensions"]]
    raise RuntimeError(f"검사 결과에서 메시를 찾을 수 없습니다: {target}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--asset", type=Path, required=True)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--jobs-root", type=Path)
    args = parser.parse_args()
    audit = E2EEvidence("blender", source_glb=str(args.asset), input_image=str(args.image))
    with audit:
        config = load_config(args.jobs_root)
        source = args.asset.resolve(strict=True)
        source_hash = sha256_file(source)
        jobs = JobService(config.jobs_root)
        adapter = BlenderAdapter(config, jobs)
        job = jobs.create("기존 GLB Blender E2E", args.image)
        job.blender_input_path = str(source)
        job.current_stage = "blender"
        job.stages["modeling"].status = "completed"
        jobs.add_artifact(
            job, Artifact("glb", str(source), "modeling", utc_now(), sha256=source_hash)
        )
        root = jobs.job_directory(job.job_id)
        audit.bind_job(jobs, job, root / "logs" / "e2e-evidence.json")
        audit.checkpoint(
            phase="blender_inspect", source_glb=str(source), source_sha256_before=source_hash
        )
        jobs.set_stage(job, "blender", "running", log_path=root / "logs" / "execution-process.log")

        inspect_command, inspect_path = adapter.build_inspect(job, source, "before")
        if run_command(inspect_command, root / "logs" / "inspect-before-process.log") != 0:
            raise RuntimeError("변경 전 scene.inspect 실패")
        before = json.loads(inspect_path.read_text(encoding="utf-8"))
        if not before.get("success"):
            raise RuntimeError("변경 전 scene.inspect 결과가 성공이 아닙니다.")
        meshes = [
            item for item in before.get("data", {}).get("objects", []) if item.get("type") == "MESH"
        ]
        if not meshes:
            raise RuntimeError("검사된 메시가 없습니다.")
        target = meshes[0]["name"]
        before_dimensions = dimensions(before, target)

        request_text = f"{target} 오브젝트의 scale을 [1.05, 1.05, 1.05]로 설정하고 {target}에 smooth shading을 적용해줘"
        audit.checkpoint(
            phase="blender_proposal", mesh_target=target, dimensions_before=before_dimensions
        )
        proposal_command, request = adapter.build_proposal(job, request_text)
        jobs.add_blender_request(job, request)
        if run_command(proposal_command, root / "logs" / "proposal-process.log") not in (0, 2):
            raise RuntimeError("자연어 계획 생성 실패")
        adapter.load_proposal(request)
        tools = [step["tool"] for step in request.plan["steps"]]
        if tools != ["asset.transform", "asset.set_smooth_shading"]:
            raise RuntimeError(f"예상과 다른 계획이므로 승인하지 않습니다: {tools}")
        for step in request.plan["steps"]:
            if step["arguments"].get("target") != target:
                raise RuntimeError("계획의 대상이 검사된 메시와 다릅니다.")
        jobs.save(job)

        audit.checkpoint(
            phase="blender_execution", approved_plan=request.plan, plan_sha256=request.plan_sha256
        )
        command, execution_path, approved_source_hash = adapter.build_execute(job, request)
        request.status = "running"
        request.approved_at = utc_now()
        jobs.save(job)
        if run_command(command, root / "logs" / "execution-process.log") != 0:
            raise RuntimeError("승인된 Blender 계획 실행 실패")
        artifacts = adapter.collect_execution(request, execution_path, approved_source_hash)
        for artifact in artifacts:
            jobs.add_artifact(job, artifact)
        result_glb = Path(next(item.path for item in artifacts if item.kind == "glb"))
        job.blender_input_path = str(result_glb)
        jobs.save(job)
        audit.checkpoint(
            phase="blender_result_inspect", result_artifacts=[item.to_dict() for item in artifacts]
        )

        after_command, after_path = adapter.build_inspect(job, result_glb, "after")
        if run_command(after_command, root / "logs" / "inspect-after-process.log") != 0:
            raise RuntimeError("변경 후 scene.inspect 실패")
        after = json.loads(after_path.read_text(encoding="utf-8"))
        if not after.get("success"):
            raise RuntimeError("변경 후 scene.inspect 결과가 성공이 아닙니다.")
        after_dimensions = dimensions(after, target)
        ratios = [after_dimensions[index] / before_dimensions[index] for index in range(3)]
        if any(abs(value - 1.05) > 0.01 for value in ratios):
            raise RuntimeError(f"dimensions가 1.05배가 아닙니다: {ratios}")
        final_source_hash = sha256_file(source)
        audit.checkpoint(
            source_sha256_after=final_source_hash,
            dimensions_after=after_dimensions,
            dimension_ratios=ratios,
        )
        if final_source_hash != source_hash:
            raise RuntimeError("외부 원본 GLB 해시가 변경되었습니다.")
        jobs.set_stage(job, "blender", "completed")
        evidence = {
            "status": "passed",
            "job_directory": str(root),
            "job_json": str(root / "job.json"),
            "input_image": job.input_image_path,
            "source_glb": str(source),
            "source_sha256_before": source_hash,
            "source_sha256_after": final_source_hash,
            "mesh_target": target,
            "dimensions_before": before_dimensions,
            "dimensions_after": after_dimensions,
            "dimension_ratios": ratios,
            "approved_plan": request.plan,
            "plan_sha256": request.plan_sha256,
            "result_artifacts": [item.to_dict() for item in artifacts],
            "inspect_before": str(inspect_path),
            "inspect_after": str(after_path),
            "execution": str(execution_path),
        }
        audit.data.update(evidence)
        audit.checkpoint(phase="completed")
    return audit.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
