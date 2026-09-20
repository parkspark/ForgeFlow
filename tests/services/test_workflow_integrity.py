from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from forgeflow.adapters.blender_adapter import BlenderAdapter
from forgeflow.adapters.modeling_adapter import ModelingAdapter
from forgeflow.adapters.unity.project import copy_humanoid_fbx, validate_import_selection
from forgeflow.adapters.unity.receipts import collect_changed_assets, parse_receipt
from forgeflow.domain.artifact import Artifact
from forgeflow.domain.job import BlenderRequest, UnityTurn, utc_now
from forgeflow.services.asset_validation import validate_glb
from forgeflow.services.job_service import JobService, sha256_file
from forgeflow.services.pipeline_service import PipelineService
from tests.adapters.test_rigging_adapter import make_adapter, produce
from tests.asset_fixtures import glb_bytes, png_bytes


def unity_project(root):
    (root / "Assets").mkdir(parents=True)
    (root / "ProjectSettings").mkdir()
    return root


def test_rerigging_invalidates_import_and_current_approval_without_erasing_history(
    config, image, tmp_path
):
    _, jobs, job, rig, _ = make_adapter(config, image)
    old = UnityTurn(
        "old", "session", "old", "old", status="succeeded", human_review_status="accepted"
    )
    job.unity_turns.append(old)
    job.stages["unity"].status = "completed"
    job.unity_asset_path = "Assets/old.fbx"
    job.latest_unity_scene_path = "Assets/old.unity"
    _, run = rig.build_rigging(job)
    produce(run)
    artifacts, _ = rig.collect_rigging(run)
    rig.register_success(job, run, artifacts)
    restored = jobs.load(job.job_id)
    assert restored.blender_input_path == restored.humanoid_blend_path
    assert restored.unity_asset_path is None
    assert restored.latest_unity_scene_path is None
    assert restored.stages["unity"].status == "stale"
    assert restored.unity_turns[-1].human_review_status == "accepted"
    with pytest.raises(ValueError, match="이전 입력"):
        jobs.review_unity_turn(restored, old.turn_id, "accepted")
    project = unity_project(tmp_path / "Unity")
    with pytest.raises(ValueError, match="가져오"):
        validate_import_selection(restored, project)


@pytest.mark.parametrize("rigged", [False, True])
@pytest.mark.parametrize("formats", [("blend", "fbx", "glb"), ("blend",)])
def test_edit_completion_keeps_native_master_and_routes_only_valid_rig_to_unity(
    config, image, rigged, formats
):
    jobs = JobService(config.jobs_root)
    job = jobs.create("edit", image)
    job.stages["rigging"].status = "completed"
    job.stages["unity"].status = "completed"
    job.unity_asset_path = "Assets/old.fbx"
    job.unity_input_path = "old.fbx"
    outputs = [
        Artifact(kind, str(jobs.root / f"new.{kind}"), "blender", utc_now()) for kind in formats
    ]
    request = BlenderRequest("edit", "source.blend", str(jobs.root), 2, preserve_rig=rigged)
    signal = SimpleNamespace(emit=lambda *args: None)
    handler = SimpleNamespace(
        jobs=jobs,
        blender=SimpleNamespace(collect_execution=lambda *args: outputs),
        job_changed=signal,
        operation_finished=signal,
    )
    PipelineService._execution_finished(
        handler, job, request, jobs.root / "execution.json", "digest", 0
    )
    assert job.blender_input_path.endswith("new.blend")
    assert job.unity_asset_path is None
    assert job.stages["unity"].status == "stale"
    if rigged:
        assert job.stages["rigging"].status == "completed"
        if "fbx" in formats:
            assert job.unity_input_path.endswith("new.fbx")
        else:
            assert job.unity_input_path is None
            assert "FBX를 내보낸 뒤" in job.stages["unity"].stale_reason
    else:
        assert job.unity_input_path is None
        assert job.stages["rigging"].status == "stale"


