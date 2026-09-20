from __future__ import annotations

import os
import shutil
from pathlib import Path

from forgeflow.config import AppConfig
from forgeflow.domain.artifact import Artifact
from forgeflow.domain.job import Job, utc_now
from forgeflow.domain.process import ProcessCommand
from forgeflow.services.job_service import JobService, sha256_file


class ModelingAdapter:
    REQUIRED = ("glb", "blend", "fbx")

    def __init__(self, config: AppConfig, jobs: JobService):
        self.config = config
        self.jobs = jobs

    def build_release_ollama(self) -> list[ProcessCommand]:
        executable = shutil.which("ollama")
        if not executable:
            raise RuntimeError("Ollama 실행 파일을 찾을 수 없어 VRAM을 해제할 수 없습니다.")
        commands = []
        seen = set()
        for host, model in (
            (self.config.ollama_base_url, self.config.ollama_model),
            (
                os.environ.get("OLLAMA_HOST") or "http://127.0.0.1:11434",
                self.config.unity_agent_model,
            ),
        ):
            host = host.rstrip("/")
            normalized_model = model if ":" in model.rsplit("/", 1)[-1] else model + ":latest"
            if (host, normalized_model) in seen:
                continue
            seen.add((host, normalized_model))
            environment = dict(os.environ)
            environment["OLLAMA_HOST"] = host
            commands.append(
                ProcessCommand(executable, ["stop", model], self.config.modeling_root, environment)
            )
        return commands

    def build_generation(self, job: Job) -> tuple[ProcessCommand, Path]:
        if self.existing_generation(job):
            raise RuntimeError("기존 원본 산출물은 보존됩니다. 미리보기만 재시도하세요.")
        image = self.jobs.validate_image(job.input_image_path)
        script = self.config.modeling_root / "scripts" / "generate_model.ps1"
        if not script.is_file():
            raise ValueError(f"이미지 생성 스크립트가 없습니다: {script}")
        attempt = job.stages["modeling"].attempts + 1
        run_root = self.jobs.job_directory(job.job_id) / ".runs" / f"modeling-{attempt:03d}"
        run_root.mkdir(parents=True, exist_ok=False)
        arguments = [
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script.resolve()),
            "-Image",
            str(image),
            "-Name",
            "source",
            "-Seed",
            str(int(job.generation_settings.get("seed", 42))),
            "-ArtifactRoot",
            str(run_root.resolve()),
        ]
        return ProcessCommand(
            self.config.powershell, arguments, self.config.modeling_root
        ), run_root

    def collect_generation(self, job: Job, run_root: Path) -> list[Artifact]:
        source_dir = run_root / "source"
        expected = {kind: source_dir / f"source.{kind}" for kind in self.REQUIRED}
        self.verify_artifacts(expected.values())
        final_dir = self.jobs.job_directory(job.job_id) / "modeling"
        for kind in self.REQUIRED:
            destination = final_dir / f"source.{kind}"
            if destination.exists():
                raise RuntimeError(f"기존 원본 산출물을 덮어쓸 수 없습니다: {destination}")
        artifacts: list[Artifact] = []
        for kind, source in expected.items():
            destination = final_dir / f"source.{kind}"
            temporary = destination.with_suffix(destination.suffix + ".tmp")
            shutil.copy2(source, temporary)
            os.replace(temporary, destination)
            artifacts.append(
                Artifact(
                    kind,
                    str(destination.resolve()),
                    "modeling",
                    utc_now(),
                    sha256=sha256_file(destination),
                )
            )
        return artifacts

    def existing_generation(self, job: Job) -> list[Artifact] | None:
        """Only a complete, registered and unchanged original set is resumable."""
        directory = self.jobs.job_directory(job.job_id)
        expected = {kind: directory / "modeling" / f"source.{kind}" for kind in self.REQUIRED}
        originals = [
            item
            for item in job.artifacts
            if item.stage == "modeling" and item.kind in self.REQUIRED
        ]
        if not originals and not any(
            path.exists() or path.is_symlink() for path in expected.values()
        ):
            return None
        validated = []
        for kind, path in expected.items():
            matches = [item for item in originals if item.kind == kind]
            if len(matches) != 1:
                raise RuntimeError(
                    f"모델링 원본 {kind.upper()} 등록이 불완전합니다. 기존 파일을 보존하고 새 작업을 만드세요."
                )
            artifact = matches[0]
            resolved = path.resolve()
            if not resolved.is_relative_to(directory) or Path(artifact.path).resolve() != resolved:
                raise RuntimeError(f"모델링 원본 경로가 일치하지 않습니다: {kind.upper()}")
            self.verify_artifacts([path])
            if not artifact.sha256 or sha256_file(path) != artifact.sha256:
                raise RuntimeError(f"모델링 원본 SHA-256 검증에 실패했습니다: {kind.upper()}")
            validated.append(artifact)
        return validated

    def build_preview(self, job: Job) -> tuple[ProcessCommand, Path]:
        if self.existing_generation(job) is None:
            raise RuntimeError("미리보기를 생성할 검증된 모델링 원본이 없습니다.")
        directory = self.jobs.job_directory(job.job_id)
        input_glb = directory / "modeling" / "source.glb"
        output = directory / "modeling" / "preview.png"
        script = self.config.modeling_root / "scripts" / "render_preview.py"
        command = ProcessCommand(
            str(self.config.blender_executable),
            [
                "--background",
                "--factory-startup",
                "--disable-autoexec",
                "--python",
                str(script),
                "--",
                "--input",
                str(input_glb),
                "--output",
                str(output),
            ],
            directory,
        )
        return command, output

    @staticmethod
    def verify_artifacts(paths) -> None:
        missing = [str(path) for path in paths if not Path(path).is_file()]
        if missing:
            raise RuntimeError("필수 산출물이 없습니다: " + ", ".join(missing))
        empty = [str(path) for path in paths if Path(path).stat().st_size <= 0]
        if empty:
            raise RuntimeError("0바이트 산출물을 거부했습니다: " + ", ".join(empty))
