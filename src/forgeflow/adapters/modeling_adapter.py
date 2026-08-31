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

    def build_release_ollama(self) -> ProcessCommand | None:
        executable = shutil.which("ollama")
        if not executable:
            return None
        return ProcessCommand(executable, ["stop", self.config.ollama_model], self.config.modeling_root)

    def build_generation(self, job: Job) -> tuple[ProcessCommand, Path]:
        image = self.jobs.validate_image(job.input_image_path)
        script = self.config.modeling_root / "scripts" / "generate_model.ps1"
        if not script.is_file():
            raise ValueError(f"이미지 생성 스크립트가 없습니다: {script}")
        attempt = job.stages["modeling"].attempts + 1
        run_root = self.jobs.job_directory(job.job_id) / ".runs" / f"modeling-{attempt:03d}"
        run_root.mkdir(parents=True, exist_ok=False)
        arguments = [
            "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", str(script.resolve()), "-Image", str(image), "-Name", "source",
            "-Seed", str(int(job.generation_settings.get("seed", 42))),
            "-ArtifactRoot", str(run_root.resolve()),
        ]
        return ProcessCommand(self.config.powershell, arguments, self.config.modeling_root), run_root

    def collect_generation(self, job: Job, run_root: Path) -> list[Artifact]:
        source_dir = run_root / "source"
        expected = {kind: source_dir / f"source.{kind}" for kind in self.REQUIRED}
        self.verify_artifacts(expected.values())
        final_dir = self.jobs.job_directory(job.job_id) / "modeling"
        artifacts: list[Artifact] = []
        for kind, source in expected.items():
            destination = final_dir / f"source.{kind}"
            if destination.exists():
                raise RuntimeError(f"기존 원본 산출물을 덮어쓸 수 없습니다: {destination}")
            temporary = destination.with_suffix(destination.suffix + ".tmp")
            shutil.copy2(source, temporary)
            os.replace(temporary, destination)
            artifacts.append(
                Artifact(kind, str(destination.resolve()), "modeling", utc_now(), sha256=sha256_file(destination))
            )
        return artifacts

    def build_preview(self, job: Job) -> tuple[ProcessCommand, Path]:
        directory = self.jobs.job_directory(job.job_id)
        input_glb = directory / "modeling" / "source.glb"
        output = directory / "modeling" / "preview.png"
        script = self.config.modeling_root / "scripts" / "render_preview.py"
        command = ProcessCommand(
            str(self.config.blender_executable),
            ["--background", "--factory-startup", "--disable-autoexec", "--python", str(script), "--", "--input", str(input_glb), "--output", str(output)],
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
