from __future__ import annotations

from pathlib import Path
from dataclasses import replace

import pytest

from forgeflow.adapters.modeling_adapter import ModelingAdapter
from forgeflow.services.job_service import JobService


def test_release_targets_both_models_and_their_servers(config, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "ollama.exe")
    monkeypatch.setenv("OLLAMA_HOST", "http://localhost:12000")
    commands = ModelingAdapter(config, JobService(config.jobs_root)).build_release_ollama()
    assert [c.arguments for c in commands] == [
        ["stop", config.ollama_model], ["stop", config.unity_agent_model],
    ]
    assert [c.environment["OLLAMA_HOST"] for c in commands] == [config.ollama_base_url, "http://localhost:12000"]


def test_release_deduplicates_same_model_on_same_server(config, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "ollama.exe")
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    config = replace(config, unity_agent_model=config.ollama_model + ":latest")
    commands = ModelingAdapter(config, JobService(config.jobs_root)).build_release_ollama()
    assert len(commands) == 1


@pytest.mark.parametrize("stage", ["modeling", "rigging"])
@pytest.mark.parametrize("failure_index", [None, 0, 1])
def test_pipeline_waits_for_all_releases_and_stops_on_failure(config, monkeypatch, stage, failure_index):
    from types import SimpleNamespace
    from forgeflow.services.pipeline_service import PipelineService

    monkeypatch.setattr("shutil.which", lambda name: "ollama.exe")
    commands = ModelingAdapter(config, JobService(config.jobs_root)).build_release_ollama()
    started, pending, ready, failures = [], [], [], []

    def begin(job, operation, command, log, handler):
        assert operation == stage + "_vram"
        started.append(command)
        pending.append(handler)

    pipeline = SimpleNamespace(_begin=begin, log_received=SimpleNamespace(emit=lambda message: None))
    pipeline._release_models = lambda *args: PipelineService._release_models(pipeline, *args)
    pipeline._release_models(None, stage, commands, None, lambda: ready.append(True), failures.append)
    assert started == commands[:1]
    assert not ready
    pending.pop(0)(1 if failure_index == 0 else 0)
    if failure_index != 0:
        assert started == commands
        assert not ready
        pending.pop(0)(1 if failure_index == 1 else 0)
    assert bool(ready) == (failure_index is None)
    assert bool(failures) == (failure_index is not None)


def test_missing_and_zero_byte_artifacts_rejected(tmp_path: Path):
    missing = tmp_path / "missing.glb"
    with pytest.raises(RuntimeError, match="없습니다"):
        ModelingAdapter.verify_artifacts([missing])
    empty = tmp_path / "empty.glb"
    empty.write_bytes(b"")
    with pytest.raises(RuntimeError, match="0바이트"):
        ModelingAdapter.verify_artifacts([empty])


def test_collect_generation_normalizes_names_without_overwrite(config, image):
    jobs = JobService(config.jobs_root)
    job = jobs.create("model", image)
    adapter = ModelingAdapter(config, jobs)
    run = jobs.job_directory(job.job_id) / ".runs" / "modeling-001"
    source = run / "source"
    source.mkdir(parents=True)
    for suffix in ("glb", "blend", "fbx"):
        (source / f"source.{suffix}").write_bytes(suffix.encode())
    artifacts = adapter.collect_generation(job, run)
    assert {item.kind for item in artifacts} == {"glb", "blend", "fbx"}
    assert all(Path(item.path).stat().st_size for item in artifacts)
    with pytest.raises(RuntimeError, match="덮어쓸"):
        adapter.collect_generation(job, run)
