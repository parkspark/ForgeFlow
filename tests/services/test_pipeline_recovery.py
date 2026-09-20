from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from forgeflow.adapters.modeling_adapter import ModelingAdapter
from forgeflow.services.job_service import JobService, sha256_file
from forgeflow.services.pipeline_service import PipelineService
from tests.asset_fixtures import png_bytes


@pytest.fixture
def generated_model(config, image):
    jobs = JobService(config.jobs_root)
    job = jobs.create("preview recovery", image)
    adapter = ModelingAdapter(config, jobs)
    source = jobs.job_directory(job.job_id) / ".runs" / "modeling-001" / "source"
    source.mkdir(parents=True)
    for kind in adapter.REQUIRED:
        (source / f"source.{kind}").write_bytes(f"original-{kind}".encode())
    artifacts = adapter.collect_generation(job, source.parent)
    jobs.add_artifacts(job, artifacts)
    jobs.set_stage(job, "modeling", "running")
    return jobs, job, adapter


def no_generation(*args):
    raise AssertionError("preview recovery must not launch generation or release Ollama")


def prepare_recovery(jobs, adapter, monkeypatch):
    pipeline = PipelineService(jobs, adapter, None, None)  # type: ignore[arg-type]
    started = []
    monkeypatch.setattr(adapter, "build_generation", no_generation)
    monkeypatch.setattr(adapter, "build_release_ollama", no_generation)
    monkeypatch.setattr(pipeline, "_begin", lambda *args: started.append(args))
    return pipeline, started


def test_retry_without_originals_starts_generation(config, image, monkeypatch):
    scripts = config.modeling_root / "scripts"
    scripts.mkdir()
    (scripts / "generate_model.ps1").write_text("# test fixture", encoding="utf-8")
    jobs = JobService(config.jobs_root)
    job = jobs.create("generation failed", image)
    jobs.set_stage(job, "modeling", "failed")
    adapter = ModelingAdapter(config, jobs)
    pipeline = PipelineService(jobs, adapter, None, None)  # type: ignore[arg-type]
    started = []
    monkeypatch.setattr(adapter, "build_release_ollama", lambda: [])
    monkeypatch.setattr(pipeline, "_begin", lambda *args: started.append(args))
    pipeline.start_modeling(job)
    assert len(started) == 1
    assert started[0][1] == "modeling"
    assert "generate_model.ps1" in " ".join(started[0][2].arguments)
    assert job.stages["modeling"].status == "running"


@pytest.mark.parametrize("interruption", ["preview-failed", "cancelled", "app-closed"])
def test_modeling_retry_resumes_only_preview_after_reload(
    generated_model, monkeypatch, interruption
):
    jobs, job, adapter = generated_model
    pipeline = PipelineService(jobs, adapter, None, None)  # type: ignore[arg-type]
    preview = jobs.job_directory(job.job_id) / "modeling" / "preview.png"
    if interruption == "preview-failed":
        pipeline._preview_finished(job, preview, 1)
    elif interruption == "cancelled":
        jobs.set_stage(job, "modeling", "cancelled")
    else:
        jobs.recover_interrupted()
    job = jobs.load(job.job_id)
    originals = {item.path: sha256_file(Path(item.path)) for item in job.artifacts}
    pipeline, started = prepare_recovery(jobs, adapter, monkeypatch)

    pipeline.start_modeling(job)

    assert len(started) == 1
    assert started[0][1] == "modeling_preview"
    assert job.stages["modeling"].status == "running"
    assert job.stages["modeling"].attempts == 2
    assert len([p for p in (jobs.job_directory(job.job_id) / ".runs").iterdir() if p.is_dir()]) == 1
    preview.write_bytes(png_bytes())
    started[0][-1](0)
    restored = jobs.load(job.job_id)
    assert restored.stages["modeling"].status == "completed"
    assert len(restored.artifacts) == 4
    assert restored.blender_input_path == next(
        item.path for item in restored.artifacts if item.kind == "glb"
    )
    assert {path: sha256_file(Path(path)) for path in originals} == originals


@pytest.mark.parametrize(
    "damage",
    [
        "missing",
        "empty",
        "modified",
        "hash-missing",
        "unregistered",
        "partial",
        "duplicate",
        "path",
    ],
)
def test_unverified_originals_never_resume_or_regenerate(generated_model, monkeypatch, damage):
    jobs, job, adapter = generated_model
    # Legacy outputs without a transaction receipt must still fail closed.
    (jobs.job_directory(job.job_id) / ".runs" / "modeling-collection.json").unlink()
    jobs.set_stage(job, "modeling", "failed", error="preview failed")
    artifact = job.artifacts[-1]
    path = Path(artifact.path)
    if damage == "missing":
        path.unlink()
    elif damage == "empty":
        path.write_bytes(b"")
    elif damage == "modified":
        path.write_bytes(b"tampered")
    elif damage == "hash-missing":
        artifact.sha256 = None
    elif damage == "unregistered":
        job.artifacts.clear()
    elif damage == "partial":
        job.artifacts.pop()
    elif damage == "duplicate":
        job.artifacts.append(replace(artifact))
    elif damage == "path":
        artifact.path = str(jobs.root / "different.fbx")
    jobs.save(job)
    job = jobs.load(job.job_id)
    before = {p.name: p.read_bytes() for p in path.parent.iterdir() if p.is_file()}
    pipeline, started = prepare_recovery(jobs, adapter, monkeypatch)

    with pytest.raises(RuntimeError):
        pipeline.start_modeling(job)

    assert not started
    assert job.stages["modeling"].status == "failed"
    assert job.stages["modeling"].attempts == 1
    assert {p.name: p.read_bytes() for p in path.parent.iterdir() if p.is_file()} == before


def test_source_hash_is_checked_after_preview_process(generated_model, monkeypatch):
    jobs, job, adapter = generated_model
    jobs.set_stage(job, "modeling", "failed")
    pipeline, started = prepare_recovery(jobs, adapter, monkeypatch)
    pipeline.start_modeling(job)
    preview = jobs.job_directory(job.job_id) / "modeling" / "preview.png"
    preview.write_bytes(b"rendered-preview")
    Path(job.artifacts[0].path).write_bytes(b"unexpected-renderer-write")
    started[0][-1](0)
    assert job.stages["modeling"].status == "failed"
    assert "SHA-256" in job.stages["modeling"].error
    assert all(item.kind != "preview" for item in job.artifacts)


def test_collect_generation_checks_all_collisions_before_copy(config, image):
    jobs = JobService(config.jobs_root)
    job = jobs.create("preserve partial original", image)
    adapter = ModelingAdapter(config, jobs)
    root = jobs.job_directory(job.job_id)
    source = root / ".runs" / "modeling-001" / "source"
    source.mkdir(parents=True)
    for kind in adapter.REQUIRED:
        (source / f"source.{kind}").write_bytes(f"new-{kind}".encode())
    original = root / "modeling" / "source.fbx"
    original.write_bytes(b"already-present")
    with pytest.raises(RuntimeError, match="덮어쓸"):
        adapter.collect_generation(job, source.parent)
    assert original.read_bytes() == b"already-present"
    assert not (root / "modeling" / "source.glb").exists()
    assert not (root / "modeling" / "source.blend").exists()
