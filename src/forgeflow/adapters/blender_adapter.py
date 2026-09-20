from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from forgeflow.config import AppConfig
from forgeflow.domain.artifact import Artifact
from forgeflow.domain.job import BlenderRequest, Job, utc_now
from forgeflow.domain.process import ProcessCommand
from forgeflow.services.job_service import JobService, sha256_file
from forgeflow.services.lineage import is_rigged_input
from forgeflow.services.path_utils import same_path


def plan_hash(plan: dict[str, Any]) -> str:
    encoded = json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


class BlenderAdapter:
    REQUIRED = {"glb", "blend", "fbx"}

    def __init__(self, config: AppConfig, jobs: JobService):
        self.config = config
        self.jobs = jobs
        self.bridge = Path(__file__).with_name("blender_bridge.py").resolve()

    def _base_command(self, job: Job, arguments: list[str]) -> ProcessCommand:
        if not self.config.agent_python.is_file():
            raise ValueError(f"Blender 에이전트 Python이 없습니다: {self.config.agent_python}")
        session_dir = self.jobs.job_directory(job.job_id) / "logs" / "sessions"
        session_dir.mkdir(parents=True, exist_ok=True)
        return ProcessCommand(
            str(self.config.agent_python),
            [str(self.bridge), *arguments],
            self.config.blender_agent_root,
            self.config.agent_environment(session_dir),
        )

    def build_inspect(
        self, job: Job, input_path: Path | None = None, suffix: str = "source"
    ) -> tuple[ProcessCommand, Path]:
        source = (input_path or Path(job.blender_input_path or "")).resolve(strict=True)
        output = self.jobs.job_directory(job.job_id) / "logs" / f"inspect-{suffix}.json"
        return self._base_command(
            job, ["inspect", "--input", str(source), "--output", str(output)]
        ), output

    def build_proposal(self, job: Job, user_request: str) -> tuple[ProcessCommand, BlenderRequest]:
        request_text = user_request.strip()
        if not request_text:
            raise ValueError("Blender 편집 요청을 입력해 주세요.")
        input_path = Path(job.blender_input_path or "").resolve(strict=True)
        preserve_rig = is_rigged_input(job, input_path)
        version = self.jobs.next_blender_version(job)
        version_dir = self.jobs.job_directory(job.job_id) / "blender" / f"v{version:03d}"
        version_dir.mkdir(parents=True, exist_ok=False)
        proposal_path = version_dir / "proposal.json"
        enriched = (
            f"input_path는 '{input_path}'이고 output_directory는 '{version_dir.resolve()}'이다. "
            "먼저 scene.inspect로 장면을 검사하고 실제 이름을 확인한 다음, 아래 요청에 필요한 허용된 작업만 계획해줘. "
            "다른 입력·출력 경로를 사용하지 마. 수정 요청은 설명문으로 끝내지 말고 필요한 허용 도구를 "
            "Ollama tool_calls 형식으로 제안해 승인 대기 계획을 만들어라.\n사용자 요청: "
            + request_text
        )
        if preserve_rig:
            enriched += (
                "\n리깅된 자산이다. rig.inspect로 본과 스킨을 먼저 확인하고 보존해라. "
                "전체 캐릭터 변환은 root를 대상으로 하고, export_profile='preserve'를 사용해라. "
                "다음 Unity 입력이 필요한 편집은 BLEND/GLB/FBX를 모두 출력해라."
            )
        record = BlenderRequest(
            request=request_text,
            input_path=str(input_path),
            output_directory=str(version_dir.resolve()),
            version=version,
            proposal_path=str(proposal_path.resolve()),
            preserve_rig=preserve_rig,
            input_sha256=sha256_file(input_path),
        )
        command = self._base_command(
            job, ["propose", "--prompt", enriched, "--output", str(proposal_path)]
        )
        return command, record

    def load_proposal(self, request: BlenderRequest) -> dict[str, Any]:
        if not request.proposal_path:
            raise RuntimeError("제안 파일 경로가 없습니다.")
        envelope = json.loads(Path(request.proposal_path).read_text(encoding="utf-8"))
        result = envelope.get("result", {})
        plan = result.get("plan")
        if result.get("status") != "denied" or not isinstance(plan, dict):
            raise RuntimeError(result.get("summary") or "실행 가능한 계획을 만들지 못했습니다.")
        digest = plan_hash(plan)
        if digest != envelope.get("plan_sha256"):
            raise RuntimeError("제안 계획 파일의 해시가 일치하지 않습니다.")
        request.plan = plan
        request.plan_sha256 = digest
        request.session_path = result.get("log_path")
        request.status = "awaiting_approval"
        return envelope

    def build_execute(self, job: Job, request: BlenderRequest) -> tuple[ProcessCommand, Path, str]:
        if (
            request.status != "awaiting_approval"
            or not request.plan
            or not request.plan_sha256
            or not request.proposal_path
        ):
            raise RuntimeError("승인할 실행 계획이 없습니다.")
        input_path = Path(request.input_path).resolve(strict=True)
        if job.blender_input_path and not same_path(job.blender_input_path, input_path):
            raise RuntimeError("Blender 입력이 변경되었습니다. 새 실행 계획을 만들어 주세요.")
        original_hash = sha256_file(input_path)
        if request.input_sha256 and request.input_sha256 != original_hash:
            raise RuntimeError(
                "계획을 만든 뒤 Blender 입력 파일이 변경되었습니다. 새 계획을 만들어 주세요."
            )
        original = next((item for item in job.artifacts if same_path(item.path, input_path)), None)
        if original and original.sha256 and original.sha256 != original_hash:
            raise RuntimeError("등록된 Blender 입력의 SHA-256이 변경되었습니다.")
        output = Path(request.output_directory) / "execution.json"
        command = self._base_command(
            job,
            [
                "execute",
                "--proposal",
                request.proposal_path,
                "--expected-plan-sha256",
                request.plan_sha256,
                "--input",
                str(input_path),
                "--output-root",
                request.output_directory,
                "--output",
                str(output),
            ],
        )
        return command, output, original_hash

    def collect_execution(
        self, request: BlenderRequest, execution_path: Path, original_hash: str
    ) -> list[Artifact]:
        input_path = Path(request.input_path).resolve(strict=True)
        if sha256_file(input_path) != original_hash:
            raise RuntimeError("Blender 실행 중 원본 파일 해시가 변경되었습니다.")
        envelope = json.loads(execution_path.read_text(encoding="utf-8"))
        if envelope.get("plan_sha256") != request.plan_sha256:
            raise RuntimeError("실행된 계획이 승인 계획과 다릅니다.")
        result = envelope.get("result", {})
        if result.get("status") != "completed":
            raise RuntimeError(result.get("summary") or "Blender 편집이 실패했습니다.")
        paths = [Path(value).resolve(strict=True) for value in result.get("artifacts", [])]
        by_kind = {path.suffix.lower().lstrip("."): path for path in paths}
        required = self.required_formats(request)
        missing = sorted(required - set(by_kind))
        if missing:
            raise RuntimeError("Blender 결과 산출물이 없습니다: " + ", ".join(missing))
        version_root = Path(request.output_directory).resolve(strict=True)
        artifacts: list[Artifact] = []
        data = result.get("data", {})
        if not isinstance(data, dict):
            raise RuntimeError("Blender 보존 검사 응답 형식이 올바르지 않습니다.")
        preservation = data.get("preservation", {})
        if isinstance(preservation, dict) and preservation.get("failures"):
            raise RuntimeError("Blender 자산 보존 검사 실패: " + str(preservation["failures"]))
        if request.preserve_rig and (
            data.get("has_armature") is not True
            or not isinstance(preservation, dict)
            or preservation.get("status") != "passed"
            or preservation.get("scope") != "native_scene"
            or preservation.get("failures") != []
        ):
            raise RuntimeError("리깅 보존을 확인하지 못했습니다. Blender 검사 결과를 확인하세요.")
        for kind in sorted(set(by_kind) & self.REQUIRED):
            path = by_kind[kind]
            if path.stat().st_size <= 0:
                raise RuntimeError(f"0바이트 Blender 결과를 거부했습니다: {path}")
            if version_root not in path.parents:
                raise RuntimeError(f"Blender 결과가 버전 폴더를 벗어났습니다: {path}")
            artifacts.append(
                Artifact(
                    kind,
                    str(path),
                    "blender",
                    utc_now(),
                    version=request.version,
                    sha256=sha256_file(path),
                    parent_path=str(input_path),
                )
            )
        if data:
            report = version_root / "blender_report.json"
            report.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            artifacts.append(
                Artifact(
                    "blender_report",
                    str(report),
                    "blender",
                    utc_now(),
                    version=request.version,
                    sha256=sha256_file(report),
                    parent_path=str(input_path),
                )
            )
        request.status = "completed"
        request.session_path = result.get("log_path")
        return artifacts

    @classmethod
    def required_formats(cls, request: BlenderRequest) -> set[str]:
        steps = (request.plan or {}).get("steps", [])
        if steps and steps[-1].get("tool") == "asset.export":
            formats = steps[-1].get("arguments", {}).get("formats", sorted(cls.REQUIRED))
            if (
                not isinstance(formats, list)
                or not formats
                or any(item not in cls.REQUIRED for item in formats)
            ):
                raise RuntimeError("승인 계획의 export 형식이 올바르지 않습니다.")
            return set(formats)
        return cls.REQUIRED

    @staticmethod
    def available_inputs(job: Job) -> list[tuple[str, str]]:
        choices: list[tuple[str, str]] = []
        seen: set[str] = set()
        for item in reversed(job.artifacts):
            path = Path(item.path)
            if path.suffix.lower() not in {".blend", ".glb", ".fbx"} or not path.is_file():
                continue
            if item.stage == "rigging" and item.kind not in {
                "humanoid_blend",
                "humanoid_fbx",
                "rigged_glb",
            }:
                continue
            key = str(path.resolve()).casefold()
            if key in seen:
                continue
            seen.add(key)
            origin = {"modeling": "모델 원본", "blender": "Blender", "rigging": "리깅"}.get(
                item.stage, item.stage
            )
            version = f" v{item.version:03d}" if item.version else ""
            choices.append(
                (
                    str(path.resolve()),
                    f"{origin}{version} · {path.suffix[1:].upper()} · {path.name}",
                )
            )
        if job.blender_input_path and Path(job.blender_input_path).is_file():
            path = str(Path(job.blender_input_path).resolve())
            if path.casefold() not in seen:
                choices.insert(0, (path, "현재 입력 · " + Path(path).name))
        return choices

    def select_input(self, job: Job, path: str) -> None:
        if not any(same_path(path, candidate) for candidate, _label in self.available_inputs(job)):
            raise ValueError("이 작업에 등록된 Blender 입력을 선택하세요.")
        job.blender_input_path = str(Path(path).resolve(strict=True))
        request = job.latest_blender_request
        if (
            request
            and request.status == "awaiting_approval"
            and not same_path(request.input_path, path)
        ):
            request.status = "cancelled"
        self.jobs.save(job)

    def build_check(self, jobs_root: Path) -> ProcessCommand:
        session_dir = jobs_root.parent / "environment-sessions"
        environment = self.config.agent_environment(session_dir)
        return ProcessCommand(
            str(self.config.agent_python),
            [str(self.bridge), "check"],
            self.config.blender_agent_root,
            environment,
        )
