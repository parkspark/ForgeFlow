"""Durable evidence and interrupted-state cleanup for live E2E commands."""

from __future__ import annotations

import json
import time
import traceback
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from forgeflow.config import AppConfig
from forgeflow.domain.job import Job, utc_now
from forgeflow.services.job_service import JobService


def load_config(jobs_root: Path | None) -> AppConfig:
    config = AppConfig.load()
    return replace(config, jobs_root=jobs_root) if jobs_root is not None else config


def default_evidence_path(name: str) -> Path:
    return (
        Path(__file__).resolve().parents[2]
        / "logs"
        / "e2e-runs"
        / f"{name}-{uuid4().hex[:12]}"
        / "evidence.json"
    )


class E2EEvidence:
    def __init__(self, name: str, **metadata):
        self.path = default_evidence_path(name)
        self._paths = [self.path]
        self.data = {"status": "running", "started_at": utc_now(), **metadata}
        self.started = time.monotonic()
        self.jobs: JobService | None = None
        self.job: Job | None = None

    def __enter__(self):
        self.save()
        return self

    def bind_job(self, jobs: JobService, job: Job, path: Path) -> None:
        self.jobs, self.job = jobs, job
        self.path = path
        self._paths.append(path)
        self.checkpoint(
            job_id=job.job_id,
            job_directory=str(jobs.job_directory(job.job_id)),
            job_json=str(jobs.job_directory(job.job_id) / "job.json"),
        )

    def checkpoint(self, **values) -> None:
        self.data.update(values)
        self.save()

    def save(self) -> None:
        payload = json.dumps(self.data, ensure_ascii=False, indent=2) + "\n"
        for path in self._paths:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(f".{path.name}-{uuid4().hex}.tmp")
            temporary.write_text(payload, encoding="utf-8")
            temporary.replace(path)

    def _fail_running_state(self, message: str) -> None:
        if self.jobs is None or self.job is None:
            return
        job = self.job
        now = utc_now()
        failed_stage = str(self.data.get("phase", "")).split("_", 1)[0]
        for name, state in job.stages.items():
            if name == failed_stage and state.status in {"pending", "running"}:
                state.status = "failed"
                state.completed_at = now
                state.error = message
                job.errors.append({"stage": name, "message": message, "at": now})
        if failed_stage == "blender":
            request = job.latest_blender_request
            if request and request.status in {"planning", "running", "awaiting_approval"}:
                request.status = "failed"
        if failed_stage == "rigging":
            request = job.latest_rigging_request
            if request and request.status in {"pending", "running"}:
                request.status = "failed"
                request.error = message
                request.completed_at = now
        for turn in job.unity_turns:
            if turn.turn_id == self.data.get("active_turn_id") and turn.status == "running":
                turn.status = "failed"
                turn.error = message
                turn.completed_at = now
            for run in self.data.get("runs", []):
                if run.get("turn_id") == turn.turn_id:
                    run.update(status=turn.status, error=turn.error)
        self.jobs.save(job)

    def __exit__(self, exc_type, exc, _traceback):
        self.data["finished_at"] = utc_now()
        self.data["duration_seconds"] = round(time.monotonic() - self.started, 3)
        failed = exc is not None or self.data["status"] == "failed"
        self.data["status"] = "failed" if failed else "passed"
        if exc is not None:
            message = f"{exc_type.__name__}: {exc}"
            self.data["error"] = message
            self.data["traceback"] = "".join(traceback.format_exception(exc_type, exc, _traceback))
        if failed:
            message = self.data.get("error", self.data.get("shutdown_error", "E2E failed"))
            try:
                self._fail_running_state(message)
            except Exception as save_error:
                self.data["state_save_error"] = f"{type(save_error).__name__}: {save_error}"
            print(message, flush=True)
        self.save()
        print(f"EVIDENCE={self.path}", flush=True)
        return exc is not None

    @property
    def exit_code(self) -> int:
        return 0 if self.data["status"] == "passed" else 1