def test_import_rejects_changed_source_wrong_project_and_empty_fbx(config, image, tmp_path):
    jobs = JobService(config.jobs_root)
    job = jobs.create("import", image)
    source = tmp_path / "rig.fbx"
    source.write_bytes(b"registered FBX fixture")
    job.unity_input_path = str(source)
    project = unity_project(tmp_path / "Unity")
    imported = copy_humanoid_fbx(job, project)
    job.unity_asset_path = imported.asset_path
    job.unity_import_source_path = imported.source_path
    job.unity_import_source_sha256 = imported.source_sha256
    job.unity_import_project_path = str(project)
    validate_import_selection(job, project)
    with pytest.raises(ValueError, match="다른 Unity"):
        validate_import_selection(job, unity_project(tmp_path / "Other"))
    source.write_bytes(b"new source version")
    with pytest.raises(ValueError, match="다릅니다"):
        validate_import_selection(job, project)
    source.write_bytes(b"")
    with pytest.raises(ValueError, match="비어"):
        copy_humanoid_fbx(job, project)


def test_interrupted_collection_recovers_without_recreating_or_overwriting_model(
    config, image, monkeypatch
):
    jobs = JobService(config.jobs_root)
    job = jobs.create("recover collection", image)
    adapter = ModelingAdapter(config, jobs)
    run = jobs.job_directory(job.job_id) / ".runs" / "modeling-001"
    source = run / "source"
    source.mkdir(parents=True)
    for suffix in adapter.REQUIRED:
        (source / f"source.{suffix}").write_bytes((suffix + " immutable fixture").encode())
    original_copy = shutil.copy2
    calls = []

    def interrupt(src, dst):
        calls.append(str(src))
        if len(calls) == 2:
            raise OSError("disk interrupted")
        return original_copy(src, dst)

    with monkeypatch.context() as patch:
        patch.setattr(shutil, "copy2", interrupt)
        with pytest.raises(OSError):
            adapter.collect_generation(job, run)
    first = jobs.job_directory(job.job_id) / "modeling" / "source.glb"
    first_mtime = first.stat().st_mtime_ns
    recovered = adapter.existing_generation(jobs.load(job.job_id))
    assert {item.kind for item in recovered} == set(adapter.REQUIRED)
    assert first.stat().st_mtime_ns == first_mtime
    assert len(jobs.load(job.job_id).artifacts) == 3
    assert all(
        sha256_file(Path(item.path)) == sha256_file(source / f"source.{item.kind}")
        for item in recovered
    )


@pytest.mark.parametrize("payload", [b"not a model", glb_bytes()[:-4]])
def test_invalid_glb_is_rejected_before_engine_start(tmp_path, payload):
    path = tmp_path / "bad.glb"
    path.write_bytes(payload)
    with pytest.raises(ValueError, match="GLB"):
        validate_glb(path)


def test_image_decode_and_preview_freshness(config, image, tmp_path):
    bad = tmp_path / "invalid.png"
    bad.write_bytes(b"not an image")
    with pytest.raises(ValueError, match="이미지"):
        JobService.validate_image(bad)
    adapter = ModelingAdapter(config, JobService(config.jobs_root))
    preview = tmp_path / "preview.png"
    preview.write_bytes(png_bytes())
    adapter.validate_preview(preview)
    from forgeflow.services.asset_validation import preview_signature

    adapter._preview_baselines[preview] = preview_signature(preview)
    with pytest.raises(RuntimeError, match="갱신"):
        adapter.validate_preview(preview)


