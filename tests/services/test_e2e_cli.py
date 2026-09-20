from __future__ import annotations

import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from forgeflow.domain.job import UnityTurn
from forgeflow.services.job_service import JobService


@pytest.fixture
def e2e_modules(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts" / "e2e"))
    return {
        name: importlib.import_module(name)
        for name in (
            "_evidence",
            "run_unity_e2e",
            "run_full_e2e",
            "run_blender_e2e",
            "run_rigging_e2e",
        )
    }


def prepare_cli(e2e_modules, config, tmp_path, monkeypatch, arguments):
    evidence = tmp_path / "fallback-evidence.json"
    module = e2e_modules["_evidence"]
    monkeypatch.setattr(module, "default_evidence_path", lambda name: evidence)
    monkeypatch.setattr(module.AppConfig, "load", lambda: config)
    monkeypatch.setattr(sys, "argv", ["e2e", *arguments])
    return evidence


@pytest.mark.parametrize("failure", ["generation-timeout", "preview-failure"])
def test_full_e2e_failure_saves_state_and_completed_originals(
    e2e_modules, config, image, tmp_path, monkeypatch, failure
):
    module = e2e_modules["run_full_e2e"]
    fallback = prepare_cli(e2e_modules, config, tmp_path, monkeypatch, ["--image", str(image)])
    scripts = config.modeling_root / "scripts"
    scripts.mkdir()
    (scripts / "generate_model.ps1").write_text("# fixture", encoding="utf-8")
    monkeypatch.setattr(module.ModelingAdapter, "build_release_ollama", lambda self: [])

    def run(command, log_path, timeout):
        if "-ArtifactRoot" in command.arguments:
            if failure == "generation-timeout":
                raise subprocess.TimeoutExpired(command.executable, timeout)
            root = Path(command.arguments[command.arguments.index("-ArtifactRoot") + 1])
            (root / "source").mkdir()
            for kind in ("glb", "blend", "fbx"):
                (root / "source" / f"source.{kind}").write_bytes(kind.encode())
            return 0
        return 1

    monkeypatch.setattr(module, "run_command", run)
    assert module.main() == 1
    jobs = JobService(config.jobs_root)
    job = jobs.list_jobs()[0]
    assert job.stages["modeling"].status == "failed"
    evidence = json.loads(fallback.read_text(encoding="utf-8"))
    assert evidence["status"] == "failed"
    if failure == "preview-failure":
        assert evidence["phase"] == "modeling_preview"
        assert len(job.artifacts) == 3
        assert job.blender_input_path
        assert module.ModelingAdapter(config, jobs).existing_generation(job)
    else:
        assert "TimeoutExpired" in evidence["error"]
        assert job.artifacts == []


def test_blender_initial_inspection_failure_is_recorded(
    e2e_modules, config, image, tmp_path, monkeypatch
):
    module = e2e_modules["run_blender_e2e"]
    asset = tmp_path / "source.glb"
    asset.write_bytes(b"source")
    fallback = prepare_cli(
        e2e_modules,
        config,
        tmp_path,
        monkeypatch,
        ["--image", str(image), "--asset", str(asset)],
    )
    config.agent_python.parent.mkdir(parents=True)
    config.agent_python.write_bytes(b"fixture")
    monkeypatch.setattr(module, "run_command", lambda *args: 1)
    assert module.main() == 1
    assert json.loads(fallback.read_text(encoding="utf-8"))["status"] == "failed"
    job = JobService(config.jobs_root).list_jobs()[0]
    assert job.stages["blender"].status == "failed"
    assert asset.read_bytes() == b"source"


@pytest.mark.parametrize("override", [False, True])
def test_rigging_uses_saved_config_and_optional_jobs_root(
    e2e_modules, config, tmp_path, monkeypatch, override
):
    module = e2e_modules["run_rigging_e2e"]
    asset = tmp_path / "source.glb"
    asset.write_bytes(b"source")
    selected_root = tmp_path / "cli-jobs" if override else config.jobs_root
    arguments = ["--asset", str(asset)]
    if override:
        arguments += ["--jobs-root", str(selected_root)]
    fallback = prepare_cli(e2e_modules, config, tmp_path, monkeypatch, arguments)
    monkeypatch.setattr(module, "unirig_commit", lambda: None)
    seen = []

    def unavailable(self, job, asset, seed):
        seen.append(self.config)
        raise RuntimeError("fixture: configured engine unavailable")

    monkeypatch.setattr(module.RiggingAdapter, "build_rigging", unavailable)
    assert module.main() == 1
    assert seen[0].modeling_root == config.modeling_root
    assert seen[0].blender_executable == config.blender_executable
    assert seen[0].jobs_root == selected_root
    job = JobService(selected_root).list_jobs()[0]
    assert job.stages["rigging"].status == "failed"
    evidence = json.loads(fallback.read_text(encoding="utf-8"))
    assert evidence["status"] == "failed"
    assert evidence["verdict"] == "FAIL"
    assert not config.config_path.exists()


def test_unity_missing_job_keeps_startup_failure_evidence(
    e2e_modules, config, tmp_path, monkeypatch, qapp
):
    module = e2e_modules["run_unity_e2e"]
    fallback = prepare_cli(
        e2e_modules,
        config,
        tmp_path,
        monkeypatch,
        ["--project", str(tmp_path), "--job-id", "missing", "--only", "read"],
    )
    assert module.main() == 1
    evidence = json.loads(fallback.read_text(encoding="utf-8"))
    assert evidence["status"] == "failed"
    assert "FileNotFoundError" in evidence["error"]


def test_unity_timeout_keeps_active_turn_evidence_and_closes_state(
    e2e_modules, config, image, tmp_path, monkeypatch, qapp
):
    module = e2e_modules["run_unity_e2e"]
    jobs = JobService(config.jobs_root)
    job = jobs.create("unity-timeout", image)
    fallback = prepare_cli(
        e2e_modules,
        config,
        tmp_path,
        monkeypatch,
        ["--project", str(tmp_path), "--job-id", job.job_id, "--only", "read"],
    )
    shutdown = []

    class Signal:
        def __init__(self):
            self.callbacks = []

        def connect(self, callback):
            self.callbacks.append(callback)

        def emit(self, value):
            for callback in self.callbacks:
                callback(value)

    class Adapter:
        def __init__(self, config, jobs):
            self.jobs = jobs
            self.ready = True
            self.event_received, self.log_received, self.protocol_error = (
                Signal(),
                Signal(),
                Signal(),
            )

        def start_session(self, job, project):
            self.job = job

        def send_prompt(self, prompt, **options):
            turn = UnityTurn("turn-timeout", "session", prompt, prompt)
            turn.run_log_path = "partial-run.log"
            self.job.unity_turns.append(turn)
            self.jobs.set_stage(self.job, "unity", "running")
            self.event_received.emit({"type": "tool_started", "tool": "scene.read"})
            return turn

        def shutdown(self):
            shutdown.append(True)

    def wait(app, predicate, timeout, label):
        if label != "session ready":
            raise TimeoutError("turn read timed out")
        assert predicate()

    monkeypatch.setattr(module, "UnityAdapter", Adapter)
    monkeypatch.setattr(module, "wait_until", wait)
    assert module.main() == 1
    evidence = json.loads(fallback.read_text(encoding="utf-8"))
    assert shutdown == [True]
    assert evidence["status"] == "failed"
    assert evidence["active_turn_id"] == "turn-timeout"
    assert evidence["runs"][0]["tool_events"][0]["tool"] == "scene.read"
    assert evidence["runs"][0]["run_log_path"] == "partial-run.log"
    assert evidence["runs"][0]["status"] == "failed"
    restored = jobs.load(job.job_id)
    assert restored.stages["unity"].status == "failed"
    assert restored.latest_unity_turn.status == "failed"
