from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from PySide6.QtCore import QObject, Signal

from forgeflow.config import AppConfig
from forgeflow.domain.job import Job, UnitySession, UnityTurn, utc_now
from forgeflow.domain.process import ProcessCommand
from forgeflow.services.job_service import JobService
from forgeflow.services.path_utils import same_path
from forgeflow.services.process_control import terminate_process_tree

from .unity.project import (
    UNITY_PROJECT_MARKERS as UNITY_PROJECT_MARKERS,
)
from .unity.project import (
    UnityImportResult,
    copy_humanoid_fbx,
    validate_project,
)
from .unity.project import (
    safe_job_id as _safe_job_id,
)
from .unity.prompts import build_effective_prompt
from .unity.receipts import collect_changed_assets, parse_receipt


class UnityAdapter(QObject):
    """ForgeFlow boundary around the separate unity_local_mcp process.

    The adapter knows the JSONL transport and persisted ForgeFlow state. It does
    not import or call any unity_mcp tool.
    """

    event_received = Signal(object)
    job_changed = Signal(object)
    session_changed = Signal(object)
    protocol_error = Signal(str)
    log_received = Signal(str)

    def __init__(
        self,
        config: AppConfig,
        jobs: JobService,
        parent: QObject | None = None,
        *,
        process_factory: Callable[..., subprocess.Popen] = subprocess.Popen,
    ):
        super().__init__(parent)
        self.config = config
        self.jobs = jobs
        self._process_factory = process_factory
        self.process: subprocess.Popen | None = None
        self.job: Job | None = None
        self.session: UnitySession | None = None
        self._active_turn: UnityTurn | None = None
        self._session_dir: Path | None = None
        self._session_log: Path | None = None
        self._stderr_log: Path | None = None
        self._write_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self._cancel_requested = False
        self._reader_threads: list[threading.Thread] = []

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    @property
    def ready(self) -> bool:
        return self.running and self.session is not None and self.session.status == "ready"

    @property
    def busy(self) -> bool:
        return self.running and (not self.ready or self._active_turn is not None)

    @staticmethod
    def validate_project(project_path: str | Path) -> Path:
        return validate_project(project_path)

    def validate_environment(self) -> dict[str, dict[str, Any]]:
        agent_main = self.config.unity_agent_root / "main.py"
        mcp_server = self.config.unity_mcp_root / "server.py"
        python = self.config.unity_agent_root / ".venv" / "Scripts" / "python.exe"
        uv = shutil.which("uv")
        return {
            "agent": {"ok": agent_main.is_file(), "detail": str(agent_main)},
            "mcp": {"ok": mcp_server.is_file(), "detail": str(mcp_server)},
            "runtime": {
                "ok": python.is_file() or bool(uv),
                "detail": str(python) if python.is_file() else (uv or "uv를 찾을 수 없음"),
            },
        }

    def build_session_command(self, project_path: str | Path, session_dir: Path) -> ProcessCommand:
        project = self.validate_project(project_path)
        checks = self.validate_environment()
        failed = [name for name, value in checks.items() if not value["ok"]]
        if failed:
            raise RuntimeError("Unity 실행 환경을 찾을 수 없습니다: " + ", ".join(failed))
        venv_python = self.config.unity_agent_root / ".venv" / "Scripts" / "python.exe"
        base_args = [
            "-u",
            "main.py",
            "--forgeflow-jsonl",
            "--project",
            str(project),
        ]
        if venv_python.is_file():
            executable = str(venv_python)
            arguments = base_args
        else:
            executable = shutil.which("uv") or "uv"
            arguments = ["run", "python", *base_args]
        return ProcessCommand(
            executable=executable,
            arguments=arguments,
            cwd=self.config.unity_agent_root.resolve(strict=False),
            environment=self.config.unity_environment(project, session_dir),
        )

    def _unity_root(self, job: Job) -> Path:
        root = self.jobs.job_directory(job.job_id) / "unity"
        for child in (root / "imports", root / "sessions"):
            child.mkdir(parents=True, exist_ok=True)
        return root

    def import_humanoid_fbx(self, job: Job, project_path: str | Path) -> UnityImportResult:
        project = self.validate_project(project_path)
        result = copy_humanoid_fbx(job, project)
        imports = self._unity_root(job) / "imports"
        record = imports / f"v{result.version:03d}.json"
        record.write_text(
            json.dumps(asdict(result), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        job.unity_project_path = str(project)
        job.unity_asset_path = result.asset_path
        self.jobs.save(job)
        self.job_changed.emit(job)
        return result

    def build_effective_prompt(
        self,
        job: Job,
        project_path: str | Path,
        user_text: str,
        *,
        include_asset: bool = False,
        include_current_scene: bool = True,
        human_review_feedback: str | None = None,
    ) -> str:
        project = self.validate_project(project_path)
        return build_effective_prompt(
            job,
            project,
            user_text,
            include_asset=include_asset,
            include_current_scene=include_current_scene,
            human_review_feedback=human_review_feedback,
        )

    def start_session(self, job: Job, project_path: str | Path) -> UnitySession:
        project = self.validate_project(project_path)
        if self.process is not None:
            if (
                self.ready
                and self.job is not None
                and self.job.job_id == job.job_id
                and same_path(self.session.project_path, project)
            ):
                return self.session
            self.shutdown(wait_seconds=5)
        session_id = f"session-{utc_now().replace(':', '').replace('-', '')[:15]}-{uuid4().hex[:8]}"
        session_dir = self._unity_root(job) / "sessions" / session_id
        (session_dir / "turns").mkdir(parents=True, exist_ok=False)
        (session_dir / "screenshots").mkdir()
        self.job = job
        self._session_dir = session_dir
        self._session_log = session_dir / "session.jsonl"
        self._stderr_log = self._unity_root(job) / "unity.log"
        self.session = UnitySession(
            session_id=session_id,
            project_path=str(project),
            model=self.config.unity_agent_model,
            status="starting",
        )
        job.unity_project_path = str(project)
        self.jobs.add_unity_session(job, self.session)
        creationflags = 0
        if os.name == "nt":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        try:
            command = self.build_session_command(project, session_dir)
            self.process = self._process_factory(
                [command.executable, *command.arguments],
                cwd=command.cwd,
                env=command.environment,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                shell=False,
                creationflags=creationflags,
            )
        except BaseException as exc:
            self.session.status = "failed"
            self.session.error = f"프로세스를 시작할 수 없습니다: {exc}"
            self.session.closed_at = utc_now()
            self.jobs.save(job)
            self.process = None
            raise
        self._cancel_requested = False
        process, session = self.process, self.session
        stdout_thread = threading.Thread(
            target=self._read_stdout, args=(process, session), name="unity-jsonl", daemon=True
        )
        self._reader_threads = [
            stdout_thread,
            threading.Thread(
                target=self._read_stderr, args=(process, session), name="unity-stderr", daemon=True
            ),
            threading.Thread(
                target=self._wait_process,
                args=(process, session, stdout_thread),
                name="unity-wait",
                daemon=True,
            ),
        ]
        for thread in self._reader_threads:
            thread.start()
        self.session_changed.emit(self.session)
        return self.session

    @staticmethod
    def parse_jsonl_event(line: str) -> dict[str, Any]:
        value = json.loads(line)
        if not isinstance(value, dict) or not isinstance(value.get("type"), str):
            raise ValueError("JSONL 이벤트에는 문자열 type이 필요합니다.")
        return value

    def _read_stdout(self, process=None, session=None) -> None:
        process = process if process is not None else self.process
        session = session if session is not None else self.session
        if process is None or process.stdout is None:
            return
        for line in process.stdout:
            stripped = line.rstrip("\r\n")
            if not stripped:
                continue
            try:
                event = self.parse_jsonl_event(stripped)
            except (json.JSONDecodeError, ValueError) as exc:
                self.protocol_error.emit(f"Unity Agent stdout JSONL 오류: {exc}: {stripped[:300]}")
                continue
            with self._state_lock:
                if process is not self.process or session is not self.session:
                    return
                self._record_event(stripped)
                self._handle_event(event)

    def _read_stderr(self, process=None, session=None) -> None:
        process = process if process is not None else self.process
        session = session if session is not None else self.session
        if process is None or process.stderr is None:
            return
        for line in process.stderr:
            text = line.rstrip("\r\n")
            with self._state_lock:
                if process is not self.process or session is not self.session:
                    return
                self.log_received.emit(text)
                if self._stderr_log:
                    self._stderr_log.parent.mkdir(parents=True, exist_ok=True)
                    with self._stderr_log.open("a", encoding="utf-8") as handle:
                        handle.write(text + "\n")
                turn = self._active_turn
                if turn and self._session_dir:
                    path = self._session_dir / "turns" / turn.turn_id / "agent.log"
                    with path.open("a", encoding="utf-8") as handle:
                        handle.write(text + "\n")

    def _record_event(self, line: str) -> None:
        if self._session_log:
            with self._session_log.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")

    def _turn_for_event(self, event: dict[str, Any]) -> UnityTurn | None:
        if self.job is None:
            return None
        turn_id = str(event.get("id") or "")
        return next((turn for turn in self.job.unity_turns if turn.turn_id == turn_id), None)

    def _handle_event(self, event: dict[str, Any]) -> None:
        with self._state_lock:
            event_type = event["type"]
            if event_type == "session_ready":
                if self._cancel_requested or (
                    self.session and self.session.status in {"closed", "failed"}
                ):
                    return
                actual = str(event.get("projectPath") or "")
                if (
                    not self.session
                    or not actual
                    or not same_path(actual, self.session.project_path)
                ):
                    expected = self.session.project_path if self.session else "(none)"
                    self._fail_session(
                        f"Unity 프로젝트 identity 불일치: expected {expected}, got {actual or '(missing)'}"
                    )
                    self.cancel_active()
                    return
                self.session.status = "ready"
                self.session.project_identity = {
                    "projectPath": actual,
                    "unityVersion": event.get("unityVersion"),
                    "productName": event.get("productName"),
                }
                self.session.model = str(event.get("model") or self.session.model or "")
                self._save_job()
                self.session_changed.emit(self.session)
            elif event_type == "session_error":
                self._fail_session(str(event.get("error") or "Unity session failed"))
            elif event_type == "assistant_text":
                turn = self._turn_for_event(event)
                if turn:
                    turn.assistant_text += str(event.get("text") or "")
            elif event_type == "screenshot":
                turn = self._turn_for_event(event)
                if turn:
                    raw = str(event.get("path") or "")
                    path = raw if os.path.isabs(raw) else str(Path(self.session.project_path) / raw)
                    path = str(Path(path).resolve(strict=False))
                    source = Path(path)
                    if source.is_file() and self._session_dir:
                        screenshot_root = self._session_dir / "screenshots"
                        destination = screenshot_root / f"{turn.turn_id}-{source.name}"
                        counter = 2
                        while destination.exists():
                            destination = (
                                screenshot_root / f"{turn.turn_id}-{counter}-{source.name}"
                            )
                            counter += 1
                        try:
                            shutil.copy2(source, destination)
                            path = str(destination.resolve())
                        except OSError:
                            pass
                    if path not in turn.screenshot_paths:
                        turn.screenshot_paths.append(path)
                    if self.job:
                        self.job.latest_unity_screenshot_path = path
                    self._save_job()
            elif event_type == "turn_completed":
                turn = self._turn_for_event(event)
                if turn:
                    self._finish_turn(turn, event, success=True)
            elif event_type == "turn_failed":
                turn = self._turn_for_event(event)
                if turn:
                    self._finish_turn(turn, event, success=False)
            elif event_type == "session_closed":
                if self.session:
                    self._end_active_turn("failed", "Unity 세션이 명령 완료 전에 닫혔습니다.")
                    if self.session.status != "failed":
                        self.session.status = "closed"
                    self.session.closed_at = utc_now()
                    self._save_job()
                    self.session_changed.emit(self.session)
            self.event_received.emit(event)

    def send_prompt(
        self,
        user_text: str,
        *,
        include_asset: bool = False,
        include_current_scene: bool = True,
        repair_existing: bool = False,
        analyze_screenshot: bool = False,
        human_review_feedback: str | None = None,
    ) -> UnityTurn:
        with self._state_lock:
            return self._send_prompt(
                user_text,
                include_asset=include_asset,
                include_current_scene=include_current_scene,
                repair_existing=repair_existing,
                analyze_screenshot=analyze_screenshot,
                human_review_feedback=human_review_feedback,
            )

    def _send_prompt(
        self,
        user_text: str,
        *,
        include_asset: bool,
        include_current_scene: bool,
        repair_existing: bool,
        analyze_screenshot: bool,
        human_review_feedback: str | None,
    ) -> UnityTurn:
        if not self.ready or self.job is None or self.session is None or self._session_dir is None:
            raise RuntimeError("Unity Agent 세션이 준비되지 않았습니다.")
        if self._active_turn and self._active_turn.status == "running":
            raise RuntimeError("이미 Unity 명령을 실행 중입니다.")
        exact_text = user_text
        if not isinstance(exact_text, str) or not exact_text.strip():
            raise ValueError("Unity 명령을 입력하세요.")
        turn_id = f"turn-{len(self.job.unity_turns) + 1:03d}-{uuid4().hex[:6]}"
        effective = self.build_effective_prompt(
            self.job,
            self.session.project_path,
            exact_text,
            include_asset=include_asset,
            include_current_scene=include_current_scene,
            human_review_feedback=human_review_feedback,
        )
        turn = UnityTurn(
            turn_id=turn_id,
            session_id=self.session.session_id,
            user_text=exact_text,
            effective_prompt=effective,
            repair_existing=repair_existing,
            status="running",
            started_at=utc_now(),
        )
        turn_dir = self._session_dir / "turns" / turn_id
        turn_dir.mkdir(parents=True, exist_ok=False)
        (turn_dir / "request.txt").write_text(exact_text, encoding="utf-8")
        (turn_dir / "effective-prompt.txt").write_text(effective, encoding="utf-8")
        (turn_dir / "agent.log").touch()
        self.job.unity_turns.append(turn)
        self._active_turn = turn
        self.jobs.set_stage(self.job, "unity", "running")
        self.job_changed.emit(self.job)
        message = {
            "type": "prompt",
            "id": turn_id,
            "text": effective,
            "user_text": exact_text,
            "repair_existing": repair_existing,
            "analyze_screenshot": analyze_screenshot,
            "human_review_feedback": human_review_feedback,
            "selected_asset_paths": (
                [self.job.unity_asset_path] if include_asset and self.job.unity_asset_path else []
            ),
            "preferred_output_root": (
                f"Assets/ForgeFlow/{_safe_job_id(self.job.job_id)}/"
                if include_asset
                and not re.search(r"Assets[/\\][^\r\n]*?\.unity\b", exact_text, re.I)
                else None
            ),
        }
        try:
            self._send(message)
        except (OSError, RuntimeError) as exc:
            self._fail_session(f"Unity 명령을 전송할 수 없습니다: {exc}")
            raise
        return turn

    def _send(self, message: dict[str, Any]) -> None:
        process = self.process
        if process is None or process.stdin is None or process.poll() is not None:
            raise RuntimeError("Unity Agent 프로세스가 종료되었습니다.")
        line = json.dumps(message, ensure_ascii=False) + "\n"
        with self._write_lock:
            process.stdin.write(line)
            process.stdin.flush()

    @classmethod
    def parse_receipt(cls, path: str | Path | None) -> dict[str, Any]:
        return parse_receipt(path)

    @staticmethod
    def collect_changed_assets(jsonl_path: str | Path | None) -> list[str]:
        """Extract project-relative assets from mutation tool audit records."""
        return collect_changed_assets(jsonl_path)

    def _finish_turn(self, turn: UnityTurn, event: dict[str, Any], *, success: bool) -> None:
        # Cancellation and transport failures are terminal even if a buffered
        # completion arrives after the user has stopped the operation.
        if turn.status != "running":
            return
        turn.completed_at = utc_now()
        turn.status = "succeeded" if success else "failed"
        turn.error = None if success else str(event.get("error") or "Unity Agent failed")
        turn.run_log_path = event.get("runLogPath") or None
        turn.jsonl_log_path = event.get("jsonlLogPath") or None
        turn.receipt_path = event.get("receiptPath") or None
        parsed = self.parse_receipt(turn.receipt_path)
        turn.automated_status = parsed["automated_status"]
        turn.requested_checks = parsed.get("requested_checks", [])
        turn.measured_checks = parsed.get("measured_checks", [])
        turn.skipped_checks = parsed.get("skipped_checks", [])
        turn.unmapped_requirements = parsed.get("unmapped_requirements", [])
        turn.changed_assets = self.collect_changed_assets(turn.jsonl_log_path)
        if self.job:
            scenes = [path for path in turn.changed_assets if path.lower().endswith(".unity")]
            if scenes:
                self.job.latest_unity_scene_path = scenes[-1]
        if self._session_dir:
            turn_dir = self._session_dir / "turns" / turn.turn_id
            if turn.jsonl_log_path and Path(turn.jsonl_log_path).is_file():
                try:
                    shutil.copy2(turn.jsonl_log_path, turn_dir / "run.jsonl")
                except OSError:
                    pass
            pointer = {
                "receiptPath": turn.receipt_path,
                "runLogPath": turn.run_log_path,
                "jsonlLogPath": turn.jsonl_log_path,
            }
            (turn_dir / "receipt-path.json").write_text(
                json.dumps(pointer, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        if self.job:
            self.jobs.set_stage(
                self.job,
                "unity",
                "awaiting_review" if success else "failed",
                error=turn.error if not success else None,
                log_path=Path(turn.run_log_path) if turn.run_log_path else None,
            )
            self.job_changed.emit(self.job)
        self._active_turn = None

    def review_turn(self, turn_id: str, accepted: bool, note: str = "") -> UnityTurn:
        if self.job is None:
            raise RuntimeError("선택된 ForgeFlow Job이 없습니다.")
        turn = self.jobs.review_unity_turn(
            self.job, turn_id, "accepted" if accepted else "rejected", note
        )
        if self._session_dir:
            review = self._session_dir / "turns" / turn.turn_id / "review.json"
            review.parent.mkdir(parents=True, exist_ok=True)
            review.write_text(
                json.dumps(
                    {
                        "human_review_status": turn.human_review_status,
                        "human_review_note": turn.human_review_note,
                        "human_reviewed_at": turn.human_reviewed_at,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
        self.job_changed.emit(self.job)
        return turn

    def send_review_repair(
        self,
        source_turn_id: str,
        feedback: str,
        *,
        include_asset: bool = False,
        include_current_scene: bool = True,
        analyze_screenshot: bool = False,
    ) -> UnityTurn:
        if self.job is None:
            raise RuntimeError("선택된 ForgeFlow Job이 없습니다.")
        source = next(
            (turn for turn in self.job.unity_turns if turn.turn_id == source_turn_id), None
        )
        if source is None:
            raise ValueError("수정할 Unity 실행을 찾을 수 없습니다.")
        if not feedback.strip():
            raise ValueError("수정 피드백을 입력하세요.")
        return self.send_prompt(
            source.user_text,
            include_asset=include_asset,
            include_current_scene=include_current_scene,
            repair_existing=True,
            analyze_screenshot=analyze_screenshot,
            human_review_feedback=feedback,
        )

    def cancel_active(self) -> None:
        with self._state_lock:
            process = self.process
            self._cancel_requested = True
            self._end_active_turn("cancelled", "사용자가 Unity 명령을 취소했습니다.")
        if process is not None and process.poll() is None:
            self._terminate_tree(process)

    @staticmethod
    def _terminate_tree(process: subprocess.Popen) -> None:
        terminate_process_tree(process)

    def shutdown(self, *, wait_seconds: float = 5) -> None:
        with self._state_lock:
            process, session = self.process, self.session
            if self._active_turn:
                self._cancel_requested = True
                self._end_active_turn("cancelled", "Unity 세션을 닫아 명령이 취소되었습니다.")
        if process is None:
            return
        if process.poll() is None:
            try:
                self._send({"type": "shutdown"})
                process.wait(timeout=wait_seconds)
            except (BrokenPipeError, OSError, RuntimeError, subprocess.TimeoutExpired):
                self._terminate_tree(process)
        # Readers must finish before another session replaces their job/log paths.
        deadline = time.monotonic() + wait_seconds
        for thread in self._reader_threads:
            if thread is not threading.current_thread() and thread.is_alive():
                thread.join(max(0, deadline - time.monotonic()))
        self._process_finished(process, session, process.poll())
        with self._state_lock:
            if self.process is process:
                self.process = None

    def build_prompt_file_command(
        self,
        project_path: str | Path,
        prompt_file: str | Path,
        session_dir: Path,
        *,
        repair_existing: bool = False,
        vision: bool = False,
    ) -> ProcessCommand:
        """One-shot compatibility boundary when persistent JSONL is unavailable."""
        project = self.validate_project(project_path)
        prompt = Path(prompt_file).expanduser().resolve(strict=True)
        command = self.build_session_command(project, session_dir)
        args = [arg for arg in command.arguments if arg != "--forgeflow-jsonl"]
        args.extend(["--prompt-file", str(prompt)])
        if repair_existing:
            args.append("--repair-existing")
        if vision:
            args.append("--vision")
        return ProcessCommand(command.executable, args, command.cwd, command.environment)

    def _wait_process(self, process=None, session=None, stdout_thread=None) -> None:
        process = process if process is not None else self.process
        session = session if session is not None else self.session
        if process is None:
            return
        code = process.wait()
        if stdout_thread is not None:
            # wait() can finish while turn_completed is still buffered in stdout.
            # Bound draining in case a descendant keeps the inherited pipe open.
            stdout_thread.join(timeout=5)
        self._process_finished(process, session, code)

    def _process_finished(self, process, session, code: int | None) -> None:
        with self._state_lock:
            if process is not self.process or session is not self.session or session is None:
                return
            message = session.error or (
                f"Unity Agent가 명령 완료 전에 종료되었습니다 (종료 코드 {code})."
            )
            self._end_active_turn("cancelled" if self._cancel_requested else "failed", message)
            if session.status != "failed":
                session.status = "closed" if self._cancel_requested or code == 0 else "failed"
                if session.status == "failed":
                    session.error = message
            session.closed_at = session.closed_at or utc_now()
            self._save_job()
            self.session_changed.emit(session)

    def _end_active_turn(self, status: str, message: str) -> None:
        turn = self._active_turn
        if turn is None:
            return
        if turn.status == "running":
            turn.status = status
            turn.completed_at = utc_now()
            turn.error = message
            if self.job:
                self.jobs.set_stage(self.job, "unity", status, error=message)
                self.job_changed.emit(self.job)
        self._active_turn = None

    def _fail_session(self, message: str) -> None:
        if self.session:
            self.session.status = "failed"
            self.session.error = message
            self.session.closed_at = utc_now()
        self._end_active_turn("failed", message)
        self._save_job()
        self.protocol_error.emit(message)
        if self.session:
            self.session_changed.emit(self.session)

    def _save_job(self) -> None:
        if self.job:
            self.jobs.save(self.job)