def test_fbx_only_export_obeys_approved_contract_and_rig_evidence(config, image, tmp_path):
    jobs = JobService(config.jobs_root)
    job = jobs.create("export", image)
    source = tmp_path / "source.blend"
    source.write_bytes(b"immutable rig")
    output = jobs.job_directory(job.job_id) / "blender" / "v001"
    output.mkdir()
    fbx = output / "edited.fbx"
    fbx.write_bytes(b"edited FBX fixture")
    request = BlenderRequest(
        "FBX only",
        str(source),
        str(output),
        1,
        plan={"steps": [{"tool": "asset.export", "arguments": {"formats": ["fbx"]}}]},
        plan_sha256="fixture",
        preserve_rig=True,
    )
    result = {"plan_sha256": "fixture", "result": {"status": "completed", "artifacts": [str(fbx)]}}
    execution = output / "execution.json"
    execution.write_text(json.dumps(result))
    adapter = BlenderAdapter(config, jobs)
    with pytest.raises(RuntimeError, match="리깅 보존"):
        adapter.collect_execution(request, execution, sha256_file(source))
    result["result"]["data"] = {
        "has_armature": True,
        "preservation": {"scope": "native_scene", "failures": []},
    }
    execution.write_text(json.dumps(result))
    with pytest.raises(RuntimeError, match="리깅 보존"):
        adapter.collect_execution(request, execution, sha256_file(source))
    result["result"]["data"]["preservation"]["status"] = "passed"
    execution.write_text(json.dumps(result))
    artifacts = adapter.collect_execution(request, execution, sha256_file(source))
    assert {item.kind for item in artifacts} == {"fbx", "blender_report"}


@pytest.mark.parametrize(
    "extra, expected", [({}, "partial"), ({"failures": ["animation_stopped"]}, "failed")]
)
def test_receipt_cannot_claim_complete_with_unmeasured_requests(tmp_path, extra, expected):
    receipt = tmp_path / "receipt.json"
    receipt.write_text(
        json.dumps(
            {
                "status": "verified",
                "requested_checks": ["scene_objects", "animation_playback"],
                "measured_checks": ["scene_objects"],
                **extra,
            }
        )
    )
    result = parse_receipt(receipt)
    assert result["automated_status"] == expected
    assert result["missing_checks"] == ["animation_playback"]


def test_animation_manifest_tracks_only_documented_mutation_outputs(tmp_path):
    path = tmp_path / "audit.jsonl"
    path.write_text(
        json.dumps(
            {
                "event": "tool_result",
                "name": "unity_create_animation_clip",
                "result": {
                    "status": "ok",
                    "result": {
                        "saved": True,
                        "changedAssets": [
                            "Assets/Test/Idle.anim",
                            "Assets/Test/Idle.anim.meta",
                            "Assets/../bad.anim",
                            "Assets/Input.fbx",
                        ],
                    },
                },
            }
        )
    )
    assert collect_changed_assets(path) == ["Assets/Test/Idle.anim", "Assets/Test/Idle.anim.meta"]


def test_blender_path_is_forwarded_to_rigging_and_generation(config, image):
    _, jobs, job, rig, _ = make_adapter(config, image)
    command, _ = rig.build_rigging(job)
    assert command.arguments[command.arguments.index("-BlenderExecutable") + 1] == str(
        config.blender_executable
    )
    (config.modeling_root / "scripts" / "generate_model.ps1").write_text("param()")
    new_job = jobs.create("new model", image)
    model_command, _ = ModelingAdapter(config, jobs).build_generation(new_job)
    assert model_command.arguments[model_command.arguments.index("-BlenderExecutable") + 1] == str(
        config.blender_executable
    )


def test_rig_report_v2_rejects_hidden_mesh_errors(config, image):
    _, _, job, rig, _ = make_adapter(config, image)
    _, run = rig.build_rigging(job)
    produce(run, schema_version=2, validation_errors=[], mesh_reports=[{"errors": ["unweighted"]}])
    with pytest.raises(RuntimeError, match="메시별"):
        rig.collect_rigging(run)


def test_plan_rejects_changed_unregistered_legacy_input(config, image, tmp_path):
    config.agent_python.parent.mkdir(parents=True)
    config.agent_python.touch()
    jobs = JobService(config.jobs_root)
    job = jobs.create("legacy input", image)
    source = tmp_path / "legacy.blend"
    source.write_bytes(b"first revision")
    job.blender_input_path = str(source)
    adapter = BlenderAdapter(config, jobs)
    _, request = adapter.build_proposal(job, "색상 수정")
    request.status = "awaiting_approval"
    request.plan = {"steps": [{"tool": "material.tint"}]}
    request.plan_sha256 = "fixture"
    source.write_bytes(b"changed after planning")
    with pytest.raises(RuntimeError, match="계획을 만든 뒤"):
        adapter.build_execute(job, request)
