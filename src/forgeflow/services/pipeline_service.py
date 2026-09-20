from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, TextIO

from PySide6.QtCore import QObject, Signal

from forgeflow.adapters.blender_adapter import BlenderAdapter
from forgeflow.adapters.modeling_adapter import ModelingAdapter
from forgeflow.adapters.rigging_adapter import RiggingAdapter, RiggingRun
from forgeflow.domain.artifact import Artifact
from forgeflow.domain.job import BlenderRequest, Job, utc_now
from forgeflow.domain.process import ProcessCommand

from .job_service import JobService, sha256_file
from .process_service import ProcessService


class PipelineService(QObject):
    log_received = Signal(str)
    event_received = Signal(object)
    job_changed = Signal(object)
    plan_ready = Signal(object, object)
    inspect_ready = Signal(object)
    operation_finished = Signal(str, bool, str)

    def __init__(
        self,
        jobs: JobService,
        modeling: ModelingAdapter,
        blender: BlenderAdapter,
        rigging: RiggingAdapter,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self.jobs = jobs
        self.modeling = modeling
        self.blender = blender
        self.rigging = rigging
        self.process = ProcessService(self)
        self.process.line_received.connect(self._on_line)
        self.process.finished.connect(self._on_finished)
        self.process.failed_to_start.connect(self._on_start_error)
        self._handler: Callable[[int], None] | None = None
        self._active_job: Job | None = None
        self._operation = ""
        self._log_path: Path | None = None
        self._log_handle: TextIO | None = None
        self._log_lines_since_flush = 0
        self._json_events: list[dict[str, Any]] = []
        self._cancelled = False
        self._rigging_preview_warnings: list[str] = []

    @property
    def busy(self) -> bool:
        return self.process.running or self._handler is not None

    def start_modeling(self, job: Job) -> None:
        if self.busy:
            raise RuntimeError("다른 작업이 실행 중입니다.")
        if job.stages["modeling"].status == "completed":
            raise RuntimeError("완료된 모델링 원본은 덮어쓸 수 없습니다.")
        originals = self.modeling.existing_generation(job)
        log_path = (
            self.jobs.job_directory(job.job_id)
            / "logs"
            / f"modeling-attempt-{job.stages['modeling'].attempts + 1:03d}.log"
        )
        if originals:
            if not job.blender_input_path:
                job.blender_input_path = next(item.path for item in originals if item.kind == "glb")
            self.jobs.set_stage(job, "modeling", "running", log_path=log_path)
            self.log_received.emit(
                "[복구] 검증된 모델링 원본을 보존하고 미리보기만 다시 생성합니다."
            )
            self._start_modeling_preview(job, log_path)
            return
        releases = self.modeling.build_release_ollama()
        command, run_root = self.modeling.build_generation(job)
        self.jobs.set_stage(job, "modeling", "running", log_path=log_path)
        self._release_models(
            job,
            "modeling",
            releases,
            log_path,
            lambda: self._begin(
                job,
                "modeling",
                command,
                log_path,
                lambda code: self._generation_finished(job, run_root, code),
            ),
            lambda message: self._fail_stage(job, "modeling", message),
        )

    def _release_models(self, job, stage, commands, log_path, on_ready, on_failure) -> None:
        if not commands:
            on_ready()
            return
        command = commands[0]
        model = command.arguments[-1]
        self.log_received.emit(f"[VRAM] Ollama 모델 해제: {model}")

        def finished(code):
            if code != 0:
                on_failure(f"Ollama 모델 VRAM 해제 실패: {model} (종료 코드 {code})")
                return
            self._release_models(job, stage, commands[1:], log_path, on_ready, on_failure)

        self._begin(job, stage + "_vram", command, log_path, finished)

    def _generation_finished(self, job: Job, run_root: Path, exit_code: int) -> None:
        if exit_code != 0:
            self._fail_stage(
                job, "modeling", f"이미지 생성 프로세스가 종료 코드 {exit_code}로 실패했습니다."
            )
            return
        try:
            artifacts = self.modeling.collect_generation(job, run_root)
            self.jobs.add_artifacts(job, artifacts, save=False)
            job.blender_input_path = next(item.path for item in artifacts if item.kind == "glb")
            self.jobs.save(job)
            self._start_modeling_preview(job, self._log_path)
        except Exception as exc:
            self._fail_stage(job, "modeling", str(exc))

    def _start_modeling_preview(self, job: Job, log_path: Path | None) -> None:
        try:
            command, preview = self.modeling.build_preview(job)
            self._begin(
                job,
                "modeling_preview",
                command,
                log_path,
                lambda code: self._preview_finished(job, preview, code),
            )
        except Exception as exc:
            self._fail_stage(job, "modeling", str(exc))

    def _preview_finished(self, job: Job, preview: Path, exit_code: int) -> None:
        try:
            if self.modeling.existing_generation(job) is None:
                raise RuntimeError("모델링 원본 검증에 실패했습니다.")
            if exit_code != 0:
                raise RuntimeError(f"3D 미리보기 생성이 종료 코드 {exit_code}로 실패했습니다.")
            self.modeling.verify_artifacts([preview])
            self.jobs.add_artifact(
                job,
                Artifact(
                    "preview",
                    str(preview.resolve()),
                    "modeling",
                    utc_now(),
                    sha256=sha256_file(preview),
                ),
                save=False,
            )
            self.jobs.set_stage(job, "modeling", "completed")
            self.job_changed.emit(job)
            self.operation_finished.emit(
                "modeling", True, "GLB/BLEND/FBX와 미리보기를 생성했습니다."
            )
        except Exception as exc:
            self._fail_stage(job, "modeling", str(exc))

    def start_inspect(
        self, job: Job, input_path: Path | None = None, suffix: str = "source"
    ) -> None:
        command, output = self.blender.build_inspect(job, input_path, suffix)
        log_path = self.jobs.job_directory(job.job_id) / "logs" / f"inspect-{suffix}.log"
        self._begin(
            job, "inspect", command, log_path, lambda code: self._inspect_finished(output, code)
        )

    def _inspect_finished(self, output: Path, exit_code: int) -> None:
        if exit_code != 0:
            self.operation_finished.emit(
                "inspect", False, f"장면 검사가 종료 코드 {exit_code}로 실패했습니다."
            )
            return
        try:
            payload = json.loads(output.read_text(encoding="utf-8"))
            if not payload.get("success"):
                raise RuntimeError(payload.get("summary", "장면 검사 실패"))
            self.inspect_ready.emit(payload)
            self.operation_finished.emit(
                "inspect", True, payload.get("summary", "장면 검사를 완료했습니다.")
            )
        except Exception as exc:
            self.operation_finished.emit("inspect", False, str(exc))

    def start_proposal(self, job: Job, user_request: str) -> None:
        command, request = self.blender.build_proposal(job, user_request)
        self.jobs.add_blender_request(job, request)
        log_path = Path(request.output_directory) / "planning.log"
        self._begin(
            job,
            "blender_plan",
            command,
            log_path,
            lambda code: self._proposal_finished(job, request, code),
        )

    def _proposal_finished(self, job: Job, request: BlenderRequest, exit_code: int) -> None:
        try:
            envelope = self.blender.load_proposal(request)
            self.jobs.save(job)
            self.job_changed.emit(job)
            self.plan_ready.emit(job, request)
            self.operation_finished.emit(
                "blender_plan", True, envelope["result"].get("summary", "계획 준비 완료")
            )
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
        self._begin(
            job,
            "blender",
            command,
            log_path,
            lambda code: self._execution_finished(job, request, execution, original_hash, code),
        )

    def _execution_finished(
        self, job: Job, request: BlenderRequest, execution: Path, original_hash: str, exit_code: int
    ) -> None:
        if exit_code != 0:
            request.status = "failed"
            self.jobs.save(job)
            source = Path(request.input_path)
            integrity = ""
            if source.is_file() and sha256_file(source) != original_hash:
                integrity = " 원본 파일 해시도 변경되었습니다."
            self._fail_stage(
                job,
                "blender",
                f"Blender 실행 프로세스가 종료 코드 {exit_code}로 실패했습니다.{integrity}",
            )
            return
        try:
            artifacts = self.blender.collect_execution(request, execution, original_hash)
            self.jobs.add_artifacts(job, artifacts, save=False)
            glb = next(item.path for item in artifacts if item.kind == "glb")
            job.blender_input_path = glb
            self.jobs.set_stage(job, "blender", "completed")
            self.job_changed.emit(job)
            self.operation_finished.emit(
                "blender", True, f"Blender v{request.version:03d} 결과를 생성했습니다."
            )
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
            self.operation_finished.emit(
                "blender_plan", False, "사용자가 실행 계획을 취소했습니다."
            )

    def start_rigging(
        self, job: Job, selected_input: str | Path | None = None, seed: int | str = 12345
    ) -> None:
        if self.busy:
            raise RuntimeError("다른 작업이 실행 중입니다.")
        releases = self.modeling.build_release_ollama()
        command, run = self.rigging.build_rigging(job, selected_input, seed)
        request = run.request
        request.status = "running"
        request.started_at = utc_now()
        job.rigging_input_path = request.input_path
        self.jobs.add_rigging_request(job, request)
        log_path = run.final_directory / "rigging.log"
        self.jobs.set_stage(job, "rigging", "running", log_path=log_path)
        self._release_models(
            job,
            "rigging",
            releases,
            log_path,
            lambda: self._begin(
                job,
                "rigging",
                command,
                log_path,
                lambda code: self._rigging_finished(job, run, code),
            ),
            lambda message: self._fail_rigging(job, run, message),
        )

    def _rigging_finished(self, job: Job, run: RiggingRun, exit_code: int) -> None:
        if exit_code != 0:
            integrity = ""
            source = Path(run.request.input_path)
            if source.is_file() and sha256_file(source) != run.request.input_sha256:
                integrity = " 입력 원본 GLB 해시도 변경되었습니다."
            self._fail_rigging(
                job, run, f"UniRig 프로세스가 종료 코드 {exit_code}로 실패했습니다.{integrity}"
            )
            return
        try:
            artifacts, _report = self.rigging.collect_rigging(run)
            self.rigging.register_success(job, run, artifacts, save=False)
            self.jobs.set_stage(job, "rigging", "completed")
            self._rigging_preview_warnings = []
            self._start_rigging_preview(job, run, pose=False)
        except Exception as exc:
            self._fail_rigging(job, run, str(exc))

    def _start_rigging_preview(self, job: Job, run: RiggingRun, pose: bool) -> None:
        try:
            command, output, kind = self.rigging.build_preview(run, pose)
            label = "pose" if pose else "rest"
            self._begin(
                job,
                f"rigging_preview_{label}",
                command,
                Path(run.request.output_directory) / "rigging.log",
                lambda code: self._rigging_preview_finished(job, run, output, kind, pose, code),
            )
        except Exception as exc:
            self._rigging_preview_warnings.append(str(exc))
            if not pose:
                self._start_rigging_preview(job, run, pose=True)
            else:
                self._finish_rigging_previews(job, run)

    def _rigging_preview_finished(
        self, job: Job, run: RiggingRun, output: Path, kind: str, pose: bool, exit_code: int
    ) -> None:
        if exit_code != 0:
            self._rigging_preview_warnings.append(
                f"{kind} 생성이 종료 코드 {exit_code}로 실패했습니다."
            )
        else:
            try:
                self.jobs.add_artifact(job, self.rigging.collect_preview(run, output, kind))
            except Exception as exc:
                self._rigging_preview_warnings.append(f"{kind}: {exc}")
        if not pose:
            self._start_rigging_preview(job, run, pose=True)
        else:
            self._finish_rigging_previews(job, run)

    def _finish_rigging_previews(self, job: Job, run: RiggingRun) -> None:
        warning = " ".join(self._rigging_preview_warnings) or None
        run.request.preview_warning = warning
        self.jobs.save(job)
        self.job_changed.emit(job)
        message = f"Humanoid 리깅 v{run.request.version:03d} 구조 검증을 통과했습니다."
        if warning:
            message += f" 미리보기 경고: {warning}"
        self.operation_finished.emit("rigging", True, message)

    def _fail_rigging(self, job: Job, run: RiggingRun, message: str) -> None:
        run.request.status = "failed"
        run.request.error = message
        run.request.completed_at = utc_now()
        self.jobs.save(job)
        self._fail_stage(job, "rigging", message)

    def cancel_active(self) -> None:
        if not self.process.running:
            return
        self._cancelled = True
        self.process.cancel()

    def _begin(
        self,
        job: Job,
        operation: str,
        command: ProcessCommand,
        log_path: Path | None,
        handler: Callable[[int], None],
    ) -> None:
        if self.process.running:
            raise RuntimeError("다른 작업이 실행 중입니다.")
        self._active_job = job
        self._operation = operation
        self._prepare_log(log_path)
        self._json_events = []
        self._cancelled = False
        self._handler = handler
        self.event_received.emit({"type": "stage_started", "stage": operation})
        self.job_changed.emit(job)
        self.process.start(command)

    def _on_line(self, channel: str, line: str) -> None:
        rendered = f"[{channel}] {line}"
        self.log_received.emit(rendered)
        if self._log_handle:
            try:
                self._log_handle.write(rendered + "\n")
                self._log_lines_since_flush += 1
                if self._log_lines_since_flush >= 64:
                    self._log_handle.flush()
                    self._log_lines_since_flush = 0
            except OSError:
                self._close_log()
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
        try:
            if self._cancelled:
                job = self._active_job
                if job and self._operation.startswith("rigging_preview"):
                    request = job.latest_rigging_request
                    if request:
                        request.preview_warning = (
                            "사용자가 미리보기 생성을 취소했습니다. 리그 구조 결과는 유효합니다."
                        )
                        self.jobs.save(job)
                    self.operation_finished.emit(
                        "rigging", True, request.preview_warning if request else "미리보기 취소"
                    )
                    return
                if job and self._operation in {
                    "modeling",
                    "modeling_vram",
                    "modeling_preview",
                    "blender",
                    "rigging",
                    "rigging_vram",
                }:
                    if self._operation.startswith("modeling"):
                        stage = "modeling"
                    elif self._operation.startswith("rigging"):
                        stage = "rigging"
                        request = job.latest_rigging_request
                        if request:
                            request.status = "cancelled"
                            request.error = "사용자가 실행을 취소했습니다."
                            request.completed_at = utc_now()
                    else:
                        stage = "blender"
                    self.jobs.set_stage(
                        job, stage, "cancelled", error="사용자가 실행을 취소했습니다."
                    )
                    self.job_changed.emit(job)
                self.operation_finished.emit(self._operation, False, "실행을 취소했습니다.")
                return
            if handler:
                handler(exit_code)
        finally:
            if self._handler is None and not self.process.running:
                self._close_log()

    def _on_start_error(self, message: str) -> None:
        self._handler = None
        try:
            job = self._active_job
            if job and self._operation in {
                "modeling",
                "modeling_vram",
                "modeling_preview",
                "blender",
                "rigging",
                "rigging_vram",
            }:
                if self._operation.startswith("modeling"):
                    stage = "modeling"
                elif self._operation.startswith("rigging"):
                    stage = "rigging"
                    request = job.latest_rigging_request
                    if request:
                        request.status = "failed"
                        request.error = f"프로세스를 시작할 수 없습니다: {message}"
                        request.completed_at = utc_now()
                else:
                    stage = "blender"
                self._fail_stage(job, stage, f"프로세스를 시작할 수 없습니다: {message}")
            else:
                self.operation_finished.emit(self._operation, False, message)
        finally:
            if self._handler is None and not self.process.running:
                self._close_log()

    def _prepare_log(self, path: Path | None) -> None:
        if path == self._log_path and self._log_handle is not None:
            return
        self._close_log()
        self._log_path = path
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._log_handle = path.open("w", encoding="utf-8", newline="\n")

    def _close_log(self) -> None:
        handle, self._log_handle = self._log_handle, None
        self._log_lines_since_flush = 0
        self._log_path = None
        if handle is not None:
            try:
                handle.flush()
                handle.close()
            except OSError:
                pass

    def _fail_stage(self, job: Job, stage: str, message: str) -> None:
        self.jobs.set_stage(job, stage, "failed", error=message)
        self.job_changed.emit(job)
        self.operation_finished.emit(stage, False, message)
