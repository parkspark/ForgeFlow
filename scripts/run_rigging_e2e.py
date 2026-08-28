from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from forgeflow.adapters.modeling_adapter import ModelingAdapter
from forgeflow.adapters.rigging_adapter import RiggingAdapter
from forgeflow.config import AppConfig
from forgeflow.domain.artifact import Artifact
from forgeflow.domain.job import Job, utc_now
from forgeflow.services.job_service import JobService, sha256_file
from forgeflow.services.process_service import SyncProcessRunner


def unirig_commit() -> str | None:
    try:
        result = subprocess.run(
            ["wsl.exe", "-d", "Ubuntu-24.04", "-u", "park", "--", "git", "-C",
             "/home/park/local-modeling/UniRig", "rev-parse", "HEAD"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            encoding="utf-8", errors="replace", timeout=30, check=False,
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
        job_id=job_id, name="melting-knight-rigging-e2e", created_at=now, updated_at=now,
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
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    evidence_path = repo_root / "logs" / "rigging-e2e-evidence.json"
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    asset = RiggingAdapter.validate_input(args.asset)
    input_before = sha256_file(asset)
    started = time.monotonic()
    evidence: dict[str, object] = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "input_glb": str(asset),
        "input_sha256_before": input_before,
        "input_sha256_after": None,
        "seed": args.seed,
        "unirig_commit": unirig_commit(),
        "exit_code": None,
        "duration_seconds": None,
        "artifacts": {},
        "rig_report": None,
        "bone_count": None,
        "vertex_count": None,
        "weighted_vertices": None,
        "max_influences": None,
        "missing_required_bones": None,
        "rest_preview": None,
        "pose_preview": None,
        "verdict": "RUNNING",
    }
    exit_code = -1
    try:
        config = AppConfig(jobs_root=repo_root / "logs" / "rigging-e2e-jobs")
        jobs = JobService(config.jobs_root)
        job = new_job(jobs, asset)
        adapter = RiggingAdapter(config, jobs)
        modeling = ModelingAdapter(config, jobs)
        command, run = adapter.build_rigging(job, asset, args.seed)
        run.request.status = "running"
        run.request.started_at = utc_now()
        jobs.add_rigging_request(job, run.request)
        jobs.set_stage(job, "rigging", "running", log_path=run.final_directory / "rigging.log")
        evidence["job_id"] = job.job_id
        evidence["output_directory"] = str(run.final_directory)
        evidence["staged"] = run.staged
        log_path = run.final_directory / "rigging.log"

        def log_line(channel: str, line: str) -> None:
            rendered = f"[{channel}] {line}"
            print(rendered, flush=True)
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write(rendered + "\n")

        release = modeling.build_release_ollama()
        if release is not None:
            print("[ForgeFlow] Ollama VRAM release", flush=True)
            SyncProcessRunner().run(release, log_line)
        print(f"[ForgeFlow] UniRig start: {asset}", flush=True)
        exit_code = SyncProcessRunner().run(command, log_line)
        evidence["exit_code"] = exit_code
        if exit_code != 0:
            raise RuntimeError(f"rig_humanoid.ps1 종료 코드 {exit_code}")

        artifacts, report = adapter.collect_rigging(run)
        adapter.register_success(job, run, artifacts)
        for pose in (False, True):
            preview_command, output, kind = adapter.build_preview(run, pose)
            preview_code = SyncProcessRunner().run(preview_command, log_line)
            if preview_code != 0:
                raise RuntimeError(f"{kind} 종료 코드 {preview_code}")
            artifact = adapter.collect_preview(run, output, kind)
            jobs.add_artifact(job, artifact)
            evidence[kind] = artifact.path
        jobs.set_stage(job, "rigging", "completed")

        evidence["artifacts"] = {
            item.kind: {
                "path": item.path,
                "sha256": item.sha256,
                "size": Path(item.path).stat().st_size,
            }
            for item in job.artifacts if item.stage == "rigging"
        }
        evidence["rig_report"] = report
        for key in ("bone_count", "vertex_count", "weighted_vertices", "max_influences", "missing_required_bones"):
            evidence[key] = report.get(key)
        evidence["humanoid_fbx"] = job.humanoid_fbx_path
        evidence["humanoid_blend"] = job.humanoid_blend_path
        evidence["unity_input_path"] = job.unity_input_path
        evidence["verdict"] = "PASS"
        print(f"[ForgeFlow] PASS: {job.humanoid_fbx_path}", flush=True)
        return 0
    except Exception as exc:
        evidence["error"] = str(exc)
        evidence["verdict"] = "FAIL"
        if evidence["exit_code"] is None:
            evidence["exit_code"] = exit_code
        print(f"[ForgeFlow] FAIL: {exc}", flush=True)
        return 1
    finally:
        evidence["input_sha256_after"] = sha256_file(asset) if asset.is_file() else None
        evidence["duration_seconds"] = round(time.monotonic() - started, 3)
        evidence["completed_at"] = datetime.now(timezone.utc).isoformat()
        temporary = evidence_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(evidence_path)
        print(f"[ForgeFlow] Evidence: {evidence_path}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
