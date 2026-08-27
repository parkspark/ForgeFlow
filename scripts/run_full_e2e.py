from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from forgeflow.adapters.blender_adapter import BlenderAdapter
from forgeflow.adapters.modeling_adapter import ModelingAdapter
from forgeflow.config import AppConfig
from forgeflow.domain.artifact import Artifact
from forgeflow.domain.job import utc_now
from forgeflow.services.job_service import JobService, sha256_file
from forgeflow.services.process_service import SyncProcessRunner


def run(command, log: Path, timeout: float = 7200) -> int:
    lines: list[str] = []
    def receive(channel: str, line: str) -> None:
        rendered = f"[{channel}] {line}"
        sys.stdout.buffer.write((rendered + "\n").encode("utf-8", errors="replace"))
        sys.stdout.buffer.flush()
        lines.append(rendered)
    code = SyncProcessRunner().run(command, receive, timeout=timeout)
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return code


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", type=Path, required=True)
    args = parser.parse_args()
    config = AppConfig.load()
    jobs = JobService(config.jobs_root)
    modeling = ModelingAdapter(config, jobs)
    blender = BlenderAdapter(config, jobs)
    job = jobs.create("이미지부터 전체 E2E", args.image)
    root = jobs.job_directory(job.job_id)
    evidence: dict = {"status": "running", "job_directory": str(root), "input_image": job.input_image_path}

    release = modeling.build_release_ollama()
    if release:
        run(release, root / "logs" / "ollama-vram-release.log", 120)
    command, run_root = modeling.build_generation(job)
    jobs.set_stage(job, "modeling", "running", log_path=root / "logs" / "modeling.log")
    if run(command, root / "logs" / "modeling.log") != 0:
        jobs.set_stage(job, "modeling", "failed", error="실제 Pixal3D 생성 실패")
        raise RuntimeError("실제 Pixal3D 생성 실패")
    modeled = modeling.collect_generation(job, run_root)
    for artifact in modeled:
        jobs.add_artifact(job, artifact)
    source = Path(next(item.path for item in modeled if item.kind == "glb"))
    job.blender_input_path = str(source)
    preview_command, preview = modeling.build_preview(job)
    if run(preview_command, root / "logs" / "preview.log", 600) != 0:
        jobs.set_stage(job, "modeling", "failed", error="미리보기 생성 실패")
        raise RuntimeError("미리보기 생성 실패")
    modeling.verify_artifacts([preview])
    jobs.add_artifact(job, Artifact("preview", str(preview), "modeling", utc_now(), sha256=sha256_file(preview)))
    jobs.set_stage(job, "modeling", "completed")
    evidence["modeling_artifacts"] = [item.to_dict() for item in job.artifacts if item.stage == "modeling"]
    evidence["automatic_blender_input"] = job.blender_input_path

    inspect_command, inspect_before = blender.build_inspect(job, source, "generated")
    if run(inspect_command, root / "logs" / "inspect-generated.log", 600) != 0:
        raise RuntimeError("생성 GLB 검사 실패")
    inspected = json.loads(inspect_before.read_text(encoding="utf-8"))
    meshes = [item for item in inspected.get("data", {}).get("objects", []) if item.get("type") == "MESH"]
    if not meshes:
        raise RuntimeError("생성 GLB에 메시가 없습니다.")
    target = meshes[0]["name"]
    source_hash = sha256_file(source)

    proposal_command, request = blender.build_proposal(job, f"{target} 오브젝트에 smooth shading을 적용해줘")
    jobs.add_blender_request(job, request)
    if run(proposal_command, root / "logs" / "proposal.log", 900) not in (0, 2):
        raise RuntimeError("생성 GLB 자연어 계획 실패")
    blender.load_proposal(request)
    tools = [step["tool"] for step in request.plan["steps"]]
    if tools != ["asset.set_smooth_shading"]:
        raise RuntimeError(f"예상과 다른 계획이므로 승인하지 않습니다: {tools}")
    jobs.save(job)
    execute_command, execution, approved_hash = blender.build_execute(job, request)
    request.status = "running"
    request.approved_at = utc_now()
    jobs.set_stage(job, "blender", "running", log_path=root / "logs" / "blender.log")
    if run(execute_command, root / "logs" / "blender.log", 1800) != 0:
        jobs.set_stage(job, "blender", "failed", error="생성 GLB Blender 편집 실패")
        raise RuntimeError("생성 GLB Blender 편집 실패")
    edited = blender.collect_execution(request, execution, approved_hash)
    for artifact in edited:
        jobs.add_artifact(job, artifact)
    result_glb = Path(next(item.path for item in edited if item.kind == "glb"))
    job.blender_input_path = str(result_glb)
    jobs.set_stage(job, "blender", "completed")
    if sha256_file(source) != source_hash:
        raise RuntimeError("전체 E2E에서 모델링 원본 해시가 변경되었습니다.")
    after_command, inspect_after = blender.build_inspect(job, result_glb, "full-e2e-result")
    if run(after_command, root / "logs" / "inspect-result.log", 600) != 0:
        raise RuntimeError("전체 E2E 결과 GLB 검사 실패")

    evidence.update({
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
    })
    evidence_path = root / "logs" / "full-e2e-evidence.json"
    evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    (root / "logs" / "test-results.txt").write_text("Full image-to-Blender E2E: PASSED\n", encoding="utf-8")
    jobs.save(job)
    print(json.dumps({"type": "full_e2e_completed", "job_directory": str(root), "evidence": str(evidence_path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

