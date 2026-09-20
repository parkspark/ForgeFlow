from __future__ import annotations

import argparse
import json
from pathlib import Path

from _evidence import E2EEvidence, load_config
from _process import run_command

from forgeflow.adapters.blender_adapter import BlenderAdapter
from forgeflow.adapters.modeling_adapter import ModelingAdapter
from forgeflow.domain.artifact import Artifact
from forgeflow.domain.job import utc_now
from forgeflow.services.job_service import JobService, sha256_file


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--jobs-root", type=Path)
    args = parser.parse_args()
    audit = E2EEvidence("full", input_image=str(args.image))
    with audit:
        config = load_config(args.jobs_root)
        jobs = JobService(config.jobs_root)
        modeling = ModelingAdapter(config, jobs)
        blender = BlenderAdapter(config, jobs)
        job = jobs.create("이미지부터 전체 E2E", args.image)
        root = jobs.job_directory(job.job_id)
        audit.bind_job(jobs, job, root / "logs" / "full-e2e-evidence.json")
        evidence = audit.data
        audit.checkpoint(phase="modeling", input_image=job.input_image_path)

        command, run_root = modeling.build_generation(job)
        jobs.set_stage(job, "modeling", "running", log_path=root / "logs" / "modeling.log")
        for index, release in enumerate(modeling.build_release_ollama()):
            if run_command(release, root / "logs" / f"ollama-vram-release-{index}.log", 120) != 0:
                raise RuntimeError("Ollama VRAM release failed")
        if run_command(command, root / "logs" / "modeling.log", 7200) != 0:
            jobs.set_stage(job, "modeling", "failed", error="실제 Pixal3D 생성 실패")
            raise RuntimeError("실제 Pixal3D 생성 실패")
        modeled = modeling.collect_generation(job, run_root)
        jobs.add_artifacts(job, modeled, save=False)
        source = Path(next(item.path for item in modeled if item.kind == "glb"))
        job.blender_input_path = str(source)
        jobs.save(job)
        audit.checkpoint(
            phase="modeling_preview",
            modeling_artifacts=[item.to_dict() for item in modeled],
            source_sha256_before=sha256_file(source),
        )
        preview_command, preview = modeling.build_preview(job)
        if run_command(preview_command, root / "logs" / "preview.log", 600) != 0:
            jobs.set_stage(job, "modeling", "failed", error="미리보기 생성 실패")
            raise RuntimeError("미리보기 생성 실패")
        modeling.verify_artifacts([preview])
        modeling.existing_generation(job)
        jobs.add_artifact(
            job,
            Artifact("preview", str(preview), "modeling", utc_now(), sha256=sha256_file(preview)),
        )
        jobs.set_stage(job, "modeling", "completed")
        evidence["modeling_artifacts"] = [
            item.to_dict() for item in job.artifacts if item.stage == "modeling"
        ]
        evidence["automatic_blender_input"] = job.blender_input_path

        audit.checkpoint(phase="blender_inspect")
        jobs.set_stage(job, "blender", "running", log_path=root / "logs" / "blender.log")
        inspect_command, inspect_before = blender.build_inspect(job, source, "generated")
        if run_command(inspect_command, root / "logs" / "inspect-generated.log", 600) != 0:
            raise RuntimeError("생성 GLB 검사 실패")
        inspected = json.loads(inspect_before.read_text(encoding="utf-8"))
        if not inspected.get("success"):
            raise RuntimeError("생성 GLB 검사 결과가 성공이 아닙니다.")
        meshes = [
            item
            for item in inspected.get("data", {}).get("objects", [])
            if item.get("type") == "MESH"
        ]
        if not meshes:
            raise RuntimeError("생성 GLB에 메시가 없습니다.")
        target = meshes[0]["name"]
        source_hash = sha256_file(source)

        audit.checkpoint(
            phase="blender_proposal", mesh_target=target, inspect_generated=str(inspect_before)
        )
        proposal_command, request = blender.build_proposal(
            job, f"{target} 오브젝트에 smooth shading을 적용해줘"
        )
        jobs.add_blender_request(job, request)
        if run_command(proposal_command, root / "logs" / "proposal.log", 900) not in (0, 2):
            raise RuntimeError("생성 GLB 자연어 계획 실패")
        blender.load_proposal(request)
        tools = [step["tool"] for step in request.plan["steps"]]
        if tools != ["asset.set_smooth_shading"]:
            raise RuntimeError(f"예상과 다른 계획이므로 승인하지 않습니다: {tools}")
        if request.plan["steps"][0]["arguments"].get("target") != target:
            raise RuntimeError("계획의 대상이 검사된 메시와 다릅니다.")
        jobs.save(job)
        audit.checkpoint(
            phase="blender_execution", approved_plan=request.plan, plan_sha256=request.plan_sha256
        )
        execute_command, execution, approved_hash = blender.build_execute(job, request)
        request.status = "running"
        request.approved_at = utc_now()
        jobs.save(job)
        if run_command(execute_command, root / "logs" / "blender.log", 1800) != 0:
            jobs.set_stage(job, "blender", "failed", error="생성 GLB Blender 편집 실패")
            raise RuntimeError("생성 GLB Blender 편집 실패")
        edited = blender.collect_execution(request, execution, approved_hash)
        for artifact in edited:
            jobs.add_artifact(job, artifact)
        result_glb = Path(next(item.path for item in edited if item.kind == "glb"))
        job.blender_input_path = str(result_glb)
        jobs.save(job)
        audit.checkpoint(
            phase="blender_result_inspect",
            source_sha256_after=sha256_file(source),
            blender_artifacts=[item.to_dict() for item in edited],
        )
        if sha256_file(source) != source_hash:
            raise RuntimeError("전체 E2E에서 모델링 원본 해시가 변경되었습니다.")
        after_command, inspect_after = blender.build_inspect(job, result_glb, "full-e2e-result")
        if run_command(after_command, root / "logs" / "inspect-result.log", 600) != 0:
            raise RuntimeError("전체 E2E 결과 GLB 검사 실패")

        after = json.loads(inspect_after.read_text(encoding="utf-8"))
        if not after.get("success"):
            raise RuntimeError("전체 E2E 결과 GLB 검사 결과가 성공이 아닙니다.")
        jobs.set_stage(job, "blender", "completed")
        evidence.update(
            {
                "status": "passed",
                "job_json": str(root / "job.json"),
                "source_sha256_before": source_hash,
                "source_sha256_after": sha256_file(source),
                "mesh_target": target,
                "inspect_generated": str(inspect_before),
                "approved_plan": request.plan,
                "plan_sha256": request.plan_sha256,
                "blender_artifacts": [item.to_dict() for item in edited],
                "inspect_result": str(inspect_after),
                "logs": [str(path) for path in (root / "logs").glob("*.log")],
            }
        )
        audit.checkpoint(phase="completed")
    return audit.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
