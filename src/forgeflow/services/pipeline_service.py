from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal

from forgeflow.adapters.blender_adapter import BlenderAdapter
from forgeflow.adapters.modeling_adapter import ModelingAdapter, ProcessCommand
from forgeflow.domain.artifact import Artifact
from forgeflow.domain.job import BlenderRequest, Job, utc_now

from .job_service import JobService, sha256_file
from .process_service import ProcessService


class PipelineService(QObject):
    log_received = Signal(str)
    event_received = Signal(object)
    job_changed = Signal(object)
    plan_ready = Signal(object, object)
    inspect_ready = Signal(object)
    operation_finished = Signal(str, bool, str)

    def __init__(self, jobs: JobService, modeling: ModelingAdapter, blender: BlenderAdapter, parent: QObject | None = None):
        super().__init__(parent)
        self.jobs = jobs
        self.modeling = modeling
        self.blender = blender
        self.process = ProcessService(self)
        self.process.line_received.connect(self._on_line)
        self.process.finished.connect(self._on_finished)
        self.process.failed_to_start.connect(self._on_start_error)
        self._handler: Callable[[int], None] | None = None
        self._active_job: Job | None = None
        self._operation = ""
        self._log_path: Path | None = None
        self._json_events: list[dict[str, Any]] = []
        self._cancelled = False

    @property
    def busy(self) -> bool:
        return self.process.running or self._handler is not None

    def start_modeling(self, job: Job) -> None:
        if job.stages["modeling"].status == "completed":
            raise RuntimeError("완료된 모델링 원본은 덮어쓸 수 없습니다.")
        command, run_root = self.modeling.build_generation(job)
        log_path = self.jobs.job_directory(job.job_id) / "logs" / f"modeling-attempt-{job.stages['modeling'].attempts + 1:03d}.log"
        self.jobs.set_stage(job, "modeling", "running", log_path=log_path)
        release = self.modeling.build_release_ollama()
        if release is not None:
            self._begin(job, "modeling_vram", release, log_path, lambda _code: self._begin(job, "modeling", command, log_path, lambda code: self._generation_finished(job, run_root, code)))
        else:
            self._begin(job, "modeling", command, log_path, lambda code: self._generation_finished(job, run_root, code))

    def _generation_finished(self, job: Job, run_root: Path, exit_code: int) -> None:
        if exit_code != 0:
            self._fail_stage(job, "modeling", f"이미지 생성 프로세스가 종료 코드 {exit_code}로 실패했습니다.")
            return
        try:
            artifacts = self.modeling.collect_generation(job, run_root)
            for artifact in artifacts:
                self.jobs.add_artifact(job, artifact)
            job.blender_input_path = next(item.path for item in artifacts if item.kind == "glb")
            self.jobs.save(job)
            command, preview = self.modeling.build_preview(job)
            self._begin(job, "modeling_preview", command, self._log_path, lambda code: self._preview_finished(job, preview, code))
        except Exception as exc:
            self._fail_stage(job, "modeling", str(exc))

    def _preview_finished(self, job: Job, preview: Path, exit_code: int) -> None:
        try:
            if exit_code != 0:
                raise RuntimeError(f"3D 미리보기 생성이 종료 코드 {exit_code}로 실패했습니다.")
            self.modeling.verify_artifacts([preview])
            self.jobs.add_artifact(
                job,
                Artifact("preview", str(preview.resolve()), "modeling", utc_now(), sha256=sha256_file(preview)),
            )
            self.jobs.set_stage(job, "modeling", "completed")
            self.job_changed.emit(job)
            self.operation_finished.emit("modeling", True, "GLB/BLEND/FBX와 미리보기를 생성했습니다.")
        except Exception as exc:
            self._fail_stage(job, "modeling", str(exc))

    def start_inspect(self, job: Job, input_path: Path | None = None, suffix: str = "source") -> None:
        command, output = self.blender.build_inspect(job, input_path, suffix)
        log_path = self.jobs.job_directory(job.job_id) / "logs" / f"inspect-{suffix}.log"
        self._begin(job, "inspect", command, log_path, lambda code: self._inspect_finished(output, code))

    def _inspect_finished(self, output: Path, exit_code: int) -> None:
        if exit_code != 0:
            self.operation_finished.emit("inspect", False, f"장면 검사가 종료 코드 {exit_code}로 실패했습니다.")
            return
        try:
            payload = json.loads(output.read_text(encoding="utf-8"))
            if not payload.get("success"):
                raise RuntimeError(payload.get("summary", "장면 검사 실패"))
            self.inspect_ready.emit(payload)
            self.operation_finished.emit("inspect", True, payload.get("summary", "장면 검사를 완료했습니다."))
        except Exception as exc:
            self.operation_finished.emit("inspect", False, str(exc))

    def start_proposal(self, job: Job, user_request: str) -> None:
        command, request = self.blender.build_proposal(job, user_request)
        self.jobs.add_blender_request(job, request)
        log_path = Path(request.output_directory) / "planning.log"
        self._begin(job, "blender_plan", command, log_path, lambda code: self._proposal_finished(job, request, code))

    def _proposal_finished(self, job: Job, request: BlenderRequest, exit_code: int) -> None:
        try:
            envelope = self.blender.load_proposal(request)
            self.jobs.save(job)
            self.job_changed.emit(job)
            self.plan_ready.emit(job, request)
            self.operation_finished.emit("blender_plan", True, envelope["result"].get("summary", "계획 준비 완료"))
        except Exception as exc:
            request.status = "failed"
            self.jobs.save(job)
            detail = f"계획 프로세스 종료 코드 {exit_code}: {exc}"
            self.job_changed.emit(job)
            self.operation_finished.emit("blender_plan", False, detail)

    def approve(self, job: Job) -> None:
        request = job.latest_blender_request
        if request is None:
            raise RuntimeError("승인할 Blender 계획이 없습니다.")
        command, execution, original_hash = self.blender.build_execute(job, request)
        request.status = "running"
        request.approved_at = utc_now()
        log_path = Path(request.output_directory) / "execution.log"
        self.jobs.set_stage(job, "blender", "running", log_path=log_path)
        self._begin(job, "blender", command, log_path, lambda code: self._execution_finished(job, request, execution, original_hash, code))

    def _execution_finished(self, job: Job, request: BlenderRequest, execution: Path, original_hash: str, exit_code: int) -> None:
        if exit_code != 0:
            request.status = "failed"
            self.jobs.save(job)
            source = Path(request.input_path)
            integrity = ""
            if source.is_file() and sha256_file(source) != original_hash:
                integrity = " 원본 파일 해시도 변경되었습니다."
            self._fail_stage(job, "blender", f"Blender 실행 프로세스가 종료 코드 {exit_code}로 실패했습니다.{integrity}")
            return
        try:
            artifacts = self.blender.collect_execution(request, execution, original_hash)
            for artifact in artifacts:
                self.jobs.add_artifact(job, artifact)
            glb = next(item.path for item in artifacts if item.kind == "glb")
            job.blender_input_path = glb
            self.jobs.set_stage(job, "blender", "completed")
            self.job_changed.emit(job)
            self.operation_finished.emit("blender", True, f"Blender v{request.version:03d} 결과를 생성했습니다.")
        except Exception as exc:
            request.status = "failed"
            self.jobs.save(job)
            self._fail_stage(job, "blender", str(exc))

    def deny(self, job: Job) -> None:
        request = job.latest_blender_request
        if request and request.status == "awaiting_approval":
            request.status = "cancelled"
            self.jobs.save(job)
            self.job_changed.emit(job)
            self.operation_finished.emit("blender_plan", False, "사용자가 실행 계획을 취소했습니다.")

    def cancel_active(self) -> None:
        if not self.process.running:
            return
        self._cancelled = True
        self.process.cancel()

    def _begin(self, job: Job, operation: str, command: ProcessCommand, log_path: Path | None, handler: Callable[[int], None]) -> None:
        if self.process.running:
            raise RuntimeError("다른 작업이 실행 중입니다.")
        self._active_job = job
        self._operation = operation
        self._log_path = log_path
        self._json_events = []
        self._cancelled = False
        self._handler = handler
        if log_path:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text("", encoding="utf-8")
        self.event_received.emit({"type": "stage_started", "stage": operation})
        self.job_changed.emit(job)
        self.process.start(command)

    def _on_line(self, channel: str, line: str) -> None:
        rendered = f"[{channel}] {line}"
        self.log_received.emit(rendered)
        if self._log_path:
            with self._log_path.open("a", encoding="utf-8") as handle:
                handle.write(rendered + "\n")
        if channel == "OUT" and line.lstrip().startswith("{"):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                return
            if isinstance(event, dict) and "type" in event:
                self._json_events.append(event)
                self.event_received.emit(event)

    def _on_finished(self, exit_code: int, _exit_status) -> None:
        self.process.flush()
        handler, self._handler = self._handler, None
        if self._cancelled:
            job = self._active_job
            if job and self._operation in {"modeling", "modeling_preview", "blender"}:
                stage = "modeling" if self._operation.startswith("modeling") else "blender"
                self.jobs.set_stage(job, stage, "cancelled", error="사용자가 실행을 취소했습니다.")
                self.job_changed.emit(job)
            self.operation_finished.emit(self._operation, False, "실행을 취소했습니다.")
            return
        if handler:
            handler(exit_code)

    def _on_start_error(self, message: str) -> None:
        handler, self._handler = self._handler, None
        job = self._active_job
        if job and self._operation in {"modeling", "modeling_preview", "blender"}:
            stage = "modeling" if self._operation.startswith("modeling") else "blender"
            self._fail_stage(job, stage, f"프로세스를 시작할 수 없습니다: {message}")
        else:
            self.operation_finished.emit(self._operation, False, message)

    def _fail_stage(self, job: Job, stage: str, message: str) -> None:
        self.jobs.set_stage(job, stage, "failed", error=message)
        self.job_changed.emit(job)
        self.operation_finished.emit(stage, False, message)
