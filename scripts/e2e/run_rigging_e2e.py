from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from _evidence import E2EEvidence, load_config
from _process import run_command

from forgeflow.adapters.modeling_adapter import ModelingAdapter
from forgeflow.adapters.rigging_adapter import RiggingAdapter
from forgeflow.domain.artifact import Artifact
from forgeflow.domain.job import Job, utc_now
from forgeflow.services.job_service import JobService, sha256_file


def unirig_commit() -> str | None:
    try:
        result = subprocess.run(
            [
                "wsl.exe",
                "-d",
                "Ubuntu-24.04",
                "-u",
                "park",
                "--",
                "git",
                "-C",
                "/home/park/local-modeling/UniRig",
                "rev-parse",
                "HEAD",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except Exception:
        return None


def new_job(jobs: JobService, asset: Path) -> Job:
    job_id = f"rigging-e2e-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:6]}"
    directory = jobs.job_directory(job_id)
    for child in ("input", "modeling", "blender", "rigging", "logs", ".runs"):
        (directory / child).mkdir(parents=True, exist_ok=False)
    now = utc_now()
    job = Job(
        job_id=job_id,
        name=f"{asset.stem}-rigging-e2e",
        created_at=now,
        updated_at=now,
        input_image_path=str(asset),
    )
    job.stages["modeling"].status = "completed"
    jobs.save(job)
    jobs.add_artifact(
        job, Artifact("glb", str(asset), "modeling", utc_now(), sha256=sha256_file(asset))
    )
    return job


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="ForgeFlow RiggingAdapter 실제 UniRig E2E")
    parser.add_argument("--asset", required=True, help="검증할 절대 GLB 경로")
    parser.add_argument("--seed", type=int, default=12345)
    parser.add_argument("--jobs-root", type=Path)
    parser.add_argument("--timeout", type=float, default=7200)
    parser.add_argument("--preview-timeout", type=float, default=600)
    args = parser.parse_args()

    audit = E2EEvidence("rigging", input_glb=args.asset, seed=args.seed, verdict="RUNNING")
    with audit:
        config = load_config(args.jobs_root)
        jobs = JobService(config.jobs_root)
        asset = RiggingAdapter.validate_input(args.asset)
        input_before = sha256_file(asset)
        job = new_job(jobs, asset)
        root = jobs.job_directory(job.job_id)
        audit.bind_job(jobs, job, root / "logs" / "rigging-e2e-evidence.json")
        audit.checkpoint(
            phase="rigging",
            input_glb=str(asset),
            input_sha256_before=input_before,
            unirig_commit=unirig_commit(),
        )
        adapter = RiggingAdapter(config, jobs)
        modeling = ModelingAdapter(config, jobs)
        command, run = adapter.build_rigging(job, asset, args.seed)
        run.request.status = "running"
        run.request.started_at = utc_now()
        jobs.add_rigging_request(job, run.request)
        log_path = run.final_directory / "rigging.log"
        jobs.set_stage(job, "rigging", "running", log_path=log_path)
        audit.checkpoint(output_directory=str(run.final_directory), staged=run.staged)
        try:
            for index, release in enumerate(modeling.build_release_ollama()):
                if (
                    run_command(release, root / "logs" / f"ollama-vram-release-{index}.log", 120)
                    != 0
                ):
                    raise RuntimeError("Ollama VRAM release failed")
            exit_code = run_command(command, log_path, args.timeout)
            audit.checkpoint(exit_code=exit_code)
            if exit_code != 0:
                raise RuntimeError(f"rig_humanoid.ps1 종료 코드 {exit_code}")
            artifacts, report = adapter.collect_rigging(run)
            adapter.register_success(job, run, artifacts)
            audit.checkpoint(phase="rigging_preview", rig_report=report)
            for pose in (False, True):
                preview_command, output, kind = adapter.build_preview(run, pose)
                preview_code = run_command(
                    preview_command, run.final_directory / f"{kind}.log", args.preview_timeout
                )
                if preview_code != 0:
                    raise RuntimeError(f"{kind} 종료 코드 {preview_code}")
                artifact = adapter.collect_preview(run, output, kind)
                jobs.add_artifact(job, artifact)
                audit.checkpoint(**{kind: artifact.path})
            if sha256_file(asset) != input_before:
                raise RuntimeError("리깅 E2E 입력 원본 GLB 해시가 변경되었습니다.")
            jobs.set_stage(job, "rigging", "completed")
            audit.checkpoint(
                phase="completed",
                artifacts={
                    item.kind: {
                        "path": item.path,
                        "sha256": item.sha256,
                        "size": Path(item.path).stat().st_size,
                    }
                    for item in job.artifacts
                    if item.stage == "rigging"
                },
                humanoid_fbx=job.humanoid_fbx_path,
                humanoid_blend=job.humanoid_blend_path,
                unity_input_path=job.unity_input_path,
                **{
                    key: report.get(key)
                    for key in (
                        "bone_count",
                        "vertex_count",
                        "weighted_vertices",
                        "max_influences",
                        "missing_required_bones",
                    )
                },
            )
        finally:
            audit.checkpoint(input_sha256_after=sha256_file(asset) if asset.is_file() else None)
    audit.checkpoint(verdict="PASS" if audit.exit_code == 0 else "FAIL")
    return audit.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
