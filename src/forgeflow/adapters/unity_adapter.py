from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from PySide6.QtCore import QObject, Signal

from forgeflow.config import AppConfig
from forgeflow.domain.job import Job, UnitySession, UnityTurn, utc_now
from forgeflow.services.job_service import JobService, sha256_file

from .modeling_adapter import ProcessCommand


UNITY_PROJECT_MARKERS = ("Assets", "ProjectSettings")


@dataclass(frozen=True)
class UnityImportResult:
    source_path: str
    source_sha256: str
    asset_path: str
    absolute_path: str
    version: int


def _same_path(left: str | Path, right: str | Path) -> bool:
    return os.path.normcase(os.path.realpath(os.path.abspath(str(left)))) == os.path.normcase(
        os.path.realpath(os.path.abspath(str(right)))
    )


def _safe_job_id(value: str) -> str:
    safe = "".join(char if char.isalnum() or char in "-_" else "_" for char in value)
    return safe.strip("_-") or "job"


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

    @staticmethod
    def validate_project(project_path: str | Path) -> Path:
        project = Path(project_path).expanduser().resolve(strict=True)
        if not project.is_dir():
            raise ValueError(f"Unity 프로젝트 폴더가 아닙니다: {project}")
        missing = [name for name in UNITY_PROJECT_MARKERS if not (project / name).is_dir()]
        if missing:
            raise ValueError(
                f"Unity 프로젝트가 아닙니다({', '.join(missing)} 폴더 없음): {project}"
            )
        return project

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
            "-u", "main.py", "--forgeflow-jsonl", "--project", str(project),
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
        if not job.unity_input_path:
            raise ValueError("현재 Job에 4단계 Humanoid FBX가 없습니다.")
        source = Path(job.unity_input_path).expanduser().resolve(strict=True)
        if not source.is_file() or source.suffix.lower() != ".fbx":
            raise ValueError(f"유효한 Humanoid FBX가 아닙니다: {source}")
        source_hash = sha256_file(source)
        relative_root = Path("Assets") / "ForgeFlow" / _safe_job_id(job.job_id) / "Models"
        absolute_root = project / relative_root
        absolute_root.mkdir(parents=True, exist_ok=True)
        version = 1
        while True:
            relative = relative_root / f"v{version:03d}" / "Character_humanoid.fbx"
            destination = project / relative
            if not destination.exists():
                break
            version += 1
        destination.parent.mkdir(parents=True, exist_ok=False)
        shutil.copy2(source, destination)
        if sha256_file(destination) != source_hash:
            raise RuntimeError("Unity 프로젝트로 복사한 FBX의 SHA-256이 원본과 다릅니다.")
        asset_path = relative.as_posix()
        result = UnityImportResult(
            source_path=str(source), source_sha256=source_hash,
            asset_path=asset_path, absolute_path=str(destination.resolve()), version=version,
        )
        imports = self._unity_root(job) / "imports"
        record = imports / f"v{version:03d}.json"
        record.write_text(
            json.dumps(asdict(result), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        job.unity_project_path = str(project)
        job.unity_asset_path = asset_path
        self.jobs.save(job)
        self.job_changed.emit(job)
        return result

    @staticmethod
    def _rig_status(job: Job) -> str:
        request = job.latest_rigging_request
        if request and request.report_path:
            try:
                payload = json.loads(Path(request.report_path).read_text(encoding="utf-8"))
                return str(payload.get("status") or "unknown")
            except (OSError, json.JSONDecodeError):
                pass
        return "unknown"

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
        assets = "(none selected)"
        if include_asset:
            if not job.unity_asset_path:
                raise ValueError("컨텍스트에 포함할 Unity Asset이 아직 없습니다.")
            assets = (
                f"- Selected Humanoid FBX (exact required path): {job.unity_asset_path}\n"
                f"- Rig report: {self._rig_status(job)}\n"
                "- Use this exact selected FBX. Do not search for or substitute another version."
            )
        scene = job.latest_unity_scene_path if include_current_scene else None
        parts = [
            "[ForgeFlow Context]",
            "Unity project:",
            str(project),
            "",
            "Current active ForgeFlow job:",
            f"{job.job_id} — {job.name}",
            "",
            "Available imported assets:",
            assets,
            "",
            "Known latest scene:",
            scene or "(none selected)",
            "",
            "Safety:",
            "- Work only in the selected Unity project.",
            "- Preserve existing user scenes and assets unless the user explicitly asks to modify them.",
            f"- Prefer Assets/ForgeFlow/{_safe_job_id(job.job_id)}/ for newly created assets.",
            "- Release simulated input and stop Play Mode after verification.",
            "- Report created and modified asset paths.",
            "- Do not claim subjective dynamic quality as automatically verified.",
        ]
        if include_asset:
            parts.append(
                f"- For this selected ForgeFlow asset request, create new assets under "
                f"Assets/ForgeFlow/{_safe_job_id(job.job_id)}/ unless the user names another path."
            )
        if human_review_feedback:
            parts.extend(["", "[Human Review Feedback]", human_review_feedback.strip()])
        parts.extend(
            [
                "",
                "[User Request]",
                user_text,
                "",
                "[Human Review Policy]",
                "Dynamic motion, animation quality, controls, camera feel, timing, and visual polish will be reviewed by a human. Perform the requested implementation and available objective checks, then leave clear instructions for human Play Mode review.",
            ]
        )
        return "\n".join(parts)

    def start_session(self, job: Job, project_path: str | Path) -> UnitySession:
        project = self.validate_project(project_path)
        if self.running:
            if self.session and _same_path(self.session.project_path, project):
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
            session_id=session_id, project_path=str(project),
            model=self.config.unity_agent_model, status="starting",
        )
        job.unity_project_path = str(project)
        self.jobs.add_unity_session(job, self.session)
        command = self.build_session_command(project, session_dir)
        creationflags = 0
        if os.name == "nt":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        try:
            self.process = self._process_factory(
                [command.executable, *command.arguments], cwd=command.cwd,
                env=command.environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                bufsize=1, shell=False, creationflags=creationflags,
            )
        except BaseException as exc:
            self.session.status = "failed"
            self.session.error = f"프로세스를 시작할 수 없습니다: {exc}"
            self.session.closed_at = utc_now()
            self.jobs.save(job)
            self.process = None
            raise
        self._cancel_requested = False
        self._reader_threads = [
            threading.Thread(target=self._read_stdout, name="unity-jsonl", daemon=True),
            threading.Thread(target=self._read_stderr, name="unity-stderr", daemon=True),
            threading.Thread(target=self._wait_process, name="unity-wait", daemon=True),
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

    def _read_stdout(self) -> None:
        process = self.process
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
            self._record_event(stripped)
            self._handle_event(event)

    def _read_stderr(self) -> None:
        process = self.process
        if process is None or process.stderr is None:
            return
        for line in process.stderr:
            text = line.rstrip("\r\n")
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
                actual = str(event.get("projectPath") or "")
                if not self.session or not actual or not _same_path(actual, self.session.project_path):
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
                            destination = screenshot_root / f"{turn.turn_id}-{counter}-{source.name}"
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
        if not self.ready or self.job is None or self.session is None or self._session_dir is None:
            raise RuntimeError("Unity Agent 세션이 준비되지 않았습니다.")
        if self._active_turn and self._active_turn.status == "running":
            raise RuntimeError("이미 Unity 명령을 실행 중입니다.")
        exact_text = user_text
        if not isinstance(exact_text, str) or not exact_text.strip():
            raise ValueError("Unity 명령을 입력하세요.")
        turn_id = f"turn-{len(self.job.unity_turns) + 1:03d}-{uuid4().hex[:6]}"
        effective = self.build_effective_prompt(
            self.job, self.session.project_path, exact_text,
            include_asset=include_asset, include_current_scene=include_current_scene,
            human_review_feedback=human_review_feedback,
        )
        turn = UnityTurn(
            turn_id=turn_id, session_id=self.session.session_id,
            user_text=exact_text, effective_prompt=effective,
            repair_existing=repair_existing, status="running", started_at=utc_now(),
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
        self._send(
            {
                "type": "prompt", "id": turn_id, "text": effective,
                "user_text": exact_text,
                "repair_existing": repair_existing,
                "analyze_screenshot": analyze_screenshot,
                "human_review_feedback": human_review_feedback,
                "selected_asset_paths": (
                    [self.job.unity_asset_path]
                    if include_asset and self.job.unity_asset_path else []
                ),
                "preferred_output_root": (
                    f"Assets/ForgeFlow/{_safe_job_id(self.job.job_id)}/"
                    if include_asset and not re.search(
                        r"Assets[/\\][^\r\n]*?\.unity\b", exact_text, re.I
                    ) else None
                ),
            }
        )
        return turn

    def _send(self, message: dict[str, Any]) -> None:
        process = self.process
        if process is None or process.stdin is None or process.poll() is not None:
            raise RuntimeError("Unity Agent 프로세스가 종료되었습니다.")
        line = json.dumps(message, ensure_ascii=False) + "\n"
        with self._write_lock:
            process.stdin.write(line)
            process.stdin.flush()

    @staticmethod
    def _receipt_lists(payload: dict[str, Any], name: str) -> list[Any]:
        value = payload.get(name, [])
        return value if isinstance(value, list) else []

    @classmethod
    def parse_receipt(cls, path: str | Path | None) -> dict[str, Any]:
        if not path:
            return {"automated_status": "unavailable"}
        receipt = Path(path)
        try:
            payload = json.loads(receipt.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            return {"automated_status": "unavailable"}
        requested = cls._receipt_lists(payload, "requested_checks")
        measured = cls._receipt_lists(payload, "measured_checks")
        skipped = cls._receipt_lists(payload, "skipped_checks")
        unmapped = cls._receipt_lists(payload, "unmapped_requirements")
        status = str(payload.get("status") or "").lower()
        if status == "failed":
            automated = "failed"
        elif not requested and not measured:
            automated = "unavailable"
        elif not requested or not measured or skipped or unmapped:
            automated = "partial"
        elif status == "verified":
            automated = "verified"
        else:
            automated = "partial"
        return {
            "automated_status": automated,
            "requested_checks": requested,
            "measured_checks": measured,
            "skipped_checks": skipped,
            "unmapped_requirements": unmapped,
            "receipt": payload,
        }

    @staticmethod
    def collect_changed_assets(jsonl_path: str | Path | None) -> list[str]:
        """Extract project-relative assets from mutation tool audit records."""
        if not jsonl_path:
            return []
        path = Path(jsonl_path)
        mutations = {
            "unity_create_gameobject", "unity_create_gameobjects",
            "unity_modify_gameobject", "unity_delete_gameobject",
            "unity_add_component", "unity_remove_component",
            "unity_set_component_property", "unity_create_material",
            "unity_create_scene", "unity_open_scene", "unity_save_scene",
            "unity_write_script", "unity_delete_script", "unity_write_level",
        }
        found: list[str] = []

        def walk(node: Any) -> None:
            if isinstance(node, str):
                normalized = node.replace("\\", "/")
                start = normalized.find("Assets/")
                if start >= 0:
                    candidate = normalized[start:].split("\n", 1)[0].strip(' "\'')
                    if candidate and candidate not in found:
                        found.append(candidate)
            elif isinstance(node, dict):
                for child in node.values():
                    walk(child)
            elif isinstance(node, list):
                for child in node:
                    walk(child)

        try:
            with path.open(encoding="utf-8") as handle:
                for line in handle:
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if event.get("event") != "tool_result" or event.get("name") not in mutations:
                        continue
                    walk(event.get("arguments"))
                    result = event.get("result")
                    if isinstance(result, str):
                        try:
                            walk(json.loads(result))
                        except json.JSONDecodeError:
                            walk(result)
                    else:
                        walk(result)
        except OSError:
            return []
        return found

    def _finish_turn(self, turn: UnityTurn, event: dict[str, Any], *, success: bool) -> None:
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
                self.job, "unity", "awaiting_review" if success else "failed",
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
                    ensure_ascii=False, indent=2,
                ) + "\n",
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
            source.user_text, include_asset=include_asset,
            include_current_scene=include_current_scene, repair_existing=True,
            analyze_screenshot=analyze_screenshot, human_review_feedback=feedback,
        )

    def cancel_active(self) -> None:
        process = self.process
        if process is None or process.poll() is not None:
            return
        self._cancel_requested = True
        if self._active_turn and self.job:
            self._active_turn.status = "cancelled"
            self._active_turn.completed_at = utc_now()
            self._active_turn.error = "사용자가 Unity 명령을 취소했습니다."
            self.jobs.set_stage(self.job, "unity", "cancelled", error=self._active_turn.error)
            self.job_changed.emit(self.job)
            self._active_turn = None
        self._terminate_tree(process)

    @staticmethod
    def _terminate_tree(process: subprocess.Popen) -> None:
        if process.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        else:
            process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    def shutdown(self, *, wait_seconds: float = 5) -> None:
        process = self.process
        if process is None:
            return
        if process.poll() is None:
            try:
                self._send({"type": "shutdown"})
                process.wait(timeout=wait_seconds)
            except (BrokenPipeError, OSError, RuntimeError, subprocess.TimeoutExpired):
                self._terminate_tree(process)
        if self.session and self.session.status not in {"closed", "failed"}:
            self.session.status = "closed"
            self.session.closed_at = utc_now()
            self._save_job()
            self.session_changed.emit(self.session)
        self.process = None

    def build_prompt_file_command(
        self, project_path: str | Path, prompt_file: str | Path,
        session_dir: Path, *, repair_existing: bool = False, vision: bool = False,
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

    def _wait_process(self) -> None:
        process = self.process
        if process is None:
            return
        code = process.wait()
        with self._state_lock:
            if self.session and self.session.status not in {"closed", "failed"}:
                if self._cancel_requested:
                    self.session.status = "closed"
                elif code == 0:
                    self.session.status = "closed"
                else:
                    self.session.status = "failed"
                    self.session.error = f"Unity Agent 프로세스가 종료 코드 {code}로 종료되었습니다."
                    if self._active_turn and self.job:
                        self._active_turn.status = "failed"
                        self._active_turn.error = self.session.error
                        self._active_turn.completed_at = utc_now()
                        self.jobs.set_stage(self.job, "unity", "failed", error=self.session.error)
                        self._active_turn = None
                self.session.closed_at = utc_now()
                self._save_job()
                self.session_changed.emit(self.session)

    def _fail_session(self, message: str) -> None:
        if self.session:
            self.session.status = "failed"
            self.session.error = message
            self.session.closed_at = utc_now()
        self._save_job()
        self.protocol_error.emit(message)
        if self.session:
            self.session_changed.emit(self.session)

    def _save_job(self) -> None:
        if self.job:
            self.jobs.save(self.job)
