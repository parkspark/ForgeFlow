from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from forgeflow.config import AppConfig
from forgeflow.domain.artifact import Artifact
from forgeflow.domain.job import Job, RiggingRequest, utc_now
from forgeflow.services.job_service import JobService, sha256_file

from .modeling_adapter import ModelingAdapter, ProcessCommand


RIGGING_KINDS = {
    "skeleton_fbx": "{name}_skeleton.fbx",
    "skin_fbx": "{name}_skin.fbx",
    "rigged_glb": "{name}_rigged.glb",
    "unity_blend": "{name}_unity.blend",
    "unity_fbx": "{name}_unity.fbx",
    "humanoid_blend": "{name}_humanoid.blend",
    "humanoid_fbx": "{name}_humanoid.fbx",
    "rig_report": "rig_report.json",
}


@dataclass(frozen=True)
class RiggingInput:
    path: Path
    stage: str
    version: int | None
    label: str


@dataclass
class RiggingRun:
    request: RiggingRequest
    input_path: Path
    execution_input: Path
    execution_directory: Path
    final_directory: Path
    staged: bool


class RiggingAdapter:
    MIN_SEED = 0
    MAX_SEED = 2_147_483_647

    def __init__(self, config: AppConfig, jobs: JobService):
        self.config = config
        self.jobs = jobs

    @staticmethod
    def validate_seed(seed: int | str) -> int:
        if isinstance(seed, bool):
            raise ValueError("Seed는 정수여야 합니다.")
        try:
            value = int(seed)
        except (TypeError, ValueError) as exc:
            raise ValueError("Seed는 정수여야 합니다.") from exc
        if str(seed).strip() != str(value) and not isinstance(seed, int):
            raise ValueError("Seed는 정수여야 합니다.")
        if not RiggingAdapter.MIN_SEED <= value <= RiggingAdapter.MAX_SEED:
            raise ValueError(f"Seed 범위는 {RiggingAdapter.MIN_SEED}~{RiggingAdapter.MAX_SEED}입니다.")
        return value

    @staticmethod
    def validate_input(path: str | Path) -> Path:
        try:
            source = Path(path).expanduser().resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ValueError(f"리깅 입력 GLB가 존재하지 않습니다: {path}") from exc
        if not source.is_file():
            raise ValueError(f"리깅 입력이 파일이 아닙니다: {source}")
        if source.suffix.lower() != ".glb":
            raise ValueError("리깅 입력은 GLB만 허용합니다.")
        if source.stat().st_size <= 0:
            raise ValueError(f"리깅 입력 GLB가 비어 있습니다: {source}")
        return source

    def available_inputs(self, job: Job) -> list[RiggingInput]:
        candidates: list[RiggingInput] = []
        seen: set[Path] = set()
        blender = sorted(
            (item for item in job.artifacts if item.stage == "blender" and item.kind == "glb"),
            key=lambda item: (item.version or 0, item.created_at), reverse=True,
        )
        modeling = sorted(
            (item for item in job.artifacts if item.stage == "modeling" and item.kind == "glb"),
            key=lambda item: item.created_at, reverse=True,
        )
        for item in [*blender, *modeling]:
            path = Path(item.path).resolve(strict=False)
            if path in seen or not path.is_file() or path.suffix.lower() != ".glb":
                continue
            seen.add(path)
            origin = f"Blender v{(item.version or 0):03d}" if item.stage == "blender" else "모델링 원본"
            candidates.append(RiggingInput(path, item.stage, item.version, f"{origin} · {path}"))
        return candidates

    def select_input(self, job: Job, selected: str | Path | None = None) -> RiggingInput:
        choices = self.available_inputs(job)
        if selected is None:
            if not choices:
                raise ValueError("작업에 리깅 가능한 GLB 산출물이 없습니다.")
            return choices[0]
        source = self.validate_input(selected)
        for choice in choices:
            if choice.path == source:
                return choice
        raise ValueError("선택한 GLB는 이 작업에 등록된 modeling/Blender 산출물이 아닙니다.")

    @staticmethod
    def safe_name(job_id: str, version: int) -> str:
        ascii_id = re.sub(r"[^A-Za-z0-9_-]+", "_", job_id.encode("ascii", "ignore").decode("ascii"))
        ascii_id = ascii_id.strip("_-") or "job"
        value = f"rig_{ascii_id}_v{version:03d}"
        if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
            raise ValueError("안전한 리깅 Name을 생성할 수 없습니다.")
        return value

    @staticmethod
    def _contains_whitespace(path: Path) -> bool:
        return bool(re.search(r"\s", str(path.resolve(strict=False))))

    def build_rigging(
        self, job: Job, selected_input: str | Path | None = None, seed: int | str = 12345
    ) -> tuple[ProcessCommand, RiggingRun]:
        script = self.config.rigging_script.resolve(strict=False)
        if not script.is_file():
            raise ValueError(f"UniRig 진입점이 없습니다: {script}")
        selected = self.select_input(job, selected_input)
        source = self.validate_input(selected.path)
        seed_value = self.validate_seed(seed)
        version = self.jobs.next_rigging_version(job)
        safe_name = self.safe_name(job.job_id, version)
        final_directory = self.jobs.job_directory(job.job_id) / "rigging" / f"v{version:03d}"
        if final_directory.exists() and any(final_directory.iterdir()):
            raise RuntimeError(f"기존 리깅 버전 폴더를 덮어쓸 수 없습니다: {final_directory}")
        final_directory.mkdir(parents=True, exist_ok=True)

        staged = self._contains_whitespace(source) or self._contains_whitespace(final_directory)
        execution_input = source
        execution_directory = final_directory
        if staged:
            staging_root = self.config.rigging_staging_root.resolve(strict=False)
            if self._contains_whitespace(staging_root):
                raise ValueError(f"UniRig staging 경로에 공백이 있어 실행할 수 없습니다: {staging_root}")
            staging = staging_root / job.job_id / f"v{version:03d}"
            if staging.exists() and any(staging.iterdir()):
                raise RuntimeError(f"기존 staging 폴더를 재사용할 수 없습니다: {staging}")
            execution_directory = staging / "output"
            execution_directory.mkdir(parents=True, exist_ok=False)
            execution_input = staging / "input.glb"
            shutil.copy2(source, execution_input)

        input_sha256 = sha256_file(source)
        if staged and sha256_file(execution_input) != input_sha256:
            raise RuntimeError("staging 복사 중 입력 GLB 내용이 변경되었습니다.")
        request = RiggingRequest(
            input_path=str(source), input_sha256=input_sha256,
            output_directory=str(final_directory.resolve()), version=version,
            seed=seed_value, safe_name=safe_name,
        )
        arguments = [
            "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", str(script), "-InputGlb", str(execution_input.resolve()),
            "-Name", safe_name, "-OutputDirectory", str(execution_directory.resolve()),
            "-Seed", str(seed_value),
        ]
        command = ProcessCommand(self.config.powershell, arguments, self.config.modeling_root)
        return command, RiggingRun(
            request, source, execution_input.resolve(), execution_directory.resolve(),
            final_directory.resolve(), staged,
        )

    @staticmethod
    def expected_paths(directory: Path, safe_name: str) -> dict[str, Path]:
        return {kind: directory / pattern.format(name=safe_name) for kind, pattern in RIGGING_KINDS.items()}

    @staticmethod
    def _same_path(left: str | Path, right: Path) -> bool:
        return os.path.normcase(str(Path(left).resolve(strict=False))) == os.path.normcase(str(right.resolve(strict=False)))

    def validate_report(self, report_path: Path, expected: dict[str, Path]) -> dict[str, Any]:
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"rig_report.json을 파싱할 수 없습니다: {exc}") from exc
        if not isinstance(report, dict):
            raise RuntimeError("rig_report.json 루트는 객체여야 합니다.")
        failures: list[str] = []
        if report.get("status") != "PASS":
            failures.append(f"status={report.get('status')!r}")
        if isinstance(report.get("bone_count"), bool) or not isinstance(report.get("bone_count"), int) or report["bone_count"] <= 0:
            failures.append("bone_count는 0보다 커야 합니다")
        missing = report.get("missing_required_bones")
        if not isinstance(missing, list) or missing:
            failures.append(f"missing_required_bones={missing!r}")
        vertex_count = report.get("vertex_count")
        weighted = report.get("weighted_vertices")
        if isinstance(vertex_count, bool) or not isinstance(vertex_count, int) or vertex_count <= 0:
            failures.append("vertex_count는 0보다 커야 합니다")
        if weighted != vertex_count:
            failures.append(f"weighted_vertices({weighted!r}) != vertex_count({vertex_count!r})")
        influences = report.get("max_influences")
        if isinstance(influences, bool) or not isinstance(influences, int) or not 1 <= influences <= 4:
            failures.append(f"max_influences={influences!r} (허용 1~4)")
        for key, kind in (("fbx", "humanoid_fbx"), ("blend", "humanoid_blend")):
            value = report.get(key)
            if not isinstance(value, str) or not self._same_path(value, expected[kind]):
                failures.append(f"report.{key} 경로가 실제 결과와 다릅니다")
        if failures:
            raise RuntimeError("Humanoid 구조 검증 실패: " + "; ".join(failures))
        return report

    def collect_rigging(self, run: RiggingRun) -> tuple[list[Artifact], dict[str, Any]]:
        if sha256_file(run.input_path) != run.request.input_sha256:
            raise RuntimeError("UniRig 실행 중 입력 원본 GLB 해시가 변경되었습니다.")
        execution_paths = self.expected_paths(run.execution_directory, run.request.safe_name)
        ModelingAdapter.verify_artifacts(execution_paths.values())
        report = self.validate_report(execution_paths["rig_report"], execution_paths)

        final_paths = self.expected_paths(run.final_directory, run.request.safe_name)
        if run.staged:
            for kind, source in execution_paths.items():
                if kind == "rig_report":
                    continue
                destination = final_paths[kind]
                if destination.exists():
                    raise RuntimeError(f"기존 리깅 산출물을 덮어쓸 수 없습니다: {destination}")
                temporary = destination.with_suffix(destination.suffix + ".tmp")
                shutil.copy2(source, temporary)
                os.replace(temporary, destination)
            report = dict(report)
            report["blend"] = str(final_paths["humanoid_blend"].resolve())
            report["fbx"] = str(final_paths["humanoid_fbx"].resolve())
            temporary_report = final_paths["rig_report"].with_suffix(".json.tmp")
            temporary_report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temporary_report, final_paths["rig_report"])

        ModelingAdapter.verify_artifacts(final_paths.values())
        report = self.validate_report(final_paths["rig_report"], final_paths)
        if sha256_file(run.input_path) != run.request.input_sha256:
            raise RuntimeError("UniRig 결과 수집 중 입력 원본 GLB 해시가 변경되었습니다.")

        artifacts = [
            Artifact(kind, str(path.resolve()), "rigging", utc_now(), version=run.request.version,
                     sha256=sha256_file(path), parent_path=str(run.input_path))
            for kind, path in final_paths.items()
        ]
        run.request.status = "completed"
        run.request.report_path = str(final_paths["rig_report"].resolve())
        run.request.completed_at = utc_now()
        return artifacts, report

    def build_preview(self, run: RiggingRun, pose: bool = False) -> tuple[ProcessCommand, Path, str]:
        script = self.config.modeling_root / "scripts" / "render_preview.py"
        if not script.is_file():
            raise ValueError(f"미리보기 스크립트가 없습니다: {script}")
        blend = self.expected_paths(run.final_directory, run.request.safe_name)["humanoid_blend"]
        kind = "pose_preview" if pose else "rest_preview"
        output = run.final_directory / ("pose_preview.png" if pose else "rest_preview.png")
        arguments = [
            "--background", "--factory-startup", "--disable-autoexec", "--python", str(script), "--",
            "--input", str(blend), "--output", str(output),
        ]
        if pose:
            arguments.append("--pose")
        return ProcessCommand(str(self.config.blender_executable), arguments, run.final_directory), output, kind

    def collect_preview(self, run: RiggingRun, output: Path, kind: str) -> Artifact:
        ModelingAdapter.verify_artifacts([output])
        return Artifact(
            kind, str(output.resolve()), "rigging", utc_now(), version=run.request.version,
            sha256=sha256_file(output), parent_path=str(
                self.expected_paths(run.final_directory, run.request.safe_name)["humanoid_blend"]
            ),
        )

    def register_success(self, job: Job, run: RiggingRun, artifacts: list[Artifact]) -> None:
        kinds = {item.kind for item in artifacts}
        missing = set(RIGGING_KINDS) - kinds
        if missing:
            raise RuntimeError("등록할 리깅 산출물이 부족합니다: " + ", ".join(sorted(missing)))
        for artifact in artifacts:
            self.jobs.add_artifact(job, artifact)
        paths = self.expected_paths(run.final_directory, run.request.safe_name)
        job.rigging_input_path = str(run.input_path)
        job.humanoid_fbx_path = str(paths["humanoid_fbx"].resolve())
        job.humanoid_blend_path = str(paths["humanoid_blend"].resolve())
        job.unity_input_path = job.humanoid_fbx_path
        self.jobs.save(job)
