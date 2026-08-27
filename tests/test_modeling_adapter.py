from __future__ import annotations

from pathlib import Path

import pytest

from forgeflow.adapters.modeling_adapter import ModelingAdapter
from forgeflow.services.job_service import JobService


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

